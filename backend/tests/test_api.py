import json
from io import BytesIO
from urllib import parse as urlparse
from urllib.error import HTTPError

import pytest
from fastapi.testclient import TestClient

import app.main as app_main
from app.github_workflows import GitHubWorkflowDispatchError, GitHubWorkflowDispatchResult
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
        sql.startswith(
            "SELECT PROJECT_ID, PROJECT_NAME, ENV, OWNER, REPO_URL, RUNBOOK_URL, OBSERVABILITY_MODE FROM PROJECT_REGISTRY"
        )
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
            return [("homelab-api", "homelab-api", "dev", None, None, None, "app-native")]

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
        "projects": [
            {
                "id": "homelab-api",
                "name": "homelab-api",
                "environment": "dev",
                "observabilityMode": "app-native",
            }
        ]
    }
    assert executed_args[0][1] == ("gitops_apps", "dev")


def test_projects_list_includes_gitops_catalog_metadata(monkeypatch) -> None:
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
                    "Homelab API",
                    "dev",
                    "wlodzimierrr",
                    "https://github.com/wlodzimierrr/homelab/tree/main/apps/portal/backend",
                    "https://github.com/wlodzimierrr/homelab/blob/main/docs/runbooks/homelab-api-service-operations.md",
                    "app-native",
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
        "/projects",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "projects": [
            {
                "id": "homelab-api",
                "name": "Homelab API",
                "environment": "dev",
                "owner": "wlodzimierrr",
                "repoUrl": "https://github.com/wlodzimierrr/homelab/tree/main/apps/portal/backend",
                "runbookUrl": "https://github.com/wlodzimierrr/homelab/blob/main/docs/runbooks/homelab-api-service-operations.md",
                "observabilityMode": "app-native",
            }
        ]
    }


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
    monkeypatch.setattr(
        "app.main._load_project_catalog_rows",
        lambda env=None, project_id=None: [
            {
                "project_id": "homelab-api",
                "project_name": "homelab-api",
                "env": "dev",
                "namespace": "homelab-api",
                "app_label": "homelab-api",
                "observability_mode": "app-native",
            }
        ],
    )

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
                "observabilityMode": "app-native",
            }
        ]
    }


def test_service_detail_returns_cluster_backed_row(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.main._load_project_catalog_rows",
        lambda env=None, project_id=None: [
            {
                "project_id": "homelab-web",
                "project_name": "homelab-web",
                "env": "dev",
                "namespace": "homelab-web",
                "app_label": "homelab-web",
                "observability_mode": "ingress-derived",
            }
        ],
    )
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
        "observabilityMode": "ingress-derived",
        "deploymentLock": None,
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
    assert body["identityDrift"]["driftCount"] == 0
    assert body["identityDrift"]["okCount"] == 0


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
                "observability_mode": "app-native",
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
    assert body["identityDrift"]["driftCount"] == 0
    assert body["identityDrift"]["driftKeys"] == []
    assert body["identityDrift"]["rows"][0]["observabilityMode"] is None


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


def test_service_metrics_trends_use_sequential_fallback(monkeypatch) -> None:
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
        "app.main._resolve_service_monitoring_metadata",
        lambda _service_id: ("homelab-api", "homelab-api"),
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


