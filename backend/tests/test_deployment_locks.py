from datetime import datetime, timezone

import pytest

from app import deployment_reconciler
from app.deployment_locks import DeploymentLockConflictError
from app.main import (
    CreateDeploymentRecordRequest,
    _upsert_deployment_record_row,
    get_service,
)


def test_upsert_deployment_record_row_syncs_pending_lock(monkeypatch) -> None:
    captured: list[dict[str, object]] = []

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("app.main._with_connection", lambda: _Conn())
    monkeypatch.setattr("app.main.cleanup_stale_deployment_locks", lambda *_args, **_kwargs: 0)
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
                "last_synced_at": "2026-03-10T00:00:00Z",
            }
        ],
    )
    monkeypatch.setattr("app.main._select_preferred_service_row", lambda _service_id, rows, _env: rows[0])

    def _fake_upsert(_conn, **kwargs):
        return {
            "deploymentId": "dep-1",
            "serviceId": kwargs["service_id"],
            "env": kwargs["env"],
            "action": kwargs["action"],
            "status": kwargs["status"],
            "requestedBy": kwargs["requested_by"],
            "requestedAt": "2026-03-10T17:00:00+00:00",
            "prUrl": kwargs["pr_url"],
            "prNumber": kwargs["pr_number"],
            "mergeSha": kwargs["merge_sha"],
            "targetImage": kwargs["target_image"],
            "previousImage": kwargs["previous_image"],
            "argoApp": kwargs["argo_app"],
            "syncStatus": kwargs["sync_status"],
            "healthStatus": kwargs["health_status"],
            "startedAt": None,
            "finishedAt": None,
            "deployWindowStart": None,
            "deployWindowEnd": None,
            "deployReason": kwargs["deploy_reason"],
            "compareUrl": kwargs["compare_url"],
            "gitRef": kwargs["git_ref"],
            "requestKey": kwargs["request_key"],
            "metadata": kwargs["metadata"] or {},
        }

    monkeypatch.setattr("app.main.upsert_deployment_record", _fake_upsert)

    def _fake_sync(_conn, row, **kwargs):
        captured.append({"row": row, **kwargs})
        return None

    monkeypatch.setattr("app.main.sync_deployment_lock_for_deployment_row", _fake_sync)

    payload = CreateDeploymentRecordRequest(
        serviceId="homelab-api",
        env="dev",
        action="deploy",
        status="pending",
        imageRef="ghcr.io/wlodzimierrr/homelab-api:sha-test",
        requestKey="gitops-pr:100:homelab-api:dev:deploy",
    )

    row = _upsert_deployment_record_row(payload, requested_by="alice")

    assert row["deploymentId"] == "dep-1"
    assert captured[0]["row"]["serviceId"] == "homelab-api"
    assert captured[0]["enforce_conflict"] is True


