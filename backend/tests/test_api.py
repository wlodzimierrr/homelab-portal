import json
from io import BytesIO
from urllib.error import HTTPError

import pytest
from fastapi.testclient import TestClient

from app.main import app, clear_observability_caches_for_tests
from app.logs_quickview import clear_rate_limit_state_for_tests


client = TestClient(app)


@pytest.fixture(autouse=True)
def _clear_caches_between_tests() -> None:
    clear_rate_limit_state_for_tests()
    clear_observability_caches_for_tests()


def test_health_endpoint() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_login_success() -> None:
    response = client.post(
        "/auth/login",
        json={"username": "admin", "password": "changeme"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"] == "dev-static-token"
    assert body["token_type"] == "bearer"
    assert body["expires_at"]


def test_login_invalid_credentials() -> None:
    response = client.post(
        "/auth/login",
        json={"username": "admin", "password": "wrong"},
    )

    assert response.status_code == 401


def test_projects_unauthorized_without_token() -> None:
    response = client.get("/projects")

    assert response.status_code == 401


def test_projects_authorized_with_forwarded_user(monkeypatch) -> None:
    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, *_args, **_kwargs):
            return None

        def fetchall(self):
            return []

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def cursor(self):
            return _Cursor()

    monkeypatch.setattr("app.main._with_connection", lambda: _Conn())

    response = client.get(
        "/projects",
        headers={"X-Auth-Request-User": "alice"},
    )

    assert response.status_code == 200


def test_projects_list_does_not_seed_defaults_on_read(monkeypatch) -> None:
    executed_sql: list[str] = []

    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql: str, *_args, **_kwargs):
            normalized = " ".join(sql.split()).upper()
            executed_sql.append(normalized)
            if normalized.startswith("INSERT INTO PROJECTS"):
                raise AssertionError("GET /projects must not write seeded rows")

        def fetchall(self):
            return []

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def cursor(self):
            return _Cursor()

    monkeypatch.setattr("app.main._with_connection", lambda: _Conn())

    response = client.get(
        "/projects",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    assert response.json() == {"projects": []}
    assert any(
        sql.startswith("SELECT PROJECT_ID, PROJECT_NAME, ENV FROM PROJECT_REGISTRY")
        for sql in executed_sql
    )


def test_projects_list_supports_env_filter(monkeypatch) -> None:
    executed_args: list[tuple[str, tuple[object, ...] | None]] = []

    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql: str, args=None):
            executed_args.append((" ".join(sql.split()).upper(), args))

        def fetchall(self):
            return [("homelab-api", "homelab-api", "dev")]

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def cursor(self):
            return _Cursor()

    monkeypatch.setattr("app.main._with_connection", lambda: _Conn())

    response = client.get(
        "/projects?env=dev",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "projects": [{"id": "homelab-api", "name": "homelab-api", "environment": "dev"}]
    }
    assert executed_args[0][1] == ("gitops_apps", "dev")


def test_create_project_rejected_for_gitops_owned_catalog() -> None:
    headers = {"Authorization": "Bearer dev-static-token"}

    response = client.post(
        "/projects",
        headers=headers,
        json={"id": "proj-e2e", "name": "E2E Project", "environment": "dev"},
    )
    assert response.status_code == 409
    assert "GitOps app definitions" in response.json()["detail"]


def test_create_project_forbidden_for_non_admin_forwarded_user() -> None:
    response = client.post(
        "/projects",
        headers={"X-Auth-Request-User": "alice"},
        json={"id": "proj-forbidden", "name": "Nope", "environment": "dev"},
    )
    assert response.status_code == 403


def test_create_project_rejected_for_admin_group_when_catalog_is_gitops_owned() -> None:
    response = client.post(
        "/projects",
        headers={
            "X-Auth-Request-User": "alice",
            "X-Auth-Request-Groups": "team-developers,team-admins",
        },
        json={"id": "proj-admin", "name": "Allowed", "environment": "dev"},
    )
    assert response.status_code == 409
    assert "GitOps app definitions" in response.json()["detail"]


