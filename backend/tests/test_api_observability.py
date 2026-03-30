import json
from io import BytesIO
from urllib import parse as urlparse
from urllib.error import HTTPError

from app.logs_quickview import clear_rate_limit_state_for_tests



class _MockPrometheusResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_health_endpoint(client) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_endpoint_supports_provider_checks(client, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.main.probe_monitoring_provider",
        lambda provider, correlation_id: {
            "provider": provider,
            "baseUrl": f"http://{provider}.local",
            "status": "healthy",
            "reachable": True,
            "checkedAt": "2026-03-06T00:00:00+00:00",
            "correlationId": correlation_id,
        },
    )

    response = client.get("/health?includeProviders=true")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert [item["provider"] for item in body["providers"]] == [
        "prometheus",
        "loki",
        "alertmanager",
    ]


def test_monitoring_provider_diagnostics_reports_reachability(client, monkeypatch) -> None:
    statuses = {
        "prometheus": {
            "provider": "prometheus",
            "baseUrl": "http://prometheus.local",
            "status": "healthy",
            "reachable": True,
            "checkedAt": "2026-03-06T00:00:00+00:00",
            "correlationId": "cid-prom",
        },
        "loki": {
            "provider": "loki",
            "baseUrl": "http://loki.local",
            "status": "unreachable",
            "reachable": False,
            "checkedAt": "2026-03-06T00:00:00+00:00",
            "correlationId": "cid-loki",
            "error": "connection refused",
        },
        "alertmanager": {
            "provider": "alertmanager",
            "baseUrl": "http://alertmanager.local",
            "status": "auth_error",
            "reachable": True,
            "checkedAt": "2026-03-06T00:00:00+00:00",
            "correlationId": "cid-alerts",
            "httpStatus": 401,
            "error": "unauthorized",
        },
    }
    monkeypatch.setattr(
        "app.main.probe_monitoring_provider",
        lambda provider, correlation_id: (
            statuses[provider] | {"correlationId": correlation_id}
        ),
    )

    response = client.get(
        "/monitoring/providers/diagnostics",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["overallStatus"] == "degraded"
    assert len(body["providers"]) == 3
    assert body["providers"][1]["provider"] == "loki"
    assert body["providers"][1]["status"] == "unreachable"
    assert body["providers"][2]["provider"] == "alertmanager"
    assert body["providers"][2]["status"] == "auth_error"


def test_service_metrics_summary_success_with_supported_range(client, monkeypatch) -> None:
    payloads = iter(
        [
            {"status": "success", "data": {"result": [{"value": [0, "99.95"]}]}},
            {"status": "success", "data": {"result": [{"value": [0, "320"]}]}},
            {"status": "success", "data": {"result": [{"value": [0, "0.42"]}]}},
            {"status": "success", "data": {"result": [{"value": [0, "3"]}]}},
        ]
    )

    def _mock_urlopen(*args, **kwargs):
        return _MockPrometheusResponse(next(payloads))

    monkeypatch.setattr("app.monitoring_providers.urlrequest.urlopen", _mock_urlopen)
    monkeypatch.setattr(
        "app.main._resolve_service_monitoring_context",
        lambda _service_id: ("team-space", "portal-api", None),
    )

    response = client.get(
        "/services/homelab-api/metrics/summary?range=24h",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["serviceId"] == "homelab-api"
    assert body["uptimePct"] == 99.95
    assert body["p95LatencyMs"] == 320.0
    assert body["errorRatePct"] == 0.42
    assert body["restartCount"] == 3.0
    assert body["windowStart"]
    assert body["windowEnd"]
    assert body["generatedAt"]
    assert body["noData"] == {
        "uptimePct": False,
        "p95LatencyMs": False,
        "errorRatePct": False,
        "restartCount": False,
    }
    assert body["providerStatus"]["provider"] == "prometheus"
    assert body["providerStatus"]["status"] == "healthy"


def test_service_metrics_summary_uses_service_registry_metadata_for_queries(client, monkeypatch,) -> None:
    requested_urls: list[str] = []
    payloads = iter(
        [
            {"status": "success", "data": {"result": []}},
            {"status": "success", "data": {"result": []}},
            {"status": "success", "data": {"result": []}},
            {"status": "success", "data": {"result": []}},
            {"status": "success", "data": {"result": []}},
            {"status": "success", "data": {"result": []}},
        ]
    )

    def _mock_urlopen(request, **kwargs):
        requested_urls.append(getattr(request, "full_url", request))
        return _MockPrometheusResponse(next(payloads))

    monkeypatch.setattr("app.monitoring_providers.urlrequest.urlopen", _mock_urlopen)
    monkeypatch.setattr(
        "app.main._resolve_service_monitoring_context",
        lambda _service_id: ("homelab-api", "homelab-api", None),
    )

    response = client.get(
        "/services/homelab-api/metrics/summary?range=24h",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    assert any("namespace%3D%22homelab-api%22" in url for url in requested_urls)
    assert any("app%3D%22homelab-api%22" in url for url in requested_urls)
    assert not any("namespace%3D%22default%22" in url for url in requested_urls)


def test_service_metrics_summary_rejects_invalid_range(client) -> None:
    response = client.get(
        "/services/homelab-api/metrics/summary?range=2h",
        headers={"Authorization": "Bearer dev-static-token"},
    )
    assert response.status_code == 422


def test_service_metrics_summary_legacy_route_works(client, monkeypatch) -> None:
    payloads = iter(
        [
            {"status": "success", "data": {"result": [{"value": [0, "99.1"]}]}},
            {"status": "success", "data": {"result": [{"value": [0, "210"]}]}},
            {"status": "success", "data": {"result": [{"value": [0, "0.1"]}]}},
            {"status": "success", "data": {"result": [{"value": [0, "0"]}]}},
        ]
    )

    def _mock_urlopen(*args, **kwargs):
        return _MockPrometheusResponse(next(payloads))

    monkeypatch.setattr("app.monitoring_providers.urlrequest.urlopen", _mock_urlopen)
    monkeypatch.setattr(
        "app.main._resolve_service_monitoring_context",
        lambda _service_id: ("homelab-api", "homelab-api", None),
    )

    response = client.get(
        "/services/homelab-api/metrics-summary?range=24h",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    assert response.json()["serviceId"] == "homelab-api"


def test_service_metrics_summary_supports_per_metric_no_data(client, monkeypatch) -> None:
    payloads = iter(
        [
            {"status": "success", "data": {"result": []}},
            {"status": "success", "data": {"result": [{"value": [0, "250"]}]}},
            {"status": "success", "data": {"result": []}},
            {"status": "success", "data": {"result": []}},
            {"status": "success", "data": {"result": [{"value": [0, "1"]}]}},
        ]
    )

    def _mock_urlopen(*args, **kwargs):
        return _MockPrometheusResponse(next(payloads))

    monkeypatch.setattr("app.monitoring_providers.urlrequest.urlopen", _mock_urlopen)
    monkeypatch.setattr(
        "app.main._resolve_service_monitoring_context",
        lambda _service_id: ("homelab-web", "homelab-web", None),
    )

    response = client.get(
        "/services/homelab-web/metrics/summary?range=1h",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["uptimePct"] is None
    assert body["p95LatencyMs"] == 250.0
    assert body["errorRatePct"] is None
    assert body["restartCount"] == 1.0
    assert body["noData"]["uptimePct"] is True
    assert body["noData"]["errorRatePct"] is True
    assert body["noData"]["p95LatencyMs"] is False
    assert body["noData"]["restartCount"] is False
    assert body["providerStatus"]["provider"] == "prometheus"


def test_service_metrics_summary_ingress_queries_include_service_id_fallback(client, monkeypatch) -> None:
    requested_urls: list[str] = []

    def _mock_urlopen(request, **kwargs):
        requested_urls.append(getattr(request, "full_url", request))
        return _MockPrometheusResponse({"status": "success", "data": {"result": []}})

    monkeypatch.setattr("app.monitoring_providers.urlrequest.urlopen", _mock_urlopen)
    monkeypatch.setattr(
        "app.main._resolve_service_monitoring_context",
        lambda _service_id: ("wordpress-ns", "web", "ingress-derived"),
    )

    response = client.get(
        "/services/homelab-wordpress/metrics/summary?range=24h",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    decoded_urls = [urlparse.unquote_plus(url) for url in requested_urls]
    assert any('service=~".*(web|homelab-wordpress).*"' in url for url in decoded_urls)


def test_service_metrics_summary_translates_prometheus_http_errors(client, monkeypatch) -> None:
    def _mock_urlopen(*args, **kwargs):
        raise HTTPError(
            url="http://prometheus.local/api/v1/query",
            code=503,
            msg="Service Unavailable",
            hdrs=None,
            fp=BytesIO(b'{"status":"error","error":"provider down"}'),
        )

    monkeypatch.setattr("app.monitoring_providers.urlrequest.urlopen", _mock_urlopen)
    monkeypatch.setattr(
        "app.main._resolve_service_monitoring_context",
        lambda _service_id: ("homelab-api", "homelab-api", None),
    )

    response = client.get(
        "/services/homelab-api/metrics/summary?range=7d",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["message"] == "Monitoring provider query failed."
    assert detail["correlationId"]
    assert detail["providerStatus"]["provider"] == "prometheus"
    assert detail["providerStatus"]["httpStatus"] == 503


def test_service_metrics_trends_use_sequential_fallback(client, monkeypatch) -> None:
    payloads = iter(
        [
            {"status": "success", "data": {"result": []}},
            {
                "status": "success",
                "data": {
                    "result": [
                        {
                            "values": [
                                [1000, "120"],
                                [1300, "240"],
                            ]
                        }
                    ]
                },
            },
            {
                "status": "success",
                "data": {
                    "result": [
                        {
                            "values": [
                                [1000, "0.2"],
                                [1300, "0.4"],
                            ]
                        }
                    ]
                },
            },
        ]
    )

    def _mock_urlopen(*args, **kwargs):
        return _MockPrometheusResponse(next(payloads))

    monkeypatch.setattr("app.monitoring_providers.urlrequest.urlopen", _mock_urlopen)
    monkeypatch.setattr(
        "app.main._resolve_service_monitoring_context",
        lambda _service_id: ("homelab-api", "homelab-api", None),
    )

    response = client.get(
        "/services/homelab-api/metrics/trends?range=24h",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["serviceId"] == "homelab-api"
    assert body["range"] == "24h"
    assert body["p95LatencyMs"]["queryStatus"] == "ok"
    assert body["p95LatencyMs"]["querySource"] == "traefik_fallback"
    assert body["p95LatencyMs"]["pointCount"] == 2
    assert body["p95LatencyMs"]["latestValue"] == 240.0
    assert body["errorRatePct"]["queryStatus"] == "ok"
    assert body["errorRatePct"]["querySource"] == "app_metrics"
    assert body["errorRatePct"]["pointCount"] == 2
    assert body["errorRatePct"]["latestValue"] == 0.4
    assert body["providerStatus"]["provider"] == "prometheus"


def test_service_metrics_trends_reject_invalid_range(client) -> None:
    response = client.get(
        "/services/homelab-api/metrics/trends?range=2h",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 422


def test_service_health_timeline_returns_segments(client, monkeypatch) -> None:
    payloads = iter(
        [
            {
                "status": "success",
                "data": {
                    "result": [
                        {
                            "values": [
                                [1000, "1"],
                                [1300, "1"],
                                [1600, "0.5"],
                            ]
                        }
                    ]
                },
            },
            {
                "status": "success",
                "data": {
                    "result": [
                        {
                            "values": [
                                [1000, "0.2"],
                                [1300, "0.4"],
                                [1600, "0.4"],
                            ]
                        }
                    ]
                },
            },
            {
                "status": "success",
                "data": {
                    "result": [
                        {
                            "values": [
                                [1000, "1"],
                                [1300, "1"],
                                [1600, "0.55"],
                            ]
                        }
                    ]
                },
            },
        ]
    )

    def _mock_urlopen(*args, **kwargs):
        return _MockPrometheusResponse(next(payloads))

    monkeypatch.setattr("app.monitoring_providers.urlrequest.urlopen", _mock_urlopen)
    monkeypatch.setattr(
        "app.main._resolve_service_monitoring_context",
        lambda _service_id: ("team-space", "portal-api", None),
    )

    response = client.get(
        "/services/homelab-api/health/timeline?range=24h&step=5m",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert len(body) >= 1
    assert set(body[0].keys()).issuperset({"start", "end", "status"})


def test_service_health_timeline_uses_service_registry_metadata_for_queries(client, monkeypatch,) -> None:
    requested_urls: list[str] = []
    payloads = iter(
        [
            {"status": "success", "data": {"result": []}},
            {"status": "success", "data": {"result": []}},
            {"status": "success", "data": {"result": []}},
            {"status": "success", "data": {"result": []}},
        ]
    )

    def _mock_urlopen(request, **kwargs):
        requested_urls.append(getattr(request, "full_url", request))
        return _MockPrometheusResponse(next(payloads))

    monkeypatch.setattr("app.monitoring_providers.urlrequest.urlopen", _mock_urlopen)
    monkeypatch.setattr(
        "app.main._resolve_service_monitoring_context",
        lambda _service_id: ("homelab-api", "portal-api", None),
    )

    response = client.get(
        "/services/homelab-api/health/timeline?range=24h&step=5m",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    assert any("namespace%3D%22homelab-api%22" in url for url in requested_urls)
    assert any("app%3D%22portal-api%22" in url for url in requested_urls)
    assert not any("namespace%3D%22default%22" in url for url in requested_urls)


def test_service_health_timeline_rejects_invalid_step(client) -> None:
    response = client.get(
        "/services/homelab-api/health/timeline?range=24h&step=1m",
        headers={"Authorization": "Bearer dev-static-token"},
    )
    assert response.status_code == 422


def test_logs_quickview_requires_approved_presets(client, monkeypatch) -> None:
    response = client.get(
        "/services/homelab-api/logs/quickview?preset=custom",
        headers={"Authorization": "Bearer dev-static-token"},
    )
    assert response.status_code == 422


def test_logs_quickview_returns_bounded_results_with_more_available(client, monkeypatch,) -> None:
    clear_rate_limit_state_for_tests()
    payload = {
        "status": "success",
        "data": {
            "result": [
                {
                    "stream": {"namespace": "default", "app": "homelab-api"},
                    "values": [
                        ["1700000002000000000", "line-2"],
                        ["1700000001000000000", "line-1"],
                    ],
                }
            ]
        },
    }

    def _mock_urlopen(*args, **kwargs):
        return _MockPrometheusResponse(payload)

    monkeypatch.setattr("app.monitoring_providers.urlrequest.urlopen", _mock_urlopen)
    monkeypatch.setattr(
        "app.main._resolve_service_monitoring_metadata",
        lambda _service_id: ("default", "homelab-api"),
    )

    response = client.get(
        "/services/homelab-api/logs/quickview?preset=errors&range=1h&limit=1",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["returned"] == 1
    assert body["moreAvailable"] is True
    assert body["nextCursor"]
    assert len(body["lines"]) == 1
    assert body["providerStatus"]["provider"] == "loki"
    assert body["providerStatus"]["status"] == "healthy"


def test_logs_quickview_enforces_rate_limit(client, monkeypatch) -> None:
    monkeypatch.setenv("LOGS_QUICKVIEW_RATE_LIMIT_PER_MIN", "1")
    payload = {"status": "success", "data": {"result": []}}

    def _mock_urlopen(*args, **kwargs):
        return _MockPrometheusResponse(payload)

    monkeypatch.setattr("app.monitoring_providers.urlrequest.urlopen", _mock_urlopen)
    monkeypatch.setattr(
        "app.main._resolve_service_monitoring_metadata",
        lambda _service_id: ("default", "homelab-api"),
    )

    first = client.get(
        "/services/homelab-api/logs/quickview?preset=errors",
        headers={"Authorization": "Bearer dev-static-token"},
    )
    second = client.get(
        "/services/homelab-api/logs/quickview?preset=errors",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert first.status_code == 200
    assert second.status_code == 429


def test_metrics_summary_uses_cache_for_repeated_service_and_range(client, monkeypatch) -> None:
    calls = {"count": 0}

    def _mock_urlopen(*args, **kwargs):
        calls["count"] += 1
        return _MockPrometheusResponse(
            {"status": "success", "data": {"result": [{"value": [0, "1"]}]}}
        )

    monkeypatch.setattr("app.monitoring_providers.urlrequest.urlopen", _mock_urlopen)
    monkeypatch.setenv("OBS_METRICS_CACHE_TTL_SECONDS", "60")
    monkeypatch.setattr(
        "app.main._resolve_service_monitoring_context",
        lambda _service_id: ("homelab-api", "homelab-api", None),
    )

    first = client.get(
        "/services/homelab-api/metrics/summary?range=24h",
        headers={"Authorization": "Bearer dev-static-token"},
    )
    second = client.get(
        "/services/homelab-api/metrics/summary?range=24h",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    # 4 Prometheus queries for first call, second call should hit cache.
    assert calls["count"] == 4


def test_logs_quickview_caps_limit_by_config(client, monkeypatch) -> None:
    payload = {
        "status": "success",
        "data": {
            "result": [
                {
                    "stream": {"namespace": "default", "app": "homelab-api"},
                    "values": [
                        ["1700000003000000000", "line-3"],
                        ["1700000002000000000", "line-2"],
                        ["1700000001000000000", "line-1"],
                    ],
                }
            ]
        },
    }

    def _mock_urlopen(*args, **kwargs):
        return _MockPrometheusResponse(payload)

    monkeypatch.setattr("app.monitoring_providers.urlrequest.urlopen", _mock_urlopen)
    monkeypatch.setenv("OBS_LOGS_MAX_LINES", "2")
    monkeypatch.setattr(
        "app.main._resolve_service_monitoring_metadata",
        lambda _service_id: ("default", "homelab-api"),
    )

    response = client.get(
        "/services/homelab-api/logs/quickview?preset=errors&range=1h&limit=200",
        headers={"Authorization": "Bearer dev-static-token"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["limit"] == 2
    assert body["returned"] == 2


def test_logs_quickview_uses_service_registry_metadata_for_query(client, monkeypatch) -> None:
    requested_urls: list[str] = []
    payload = {"status": "success", "data": {"result": []}}

    def _mock_urlopen(request, **kwargs):
        requested_urls.append(getattr(request, "full_url", request))
        return _MockPrometheusResponse(payload)

    monkeypatch.setattr("app.monitoring_providers.urlrequest.urlopen", _mock_urlopen)
    monkeypatch.setattr(
        "app.main._resolve_service_monitoring_metadata",
        lambda _service_id: ("homelab-api", "portal-api"),
    )

    response = client.get(
        "/services/homelab-api/logs/quickview?preset=errors&range=1h",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    assert requested_urls
    decoded = urlparse.unquote_plus(requested_urls[0])
    assert '{namespace="homelab-api", app="portal-api"}' in decoded


def test_alerts_active_caps_limit_by_config(client, monkeypatch) -> None:
    payload = [
        {
            "status": {"state": "active"},
            "labels": {"alertname": "A", "severity": "warning"},
            "annotations": {"summary": "A"},
            "startsAt": "2026-03-05T12:00:00Z",
        },
        {
            "status": {"state": "active"},
            "labels": {"alertname": "B", "severity": "critical"},
            "annotations": {"summary": "B"},
            "startsAt": "2026-03-05T12:01:00Z",
        },
    ]

    def _mock_urlopen(*args, **kwargs):
        return _MockPrometheusResponse(payload)

    monkeypatch.setattr("app.monitoring_providers.urlrequest.urlopen", _mock_urlopen)
    monkeypatch.setenv("OBS_ALERTS_MAX_ROWS", "1")

    response = client.get(
        "/alerts/active?limit=50",
        headers={"Authorization": "Bearer dev-static-token"},
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["alerts"]) == 1
    assert body["providerStatus"]["provider"] == "alertmanager"


def test_alerts_active_returns_mapped_alerts(client, monkeypatch) -> None:
    payload = [
        {
            "status": {"state": "active"},
            "labels": {
                "alertname": "HighErrorRate",
                "severity": "critical",
                "service": "homelab-api",
                "env": "dev",
            },
            "annotations": {
                "summary": "High error rate",
                "description": "5xx exceeded threshold",
            },
            "startsAt": "2026-03-05T12:00:00Z",
        }
    ]

    def _mock_urlopen(*args, **kwargs):
        return _MockPrometheusResponse(payload)

    monkeypatch.setattr("app.monitoring_providers.urlrequest.urlopen", _mock_urlopen)

    response = client.get(
        "/alerts/active",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["alerts"]) == 1
    assert body["alerts"][0]["severity"] == "critical"
    assert body["alerts"][0]["title"] == "High error rate"
    assert body["alerts"][0]["serviceId"] == "homelab-api"
    assert body["alerts"][0]["env"] == "dev"
    assert body["providerStatus"]["status"] == "healthy"


def test_alerts_active_supports_filters(client, monkeypatch) -> None:
    payload = [
        {
            "status": {"state": "active"},
            "labels": {
                "alertname": "A",
                "severity": "warning",
                "service": "homelab-api",
                "env": "dev",
            },
            "annotations": {"summary": "A"},
            "startsAt": "2026-03-05T12:00:00Z",
        },
        {
            "status": {"state": "active"},
            "labels": {
                "alertname": "B",
                "severity": "critical",
                "service": "homelab-web",
                "env": "prod",
            },
            "annotations": {"summary": "B"},
            "startsAt": "2026-03-05T12:10:00Z",
        },
    ]

    def _mock_urlopen(*args, **kwargs):
        return _MockPrometheusResponse(payload)

    monkeypatch.setattr("app.monitoring_providers.urlrequest.urlopen", _mock_urlopen)

    response = client.get(
        "/alerts/active?serviceId=homelab-api&env=dev",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["alerts"]) == 1
    assert body["alerts"][0]["serviceId"] == "homelab-api"
    assert body["alerts"][0]["env"] == "dev"


def test_alerts_active_gracefully_degrades_on_upstream_failure(client, monkeypatch) -> None:
    def _mock_urlopen(*args, **kwargs):
        raise HTTPError(
            url="http://alertmanager.local/api/v2/alerts",
            code=503,
            msg="Service Unavailable",
            hdrs=None,
            fp=BytesIO(b'{"status":"error","error":"provider down"}'),
        )

    monkeypatch.setattr("app.monitoring_providers.urlrequest.urlopen", _mock_urlopen)

    response = client.get(
        "/alerts/active",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["alerts"] == []
    assert body["providerStatus"]["provider"] == "alertmanager"
    assert body["providerStatus"]["status"] == "http_error"
    assert body["providerStatus"]["correlationId"]


def test_monitoring_incidents_compat_route_available(client, monkeypatch) -> None:
    payload = [
        {
            "status": {"state": "active"},
            "labels": {
                "alertname": "HighLatency",
                "severity": "warning",
                "service": "homelab-api",
            },
            "annotations": {"summary": "High latency"},
            "startsAt": "2026-03-05T11:00:00Z",
        }
    ]

    def _mock_urlopen(*args, **kwargs):
        return _MockPrometheusResponse(payload)

    monkeypatch.setattr("app.monitoring_providers.urlrequest.urlopen", _mock_urlopen)

    response = client.get(
        "/monitoring/incidents",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert "incidents" in body
    assert len(body["incidents"]) == 1
    assert body["incidents"][0]["severity"] == "warning"
    assert body["providerStatus"]["provider"] == "alertmanager"
