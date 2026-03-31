from __future__ import annotations

import base64
from io import BytesIO
import json
import logging
from urllib.error import HTTPError

import pytest

from app.lib.git_service import (
    GitHubGitProvider,
    GitHubGitService,
    GitProvider,
    GitService,
    GitServiceAuthError,
    GitServiceConfigurationError,
    GitServiceConflictError,
    build_default_git_provider,
    build_default_git_service,
)


class _MockResponse:
    def __init__(self, payload: object):
        if isinstance(payload, bytes):
            self._payload = payload
        else:
            self._payload = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _http_error(*, url: str, status_code: int, payload: object, message: str, headers=None) -> HTTPError:
    encoded = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    return HTTPError(
        url=url,
        code=status_code,
        msg=message,
        hdrs=headers,
        fp=BytesIO(encoded),
    )


def test_create_branch_returns_branch_ref() -> None:
    requests: list[tuple[str, str, object | None]] = []

    def _urlopen(request, timeout=0):
        requests.append((request.get_method(), request.full_url, request.data))
        if request.get_method() == "GET":
            return _MockResponse(
                {
                    "ref": "refs/heads/main",
                    "object": {"sha": "abc123", "url": "https://api.github.com/ref/main"},
                }
            )
        return _MockResponse(
            {
                "ref": "refs/heads/portal/dev/test",
                "object": {"sha": "abc123", "url": "https://api.github.com/ref/portal/dev/test"},
            }
        )

    service = GitHubGitService(token="test-token", urlopen_func=_urlopen)

    result = service.create_branch("example/workloads", "main", "portal/dev/test")

    assert result == {
        "branch": "portal/dev/test",
        "ref": "refs/heads/portal/dev/test",
        "sha": "abc123",
        "url": "https://api.github.com/ref/portal/dev/test",
    }
    assert requests[0][0] == "GET"
    assert requests[1][0] == "POST"
    assert json.loads(requests[1][2].decode("utf-8")) == {
        "ref": "refs/heads/portal/dev/test",
        "sha": "abc123",
    }


def test_modify_file_replaces_contents_and_returns_diff() -> None:
    requests: list[tuple[str, str, object | None]] = []
    existing = base64.b64encode(b"replicas: 1\n").decode("ascii")

    def _urlopen(request, timeout=0):
        requests.append((request.get_method(), request.full_url, request.data))
        if request.get_method() == "GET":
            return _MockResponse(
                {
                    "sha": "file-sha-1",
                    "encoding": "base64",
                    "content": existing,
                }
            )
        return _MockResponse(
            {
                "content": {"sha": "file-sha-2"},
                "commit": {"sha": "commit-sha-2"},
            }
        )

    service = GitHubGitService(token="test-token", urlopen_func=_urlopen)

    result = service.modify_file(
        "example/workloads",
        "main",
        "apps/demo/envs/dev/patch.yaml",
        "replicas: 2\n",
    )

    assert result["path"] == "apps/demo/envs/dev/patch.yaml"
    assert result["sha"] == "file-sha-2"
    assert result["commit_sha"] == "commit-sha-2"
    assert "-replicas: 1" in result["diff"]
    assert "+replicas: 2" in result["diff"]
    assert "ref=main" in requests[0][1]
    assert json.loads(requests[1][2].decode("utf-8")) == {
        "message": "chore(gitops): update apps/demo/envs/dev/patch.yaml",
        "content": base64.b64encode(b"replicas: 2\n").decode("ascii"),
        "branch": "main",
        "sha": "file-sha-1",
    }


def test_read_file_returns_decoded_contents() -> None:
    existing = base64.b64encode(b"replicas: 1\n").decode("ascii")

    def _urlopen(request, timeout=0):
        assert request.get_method() == "GET"
        assert "ref=main" in request.full_url
        return _MockResponse(
            {
                "sha": "file-sha-1",
                "encoding": "base64",
                "content": existing,
            }
        )

    service = GitHubGitService(token="test-token", urlopen_func=_urlopen)

    assert service.read_file("example/workloads", "main", "apps/demo/envs/dev/patch.yaml") == "replicas: 1\n"


