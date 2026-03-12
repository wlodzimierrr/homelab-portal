from __future__ import annotations

from io import BytesIO
import json
from urllib.error import HTTPError

import pytest

from app.main import PortalPromoteToProdError, _ensure_ghcr_tag_exists


class _MockResponse:
    def __init__(self, payload: object):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _http_error(*, url: str, status_code: int, payload: object, message: str) -> HTTPError:
    encoded = json.dumps(payload).encode("utf-8")
    return HTTPError(
        url=url,
        code=status_code,
        msg=message,
        hdrs={},
        fp=BytesIO(encoded),
    )


def test_ensure_ghcr_tag_exists_accepts_tag_from_github_packages(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[str] = []

    def _urlopen(request, timeout=0):
        requests.append(request.full_url)
        assert request.headers["Authorization"] == "Bearer read-token"
        return _MockResponse(
            [
                {
                    "metadata": {
                        "container": {
                            "tags": [
                                "sha-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                                "latest",
                            ]
                        }
                    }
                }
            ]
        )

    monkeypatch.setattr("app.main._ghcr_token", lambda: "read-token")
    monkeypatch.setattr("app.main._github_api_base_url", lambda: "https://api.github.test")
    monkeypatch.setattr("app.main.urlrequest.urlopen", _urlopen)

    _ensure_ghcr_tag_exists(
        "ghcr.io/wlodzimierrr/homelab-web",
        "sha-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )

    assert requests == [
        "https://api.github.test/users/wlodzimierrr/packages/container/homelab-web/versions?per_page=100&page=1"
    ]


def test_ensure_ghcr_tag_exists_tries_org_path_after_user_404(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[str] = []

    def _urlopen(request, timeout=0):
        requests.append(request.full_url)
        if "/users/" in request.full_url:
            raise _http_error(
                url=request.full_url,
                status_code=404,
                payload={"message": "Not Found"},
                message="Not Found",
            )
        return _MockResponse(
            [
                {
                    "metadata": {
                        "container": {
                            "tags": [
                                "sha-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                            ]
                        }
                    }
                }
            ]
        )

    monkeypatch.setattr("app.main._ghcr_token", lambda: "read-token")
    monkeypatch.setattr("app.main._github_api_base_url", lambda: "https://api.github.test")
    monkeypatch.setattr("app.main.urlrequest.urlopen", _urlopen)

    _ensure_ghcr_tag_exists(
        "ghcr.io/wlodzimierrr/homelab-api",
        "sha-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    )

    assert requests == [
        "https://api.github.test/users/wlodzimierrr/packages/container/homelab-api/versions?per_page=100&page=1",
        "https://api.github.test/orgs/wlodzimierrr/packages/container/homelab-api/versions?per_page=100&page=1",
    ]


def test_ensure_ghcr_tag_exists_raises_conflict_when_tag_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def _urlopen(request, timeout=0):
        if "page=1" in request.full_url and "/users/" in request.full_url:
            return _MockResponse([])
        raise _http_error(
            url=request.full_url,
            status_code=404,
            payload={"message": "Not Found"},
            message="Not Found",
        )

    monkeypatch.setattr("app.main._ghcr_token", lambda: "read-token")
    monkeypatch.setattr("app.main._github_api_base_url", lambda: "https://api.github.test")
    monkeypatch.setattr("app.main.urlrequest.urlopen", _urlopen)

    with pytest.raises(PortalPromoteToProdError) as exc_info:
        _ensure_ghcr_tag_exists(
            "ghcr.io/wlodzimierrr/homelab-web",
            "sha-cccccccccccccccccccccccccccccccccccccccc",
        )

    assert exc_info.value.status_code == 409
    assert "was not found in GitHub Packages" in str(exc_info.value)