def test_services_list_returns_cluster_backed_rows(monkeypatch) -> None:
    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, *_args, **_kwargs):
            return None

        def fetchall(self):
            return [
                (
                    "homelab-api",
                    "homelab-api",
                    "dev",
                    "homelab-api",
                    "homelab-api",
                    "homelab-api-dev",
                    "cluster_services",
                    "kubernetes_api",
                    None,
                )
            ]

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def cursor(self):
            return _Cursor()

    monkeypatch.setattr("app.main._with_connection", lambda: _Conn())

    response = client.get(
        "/services?env=dev",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "services": [
            {
                "serviceId": "homelab-api",
                "serviceName": "homelab-api",
                "env": "dev",
                "namespace": "homelab-api",
                "appLabel": "homelab-api",
                "argoAppName": "homelab-api-dev",
                "source": "cluster_services",
                "sourceRef": "kubernetes_api",
                "lastSyncedAt": None,
            }
        ]
    }


def test_service_detail_returns_cluster_backed_row(monkeypatch) -> None:
    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, *_args, **_kwargs):
            return None

        def fetchall(self):
            return [
                (
                    "homelab-web",
                    "homelab-web",
                    "dev",
                    "homelab-web",
                    "homelab-web",
                    "homelab-web-dev",
                    "cluster_services",
                    "kubernetes_api",
                    None,
                )
            ]

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def cursor(self):
            return _Cursor()

    monkeypatch.setattr("app.main._with_connection", lambda: _Conn())

    response = client.get(
        "/services/homelab-web",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": "homelab-web",
        "name": "homelab-web",
        "namespace": "homelab-web",
        "env": "dev",
        "appLabel": "homelab-web",
        "argoAppName": "homelab-web-dev",
        "source": "cluster_services",
        "sourceRef": "kubernetes_api",
        "lastSyncedAt": None,
    }


def test_service_registry_sync_requires_auth() -> None:
    response = client.post("/service-registry/sync")
    assert response.status_code == 401


