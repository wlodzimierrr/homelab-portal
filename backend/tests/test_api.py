import json
from io import BytesIO
from urllib import parse as urlparse
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


def test_health_endpoint_supports_provider_checks(monkeypatch) -> None:
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


def test_project_catalog_diagnostics_reports_freshness(monkeypatch) -> None:
    from datetime import datetime, timezone

    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, *_args, **_kwargs):
            return None

        def fetchone(self):
            return (2, datetime.now(tz=timezone.utc))

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def cursor(self):
            return _Cursor()

    monkeypatch.setattr("app.main._with_connection", lambda: _Conn())
    monkeypatch.setattr(
        "app.main._load_project_catalog_rows",
        lambda env=None, project_id=None: [
            {
                "project_id": "homelab-api",
                "project_name": "Homelab API",
                "env": "dev",
                "namespace": "homelab-api",
                "app_label": "homelab-api",
            }
        ],
    )
    monkeypatch.setattr(
        "app.main._load_service_catalog_rows",
        lambda env=None, service_id=None: [
            {
                "service_id": "homelab-api",
                "service_name": "homelab-api",
                "env": "dev",
                "namespace": "homelab-api",
                "app_label": "homelab-api",
                "argo_app_name": "homelab-api-dev",
                "source": "cluster_services",
                "source_ref": "kubernetes_api",
                "last_synced_at": None,
            }
        ],
    )

    response = client.get(
        "/projects/diagnostics?env=dev",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["env"] == "dev"
    assert body["freshness"]["rowCount"] == 2
    assert body["freshness"]["state"] == "fresh"
    assert body["freshness"]["isWarning"] is False
    assert body["freshness"]["warningAfterMinutes"] >= 1
    assert body["catalogJoin"]["projectOnlyCount"] == 0
    assert body["catalogJoin"]["serviceOnlyCount"] == 0


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
    monkeypatch.setattr(
        "app.main._load_service_rows",
        lambda **_kwargs: [
            {
                "service_id": "homelab-web",
                "service_name": "homelab-web",
                "env": "dev",
                "namespace": "homelab-web",
                "app_label": "homelab-web",
                "argo_app_name": "homelab-web-dev",
                "source": "cluster_services",
                "source_ref": "kubernetes_api",
                "last_synced_at": None,
            }
        ],
    )
    monkeypatch.setattr("app.main._load_release_rows_for_service", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("app.main._load_live_service_runtime_rows", lambda _row: [])

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
        "version": None,
        "health": None,
        "sync": None,
        "source": "cluster_services",
        "sourceRef": "kubernetes_api",
        "lastSyncedAt": None,
    }


def test_catalog_reconciliation_returns_join_rows(monkeypatch) -> None:
    class _Cursor:
        def __init__(self):
            self.last_sql = ""

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql: str, *_args, **_kwargs):
            self.last_sql = " ".join(sql.split()).upper()

        def fetchall(self):
            if "FROM PROJECT_REGISTRY" in self.last_sql:
                return [("homelab-api", "Homelab API", "dev", "homelab-api", "homelab-api")]
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
        "/catalog/reconciliation?env=dev",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["diagnostics"]["projectOnlyCount"] == 0
    assert body["diagnostics"]["serviceOnlyCount"] == 0
    assert body["rows"][0]["projectId"] == "homelab-api"
    assert body["rows"][0]["primaryServiceId"] == "homelab-api"
    assert body["rows"][0]["serviceIds"] == ["homelab-api"]


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
    monkeypatch.setattr("app.main._load_project_catalog_rows", lambda env=None, project_id=None: [])
    monkeypatch.setattr("app.main._load_service_catalog_rows", lambda env=None, service_id=None: [])
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
    assert body["freshness"]["isWarning"] is False
    assert body["freshness"]["isStale"] is False
    assert body["joinMismatch"]["ciUnmatchedCount"] == 0
    assert body["joinMismatch"]["argoUnmatchedCount"] == 0
    assert body["catalogJoin"]["projectOnlyCount"] == 0
    assert body["catalogJoin"]["serviceOnlyCount"] == 0


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
        "app.main._load_project_catalog_rows",
        lambda env=None, project_id=None: [
            {
                "project_id": "portal-project",
                "project_name": "Portal Project",
                "env": "dev",
                "namespace": "portal",
                "app_label": "portal-project",
            }
        ],
    )
    monkeypatch.setattr(
        "app.main._load_service_catalog_rows",
        lambda env=None, service_id=None: [
            {
                "service_id": "homelab-api",
                "service_name": "homelab-api",
                "env": "dev",
                "namespace": "homelab-api",
                "app_label": "homelab-api",
                "argo_app_name": "homelab-api-dev",
                "source": "cluster_services",
                "source_ref": "kubernetes_api",
                "last_synced_at": None,
            }
        ],
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
    assert body["freshness"]["isWarning"] is True
    assert body["freshness"]["isStale"] is True
    assert body["joinMismatch"]["ciUnmatchedCount"] == 1
    assert body["joinMismatch"]["ciUnmatchedKeys"] == [
        "portal-project|Portal Project|dev"
    ]
    assert body["catalogJoin"]["projectOnlyCount"] == 1
    assert body["catalogJoin"]["serviceOnlyCount"] == 1


def test_service_registry_diagnostics_reports_warning_before_stale(monkeypatch) -> None:
    from datetime import datetime, timedelta, timezone

    warning_ts = datetime.now(tz=timezone.utc) - timedelta(minutes=25)

    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, *_args, **_kwargs):
            return None

        def fetchone(self):
            return (2, warning_ts)

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def cursor(self):
            return _Cursor()

    monkeypatch.setenv("REGISTRY_WARN_AFTER_MINUTES", "20")
    monkeypatch.setenv("REGISTRY_STALE_AFTER_MINUTES", "30")
    monkeypatch.setattr("app.main._with_connection", lambda: _Conn())
    monkeypatch.setattr("app.main._load_project_rows", lambda: [])
    monkeypatch.setattr("app.main._load_project_catalog_rows", lambda env=None, project_id=None: [])
    monkeypatch.setattr("app.main._load_service_catalog_rows", lambda env=None, service_id=None: [])
    monkeypatch.setattr("app.main.load_ci_metadata_rows", lambda: [])
    monkeypatch.setattr("app.main.load_argo_metadata_rows", lambda: [])

    response = client.get(
        "/service-registry/diagnostics?env=dev",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["freshness"]["state"] == "warning"
    assert body["freshness"]["isWarning"] is True
    assert body["freshness"]["isStale"] is False
    assert body["freshness"]["warningAfterMinutes"] == 20
    assert body["freshness"]["staleAfterMinutes"] == 30


def test_monitoring_provider_diagnostics_reports_reachability(monkeypatch) -> None:
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
        lambda provider, correlation_id: statuses[provider] | {"correlationId": correlation_id},
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

    monkeypatch.setattr("app.monitoring_providers.urlrequest.urlopen", _mock_urlopen)
    monkeypatch.setattr(
        "app.main._load_service_rows",
        lambda **_kwargs: [
            {
                "service_id": "homelab-api",
                "service_name": "homelab-api",
                "env": "dev",
                "namespace": "team-space",
                "app_label": "portal-api",
                "argo_app_name": "homelab-api-dev",
                "source": "cluster_services",
                "source_ref": "kubernetes_api",
                "last_synced_at": "2026-03-06T00:00:00+00:00",
            }
        ],
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


def test_service_metrics_summary_uses_service_registry_metadata_for_queries(monkeypatch) -> None:
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
        "app.main._load_service_rows",
        lambda **_kwargs: [
            {
                "service_id": "homelab-api",
                "service_name": "homelab-api-postgres",
                "env": "dev",
                "namespace": "homelab-api",
                "app_label": "homelab-api",
                "argo_app_name": "homelab-api-dev",
                "source": "cluster_services",
                "source_ref": "kubernetes_api",
                "last_synced_at": "2026-03-06T00:00:00+00:00",
            }
        ],
    )

    response = client.get(
        "/services/homelab-api/metrics/summary?range=24h",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    assert any('namespace%3D%22homelab-api%22' in url for url in requested_urls)
    assert any('app%3D%22homelab-api%22' in url for url in requested_urls)
    assert not any('namespace%3D%22default%22' in url for url in requested_urls)


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

    monkeypatch.setattr("app.monitoring_providers.urlrequest.urlopen", _mock_urlopen)
    monkeypatch.setattr(
        "app.main._resolve_service_monitoring_metadata",
        lambda _service_id: ("homelab-api", "homelab-api"),
    )

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
            {"status": "success", "data": {"result": []}},
            {"status": "success", "data": {"result": [{"value": [0, "1"]}]}},
        ]
    )

    def _mock_urlopen(*args, **kwargs):
        return _MockPrometheusResponse(next(payloads))

    monkeypatch.setattr("app.monitoring_providers.urlrequest.urlopen", _mock_urlopen)
    monkeypatch.setattr(
        "app.main._resolve_service_monitoring_metadata",
        lambda _service_id: ("homelab-web", "homelab-web"),
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


def test_service_metrics_summary_translates_prometheus_http_errors(monkeypatch) -> None:
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
        "app.main._resolve_service_monitoring_metadata",
        lambda _service_id: ("homelab-api", "homelab-api"),
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

    monkeypatch.setattr("app.monitoring_providers.urlrequest.urlopen", _mock_urlopen)
    monkeypatch.setattr(
        "app.main._load_service_rows",
        lambda **_kwargs: [
            {
                "service_id": "homelab-api",
                "service_name": "homelab-api",
                "env": "dev",
                "namespace": "team-space",
                "app_label": "portal-api",
                "argo_app_name": "homelab-api-dev",
                "source": "cluster_services",
                "source_ref": "kubernetes_api",
                "last_synced_at": "2026-03-06T00:00:00+00:00",
            }
        ],
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


def test_service_health_timeline_uses_service_registry_metadata_for_queries(monkeypatch) -> None:
    requested_urls: list[str] = []
    payloads = iter(
        [
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
        "app.main._load_service_rows",
        lambda **_kwargs: [
            {
                "service_id": "homelab-api",
                "service_name": "homelab-api",
                "env": "dev",
                "namespace": "homelab-api",
                "app_label": "portal-api",
                "argo_app_name": "homelab-api-dev",
                "source": "cluster_services",
                "source_ref": "kubernetes_api",
                "last_synced_at": "2026-03-06T00:00:00+00:00",
            }
        ],
    )

    response = client.get(
        "/services/homelab-api/health/timeline?range=24h&step=5m",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    assert any('namespace%3D%22homelab-api%22' in url for url in requested_urls)
    assert any('app%3D%22portal-api%22' in url for url in requested_urls)
    assert not any('namespace%3D%22default%22' in url for url in requested_urls)


def test_service_health_timeline_rejects_invalid_step() -> None:
    response = client.get(
        "/services/homelab-api/health/timeline?range=24h&step=1m",
        headers={"Authorization": "Bearer dev-static-token"},
    )
    assert response.status_code == 422


def test_service_details_include_release_metadata(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.main._load_service_rows",
        lambda **_kwargs: [
            {
                "service_id": "homelab-api",
                "service_name": "homelab-api",
                "env": "dev",
                "namespace": "homelab-api",
                "app_label": "homelab-api",
                "argo_app_name": "homelab-api-dev",
                "source": "cluster_services",
                "source_ref": "kubernetes_api",
                "last_synced_at": "2026-03-06T00:00:00+00:00",
            }
        ],
    )
    monkeypatch.setattr(
        "app.main._load_release_rows_for_service",
        lambda *_args, **_kwargs: [
            {
                "serviceId": "homelab-api",
                "env": "dev",
                "commitSha": "abc123",
                "imageRef": "ghcr.io/example/homelab-api:v1.2.3",
                "deployedAt": "2026-03-06T12:00:00Z",
                "argo": {
                    "appName": "homelab-api-dev",
                    "syncStatus": "synced",
                    "healthStatus": "healthy",
                    "revision": "abc123",
                },
                "drift": {
                    "isDrifted": False,
                    "expectedRevision": "abc123",
                    "liveRevision": "abc123",
                },
            }
        ],
    )

    response = client.get(
        "/services/homelab-api?env=dev",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "homelab-api"
    assert body["version"] == "v1.2.3"
    assert body["health"] == "healthy"
    assert body["sync"] == "synced"


def test_service_details_fall_back_to_live_runtime_metadata(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.main._load_service_rows",
        lambda **_kwargs: [
            {
                "service_id": "homelab-api",
                "service_name": "homelab-api",
                "env": "dev",
                "namespace": "homelab-api",
                "app_label": "homelab-api",
                "argo_app_name": "homelab-api-dev",
                "source": "cluster_services",
                "source_ref": "kubernetes_api",
                "last_synced_at": "2026-03-07T00:00:00+00:00",
            }
        ],
    )
    monkeypatch.setattr(
        "app.main._load_release_rows_for_service",
        lambda *_args, **_kwargs: [
            {
                "serviceId": "homelab-api",
                "env": "dev",
                "commitSha": None,
                "imageRef": None,
                "deployedAt": None,
                "argo": {
                    "appName": "homelab-api-dev",
                    "syncStatus": "unknown",
                    "healthStatus": "unknown",
                    "revision": None,
                },
                "drift": {
                    "isDrifted": False,
                    "expectedRevision": None,
                    "liveRevision": None,
                },
            }
        ],
    )
    monkeypatch.setattr(
        "app.main._load_live_service_runtime_rows",
        lambda _row: [
            {
                "serviceId": "homelab-api",
                "env": "dev",
                "commitSha": None,
                "imageRef": "ghcr.io/example/homelab-api:v2.0.0",
                "deployedAt": "2026-03-07T10:00:00Z",
                "argo": {
                    "appName": "homelab-api-dev",
                    "syncStatus": "synced",
                    "healthStatus": "healthy",
                    "revision": "def456",
                },
                "drift": {
                    "isDrifted": False,
                    "expectedRevision": None,
                    "liveRevision": "def456",
                },
            }
        ],
    )

    response = client.get(
        "/services/homelab-api?env=dev",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["version"] == "v2.0.0"
    assert body["health"] == "healthy"
    assert body["sync"] == "synced"


def test_service_deployments_endpoint_returns_release_rows(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.main._load_service_rows",
        lambda **_kwargs: [
            {
                "service_id": "homelab-api",
                "service_name": "homelab-api",
                "env": "dev",
                "namespace": "homelab-api",
                "app_label": "homelab-api",
                "argo_app_name": "homelab-api-dev",
                "source": "cluster_services",
                "source_ref": "kubernetes_api",
                "last_synced_at": "2026-03-07T00:00:00+00:00",
            }
        ],
    )
    monkeypatch.setattr(
        "app.main._load_release_rows_for_service",
        lambda *_args, **_kwargs: [
            {
                "serviceId": "homelab-api",
                "env": "dev",
                "commitSha": "abc123",
                "imageRef": "ghcr.io/example/homelab-api:v1.2.3",
                "deployedAt": "2026-03-06T12:00:00Z",
                "argo": {
                    "appName": "homelab-api-dev",
                    "syncStatus": "synced",
                    "healthStatus": "healthy",
                    "revision": "abc123",
                },
                "drift": {
                    "isDrifted": False,
                    "expectedRevision": "abc123",
                    "liveRevision": "abc123",
                },
            }
        ],
    )
    monkeypatch.setattr("app.main._load_live_service_runtime_rows", lambda _row: [])

    response = client.get(
        "/services/homelab-api/deployments?env=dev",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["deployments"]) == 1
    assert body["deployments"][0]["id"] == "abc123"
    assert body["deployments"][0]["version"] == "v1.2.3"
    assert body["deployments"][0]["status"] == "healthy"
    assert body["deployments"][0]["deployedAt"] == "2026-03-06T12:00:00Z"


def test_service_deployments_endpoint_falls_back_to_live_runtime_rows(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.main._load_service_rows",
        lambda **_kwargs: [
            {
                "service_id": "homelab-api",
                "service_name": "homelab-api",
                "env": "dev",
                "namespace": "homelab-api",
                "app_label": "homelab-api",
                "argo_app_name": "homelab-api-dev",
                "source": "cluster_services",
                "source_ref": "kubernetes_api",
                "last_synced_at": "2026-03-07T00:00:00+00:00",
            }
        ],
    )
    monkeypatch.setattr(
        "app.main._load_release_rows_for_service",
        lambda *_args, **_kwargs: [
            {
                "serviceId": "homelab-api",
                "env": "dev",
                "commitSha": None,
                "imageRef": None,
                "deployedAt": None,
                "argo": {
                    "appName": "homelab-api-dev",
                    "syncStatus": "unknown",
                    "healthStatus": "unknown",
                    "revision": None,
                },
                "drift": {
                    "isDrifted": False,
                    "expectedRevision": None,
                    "liveRevision": None,
                },
            }
        ],
    )
    monkeypatch.setattr(
        "app.main._load_live_service_runtime_rows",
        lambda _row: [
            {
                "serviceId": "homelab-api",
                "env": "dev",
                "commitSha": None,
                "imageRef": "ghcr.io/example/homelab-api:v2.0.0",
                "deployedAt": "2026-03-07T10:00:00Z",
                "argo": {
                    "appName": "homelab-api-dev",
                    "syncStatus": "synced",
                    "healthStatus": "healthy",
                    "revision": "def456",
                },
                "drift": {
                    "isDrifted": False,
                    "expectedRevision": None,
                    "liveRevision": "def456",
                },
            }
        ],
    )

    response = client.get(
        "/services/homelab-api/deployments?env=dev",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["deployments"]) == 1
    assert body["deployments"][0]["version"] == "v2.0.0"
    assert body["deployments"][0]["status"] == "healthy"
    assert body["deployments"][0]["deployedAt"] == "2026-03-07T10:00:00Z"


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


def test_logs_quickview_enforces_rate_limit(monkeypatch) -> None:
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


def test_metrics_summary_uses_cache_for_repeated_service_and_range(monkeypatch) -> None:
    calls = {"count": 0}

    def _mock_urlopen(*args, **kwargs):
        calls["count"] += 1
        return _MockPrometheusResponse(
            {"status": "success", "data": {"result": [{"value": [0, "1"]}]}}
        )

    monkeypatch.setattr("app.monitoring_providers.urlrequest.urlopen", _mock_urlopen)
    monkeypatch.setenv("OBS_METRICS_CACHE_TTL_SECONDS", "60")
    monkeypatch.setattr(
        "app.main._resolve_service_monitoring_metadata",
        lambda _service_id: ("homelab-api", "homelab-api"),
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


def test_logs_quickview_uses_service_registry_metadata_for_query(monkeypatch) -> None:
    requested_urls: list[str] = []
    payload = {"status": "success", "data": {"result": []}}

    def _mock_urlopen(request, **kwargs):
        requested_urls.append(getattr(request, "full_url", request))
        return _MockPrometheusResponse(payload)

    monkeypatch.setattr("app.monitoring_providers.urlrequest.urlopen", _mock_urlopen)
    monkeypatch.setattr(
        "app.main._load_service_rows",
        lambda **_kwargs: [
            {
                "service_id": "homelab-api",
                "service_name": "homelab-api",
                "env": "dev",
                "namespace": "homelab-api",
                "app_label": "portal-api",
                "argo_app_name": "homelab-api-dev",
                "source": "cluster_services",
                "source_ref": "kubernetes_api",
                "last_synced_at": "2026-03-06T00:00:00+00:00",
            }
        ],
    )

    response = client.get(
        "/services/homelab-api/logs/quickview?preset=errors&range=1h",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    assert requested_urls
    decoded = urlparse.unquote_plus(requested_urls[0])
    assert '{namespace="homelab-api", app="portal-api"}' in decoded


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


def test_alerts_active_gracefully_degrades_on_upstream_failure(monkeypatch) -> None:
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
