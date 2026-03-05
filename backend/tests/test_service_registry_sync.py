from __future__ import annotations

from datetime import datetime, timezone

from app import service_registry_sync


class _DummyConn:
    pass


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


def test_sync_service_registry_collects_source_failures(monkeypatch) -> None:
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

    summary = service_registry_sync.sync_service_registry_from_cluster(
        _DummyConn(),
        env_name="dev",
        namespaces=("homelab-web", "broken"),
        argo_namespace="argocd",
    )

    assert summary["env"] == "dev"
    assert summary["discovered"] == 1
    assert summary["upserted"] == 1
    assert summary["inserted"] == 1
    assert len(summary["sourceFailures"]) == 1
    assert summary["sourceFailures"][0]["source"] == "kubernetes"
    assert summary["sourceFailures"][0]["scope"] == "broken"


def test_normalize_service_id_removes_unsafe_chars() -> None:
    assert service_registry_sync._normalize_service_id("Portal Project") == "portal-project"
    assert service_registry_sync._normalize_service_id("  ") == "unknown-service"

