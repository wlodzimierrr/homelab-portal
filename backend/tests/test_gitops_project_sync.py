from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app import gitops_project_sync


class _DummyConn:
    pass


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_discover_gitops_project_records_reads_app_env_directories(tmp_path: Path) -> None:
    repo_path = tmp_path / "workloads"
    _write(
        repo_path / "apps" / "homelab-api" / "envs" / "dev" / "kustomization.yaml",
        "apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\nresources:\n  - ../../base\n",
    )
    _write(
        repo_path / "apps" / "homelab-api" / "base" / "namespace.yaml",
        "apiVersion: v1\nkind: Namespace\nmetadata:\n  name: homelab-api\n",
    )
    _write(
        repo_path / "apps" / "homelab-api" / "base" / "deployment.yaml",
        (
            "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n"
            "  name: homelab-api\n  namespace: homelab-api\n"
            "  labels:\n    app.kubernetes.io/name: homelab-api\n"
        ),
    )

    synced_at = datetime(2026, 3, 5, tzinfo=timezone.utc)
    rows, failures = gitops_project_sync.discover_gitops_project_records(
        repo_path=repo_path,
        env_name="dev",
        synced_at=synced_at,
    )

    assert failures == []
    assert len(rows) == 1
    row = rows[0]
    assert row.project_id == "homelab-api"
    assert row.project_name == "homelab-api"
    assert row.namespace == "homelab-api"
    assert row.env == "dev"
    assert row.app_label == "homelab-api"
    assert row.source == "gitops_apps"
    assert row.last_synced_at == synced_at
    assert row.source_ref.endswith(":apps/homelab-api/envs/dev")


def test_resolve_default_workloads_repo_path_does_not_raise_for_shallow_container_layout() -> None:
    resolved = gitops_project_sync._resolve_default_workloads_repo_path(
        Path("/app/app/gitops_project_sync.py")
    )

    assert resolved == Path("/app/app/workloads")


def test_sync_project_registry_from_gitops_collects_failures(monkeypatch) -> None:
    synced_at = datetime(2026, 3, 5, tzinfo=timezone.utc)
    monkeypatch.setattr(gitops_project_sync, "_utc_now", lambda: synced_at)
    monkeypatch.setattr(
        gitops_project_sync,
        "discover_gitops_project_records",
        lambda **kwargs: (
            [],
            [
                {
                    "source": "gitops_apps",
                    "scope": "apps/missing/envs/dev",
                    "error": "boom",
                }
            ],
        ),
    )
    monkeypatch.setattr(
        gitops_project_sync,
        "_upsert_project_registry_records",
        lambda conn, records: (0, 0),
    )
    monkeypatch.setattr(
        gitops_project_sync,
        "_prune_project_registry_records",
        lambda conn, envs, keep_keys: 0,
    )

    summary = gitops_project_sync.sync_project_registry_from_gitops(
        _DummyConn(),
        env_name="dev",
        repo_path=Path("/tmp/does-not-matter"),
    )

    assert summary["source"] == "gitops_apps"
    assert summary["env"] == "dev"
    assert summary["discovered"] == 0
    assert summary["upserted"] == 0
    assert summary["deleted"] == 0
    assert len(summary["sourceFailures"]) == 1
    assert summary["sourceFailures"][0]["scope"] == "apps/missing/envs/dev"