def test_service_registry_sync_returns_summary_for_admin(monkeypatch) -> None:
    class _ConnContext:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("app.main._with_connection", lambda: _ConnContext())
    monkeypatch.setattr(
        "app.main.sync_service_registry_from_cluster",
        lambda conn, env_name=None: {
            "correlationId": "cid-1",
            "source": "cluster_services",
            "env": env_name or "dev",
            "namespaces": ["homelab-api"],
            "discovered": 2,
            "upserted": 2,
            "inserted": 1,
            "updated": 1,
            "deleted": 0,
            "sourceFailures": [],
            "generatedAt": "2026-03-05T00:00:00+00:00",
            "durationMs": 12,
        },
    )

    response = client.post(
        "/service-registry/sync",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["correlationId"] == "cid-1"
    assert body["source"] == "cluster_services"
    assert body["inserted"] == 1
    assert body["updated"] == 1
    assert body["deleted"] == 0


def test_service_registry_sync_dispatches_gitops_source(monkeypatch) -> None:
    class _ConnContext:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("app.main._with_connection", lambda: _ConnContext())
    monkeypatch.setattr(
        "app.main.sync_project_registry_from_gitops",
        lambda conn, env_name=None: {
            "correlationId": "cid-gitops",
            "source": "gitops_apps",
            "env": env_name or "all",
            "namespaces": ["homelab-api", "homelab-web"],
            "discovered": 2,
            "upserted": 2,
            "inserted": 2,
            "updated": 0,
            "deleted": 1,
            "sourceFailures": [],
            "generatedAt": "2026-03-05T00:00:00+00:00",
            "durationMs": 9,
        },
    )

    response = client.post(
        "/service-registry/sync?source=gitops_apps&env=dev",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["correlationId"] == "cid-gitops"
    assert body["source"] == "gitops_apps"
    assert body["env"] == "dev"
    assert body["deleted"] == 1


def test_service_registry_diagnostics_reports_empty_registry(monkeypatch) -> None:
    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, *_args, **_kwargs):
            return None

        def fetchone(self):
            return (0, None)

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def cursor(self):
            return _Cursor()

    monkeypatch.setattr("app.main._with_connection", lambda: _Conn())
    monkeypatch.setattr("app.main._load_project_rows", lambda: [])
    monkeypatch.setattr("app.main.load_ci_metadata_rows", lambda: [])
    monkeypatch.setattr("app.main.load_argo_metadata_rows", lambda: [])

    response = client.get(
        "/service-registry/diagnostics",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["freshness"]["rowCount"] == 0
    assert body["freshness"]["state"] == "empty"
    assert body["freshness"]["isStale"] is False
    assert body["joinMismatch"]["ciUnmatchedCount"] == 0
    assert body["joinMismatch"]["argoUnmatchedCount"] == 0


def test_service_registry_diagnostics_reports_stale_registry_with_mismatches(
    monkeypatch,
) -> None:
    stale_ts = "2026-03-01T00:00:00+00:00"

    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, *_args, **_kwargs):
            return None

        def fetchone(self):
            from datetime import datetime

            return (3, datetime.fromisoformat(stale_ts))

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def cursor(self):
            return _Cursor()

    monkeypatch.setenv("REGISTRY_STALE_AFTER_MINUTES", "30")
    monkeypatch.setattr("app.main._with_connection", lambda: _Conn())
    monkeypatch.setattr(
        "app.main._load_project_rows",
        lambda: [{"service_id": "homelab-api", "service_name": "Homelab API", "env": "dev"}],
    )
    monkeypatch.setattr(
        "app.main.load_ci_metadata_rows",
        lambda: [{"serviceId": "portal-project", "serviceName": "Portal Project", "env": "dev"}],
    )
    monkeypatch.setattr("app.main.load_argo_metadata_rows", lambda: [])

    response = client.get(
        "/service-registry/diagnostics?env=dev",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["env"] == "dev"
    assert body["freshness"]["rowCount"] == 3
    assert body["freshness"]["state"] == "stale"
    assert body["freshness"]["isStale"] is True
    assert body["joinMismatch"]["ciUnmatchedCount"] == 1
    assert body["joinMismatch"]["ciUnmatchedKeys"] == [
        "portal-project|Portal Project|dev"
    ]


class _MockPrometheusResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_service_metrics_summary_success_with_supported_range(monkeypatch) -> None:
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

    monkeypatch.setattr("app.main.urlrequest.urlopen", _mock_urlopen)

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


def test_service_metrics_summary_rejects_invalid_range() -> None:
    response = client.get(
        "/services/homelab-api/metrics/summary?range=2h",
        headers={"Authorization": "Bearer dev-static-token"},
    )
    assert response.status_code == 422


def test_service_metrics_summary_legacy_route_works(monkeypatch) -> None:
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

    monkeypatch.setattr("app.main.urlrequest.urlopen", _mock_urlopen)

    response = client.get(
        "/services/homelab-api/metrics-summary?range=24h",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    assert response.json()["serviceId"] == "homelab-api"


def test_service_metrics_summary_supports_per_metric_no_data(monkeypatch) -> None:
    payloads = iter(
        [
            {"status": "success", "data": {"result": []}},
            {"status": "success", "data": {"result": [{"value": [0, "250"]}]}},
            {"status": "success", "data": {"result": []}},
            {"status": "success", "data": {"result": [{"value": [0, "1"]}]}},
        ]
    )

    def _mock_urlopen(*args, **kwargs):
        return _MockPrometheusResponse(next(payloads))

    monkeypatch.setattr("app.main.urlrequest.urlopen", _mock_urlopen)

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


def test_service_metrics_summary_translates_prometheus_http_errors(monkeypatch) -> None:
    def _mock_urlopen(*args, **kwargs):
        raise HTTPError(
            url="http://prometheus.local/api/v1/query",
            code=503,
            msg="Service Unavailable",
            hdrs=None,
            fp=BytesIO(b'{"status":"error","error":"provider down"}'),
        )

    monkeypatch.setattr("app.main.urlrequest.urlopen", _mock_urlopen)

    response = client.get(
        "/services/homelab-api/metrics/summary?range=7d",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "Monitoring provider query failed." in detail
    assert "correlation_id=" in detail


def test_service_health_timeline_returns_segments(monkeypatch) -> None:
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

    monkeypatch.setattr("app.main.urlrequest.urlopen", _mock_urlopen)

    response = client.get(
        "/services/homelab-api/health/timeline?range=24h&step=5m",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert len(body) >= 1
    assert set(body[0].keys()).issuperset({"start", "end", "status"})


def test_service_health_timeline_rejects_invalid_step() -> None:
    response = client.get(
        "/services/homelab-api/health/timeline?range=24h&step=1m",
        headers={"Authorization": "Bearer dev-static-token"},
    )
    assert response.status_code == 422


def test_releases_endpoint_returns_traceability_rows(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.main._load_project_rows",
        lambda: [{"service_id": "homelab-api", "env": "dev"}],
    )
    monkeypatch.setattr(
        "app.main.load_ci_metadata_rows",
        lambda: [
            {
                "serviceId": "homelab-api",
                "env": "dev",
                "commitSha": "abc123",
                "imageRef": "ghcr.io/example/homelab-api:v1",
                "expectedRevision": "abc123",
                "deployedAt": "2026-03-05T12:00:00Z",
            }
        ],
    )
    monkeypatch.setattr(
        "app.main.load_argo_metadata_rows",
        lambda: [
            {
                "serviceId": "homelab-api",
                "env": "dev",
                "appName": "homelab-api-dev",
                "syncStatus": "synced",
                "healthStatus": "healthy",
                "revision": "abc123",
            }
        ],
    )

    response = client.get(
        "/releases?env=dev&limit=50",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert len(body) == 1
    row = body[0]
    assert row["serviceId"] == "homelab-api"
    assert row["env"] == "dev"
    assert row["argo"]["syncStatus"] == "synced"
    assert row["drift"]["isDrifted"] is False


def test_releases_endpoint_supports_service_filter(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.main._load_project_rows",
        lambda: [
            {"service_id": "homelab-api", "env": "dev"},
            {"service_id": "homelab-web", "env": "dev"},
        ],
    )
    monkeypatch.setattr("app.main.load_ci_metadata_rows", lambda: [])
    monkeypatch.setattr("app.main.load_argo_metadata_rows", lambda: [])

    response = client.get(
        "/releases?serviceId=homelab-web",
        headers={"Authorization": "Bearer dev-static-token"},
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["serviceId"] == "homelab-web"


def test_release_dashboard_compat_endpoint_available(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.main._load_project_rows",
        lambda: [{"service_id": "homelab-api", "env": "dev"}],
    )
    monkeypatch.setattr("app.main.load_ci_metadata_rows", lambda: [])
    monkeypatch.setattr("app.main.load_argo_metadata_rows", lambda: [])

    response = client.get(
        "/release-dashboard?env=dev",
        headers={"Authorization": "Bearer dev-static-token"},
    )
    assert response.status_code == 200
    body = response.json()
    assert "releases" in body
    assert len(body["releases"]) == 1


def test_logs_quickview_requires_approved_presets(monkeypatch) -> None:
    response = client.get(
        "/services/homelab-api/logs/quickview?preset=custom",
        headers={"Authorization": "Bearer dev-static-token"},
    )
    assert response.status_code == 422


def test_logs_quickview_returns_bounded_results_with_more_available(monkeypatch) -> None:
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

    monkeypatch.setattr("app.main.urlrequest.urlopen", _mock_urlopen)

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


def test_logs_quickview_enforces_rate_limit(monkeypatch) -> None:
    monkeypatch.setenv("LOGS_QUICKVIEW_RATE_LIMIT_PER_MIN", "1")
    payload = {"status": "success", "data": {"result": []}}

    def _mock_urlopen(*args, **kwargs):
        return _MockPrometheusResponse(payload)

    monkeypatch.setattr("app.main.urlrequest.urlopen", _mock_urlopen)

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


def test_metrics_summary_uses_cache_for_repeated_service_and_range(monkeypatch) -> None:
    calls = {"count": 0}

    def _mock_urlopen(*args, **kwargs):
        calls["count"] += 1
        return _MockPrometheusResponse(
            {"status": "success", "data": {"result": [{"value": [0, "1"]}]}}
        )

    monkeypatch.setattr("app.main.urlrequest.urlopen", _mock_urlopen)
    monkeypatch.setenv("OBS_METRICS_CACHE_TTL_SECONDS", "60")

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


def test_logs_quickview_caps_limit_by_config(monkeypatch) -> None:
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

    monkeypatch.setattr("app.main.urlrequest.urlopen", _mock_urlopen)
    monkeypatch.setenv("OBS_LOGS_MAX_LINES", "2")

    response = client.get(
        "/services/homelab-api/logs/quickview?preset=errors&range=1h&limit=200",
        headers={"Authorization": "Bearer dev-static-token"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["limit"] == 2
    assert body["returned"] == 2


def test_alerts_active_caps_limit_by_config(monkeypatch) -> None:
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

    monkeypatch.setattr("app.main.urlrequest.urlopen", _mock_urlopen)
    monkeypatch.setenv("OBS_ALERTS_MAX_ROWS", "1")

    response = client.get(
        "/alerts/active?limit=50",
        headers={"Authorization": "Bearer dev-static-token"},
    )
    assert response.status_code == 200
    assert len(response.json()) == 1


def test_alerts_active_returns_mapped_alerts(monkeypatch) -> None:
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

    monkeypatch.setattr("app.main.urlrequest.urlopen", _mock_urlopen)

    response = client.get(
        "/alerts/active",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["severity"] == "critical"
    assert body[0]["title"] == "High error rate"
    assert body[0]["serviceId"] == "homelab-api"
    assert body[0]["env"] == "dev"


def test_alerts_active_supports_filters(monkeypatch) -> None:
    payload = [
        {
            "status": {"state": "active"},
            "labels": {"alertname": "A", "severity": "warning", "service": "homelab-api", "env": "dev"},
            "annotations": {"summary": "A"},
            "startsAt": "2026-03-05T12:00:00Z",
        },
        {
            "status": {"state": "active"},
            "labels": {"alertname": "B", "severity": "critical", "service": "homelab-web", "env": "prod"},
            "annotations": {"summary": "B"},
            "startsAt": "2026-03-05T12:10:00Z",
        },
    ]

    def _mock_urlopen(*args, **kwargs):
        return _MockPrometheusResponse(payload)

    monkeypatch.setattr("app.main.urlrequest.urlopen", _mock_urlopen)

    response = client.get(
        "/alerts/active?serviceId=homelab-api&env=dev",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["serviceId"] == "homelab-api"
    assert body[0]["env"] == "dev"


def test_alerts_active_gracefully_degrades_on_upstream_failure(monkeypatch) -> None:
    def _mock_urlopen(*args, **kwargs):
        raise HTTPError(
            url="http://alertmanager.local/api/v2/alerts",
            code=503,
            msg="Service Unavailable",
            hdrs=None,
            fp=BytesIO(b'{"status":"error","error":"provider down"}'),
        )

    monkeypatch.setattr("app.main.urlrequest.urlopen", _mock_urlopen)

    response = client.get(
        "/alerts/active",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    assert response.json() == []


def test_monitoring_incidents_compat_route_available(monkeypatch) -> None:
    payload = [
        {
            "status": {"state": "active"},
            "labels": {"alertname": "HighLatency", "severity": "warning", "service": "homelab-api"},
            "annotations": {"summary": "High latency"},
            "startsAt": "2026-03-05T11:00:00Z",
        }
    ]

    def _mock_urlopen(*args, **kwargs):
        return _MockPrometheusResponse(payload)

    monkeypatch.setattr("app.main.urlrequest.urlopen", _mock_urlopen)

    response = client.get(
        "/monitoring/incidents",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert "incidents" in body
    assert len(body["incidents"]) == 1
    assert body["incidents"][0]["severity"] == "warning"