def test_open_pr_returns_minimal_object() -> None:
    def _urlopen(request, timeout=0):
        assert request.get_method() == "POST"
        body = json.loads(request.data.decode("utf-8"))
        assert body == {
            "title": "chore(dev): update demo",
            "head": "portal/dev/demo-123",
            "base": "main",
            "body": "Automated change",
        }
        return _MockResponse(
            {
                "id": 17,
                "number": 9,
                "html_url": "https://github.com/example/workloads/pull/9",
                "state": "open",
                "node_id": "ignored",
            }
        )

    service = GitHubGitService(token="test-token", urlopen_func=_urlopen)

    result = service.open_pr(
        "example/workloads",
        "portal/dev/demo-123",
        "main",
        "chore(dev): update demo",
        "Automated change",
    )

    assert result == {
        "id": 17,
        "url": "https://github.com/example/workloads/pull/9",
        "number": 9,
        "state": "open",
    }


def test_list_files_returns_filtered_recursive_blob_paths() -> None:
    def _urlopen(request, timeout=0):
        url = request.full_url
        method = request.get_method()
        if method == "GET" and url.endswith("/git/ref/heads/main"):
            return _MockResponse(
                {
                    "ref": "refs/heads/main",
                    "object": {"sha": "head-sha", "url": "https://api.github.com/ref/main"},
                }
            )
        if method == "GET" and url.endswith("/git/commits/head-sha"):
            return _MockResponse({"sha": "head-sha", "tree": {"sha": "base-tree-sha"}})
        if method == "GET" and "recursive=1" in url and url.endswith("/git/trees/base-tree-sha?recursive=1"):
            return _MockResponse(
                {
                    "tree": [
                        {"path": "apps/demo/base/deployment.yaml", "type": "blob"},
                        {"path": "apps/demo/base", "type": "tree"},
                        {"path": "apps/demo/envs/dev/patch-deployment.yaml", "type": "blob"},
                        {"path": "services.yaml", "type": "blob"},
                    ]
                }
            )
        raise AssertionError(f"Unexpected request: {method} {url}")

    service = GitHubGitService(token="test-token", urlopen_func=_urlopen)

    result = service.list_files("example/workloads", "main", "apps/demo")

    assert result == [
        "apps/demo/base/deployment.yaml",
        "apps/demo/envs/dev/patch-deployment.yaml",
    ]


def test_commit_to_branch_creates_single_multi_file_commit() -> None:
    requests: list[tuple[str, str, object | None]] = []
    blob_counter = 0

    def _urlopen(request, timeout=0):
        nonlocal blob_counter
        requests.append((request.get_method(), request.full_url, request.data))
        url = request.full_url
        method = request.get_method()
        if method == "GET" and url.endswith("/git/ref/heads/main"):
            return _MockResponse(
                {
                    "ref": "refs/heads/main",
                    "object": {"sha": "head-sha", "url": "https://api.github.com/ref/main"},
                }
            )
        if method == "GET" and url.endswith("/git/commits/head-sha"):
            return _MockResponse({"sha": "head-sha", "tree": {"sha": "base-tree-sha"}})
        if method == "POST" and url.endswith("/git/blobs"):
            blob_counter += 1
            return _MockResponse({"sha": f"blob-sha-{blob_counter}"})
        if method == "POST" and url.endswith("/git/trees"):
            payload = json.loads(request.data.decode("utf-8"))
            assert payload["base_tree"] == "base-tree-sha"
            assert len(payload["tree"]) == 2
            return _MockResponse({"sha": "tree-sha-2"})
        if method == "POST" and url.endswith("/git/commits"):
            payload = json.loads(request.data.decode("utf-8"))
            assert payload == {
                "message": "chore(dev): update overlay files",
                "tree": "tree-sha-2",
                "parents": ["head-sha"],
            }
            return _MockResponse({"sha": "commit-sha-2"})
        if method == "PATCH" and url.endswith("/git/refs/heads/main"):
            payload = json.loads(request.data.decode("utf-8"))
            assert payload == {"sha": "commit-sha-2", "force": False}
            return _MockResponse({"ref": "refs/heads/main"})
        raise AssertionError(f"Unexpected request: {method} {url}")

    service = GitHubGitService(token="test-token", urlopen_func=_urlopen)

    result = service.commit_to_branch(
        "example/workloads",
        "main",
        {
            "apps/demo/envs/dev/patch.yaml": "replicas: 2\n",
            "apps/demo/envs/dev/kustomization.yaml": "resources:\n- ../../base\n",
        },
        "chore(dev): update overlay files",
    )

    assert result == {
        "branch": "main",
        "commit_sha": "commit-sha-2",
        "tree_sha": "tree-sha-2",
        "files": [
            "apps/demo/envs/dev/kustomization.yaml",
            "apps/demo/envs/dev/patch.yaml",
        ],
    }
    assert len(requests) == 7