def test_service_metrics_trends_reject_invalid_range() -> None:
    response = client.get(
        "/services/homelab-api/metrics/trends?range=2h",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 422


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


def test_service_deployments_endpoint_returns_deployment_records(monkeypatch) -> None:
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
        "app.main._list_deployment_records_for_service",
        lambda *_args, **_kwargs: [
            {
                "deploymentId": "dep-123",
                "serviceId": "homelab-api",
                "env": "dev",
                "action": "deploy",
                "status": "live",
                "requestedAt": "2026-03-06T11:55:00Z",
                "finishedAt": "2026-03-06T12:00:00Z",
                "mergeSha": "abc123",
                "targetImage": "ghcr.io/example/homelab-api:v1.2.3",
                "prUrl": "https://github.com/example/homelab-workloads/pull/12",
                "prNumber": 12,
                "compareUrl": "https://github.com/example/homelab-portal/compare/old...new",
                "gitRef": "automation/dev-image-bump-abc123",
                "metadata": {"source": "workflow"},
            }
        ],
    )
    monkeypatch.setattr("app.main._load_deployment_metric_snapshots", lambda *_args, **_kwargs: {})

    response = client.get(
        "/services/homelab-api/deployments?env=dev",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["deployments"]) == 1
    assert body["deployments"][0]["id"] == "dep-123"
    assert body["deployments"][0]["serviceId"] == "homelab-api"
    assert body["deployments"][0]["env"] == "dev"
    assert body["deployments"][0]["action"] == "deploy"
    assert body["deployments"][0]["version"] == "v1.2.3"
    assert body["deployments"][0]["status"] == "live"
    assert body["deployments"][0]["requestedAt"] == "2026-03-06T11:55:00Z"
    assert body["deployments"][0]["deployedAt"] == "2026-03-06T12:00:00Z"
    assert body["deployments"][0]["gitPrUrl"] == "https://github.com/example/homelab-workloads/pull/12"
    assert body["deployments"][0]["compareUrl"] == "https://github.com/example/homelab-portal/compare/old...new"


