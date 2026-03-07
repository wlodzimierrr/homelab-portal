from __future__ import annotations

from datetime import datetime, timezone

from app import service_registry_sync


class _DummyConn:
    pass


def test_upsert_service_registry_records_prunes_conflicting_noncanonical_rows() -> None:
    synced_at = datetime(2026, 3, 6, tzinfo=timezone.utc)
    record = service_registry_sync.ServiceRegistryRecord(
        service_id="homelab-api",
        service_name="Allowed",
        namespace="default",
        env="dev",
        app_label="homelab-api",
        argo_app_name="homelab-api-dev",
        source="cluster_services",
        source_ref="kubernetes_api",
        last_synced_at=synced_at,
    )

    class _Cursor:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql: str, params: tuple[object, ...]) -> None:
            self.calls.append((" ".join(sql.split()), params))

        def fetchone(self):
            return (True,)

    class _Conn:
        def __init__(self) -> None:
            self.cursor_instance = _Cursor()

        def cursor(self):
            return self.cursor_instance

    conn = _Conn()

    inserted, updated = service_registry_sync._upsert_service_registry_records(conn, [record])

    assert inserted == 1
    assert updated == 0
    assert len(conn.cursor_instance.calls) == 2
    delete_sql, delete_params = conn.cursor_instance.calls[0]
    assert delete_sql.startswith("DELETE FROM service_registry")
    assert delete_params == ("dev", "Allowed", "default", "homelab-api", "cluster_services")
    insert_sql, _insert_params = conn.cursor_instance.calls[1]
    assert insert_sql.startswith("INSERT INTO service_registry")


def test_build_records_from_deployments_uses_labels_and_argo_mapping() -> None:
    deployments = [
        {
            "metadata": {
                "name": "homelab-api",
                "namespace": "homelab-api",
                "labels": {"app.kubernetes.io/name": "homelab-api"},
                "annotations": {},
            }
        }
    ]
    synced_at = datetime(2026, 3, 5, tzinfo=timezone.utc)
    rows = service_registry_sync._build_records_from_deployments(
        deployments=deployments,
        env_name="dev",
        source_ref="kubernetes_api",
        synced_at=synced_at,
        argo_by_namespace={"homelab-api": "homelab-api-dev"},
    )

    assert len(rows) == 1
    row = rows[0]
    assert row.service_id == "homelab-api"
    assert row.service_name == "homelab-api"
    assert row.namespace == "homelab-api"
    assert row.env == "dev"
    assert row.app_label == "homelab-api"
    assert row.argo_app_name == "homelab-api-dev"
    assert row.source == "cluster_services"


def test_build_records_from_services_and_deployments_prefers_service_rows() -> None:
    services = [
        {
            "metadata": {
                "name": "homelab-web",
                "namespace": "homelab-web",
                "labels": {"app.kubernetes.io/name": "homelab-web"},
            },
            "spec": {
                "selector": {"app.kubernetes.io/name": "homelab-web"},
            },
        }
    ]
    deployments = [
        {
            "metadata": {
                "name": "homelab-web-deployment",
                "namespace": "homelab-web",
                "labels": {"app.kubernetes.io/name": "homelab-web"},
                "annotations": {},
            }
        }
    ]
    synced_at = datetime(2026, 3, 5, tzinfo=timezone.utc)

    rows = service_registry_sync._build_records_from_services_and_deployments(
        services=services,
        deployments=deployments,
        env_name="dev",
        source_ref="kubernetes_api",
        synced_at=synced_at,
        argo_by_namespace={"homelab-web": "homelab-web-dev"},
    )

    assert len(rows) == 1
    row = rows[0]
    assert row.service_id == "homelab-web"
    assert row.service_name == "homelab-web"
    assert row.namespace == "homelab-web"
    assert row.argo_app_name == "homelab-web-dev"