def test_upsert_deployment_record_row_surfaces_lock_conflict(monkeypatch) -> None:
    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("app.main._with_connection", lambda: _Conn())
    monkeypatch.setattr("app.main.cleanup_stale_deployment_locks", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(
        "app.main._load_service_rows",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr("app.main._select_preferred_service_row", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "app.main.upsert_deployment_record",
        lambda _conn, **kwargs: {
            "deploymentId": "dep-2",
            "serviceId": kwargs["service_id"],
            "env": kwargs["env"],
            "action": kwargs["action"],
            "status": kwargs["status"],
            "requestedBy": kwargs["requested_by"],
            "requestedAt": "2026-03-10T17:05:00+00:00",
            "prUrl": kwargs["pr_url"],
            "prNumber": kwargs["pr_number"],
            "mergeSha": kwargs["merge_sha"],
            "targetImage": kwargs["target_image"],
            "previousImage": kwargs["previous_image"],
            "argoApp": kwargs["argo_app"],
            "syncStatus": kwargs["sync_status"],
            "healthStatus": kwargs["health_status"],
            "startedAt": None,
            "finishedAt": None,
            "deployWindowStart": None,
            "deployWindowEnd": None,
            "deployReason": kwargs["deploy_reason"],
            "compareUrl": kwargs["compare_url"],
            "gitRef": kwargs["git_ref"],
            "requestKey": kwargs["request_key"],
            "metadata": kwargs["metadata"] or {},
        },
    )

    def _raise_conflict(_conn, _row, **_kwargs):
        raise DeploymentLockConflictError(
            {
                "serviceId": "homelab-api",
                "env": "dev",
                "deploymentId": "dep-1",
                "requestKey": "gitops-pr:99:homelab-api:dev:deploy",
                "action": "deploy",
                "status": "pending",
                "requestedAt": "2026-03-10T17:00:00+00:00",
                "lockedAt": "2026-03-10T17:00:00+00:00",
                "expiresAt": "2026-03-10T17:30:00+00:00",
                "metadata": {},
            }
        )

    monkeypatch.setattr("app.main.sync_deployment_lock_for_deployment_row", _raise_conflict)

    payload = CreateDeploymentRecordRequest(
        serviceId="homelab-api",
        env="dev",
        action="deploy",
        status="pending",
        imageRef="ghcr.io/wlodzimierrr/homelab-api:sha-test",
        requestKey="gitops-pr:100:homelab-api:dev:deploy",
    )

    with pytest.raises(DeploymentLockConflictError):
        _upsert_deployment_record_row(payload, requested_by="alice")


def test_get_service_includes_active_deployment_lock(monkeypatch) -> None:
    monkeypatch.setattr("app.main._maybe_reconcile_recent_deployments", lambda **_kwargs: None)
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
                "last_synced_at": "2026-03-10T00:00:00Z",
            }
        ],
    )
    monkeypatch.setattr("app.main._select_preferred_service_row", lambda _service_id, rows, _env: rows[0])
    monkeypatch.setattr("app.main._load_release_rows_for_service", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("app.main._load_live_service_runtime_rows", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        "app.main._get_active_deployment_lock",
        lambda *_args, **_kwargs: {
            "serviceId": "homelab-api",
            "env": "dev",
            "deploymentId": "dep-lock",
            "requestKey": "gitops-pr:100:homelab-api:dev:deploy",
            "action": "deploy",
            "status": "pending",
            "requestedAt": "2026-03-10T17:10:00+00:00",
            "lockedAt": "2026-03-10T17:10:00+00:00",
            "expiresAt": "2026-03-10T17:40:00+00:00",
            "metadata": {"source": "test"},
        },
    )

    response = get_service("homelab-api", env="dev", _=("alice", {"admin"}))

    assert response.deployment_lock is not None
    assert response.deployment_lock.request_key == "gitops-pr:100:homelab-api:dev:deploy"


def test_reconciler_only_updates_lock_for_latest_service_event(monkeypatch) -> None:
    source_new = "a" * 40
    source_old = "b" * 40
    captured_rows: list[dict[str, object]] = []

    def _fake_fetch(_path: str) -> object:
        return [
            {
                "number": 46,
                "title": f"chore(dev): bump portal images to sha-{source_new}",
                "html_url": "https://github.com/wlodzimierrr/homelab-workloads/pull/46",
                "state": "closed",
                "created_at": "2026-03-10T17:10:11Z",
                "closed_at": "2026-03-10T17:12:30Z",
                "merged_at": "2026-03-10T17:12:30Z",
                "merge_commit_sha": "merge-sha-46",
                "body": f"- backend -> `ghcr.io/wlodzimierrr/homelab-api:sha-{source_new}`",
                "user": {"login": "github-actions[bot]"},
                "head": {"ref": f"automation/dev-image-bump-{source_new}"},
            },
            {
                "number": 40,
                "title": f"chore(dev): bump portal images to sha-{source_old}",
                "html_url": "https://github.com/wlodzimierrr/homelab-workloads/pull/40",
                "state": "open",
                "created_at": "2026-03-10T16:00:00Z",
                "closed_at": None,
                "merged_at": None,
                "merge_commit_sha": None,
                "body": f"- backend -> `ghcr.io/wlodzimierrr/homelab-api:sha-{source_old}`",
                "user": {"login": "github-actions[bot]"},
                "head": {"ref": f"automation/dev-image-bump-{source_old}"},
            },
        ]

    monkeypatch.setattr(
        deployment_reconciler,
        "get_deployment_record_by_request_key",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        deployment_reconciler,
        "get_latest_deployment_record_for_service",
        lambda *_args, **_kwargs: None,
    )

    def _fake_upsert(_conn, **kwargs):
        row = {
            "deploymentId": f"dep-{kwargs['request_key']}",
            "serviceId": kwargs["service_id"],
            "env": kwargs["env"],
            "action": kwargs["action"],
            "status": kwargs["status"],
            "requestedBy": kwargs["requested_by"],
            "requestedAt": kwargs["requested_at"].astimezone(timezone.utc).isoformat()
            if isinstance(kwargs["requested_at"], datetime)
            else kwargs["requested_at"],
            "prUrl": kwargs["pr_url"],
            "prNumber": kwargs["pr_number"],
            "mergeSha": kwargs["merge_sha"],
            "targetImage": kwargs["target_image"],
            "previousImage": kwargs["previous_image"],
            "argoApp": kwargs["argo_app"],
            "syncStatus": kwargs["sync_status"],
            "healthStatus": kwargs["health_status"],
            "startedAt": kwargs["started_at"],
            "finishedAt": kwargs["finished_at"],
            "deployWindowStart": kwargs["deploy_window_start"],
            "deployWindowEnd": kwargs["deploy_window_end"],
            "deployReason": kwargs["deploy_reason"],
            "compareUrl": kwargs["compare_url"],
            "gitRef": kwargs["git_ref"],
            "requestKey": kwargs["request_key"],
            "metadata": kwargs["metadata"],
        }
        return row

    monkeypatch.setattr(deployment_reconciler, "upsert_deployment_record", _fake_upsert)

    def _fake_sync(_conn, row, **_kwargs):
        captured_rows.append(row)
        return None

    monkeypatch.setattr(deployment_reconciler, "sync_deployment_lock_for_deployment_row", _fake_sync)

    deployment_reconciler.reconcile_recent_gitops_deployments(
        conn=object(),  # type: ignore[arg-type]
        load_service_rows=lambda **_kwargs: [
            {
                "service_id": "homelab-api",
                "service_name": "homelab-api",
                "env": "dev",
                "namespace": "homelab-api",
                "app_label": "homelab-api",
                "argo_app_name": "homelab-api-dev",
                "source": "cluster_services",
                "source_ref": "kubernetes_api",
                "last_synced_at": "2026-03-10T17:00:00Z",
            }
        ],
        select_preferred_service_row=lambda _service_id, rows, _env: rows[0],
        load_live_argo_status=lambda _row: {
            "syncStatus": "synced",
            "healthStatus": "healthy",
            "revision": "merge-sha-46",
            "deployedAt": "2026-03-10T17:14:31Z",
        },
        list_live_deployments=lambda _row: [{"kind": "Deployment"}],
        extract_live_image_ref=lambda _deployment: f"ghcr.io/wlodzimierrr/homelab-api:sha-{source_new}",
        service_id="homelab-api",
        github_fetch_json=_fake_fetch,
        now=datetime(2026, 3, 10, 17, 15, tzinfo=timezone.utc),
    )

    assert len(captured_rows) == 1
    assert captured_rows[0]["requestKey"] == "gitops-pr:46:homelab-api:dev:deploy"
