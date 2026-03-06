from io import BytesIO
from urllib.error import HTTPError, URLError

from fastapi import HTTPException

from app.monitoring_providers import (
    build_provider_error_detail,
    load_json_from_provider,
    probe_monitoring_provider,
)


class _MockResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_load_json_from_provider_returns_payload_and_healthy_status(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.monitoring_providers.urlrequest.urlopen",
        lambda *args, **kwargs: _MockResponse(b'{"status":"success"}'),
    )

    payload, provider_status = load_json_from_provider(
        provider="prometheus",
        endpoint="http://prometheus.local/api/v1/query?query=1",
        correlation_id="cid-1",
    )

    assert payload == {"status": "success"}
    assert provider_status["provider"] == "prometheus"
    assert provider_status["status"] == "healthy"
    assert provider_status["correlationId"] == "cid-1"


def test_load_json_from_provider_raises_structured_http_error(monkeypatch) -> None:
    def _mock_urlopen(*args, **kwargs):
        raise HTTPError(
            url="http://loki.local/loki/api/v1/query_range",
            code=503,
            msg="Service Unavailable",
            hdrs=None,
            fp=BytesIO(b'{"status":"error","error":"provider down"}'),
        )

    monkeypatch.setattr("app.monitoring_providers.urlrequest.urlopen", _mock_urlopen)

    try:
        load_json_from_provider(
            provider="loki",
            endpoint="http://loki.local/loki/api/v1/query_range?query=test",
            correlation_id="cid-loki",
        )
    except HTTPException as exc:
        detail = exc.detail
        assert detail["message"] == "Monitoring provider query failed."
        assert detail["correlationId"] == "cid-loki"
        assert detail["providerStatus"]["provider"] == "loki"
        assert detail["providerStatus"]["httpStatus"] == 503
    else:
        raise AssertionError("Expected HTTPException")


def test_probe_monitoring_provider_classifies_auth_and_network_failures(monkeypatch) -> None:
    def _mock_urlopen(request, timeout=0):
        url = getattr(request, "full_url", "")
        if "alertmanager" in url:
            raise HTTPError(
                url=url,
                code=401,
                msg="Unauthorized",
                hdrs=None,
                fp=BytesIO(b"unauthorized"),
            )
        raise URLError("connection refused")

    monkeypatch.setattr("app.monitoring_providers.urlrequest.urlopen", _mock_urlopen)

    alertmanager = probe_monitoring_provider("alertmanager", correlation_id="cid-alerts")
    loki = probe_monitoring_provider("loki", correlation_id="cid-loki")

    assert alertmanager["status"] == "auth_error"
    assert alertmanager["reachable"] is True
    assert alertmanager["httpStatus"] == 401
    assert loki["status"] == "unreachable"
    assert loki["reachable"] is False


def test_build_provider_error_detail_includes_correlation_id() -> None:
    detail = build_provider_error_detail(
        message="Monitoring provider query failed.",
        provider_status={
            "provider": "prometheus",
            "baseUrl": "http://prometheus.local",
            "status": "unreachable",
            "reachable": False,
            "checkedAt": "2026-03-06T00:00:00+00:00",
            "correlationId": "cid-prom",
        },
    )

    assert detail["message"] == "Monitoring provider query failed."
    assert detail["correlationId"] == "cid-prom"
    assert detail["providerStatus"]["provider"] == "prometheus"