def test_build_records_from_services_and_deployments_skips_backing_postgres_services() -> None:
    services = [
        {
            "metadata": {
                "name": "homelab-api",
                "namespace": "homelab-api",
                "labels": {
                    "app.kubernetes.io/name": "homelab-api",
                    "app.kubernetes.io/component": "api",
                },
            },
            "spec": {
                "selector": {
                    "app.kubernetes.io/name": "homelab-api",
                    "app.kubernetes.io/component": "api",
                },
                "ports": [{"name": "http"}],
            },
        },
        {
            "metadata": {
                "name": "homelab-api-postgres",
                "namespace": "homelab-api",
                "labels": {
                    "app.kubernetes.io/name": "homelab-api",
                    "app.kubernetes.io/component": "postgres",
                },
            },
            "spec": {
                "selector": {
                    "app.kubernetes.io/name": "homelab-api",
                    "app.kubernetes.io/component": "postgres",
                },
                "ports": [{"name": "postgres"}],
            },
        },
    ]
    deployments = [
        {
            "metadata": {
                "name": "homelab-api",
                "namespace": "homelab-api",
                "labels": {
                    "app.kubernetes.io/name": "homelab-api",
                    "app.kubernetes.io/component": "api",
                },
                "annotations": {},
            }
        }
    ]
    synced_at = datetime(2026, 3, 7, tzinfo=timezone.utc)

    rows = service_registry_sync._build_records_from_services_and_deployments(
        services=services,
        deployments=deployments,
        env_name="dev",
        source_ref="kubernetes_api",
        synced_at=synced_at,
        argo_by_namespace={"homelab-api": "homelab-api-dev"},
    )

    assert len(rows) == 1
    row = rows[0]
    assert row.service_id == "homelab-api"
    assert row.service_name == "homelab-api"
    assert row.namespace == "homelab-api"


def test_sync_service_registry_collects_source_failures(monkeypatch) -> None:
    monkeypatch.setattr(
        service_registry_sync,
        "_fetch_services_in_namespace",
        lambda namespace: [
            {
                "metadata": {
                    "name": "homelab-web",
                    "namespace": namespace,
                    "labels": {"app.kubernetes.io/name": "homelab-web"},
                },
                "spec": {"selector": {"app.kubernetes.io/name": "homelab-web"}},
            }
        ]
        if namespace != "broken"
        else (_ for _ in ()).throw(RuntimeError("boom")),
    )

    def _fake_fetch_deployments(namespace: str) -> list[dict]:
        if namespace == "broken":
            raise RuntimeError("boom")
        return [
            {
                "metadata": {
                    "name": "homelab-web",
                    "namespace": namespace,
                    "labels": {"app.kubernetes.io/name": "homelab-web"},
                    "annotations": {},
                }
            }
        ]

    monkeypatch.setattr(
        service_registry_sync,
        "_fetch_deployments_in_namespace",
        _fake_fetch_deployments,
    )
    monkeypatch.setattr(
        service_registry_sync,
        "_fetch_argocd_applications",
        lambda _: [],
    )
    monkeypatch.setattr(
        service_registry_sync,
        "_upsert_service_registry_records",
        lambda conn, records: (len(records), 0),
    )
    monkeypatch.setattr(
        service_registry_sync,
        "_prune_service_registry_records",
        lambda conn, env_name, namespaces, keep_keys: 0,
    )

    summary = service_registry_sync.sync_service_registry_from_cluster(
        _DummyConn(),
        env_name="dev",
        namespaces=("homelab-web", "broken"),
        argo_namespace="argocd",
    )

    assert summary["env"] == "dev"
    assert summary["source"] == "cluster_services"
    assert summary["discovered"] == 1
    assert summary["upserted"] == 1
    assert summary["inserted"] == 1
    assert summary["deleted"] == 0
    assert len(summary["sourceFailures"]) == 2
    assert {item["source"] for item in summary["sourceFailures"]} == {
        "kubernetes_services",
        "kubernetes_deployments",
    }
    assert summary["sourceFailures"][0]["scope"] == "broken"


def test_normalize_service_id_removes_unsafe_chars() -> None:
    assert service_registry_sync._normalize_service_id("Portal Project") == "portal-project"
    assert service_registry_sync._normalize_service_id("  ") == "unknown-service"