def test_commit_to_branch_supports_file_deletions() -> None:
    def _urlopen(request, timeout=0):
        url = request.full_url
        method = request.get_method()
        if method == "GET" and url.endswith("/git/ref/heads/main"):
            return _MockResponse(
                {
                    "ref": "refs/heads/main",
                    "object": {"sha": "head-sha", "url": "https://api.github.com/ref/main"},
                }
            )
        if method == "GET" and url.endswith("/git/commits/head-sha"):
            return _MockResponse({"sha": "head-sha", "tree": {"sha": "base-tree-sha"}})
        if method == "POST" and url.endswith("/git/blobs"):
            return _MockResponse({"sha": "blob-sha-1"})
        if method == "POST" and url.endswith("/git/trees"):
            payload = json.loads(request.data.decode("utf-8"))
            assert payload["base_tree"] == "base-tree-sha"
            assert payload["tree"] == [
                {
                    "path": "services.yaml",
                    "mode": "100644",
                    "type": "blob",
                    "sha": "blob-sha-1",
                },
                {
                    "path": "apps/demo/base/deployment.yaml",
                    "mode": "100644",
                    "type": "blob",
                    "sha": None,
                },
            ]
            return _MockResponse({"sha": "tree-sha-2"})
        if method == "POST" and url.endswith("/git/commits"):
            return _MockResponse({"sha": "commit-sha-2"})
        if method == "PATCH" and url.endswith("/git/refs/heads/main"):
            return _MockResponse({"ref": "refs/heads/main"})
        raise AssertionError(f"Unexpected request: {method} {url}")

    service = GitHubGitService(token="test-token", urlopen_func=_urlopen)

    result = service.commit_to_branch(
        "example/workloads",
        "main",
        {
            "services.yaml": "services: []\n",
            "apps/demo/base/deployment.yaml": None,
        },
        "chore(decommission): remove demo from workloads",
    )

    assert result["files"] == [
        "apps/demo/base/deployment.yaml",
        "services.yaml",
    ]


def test_close_pr_returns_closed_minimal_object() -> None:
    def _urlopen(request, timeout=0):
        assert request.get_method() == "PATCH"
        assert request.full_url.endswith("/repos/example/workloads/pulls/12")
        assert json.loads(request.data.decode("utf-8")) == {"state": "closed"}
        return _MockResponse(
            {
                "id": 99,
                "number": 12,
                "html_url": "https://github.com/example/workloads/pull/12",
                "state": "closed",
            }
        )

    service = GitHubGitService(token="test-token", urlopen_func=_urlopen)

    result = service.close_pr("example/workloads", 12)

    assert result == {
        "id": 99,
        "url": "https://github.com/example/workloads/pull/12",
        "number": 12,
        "state": "closed",
    }


def test_open_pr_raises_auth_error_on_unauthorized_response() -> None:
    def _urlopen(request, timeout=0):
        raise _http_error(
            url=request.full_url,
            status_code=401,
            payload={"message": "Bad credentials"},
            message="Unauthorized",
        )

    service = GitHubGitService(token="test-token", urlopen_func=_urlopen)

    with pytest.raises(GitServiceAuthError) as exc_info:
        service.open_pr("example/workloads", "feature", "main", "title", "body")

    assert exc_info.value.status_code == 401
    assert "Bad credentials" in str(exc_info.value)