def test_service_deployments_endpoint_returns_empty_list_without_records(monkeypatch) -> None:
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
    monkeypatch.setattr("app.main._list_deployment_records_for_service", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("app.main._load_deployment_metric_snapshots", lambda *_args, **_kwargs: {})

    response = client.get(
        "/services/homelab-api/deployments?env=dev",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["deployments"] == []


def test_service_deployments_endpoint_includes_observability_snapshots(monkeypatch) -> None:
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
        "app.main._list_deployment_records_for_service",
        lambda *_args, **_kwargs: [
            {
                "deploymentId": "dep-123",
                "serviceId": "homelab-api",
                "env": "dev",
                "action": "deploy",
                "status": "live",
                "requestedAt": "2026-03-06T11:55:00Z",
                "finishedAt": "2026-03-06T12:00:00Z",
                "mergeSha": "abc123",
                "targetImage": "ghcr.io/example/homelab-api:v1.2.3",
            }
        ],
    )
    monkeypatch.setattr(
        "app.main._load_deployment_metric_snapshots",
        lambda *_args, **_kwargs: {
            "errorRatePct": {"before": 0.1, "after": 0.3, "delta": 0.2},
            "p95LatencyMs": {"before": 110.0, "after": 140.0, "delta": 30.0},
            "availabilityPct": {"before": 99.9, "after": 99.4, "delta": -0.5},
        },
    )

    response = client.get(
        "/services/homelab-api/deployments?env=dev",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["deployments"][0]["errorRatePct"] == {"before": 0.1, "after": 0.3, "delta": 0.2}
    assert body["deployments"][0]["p95LatencyMs"] == {"before": 110.0, "after": 140.0, "delta": 30.0}
    assert body["deployments"][0]["availabilityPct"] == {"before": 99.9, "after": 99.4, "delta": -0.5}


def test_load_deployment_metric_snapshots_uses_record_window(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake_load_window(_service_row, *, window_start, window_end):
        captured["window_start"] = window_start.isoformat()
        captured["window_end"] = window_end.isoformat()
        return {
            "errorRatePct": {"before": 0.1, "after": 0.3, "delta": 0.2},
        }

    monkeypatch.setattr("app.main._load_metric_snapshots_for_window", _fake_load_window)

    result = app_main._load_deployment_metric_snapshots(
        {
            "service_id": "homelab-api",
            "env": "dev",
            "namespace": "homelab-api",
            "app_label": "homelab-api",
        },
        {
            "deployWindowStart": "2026-03-10T16:35:09+00:00",
            "deployWindowEnd": "2026-03-10T16:37:20+00:00",
            "finishedAt": "2026-03-10T16:37:20+00:00",
        },
    )

    assert captured == {
        "window_start": "2026-03-10T16:35:09+00:00",
        "window_end": "2026-03-10T16:37:20+00:00",
    }
    assert result["errorRatePct"]["delta"] == 0.2


def test_service_deployment_observability_returns_window_scoped_sections(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.main._get_deployment_record_by_id",
        lambda _deployment_id: {
            "deploymentId": "dep-123",
            "serviceId": "homelab-api",
            "env": "dev",
            "action": "deploy",
            "status": "live",
            "deployWindowStart": "2026-03-10T16:35:09+00:00",
            "deployWindowEnd": "2026-03-10T16:37:20+00:00",
            "compareUrl": "https://github.com/example/homelab-portal/compare/a...b",
            "prUrl": "https://github.com/example/homelab-workloads/pull/46",
            "prNumber": 46,
            "deployReason": "Ship observability fix",
        },
    )
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
                "last_synced_at": "2026-03-11T00:00:00+00:00",
            }
        ],
    )
    monkeypatch.setattr(
        "app.main._load_metric_snapshots_for_window",
        lambda *_args, **_kwargs: {
            "errorRatePct": {"before": 0.1, "after": 0.2, "delta": 0.1},
            "p95LatencyMs": {"before": 120.0, "after": 160.0, "delta": 40.0},
            "availabilityPct": {"before": 99.9, "after": 99.7, "delta": -0.2},
        },
    )
    monkeypatch.setattr(
        "app.main._build_deployment_timeline_response",
        lambda **_kwargs: {
            "queryStatus": "ok",
            "queryMessage": None,
            "serviceId": "homelab-api",
            "windowStart": "2026-03-10T16:35:09+00:00",
            "windowEnd": "2026-03-10T16:37:20+00:00",
            "generatedAt": "2026-03-11T12:00:00+00:00",
            "providerStatus": {
                "provider": "prometheus",
                "baseUrl": "http://prometheus.local",
                "status": "healthy",
                "reachable": True,
                "checkedAt": "2026-03-11T12:00:00+00:00",
            },
            "segments": [
                {
                    "start": "2026-03-10T16:35:09+00:00",
                    "end": "2026-03-10T16:37:20+00:00",
                    "status": "healthy",
                }
            ],
        },
    )
    monkeypatch.setattr(
        "app.main._build_deployment_logs_response",
        lambda **_kwargs: {
            "queryStatus": "ok",
            "queryMessage": None,
            "serviceId": "homelab-api",
            "preset": "errors",
            "generatedAt": "2026-03-11T12:00:00+00:00",
            "windowStart": "2026-03-10T16:35:09+00:00",
            "windowEnd": "2026-03-10T16:37:20+00:00",
            "limit": 50,
            "returned": 1,
            "moreAvailable": False,
            "lines": [
                {
                    "timestamp": "2026-03-10T16:36:00+00:00",
                    "message": "line-1",
                    "labels": {"app": "homelab-api"},
                }
            ],
            "providerStatus": {
                "provider": "loki",
                "baseUrl": "http://loki.local",
                "status": "healthy",
                "reachable": True,
                "checkedAt": "2026-03-11T12:00:00+00:00",
            },
        },
    )

    response = client.get(
        "/services/homelab-api/observability/window?deploymentId=dep-123&logsPreset=errors&logsLimit=50",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["context"]["deploymentId"] == "dep-123"
    assert body["context"]["windowSource"] == "deployment_record"
    assert body["metrics"]["queryStatus"] == "ok"
    assert body["metrics"]["errorRatePct"]["delta"] == 0.1
    assert body["healthTimeline"]["queryStatus"] == "ok"
    assert body["logsQuickView"]["queryStatus"] == "ok"
    assert body["logsQuickView"]["returned"] == 1


def test_service_deployment_observability_reports_missing_deploy_window(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.main._get_deployment_record_by_id",
        lambda _deployment_id: {
            "deploymentId": "dep-missing",
            "serviceId": "homelab-api",
            "env": "dev",
            "action": "deploy",
            "status": "pending",
            "requestedAt": None,
            "startedAt": None,
            "deployWindowStart": None,
            "deployWindowEnd": None,
        },
    )
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
                "last_synced_at": "2026-03-11T00:00:00+00:00",
            }
        ],
    )

    response = client.get(
        "/services/homelab-api/observability/window?deploymentId=dep-missing",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["context"]["evidenceStatus"] == "missing"
    assert body["metrics"]["queryStatus"] == "no_deployment_window"
    assert body["healthTimeline"]["queryStatus"] == "no_deployment_window"
    assert body["logsQuickView"]["queryStatus"] == "no_deployment_window"


def test_service_deployment_observability_supports_explicit_window(monkeypatch) -> None:
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
                "last_synced_at": "2026-03-11T00:00:00+00:00",
            }
        ],
    )
    monkeypatch.setattr(
        "app.main._load_metric_snapshots_for_window",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "app.main._build_deployment_timeline_response",
        lambda **_kwargs: {
            "queryStatus": "no_data",
            "queryMessage": "No health data.",
            "serviceId": "homelab-api",
            "windowStart": "2026-03-11T10:00:00+00:00",
            "windowEnd": "2026-03-11T10:10:00+00:00",
            "generatedAt": "2026-03-11T12:00:00+00:00",
            "providerStatus": None,
            "segments": [],
        },
    )
    monkeypatch.setattr(
        "app.main._build_deployment_logs_response",
        lambda **_kwargs: {
            "queryStatus": "no_data",
            "queryMessage": "No logs retained.",
            "serviceId": "homelab-api",
            "preset": "errors",
            "generatedAt": "2026-03-11T12:00:00+00:00",
            "windowStart": "2026-03-11T10:00:00+00:00",
            "windowEnd": "2026-03-11T10:10:00+00:00",
            "limit": 50,
            "returned": 0,
            "moreAvailable": False,
            "lines": [],
            "providerStatus": None,
        },
    )

    response = client.get(
        "/services/homelab-api/observability/window?windowStart=2026-03-11T10:00:00%2B00:00&windowEnd=2026-03-11T10:10:00%2B00:00",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["context"]["windowSource"] == "explicit_window"
    assert body["context"]["deploymentId"] is None
    assert body["metrics"]["queryStatus"] == "no_data"


def test_create_deployment_record_endpoint_returns_record(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake_upsert(payload, *, requested_by):
        captured["service_id"] = payload.service_id
        captured["env"] = payload.env
        captured["action"] = payload.action
        captured["requested_by"] = requested_by
        return {
            "deploymentId": "dep-123",
            "serviceId": payload.service_id,
            "env": payload.env,
            "action": payload.action,
            "status": payload.status,
            "requestedAt": "2026-03-09T20:00:00Z",
            "requestedBy": requested_by,
            "prUrl": payload.pr_url,
            "prNumber": payload.pr_number,
            "mergeSha": payload.merge_sha,
            "targetImage": payload.target_image,
            "previousImage": payload.previous_image,
            "argoApp": "homelab-api-dev",
            "syncStatus": None,
            "healthStatus": None,
            "startedAt": None,
            "finishedAt": None,
            "deployWindowStart": None,
            "deployWindowEnd": None,
            "deployReason": payload.deploy_reason,
            "compareUrl": payload.compare_url,
            "gitRef": payload.git_ref,
            "metadata": payload.metadata or {},
        }

    monkeypatch.setattr("app.main._upsert_deployment_record_row", _fake_upsert)
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
    monkeypatch.setattr("app.main._load_deployment_metric_snapshots", lambda *_args, **_kwargs: {})

    response = client.post(
        "/deployments",
        headers={"Authorization": "Bearer dev-static-token"},
        json={
            "serviceId": "homelab-api",
            "env": "dev",
            "action": "deploy",
            "status": "pending",
            "requestedAt": "2026-03-09T20:00:00Z",
            "gitPrUrl": "https://github.com/example/homelab-workloads/pull/12",
            "gitPrNumber": 12,
            "mergeSha": "abc123",
            "imageRef": "ghcr.io/example/homelab-api:sha-abc123",
            "previousImageRef": "ghcr.io/example/homelab-api:sha-old",
            "gitRef": "automation/dev-image-bump-abc123",
            "compareUrl": "https://github.com/example/homelab-portal/compare/old...new",
            "deployReason": "Ship fix",
            "metadata": {"source": "workflow"},
        },
    )

    assert response.status_code == 201
    assert captured == {
        "service_id": "homelab-api",
        "env": "dev",
        "action": "deploy",
        "requested_by": "dev-static-token",
    }
    body = response.json()
    assert body["id"] == "dep-123"
    assert body["serviceId"] == "homelab-api"
    assert body["action"] == "deploy"
    assert body["status"] == "pending"
    assert body["gitPrUrl"] == "https://github.com/example/homelab-workloads/pull/12"
    assert body["imageRef"] == "ghcr.io/example/homelab-api:sha-abc123"


def test_request_portal_rollback_endpoint_dispatches_workflow(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake_dispatch(**kwargs):
        captured.update(kwargs)
        return GitHubWorkflowDispatchResult(
            repository="wlodzimierrr/homelab-portal",
            workflow_file="gated-promotion.yml",
            workflow_ref="main",
            workflow_url="https://github.com/wlodzimierrr/homelab-portal/actions/workflows/gated-promotion.yml",
        )

    monkeypatch.setattr("app.main.dispatch_portal_rollback_workflow", _fake_dispatch)

    response = client.post(
        "/rollbacks",
        headers={"Authorization": "Bearer dev-static-token"},
        json={
            "targetEnvironment": "prod",
            "rollbackApiTag": "sha-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "rollbackWebTag": "sha-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "reason": "Restore known-good portal release after login regression.",
        },
    )

    assert response.status_code == 202
    assert captured == {
        "rollback_api_tag": "sha-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "rollback_web_tag": "sha-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "operator_reason": "Restore known-good portal release after login regression.",
        "target_environment": "prod",
    }
    body = response.json()
    assert body["status"] == "accepted"
    assert body["action"] == "rollback"
    assert body["targetEnvironment"] == "prod"
    assert body["rollbackApiTag"] == "sha-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert body["rollbackWebTag"] == "sha-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    assert body["requestedBy"] == "dev-static-token"
    assert body["workflowFile"] == "gated-promotion.yml"


def test_request_portal_rollback_endpoint_validates_tags_and_reason() -> None:
    response = client.post(
        "/rollbacks",
        headers={"Authorization": "Bearer dev-static-token"},
        json={
            "targetEnvironment": "prod",
            "rollbackApiTag": "latest",
            "rollbackWebTag": "sha-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "reason": "bad",
        },
    )

    assert response.status_code == 422


def test_request_portal_rollback_endpoint_maps_dispatch_errors(monkeypatch) -> None:
    def _fake_dispatch(**_kwargs):
        raise GitHubWorkflowDispatchError("upstream unavailable", status_code=503)

    monkeypatch.setattr("app.main.dispatch_portal_rollback_workflow", _fake_dispatch)

    response = client.post(
        "/rollbacks",
        headers={"Authorization": "Bearer dev-static-token"},
        json={
            "targetEnvironment": "prod",
            "rollbackApiTag": "sha-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "rollbackWebTag": "sha-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "reason": "Restore known-good portal release after login regression.",
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "upstream unavailable"


def test_get_deployment_endpoint_returns_record(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.main._get_deployment_record_by_id",
        lambda _deployment_id: {
            "deploymentId": "dep-123",
            "serviceId": "homelab-api",
            "env": "dev",
            "action": "deploy",
            "status": "live",
            "requestedAt": "2026-03-09T20:00:00Z",
            "requestedBy": "dev-static-token",
            "prUrl": "https://github.com/example/homelab-workloads/pull/12",
            "prNumber": 12,
            "mergeSha": "abc123",
            "targetImage": "ghcr.io/example/homelab-api:v1.2.3",
            "previousImage": "ghcr.io/example/homelab-api:v1.2.2",
            "argoApp": "homelab-api-dev",
            "syncStatus": "synced",
            "healthStatus": "healthy",
            "startedAt": "2026-03-09T20:01:00Z",
            "finishedAt": "2026-03-09T20:03:00Z",
            "deployWindowStart": "2026-03-09T20:01:00Z",
            "deployWindowEnd": "2026-03-09T20:08:00Z",
            "deployReason": "Ship fix",
            "compareUrl": "https://github.com/example/homelab-portal/compare/old...new",
            "gitRef": "automation/dev-image-bump-abc123",
            "metadata": {"source": "workflow"},
        },
    )
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
    monkeypatch.setattr("app.main._load_deployment_metric_snapshots", lambda *_args, **_kwargs: {})

    response = client.get(
        "/deployments/dep-123",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "dep-123"
    assert body["serviceId"] == "homelab-api"
    assert body["status"] == "live"
    assert body["deployedAt"] == "2026-03-09T20:03:00Z"
    assert body["healthStatus"] == "healthy"


def test_releases_endpoint_returns_traceability_rows(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.main._load_project_rows",
        lambda: [{"service_id": "homelab-api", "env": "dev"}],
    )
    monkeypatch.setattr("app.main._load_service_rows", lambda **_kwargs: [])
    monkeypatch.setattr("app.main._load_live_service_runtime_rows", lambda _row: [])
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
    monkeypatch.setattr("app.main._load_service_rows", lambda **_kwargs: [])
    monkeypatch.setattr("app.main._load_live_service_runtime_rows", lambda _row: [])
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


def test_releases_endpoint_falls_back_to_live_runtime_metadata(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.main._load_project_rows",
        lambda: [{"service_id": "homelab-api", "service_name": "homelab-api", "env": "dev"}],
    )
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
                "last_synced_at": None,
            }
        ],
    )
    monkeypatch.setattr("app.main.load_ci_metadata_rows", lambda: [])
    monkeypatch.setattr("app.main.load_argo_metadata_rows", lambda: [])
    monkeypatch.setattr(
        "app.main._load_live_service_runtime_rows",
        lambda _row: [
            {
                "serviceId": "homelab-api",
                "env": "dev",
                "commitSha": None,
                "imageRef": "ghcr.io/wlodzimierrr/homelab-api:sha-live123",
                "deployedAt": "2026-03-09T10:00:00Z",
                "argo": {
                    "appName": "homelab-api-dev",
                    "syncStatus": "synced",
                    "healthStatus": "healthy",
                    "revision": "abcdef1234567890",
                },
                "drift": {
                    "isDrifted": False,
                    "expectedRevision": None,
                    "liveRevision": "abcdef1234567890",
                },
            }
        ],
    )

    response = client.get(
        "/releases?env=dev&limit=50",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    row = body[0]
    assert row["commitSha"] == "abcdef1234567890"
    assert row["imageRef"] == "ghcr.io/wlodzimierrr/homelab-api:sha-live123"
    assert row["deployedAt"] == "2026-03-09T10:00:00Z"
    assert row["argo"]["appName"] == "homelab-api-dev"
    assert row["argo"]["syncStatus"] == "synced"
    assert row["argo"]["healthStatus"] == "healthy"
    assert row["argo"]["revision"] == "abcdef1234567890"


def test_release_dashboard_compat_endpoint_available(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.main._load_project_rows",
        lambda: [{"service_id": "homelab-api", "env": "dev"}],
    )
    monkeypatch.setattr("app.main._load_service_rows", lambda **_kwargs: [])
    monkeypatch.setattr("app.main._load_live_service_runtime_rows", lambda _row: [])
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
