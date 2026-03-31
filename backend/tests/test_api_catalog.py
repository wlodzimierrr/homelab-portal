def test_projects_list_does_not_seed_defaults_on_read(client, monkeypatch) -> None:
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


def test_projects_list_supports_env_filter(client, monkeypatch) -> None:
    executed_args: list[tuple[str, tuple[object, ...] | None]] = []

    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql: str, args=None):
            executed_args.append((" ".join(sql.split()).upper(), args))

        def fetchall(self):
            return [
                ("homelab-api", "homelab-api", "dev", None, None, None, "app-native")
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


def test_projects_list_includes_gitops_catalog_metadata(client, monkeypatch) -> None:
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


def test_project_catalog_diagnostics_reports_freshness(client, monkeypatch) -> None:
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


def test_services_list_returns_cluster_backed_rows(client, monkeypatch) -> None:
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
                    "homelab-api",
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


def test_service_detail_returns_cluster_backed_row(client, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.main._load_project_catalog_rows",
        lambda env=None, project_id=None: (
            [
                {
                    "project_id": "homelab-web",
                    "project_name": "homelab-web",
                    "env": "prod",
                    "namespace": "homelab-web",
                    "app_label": "homelab-web",
                    "public_host": "homelab.local",
                }
            ]
            if env == "prod"
            else [
                {
                    "project_id": "homelab-web",
                    "project_name": "homelab-web",
                    "env": "dev",
                    "namespace": "homelab-web",
                    "app_label": "homelab-web",
                    "observability_mode": "ingress-derived",
                }
            ]
        ),
    )
    monkeypatch.setattr(
        "app.main._load_service_catalog_rows",
        lambda env=None, service_id=None: [
            {
                "service_id": "homelab-web",
                "service_name": "homelab-web",
                "env": "dev",
                "namespace": "homelab-web",
                "app_label": "homelab-web",
                "argo_app_name": "homelab-web-dev",
                "project_id": "homelab-web",
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
    monkeypatch.setattr(
        "app.main._load_release_rows_for_service", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr("app.main._load_live_service_runtime_rows", lambda _row: [])
    monkeypatch.setattr("app.main._get_active_deployment_lock", lambda *_args, **_kwargs: None)

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
        "publicHost": None,
        "projectContext": {
            "projectId": "homelab-web",
            "projectName": "homelab-web",
            "namespace": "homelab-web",
            "siblingServiceIds": [],
            "isLinked": True,
        },
        "capabilities": {
            "canDeployToDev": True,
            "canPromoteToProd": True,
            "canRollback": True,
            "rollbackEnvs": ["dev", "prod"],
            "canEditConfig": False,
            "configEnvs": [],
            "canEditPublicHostname": True,
            "canAdopt": True,
            "canDelete": True,
            "decommissionMode": "standalone",
            "decommissionReason": None,
        },
    }


def test_service_detail_blocks_delete_for_project_linked_service(client, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.main._load_project_catalog_rows",
        lambda env=None, project_id=None: (
            []
            if project_id == "oauth2-proxy"
            else [
                {
                    "project_id": "homelab-web",
                    "project_name": "Homelab Web",
                    "env": "dev",
                    "namespace": "homelab-web",
                    "app_label": "homelab-web",
                    "observability_mode": "no-http",
                }
            ]
        ),
    )
    monkeypatch.setattr(
        "app.main._load_service_catalog_rows",
        lambda env=None, service_id=None: [
            {
                "service_id": "oauth2-proxy",
                "service_name": "oauth2-proxy",
                "env": "dev",
                "namespace": "homelab-web",
                "app_label": "oauth2-proxy",
                "argo_app_name": "homelab-web-dev",
                "project_id": "homelab-web",
            }
        ],
    )
    monkeypatch.setattr(
        "app.main._load_service_rows",
        lambda **_kwargs: [
            {
                "service_id": "oauth2-proxy",
                "service_name": "oauth2-proxy",
                "env": "dev",
                "namespace": "homelab-web",
                "app_label": "oauth2-proxy",
                "argo_app_name": "homelab-web-dev",
                "source": "cluster_services",
                "source_ref": "kubernetes_api",
                "last_synced_at": None,
            }
        ],
    )
    monkeypatch.setattr(
        "app.main._load_release_rows_for_service", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr("app.main._load_live_service_runtime_rows", lambda _row: [])
    monkeypatch.setattr("app.main._get_active_deployment_lock", lambda *_args, **_kwargs: None)

    response = client.get(
        "/services/oauth2-proxy",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["projectContext"]["projectId"] == "homelab-web"
    assert body["capabilities"]["canAdopt"] is False
    assert body["capabilities"]["canDelete"] is False
    assert body["capabilities"]["decommissionMode"] == "unsupported"
    assert "manually managed shared services" in body["capabilities"]["decommissionReason"]


def test_service_detail_allows_project_component_decommission_for_scaffold_generated_shared_service(client, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.main._load_project_catalog_rows",
        lambda env=None, project_id=None: (
            []
            if project_id == "portal-project-worker"
            else [
                {
                    "project_id": "portal-project",
                    "project_name": "Portal Project",
                    "env": "dev",
                    "namespace": "portal-project",
                    "app_label": "portal-project",
                    "observability_mode": "ingress-derived",
                }
            ]
        ),
    )
    monkeypatch.setattr(
        "app.main._load_service_catalog_rows",
        lambda env=None, service_id=None: [
            {
                "service_id": "portal-project-worker",
                "service_name": "portal-project-worker",
                "env": "dev",
                "namespace": "portal-project",
                "app_label": "portal-project-worker",
                "argo_app_name": "portal-project-dev",
                "project_id": "portal-project",
            }
        ],
    )
    monkeypatch.setattr(
        "app.main._load_service_rows",
        lambda **_kwargs: [
            {
                "service_id": "portal-project-worker",
                "service_name": "portal-project-worker",
                "env": "dev",
                "namespace": "portal-project",
                "app_label": "portal-project-worker",
                "argo_app_name": "portal-project-dev",
                "source": "cluster_services",
                "source_ref": "kubernetes_api",
                "last_synced_at": None,
                "project_id": "portal-project",
            }
        ],
    )
    monkeypatch.setattr("app.main._load_release_rows_for_service", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("app.main._load_live_service_runtime_rows", lambda _row: [])
    monkeypatch.setattr("app.main._get_active_deployment_lock", lambda *_args, **_kwargs: None)

    response = client.get(
        "/services/portal-project-worker",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["projectContext"]["projectId"] == "portal-project"
    assert body["capabilities"]["canDelete"] is True
    assert body["capabilities"]["decommissionMode"] == "project-component"
    assert "preserving the project and sibling services" in body["capabilities"]["decommissionReason"]


def test_service_detail_blocks_bundle_core_component_decommission(client, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.main._load_project_catalog_rows",
        lambda env=None, project_id=None: (
            []
            if project_id == "portal-project-frontend"
            else [
                {
                    "project_id": "portal-project",
                    "project_name": "Portal Project",
                    "env": "dev",
                    "namespace": "portal-project",
                    "app_label": "portal-project",
                    "observability_mode": "ingress-derived",
                }
            ]
        ),
    )
    monkeypatch.setattr(
        "app.main._load_service_catalog_rows",
        lambda env=None, service_id=None: [
            {
                "service_id": "portal-project-frontend",
                "service_name": "portal-project-frontend",
                "env": "dev",
                "namespace": "portal-project",
                "app_label": "portal-project-frontend",
                "argo_app_name": "portal-project-dev",
                "project_id": "portal-project",
            }
        ],
    )
    monkeypatch.setattr(
        "app.main._load_service_rows",
        lambda **_kwargs: [
            {
                "service_id": "portal-project-frontend",
                "service_name": "portal-project-frontend",
                "env": "dev",
                "namespace": "portal-project",
                "app_label": "portal-project-frontend",
                "argo_app_name": "portal-project-dev",
                "source": "cluster_services",
                "source_ref": "kubernetes_api",
                "last_synced_at": None,
                "project_id": "portal-project",
            }
        ],
    )
    monkeypatch.setattr("app.main._load_release_rows_for_service", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("app.main._load_live_service_runtime_rows", lambda _row: [])
    monkeypatch.setattr("app.main._get_active_deployment_lock", lambda *_args, **_kwargs: None)

    response = client.get(
        "/services/portal-project-frontend",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["capabilities"]["canDelete"] is False
    assert body["capabilities"]["decommissionMode"] == "unsupported"
    assert "Bundle core components" in body["capabilities"]["decommissionReason"]


def test_catalog_reconciliation_returns_join_rows(client, monkeypatch) -> None:
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
                return [
                    ("homelab-api", "Homelab API", "dev", "homelab-api", "homelab-api")
                ]
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
                    "homelab-api",
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


def test_service_registry_diagnostics_reports_empty_registry(client, monkeypatch) -> None:
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
    monkeypatch.setattr(
        "app.main._load_project_catalog_rows", lambda env=None, project_id=None: []
    )
    monkeypatch.setattr(
        "app.main._load_service_catalog_rows", lambda env=None, service_id=None: []
    )
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


def test_service_registry_diagnostics_reports_stale_registry_with_mismatches(client, monkeypatch,) -> None:
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
        lambda: [
            {"service_id": "homelab-api", "service_name": "Homelab API", "env": "dev"}
        ],
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
        lambda: [
            {
                "serviceId": "portal-project",
                "serviceName": "Portal Project",
                "env": "dev",
            }
        ],
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


def test_service_registry_diagnostics_reports_warning_before_stale(client, monkeypatch) -> None:
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
    monkeypatch.setattr(
        "app.main._load_project_catalog_rows", lambda env=None, project_id=None: []
    )
    monkeypatch.setattr(
        "app.main._load_service_catalog_rows", lambda env=None, service_id=None: []
    )
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