def test_modify_file_raises_conflict_error_when_update_is_out_of_date() -> None:
    existing = base64.b64encode(b"replicas: 1\n").decode("ascii")

    def _urlopen(request, timeout=0):
        if request.get_method() == "GET":
            return _MockResponse(
                {
                    "sha": "file-sha-1",
                    "encoding": "base64",
                    "content": existing,
                }
            )
        raise _http_error(
            url=request.full_url,
            status_code=409,
            payload={"message": "Update is not a fast forward"},
            message="Conflict",
        )

    service = GitHubGitService(token="test-token", urlopen_func=_urlopen)

    with pytest.raises(GitServiceConflictError) as exc_info:
        service.modify_file("example/workloads", "main", "apps/demo/dev.yaml", "replicas: 2\n")

    assert exc_info.value.status_code == 409
    assert "fast forward" in str(exc_info.value)


def test_open_pr_retries_rate_limit_then_succeeds() -> None:
    attempts = 0
    sleep_calls: list[float] = []

    def _sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    def _urlopen(request, timeout=0):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise _http_error(
                url=request.full_url,
                status_code=429,
                payload={"message": "You have exceeded a secondary rate limit."},
                message="Too Many Requests",
                headers={"Retry-After": "0"},
            )
        return _MockResponse(
            {
                "id": 7,
                "number": 5,
                "html_url": "https://github.com/example/workloads/pull/5",
                "state": "open",
            }
        )

    service = GitHubGitService(
        token="test-token",
        urlopen_func=_urlopen,
        sleep_func=_sleep,
        retry_attempts=1,
    )

    result = service.open_pr("example/workloads", "feature", "main", "title", "body")

    assert result["number"] == 5
    assert attempts == 2
    assert sleep_calls == [0.0]


def test_dry_run_logs_and_skips_network_calls(caplog) -> None:
    def _urlopen(_request, timeout=0):
        raise AssertionError("dry-run should not reach urlopen")

    service = GitHubGitService(token="test-token", dry_run=True, urlopen_func=_urlopen)

    with caplog.at_level(logging.INFO):
        branch = service.create_branch("example/workloads", "main", "portal/dev/test")
        pull_request = service.open_pr("example/workloads", "portal/dev/test", "main", "title", "body")
        commit = service.commit_to_branch(
            "example/workloads",
            "main",
            {"apps/demo/dev.yaml": "replicas: 2\n"},
            "message",
        )

    assert branch["sha"] == "dry-run"
    assert pull_request["state"] == "dry-run"
    assert commit["commit_sha"] == "dry-run"
    assert "git_service_dry_run action=create_branch" in caplog.text
    assert "git_service_dry_run action=open_pr" in caplog.text
    assert "git_service_dry_run action=commit_to_branch" in caplog.text


def test_build_default_git_provider_selects_github(monkeypatch) -> None:
    monkeypatch.setenv("GIT_PROVIDER", "github")
    monkeypatch.setenv("GIT_GITHUB_TOKEN", "test-token")

    provider = build_default_git_provider(dry_run=True)

    assert isinstance(provider, GitHubGitProvider)


def test_build_default_git_service_retains_backward_compatible_factory(monkeypatch) -> None:
    monkeypatch.setenv("GIT_PROVIDER", "github")
    monkeypatch.setenv("GIT_GITHUB_TOKEN", "test-token")

    provider = build_default_git_service(dry_run=True)

    assert isinstance(provider, GitHubGitProvider)


def test_git_service_alias_points_to_git_provider_protocol() -> None:
    assert GitService is GitProvider


def test_build_default_git_provider_rejects_unsupported_provider(monkeypatch) -> None:
    monkeypatch.setenv("GIT_PROVIDER", "gitlab")
    monkeypatch.setenv("GIT_GITHUB_TOKEN", "test-token")

    with pytest.raises(GitServiceConfigurationError) as exc_info:
        build_default_git_provider(dry_run=True)

    assert "Unsupported GIT_PROVIDER" in str(exc_info.value)
