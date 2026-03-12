from datetime import datetime, timezone

from app import deployment_reconciler


def test_load_recent_gitops_deployment_events_parses_dev_autobump_pull_request() -> None:
    source_sha = "a" * 40

    def _fake_fetch(_path: str) -> object:
        return [
            {
                "number": 38,
                "title": f"chore(dev): bump portal images to sha-{source_sha}",
                "html_url": "https://github.com/wlodzimierrr/homelab-workloads/pull/38",
                "state": "open",
                "created_at": "2026-03-10T14:00:00Z",
                "closed_at": None,
                "merged_at": None,
                "merge_commit_sha": None,
                "body": "\n".join(
                    [
                        "Automated update from apps/portal image pipeline.",
                        f"- backend -> `ghcr.io/wlodzimierrr/homelab-api:sha-{source_sha}`",
                        f"- frontend -> `ghcr.io/wlodzimierrr/homelab-web:sha-{source_sha}`",
                    ]
                ),
                "user": {"login": "github-actions[bot]"},
                "head": {"ref": f"automation/dev-image-bump-{source_sha}"},
            }
        ]

    events = deployment_reconciler.load_recent_gitops_deployment_events(
        github_fetch_json=_fake_fetch,
    )

    assert len(events) == 2
    api_event = next(event for event in events if event.service_id == "homelab-api")
    web_event = next(event for event in events if event.service_id == "homelab-web")

    assert api_event.env == "dev"
    assert api_event.action == "deploy"
    assert api_event.target_image == f"ghcr.io/wlodzimierrr/homelab-api:sha-{source_sha}"
    assert api_event.source_commit_sha == source_sha
    assert api_event.request_key == "gitops-pr:38:homelab-api:dev:deploy"
    assert web_event.target_image == f"ghcr.io/wlodzimierrr/homelab-web:sha-{source_sha}"


def test_load_recent_gitops_deployment_events_parses_manual_dev_deploy_pull_request() -> None:
    source_sha = "b" * 40

    def _fake_fetch(_path: str) -> object:
        return [
            {
                "number": 60,
                "title": f"Deploy homelab-api: sha-{source_sha} to dev",
                "html_url": "https://github.com/wlodzimierrr/homelab-workloads/pull/60",
                "state": "open",
                "created_at": "2026-03-12T10:00:00Z",
                "closed_at": None,
                "merged_at": None,
                "merge_commit_sha": None,
                "body": "\n".join(
                    [
                        "Portal-requested dev deploy.",
                        "- Service: `homelab-api`",
                        f"- Target image: `ghcr.io/wlodzimierrr/homelab-api:sha-{source_sha}`",
                        "- Reason: Ship login fix safely to dev.",
                    ]
                ),
                "user": {"login": "wlodzimierrr"},
                "head": {"ref": f"automation/dev-deploy-homelab-api-sha-{source_sha[:12]}-20260312100000"},
            }
        ]

    events = deployment_reconciler.load_recent_gitops_deployment_events(
        github_fetch_json=_fake_fetch,
    )

    assert len(events) == 1
    event = events[0]
    assert event.service_id == "homelab-api"
    assert event.env == "dev"
    assert event.action == "deploy"
    assert event.source_commit_sha == source_sha
    assert event.target_image == f"ghcr.io/wlodzimierrr/homelab-api:sha-{source_sha}"
    assert event.metadata["operatorReason"] == "Ship login fix safely to dev."


def test_load_recent_gitops_deployment_events_parses_manual_prod_promote_pull_request() -> None:
    source_sha = "c" * 40

    def _fake_fetch(_path: str) -> object:
        return [
            {
                "number": 73,
                "title": f"Promote homelab-web: sha-{source_sha} to prod",
                "html_url": "https://github.com/wlodzimierrr/homelab-workloads/pull/73",
                "state": "open",
                "created_at": "2026-03-12T11:00:00Z",
                "closed_at": None,
                "merged_at": None,
                "merge_commit_sha": None,
                "body": "\n".join(
                    [
                        "Portal-requested promote-to-prod.",
                        "- Service: `homelab-web`",
                        f"- Target image: `ghcr.io/wlodzimierrr/homelab-web:sha-{source_sha}`",
                        "- Reason: Promote the verified dev release to prod.",
                    ]
                ),
                "user": {"login": "wlodzimierrr"},
                "head": {"ref": f"automation/prod-promote-homelab-web-sha-{source_sha[:12]}-20260312110000"},
            }
        ]

    events = deployment_reconciler.load_recent_gitops_deployment_events(
        github_fetch_json=_fake_fetch,
    )

    assert len(events) == 1
    event = events[0]
    assert event.service_id == "homelab-web"
    assert event.env == "prod"
    assert event.action == "promote"
    assert event.source_commit_sha == source_sha
    assert event.target_image == f"ghcr.io/wlodzimierrr/homelab-web:sha-{source_sha}"
    assert event.request_key == "gitops-pr:73:homelab-web:prod:promote"
    assert event.metadata["operatorReason"] == "Promote the verified dev release to prod."


def test_load_recent_gitops_deployment_events_parses_config_change_pull_request() -> None:
    def _fake_fetch(_path: str) -> object:
        return [
            {
                "number": 41,
                "title": "chore(dev): set homelab-api replicas to 2",
                "html_url": "https://github.com/wlodzimierrr/homelab-workloads/pull/41",
                "state": "open",
                "created_at": "2026-03-10T15:00:00Z",
                "closed_at": None,
                "merged_at": None,
                "merge_commit_sha": None,
                "body": "\n".join(
                    [
                        "Automated GitOps config change request.",
                        "- Service: `homelab-api`",
                        "- Current image: `ghcr.io/wlodzimierrr/homelab-api:sha-feedfacefeedfacefeedfacefeedfacefeedface`",
                    ]
                ),
                "user": {"login": "wlodzimierrr"},
                "head": {"ref": "automation/dev-config-change-homelab-api-replicas-2-123456789"},
            }
        ]

    events = deployment_reconciler.load_recent_gitops_deployment_events(
        github_fetch_json=_fake_fetch,
    )

    assert len(events) == 1
    event = events[0]
    assert event.service_id == "homelab-api"
    assert event.env == "dev"
    assert event.action == "config-change"
    assert event.target_image == "ghcr.io/wlodzimierrr/homelab-api:sha-feedfacefeedfacefeedfacefeedfacefeedface"
    assert event.request_key == "gitops-pr:41:homelab-api:dev:config-change"


def test_load_recent_gitops_deployment_events_extracts_rollback_reason_from_pull_request() -> None:
    reason = "Restore known-good prod tags after auth regression."

    def _fake_fetch(_path: str) -> object:
        return [
            {
                "number": 42,
                "title": "chore(prod): rollback portal images to requested tags",
                "html_url": "https://github.com/wlodzimierrr/homelab-workloads/pull/42",
                "state": "open",
                "created_at": "2026-03-10T16:22:12Z",
                "closed_at": None,
                "merged_at": None,
                "merge_commit_sha": None,
                "body": "\n".join(
                    [
                        "Automated gated rollback request.",
                        "- API image: `ghcr.io/wlodzimierrr/homelab-api:sha-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`",
                        "- Web image: `ghcr.io/wlodzimierrr/homelab-web:sha-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb`",
                        f"- Reason: {reason}",
                    ]
                ),
                "user": {"login": "wlodzimierrr"},
                "head": {"ref": "automation/prod-rollback-image-update-22912496498"},
            }
        ]

    events = deployment_reconciler.load_recent_gitops_deployment_events(
        github_fetch_json=_fake_fetch,
    )

    assert len(events) == 2
    api_event = next(event for event in events if event.service_id == "homelab-api")
    web_event = next(event for event in events if event.service_id == "homelab-web")

    assert api_event.action == "rollback"
    assert api_event.metadata["operatorReason"] == reason
    assert api_event.request_key == "gitops-pr:42:homelab-api:prod:rollback"
    assert web_event.metadata["operatorReason"] == reason


def test_load_recent_gitops_deployment_events_parses_manual_service_rollback_pull_request() -> None:
    rollback_sha = "f" * 40
    reason = "Rollback homelab-web dev to a previously verified image."

    def _fake_fetch(_path: str) -> object:
        return [
            {
                "number": 74,
                "title": f"Rollback homelab-web: sha-{rollback_sha} in dev",
                "html_url": "https://github.com/wlodzimierrr/homelab-workloads/pull/74",
                "state": "open",
                "created_at": "2026-03-12T13:57:31Z",
                "closed_at": None,
                "merged_at": None,
                "merge_commit_sha": None,
                "body": "\n".join(
                    [
                        "Portal-requested rollback.",
                        "- Service: `homelab-web`",
                        "- Target environment: `dev`",
                        f"- Target image: `ghcr.io/wlodzimierrr/homelab-web:sha-{rollback_sha}`",
                        f"- Reason: {reason}",
                    ]
                ),
                "user": {"login": "wlodzimierrr"},
                "head": {"ref": f"automation/dev-rollback-homelab-web-sha-{rollback_sha[:12]}-20260312135731"},
            }
        ]

    events = deployment_reconciler.load_recent_gitops_deployment_events(
        github_fetch_json=_fake_fetch,
    )

    assert len(events) == 1
    event = events[0]
    assert event.service_id == "homelab-web"
    assert event.env == "dev"
    assert event.action == "rollback"
    assert event.source_commit_sha == rollback_sha
    assert event.target_image == f"ghcr.io/wlodzimierrr/homelab-web:sha-{rollback_sha}"
    assert event.request_key == "gitops-pr:74:homelab-web:dev:rollback"
    assert event.metadata["operatorReason"] == reason


def test_reconcile_recent_gitops_deployments_marks_merged_pull_request_live(monkeypatch) -> None:
    source_sha = "b" * 40
    target_image = f"ghcr.io/wlodzimierrr/homelab-api:sha-{source_sha}"
    captured: list[dict[str, object]] = []

    def _fake_fetch(_path: str) -> object:
        return [
            {
                "number": 39,
                "title": f"chore(dev): bump portal images to sha-{source_sha}",
                "html_url": "https://github.com/wlodzimierrr/homelab-workloads/pull/39",
                "state": "closed",
                "created_at": "2026-03-10T14:05:00Z",
                "closed_at": "2026-03-10T14:07:00Z",
                "merged_at": "2026-03-10T14:07:00Z",
                "merge_commit_sha": "merge-sha-39",
                "body": "\n".join(
                    [
                        "Automated update from apps/portal image pipeline.",
                        f"- backend -> `ghcr.io/wlodzimierrr/homelab-api:sha-{source_sha}`",
                    ]
                ),
                "user": {"login": "github-actions[bot]"},
                "head": {"ref": f"automation/dev-image-bump-{source_sha}"},
            }
        ]

    monkeypatch.setattr(
        deployment_reconciler,
        "get_deployment_record_by_request_key",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        deployment_reconciler,
        "get_latest_deployment_record_for_service",
        lambda *_args, **_kwargs: {
            "targetImage": "ghcr.io/wlodzimierrr/homelab-api:sha-old",
            "mergeSha": "old-merge-sha",
            "metadata": {"sourceCommitSha": "c" * 40},
        },
    )

    def _fake_upsert(_conn, **kwargs):
        captured.append(kwargs)
        return {"status": kwargs["status"]}

    monkeypatch.setattr(deployment_reconciler, "upsert_deployment_record", _fake_upsert)

    summary = deployment_reconciler.reconcile_recent_gitops_deployments(
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
                "last_synced_at": "2026-03-10T14:00:00Z",
            }
        ],
        select_preferred_service_row=lambda _service_id, rows, _env: rows[0],
        load_live_argo_status=lambda _row: {
            "syncStatus": "synced",
            "healthStatus": "healthy",
            "revision": "merge-sha-39",
            "deployedAt": "2026-03-10T14:08:00Z",
        },
        list_live_deployments=lambda _row: [{"kind": "Deployment"}],
        extract_live_image_ref=lambda _deployment: target_image,
        service_id="homelab-api",
        github_fetch_json=_fake_fetch,
        now=datetime(2026, 3, 10, 14, 9, tzinfo=timezone.utc),
    )

    assert summary["recordsUpserted"] == 1
    assert summary["statusCounts"]["live"] == 1
    assert captured[0]["status"] == "live"
    assert captured[0]["target_image"] == target_image
    assert captured[0]["previous_image"] == "ghcr.io/wlodzimierrr/homelab-api:sha-old"
    assert captured[0]["argo_app"] == "homelab-api-dev"
    assert captured[0]["compare_url"] == (
        f"https://github.com/wlodzimierrr/homelab-portal/compare/{'c' * 40}...{source_sha}"
    )
    assert isinstance(captured[0]["metadata"], dict)
    assert captured[0]["metadata"]["argoSyncStatus"] == "synced"


def test_reconcile_recent_gitops_deployments_marks_closed_unmerged_pull_request_failed(monkeypatch) -> None:
    source_sha = "d" * 40
    captured: list[dict[str, object]] = []

    def _fake_fetch(_path: str) -> object:
        return [
            {
                "number": 40,
                "title": f"chore(dev): bump portal images to sha-{source_sha}",
                "html_url": "https://github.com/wlodzimierrr/homelab-workloads/pull/40",
                "state": "closed",
                "created_at": "2026-03-10T14:10:00Z",
                "closed_at": "2026-03-10T14:12:00Z",
                "merged_at": None,
                "merge_commit_sha": None,
                "body": f"- backend -> `ghcr.io/wlodzimierrr/homelab-api:sha-{source_sha}`",
                "user": {"login": "github-actions[bot]"},
                "head": {"ref": f"automation/dev-image-bump-{source_sha}"},
            }
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
        captured.append(kwargs)
        return {"status": kwargs["status"]}

    monkeypatch.setattr(deployment_reconciler, "upsert_deployment_record", _fake_upsert)

    summary = deployment_reconciler.reconcile_recent_gitops_deployments(
        conn=object(),  # type: ignore[arg-type]
        load_service_rows=lambda **_kwargs: [],
        select_preferred_service_row=lambda *_args, **_kwargs: None,
        load_live_argo_status=lambda _row: {},
        list_live_deployments=lambda _row: [],
        extract_live_image_ref=lambda _deployment: None,
        service_id="homelab-api",
        github_fetch_json=_fake_fetch,
        now=datetime(2026, 3, 10, 14, 20, tzinfo=timezone.utc),
    )

    assert summary["statusCounts"]["failed"] == 1
    assert captured[0]["status"] == "failed"
    assert captured[0]["metadata"]["failureReason"] == "GitOps pull request was closed without merge."


def test_reconcile_recent_gitops_deployments_uses_operator_reason_for_rollback_records(monkeypatch) -> None:
    captured: list[dict[str, object]] = []
    reason = "Restore known-good prod tags after auth regression."

    def _fake_fetch(_path: str) -> object:
        return [
            {
                "number": 42,
                "title": "chore(prod): rollback portal images to requested tags",
                "html_url": "https://github.com/wlodzimierrr/homelab-workloads/pull/42",
                "state": "open",
                "created_at": "2026-03-10T16:22:12Z",
                "closed_at": None,
                "merged_at": None,
                "merge_commit_sha": None,
                "body": "\n".join(
                    [
                        "Automated gated rollback request.",
                        "- API image: `ghcr.io/wlodzimierrr/homelab-api:sha-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`",
                        "- Web image: `ghcr.io/wlodzimierrr/homelab-web:sha-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb`",
                        f"- Reason: {reason}",
                    ]
                ),
                "user": {"login": "wlodzimierrr"},
                "head": {"ref": "automation/prod-rollback-image-update-22912496498"},
            }
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
        captured.append(kwargs)
        return {"status": kwargs["status"]}

    monkeypatch.setattr(deployment_reconciler, "upsert_deployment_record", _fake_upsert)

    summary = deployment_reconciler.reconcile_recent_gitops_deployments(
        conn=object(),  # type: ignore[arg-type]
        load_service_rows=lambda **_kwargs: [],
        select_preferred_service_row=lambda *_args, **_kwargs: None,
        load_live_argo_status=lambda _row: {},
        list_live_deployments=lambda _row: [],
        extract_live_image_ref=lambda _deployment: None,
        service_id="homelab-api",
        github_fetch_json=_fake_fetch,
        now=datetime(2026, 3, 10, 16, 24, tzinfo=timezone.utc),
    )

    assert summary["statusCounts"]["pending"] == 1
    assert captured[0]["status"] == "pending"
    assert captured[0]["deploy_reason"] == reason
    assert captured[0]["metadata"]["operatorReason"] == reason


def test_reconcile_config_change_requires_argo_revision_match(monkeypatch) -> None:
    captured: list[dict[str, object]] = []

    def _fake_fetch(_path: str) -> object:
        return [
            {
                "number": 41,
                "title": "chore(dev): set homelab-web replicas to 2",
                "html_url": "https://github.com/wlodzimierrr/homelab-workloads/pull/41",
                "state": "closed",
                "created_at": "2026-03-10T16:10:32Z",
                "closed_at": "2026-03-10T16:16:02Z",
                "merged_at": "2026-03-10T16:16:02Z",
                "merge_commit_sha": "merge-sha-41",
                "body": "\n".join(
                    [
                        "Automated GitOps config change request.",
                        "- Service: `homelab-web`",
                        "- Current image: `ghcr.io/wlodzimierrr/homelab-web:sha-feedfacefeedfacefeedfacefeedfacefeedface`",
                    ]
                ),
                "user": {"login": "wlodzimierrr"},
                "head": {"ref": "automation/dev-config-change-homelab-web-replicas-2-22912164133"},
            }
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
        captured.append(kwargs)
        return {"status": kwargs["status"]}

    monkeypatch.setattr(deployment_reconciler, "upsert_deployment_record", _fake_upsert)

    summary = deployment_reconciler.reconcile_recent_gitops_deployments(
        conn=object(),  # type: ignore[arg-type]
        load_service_rows=lambda **_kwargs: [
            {
                "service_id": "homelab-web",
                "service_name": "homelab-web",
                "env": "dev",
                "namespace": "homelab-web",
                "app_label": "homelab-web",
                "argo_app_name": "homelab-web-dev",
                "source": "cluster_services",
                "source_ref": "kubernetes_api",
                "last_synced_at": "2026-03-10T16:12:24Z",
            }
        ],
        select_preferred_service_row=lambda _service_id, rows, _env: rows[0],
        load_live_argo_status=lambda _row: {
            "syncStatus": "synced",
            "healthStatus": "healthy",
            "revision": "older-merge-sha",
            "deployedAt": "2026-03-10T16:12:24Z",
        },
        list_live_deployments=lambda _row: [{"kind": "Deployment"}],
        extract_live_image_ref=lambda _deployment: "ghcr.io/wlodzimierrr/homelab-web:sha-feedfacefeedfacefeedfacefeedfacefeedface",
        service_id="homelab-web",
        github_fetch_json=_fake_fetch,
        now=datetime(2026, 3, 10, 16, 16, 10, tzinfo=timezone.utc),
    )

    assert summary["statusCounts"]["deploying"] == 1
    assert captured[0]["status"] == "deploying"
    assert captured[0]["started_at"] == "2026-03-10T16:16:02+00:00"
    assert captured[0]["finished_at"] is None


def test_reconcile_config_change_finished_at_never_precedes_started_at(monkeypatch) -> None:
    captured: list[dict[str, object]] = []

    def _fake_fetch(_path: str) -> object:
        return [
            {
                "number": 41,
                "title": "chore(dev): set homelab-web replicas to 2",
                "html_url": "https://github.com/wlodzimierrr/homelab-workloads/pull/41",
                "state": "closed",
                "created_at": "2026-03-10T16:10:32Z",
                "closed_at": "2026-03-10T16:16:02Z",
                "merged_at": "2026-03-10T16:16:02Z",
                "merge_commit_sha": "merge-sha-41",
                "body": "\n".join(
                    [
                        "Automated GitOps config change request.",
                        "- Service: `homelab-web`",
                        "- Current image: `ghcr.io/wlodzimierrr/homelab-web:sha-feedfacefeedfacefeedfacefeedfacefeedface`",
                    ]
                ),
                "user": {"login": "wlodzimierrr"},
                "head": {"ref": "automation/dev-config-change-homelab-web-replicas-2-22912164133"},
            }
        ]

    monkeypatch.setattr(
        deployment_reconciler,
        "get_deployment_record_by_request_key",
        lambda *_args, **_kwargs: {
            "finishedAt": "2026-03-10T16:12:24+00:00",
            "deployWindowEnd": "2026-03-10T16:12:24+00:00",
        },
    )
    monkeypatch.setattr(
        deployment_reconciler,
        "get_latest_deployment_record_for_service",
        lambda *_args, **_kwargs: None,
    )

    def _fake_upsert(_conn, **kwargs):
        captured.append(kwargs)
        return {"status": kwargs["status"]}

    monkeypatch.setattr(deployment_reconciler, "upsert_deployment_record", _fake_upsert)

    summary = deployment_reconciler.reconcile_recent_gitops_deployments(
        conn=object(),  # type: ignore[arg-type]
        load_service_rows=lambda **_kwargs: [
            {
                "service_id": "homelab-web",
                "service_name": "homelab-web",
                "env": "dev",
                "namespace": "homelab-web",
                "app_label": "homelab-web",
                "argo_app_name": "homelab-web-dev",
                "source": "cluster_services",
                "source_ref": "kubernetes_api",
                "last_synced_at": "2026-03-10T16:16:30Z",
            }
        ],
        select_preferred_service_row=lambda _service_id, rows, _env: rows[0],
        load_live_argo_status=lambda _row: {
            "syncStatus": "synced",
            "healthStatus": "healthy",
            "revision": "merge-sha-41",
            "deployedAt": "2026-03-10T16:12:24Z",
        },
        list_live_deployments=lambda _row: [{"kind": "Deployment"}],
        extract_live_image_ref=lambda _deployment: "ghcr.io/wlodzimierrr/homelab-web:sha-feedfacefeedfacefeedfacefeedfacefeedface",
        service_id="homelab-web",
        github_fetch_json=_fake_fetch,
        now=datetime(2026, 3, 10, 16, 16, 35, tzinfo=timezone.utc),
    )

    assert summary["statusCounts"]["live"] == 1
    assert captured[0]["status"] == "live"
    assert captured[0]["started_at"] == "2026-03-10T16:16:02+00:00"
    assert captured[0]["finished_at"] == "2026-03-10T16:16:35+00:00"


def test_reconcile_preserves_terminal_live_status_for_historical_rows(monkeypatch) -> None:
    captured: list[dict[str, object]] = []
    source_sha = "e" * 40

    def _fake_fetch(_path: str) -> object:
        return [
            {
                "number": 39,
                "title": f"chore(dev): bump portal images to sha-{source_sha}",
                "html_url": "https://github.com/wlodzimierrr/homelab-workloads/pull/39",
                "state": "closed",
                "created_at": "2026-03-10T15:55:57Z",
                "closed_at": "2026-03-10T16:08:08Z",
                "merged_at": "2026-03-10T16:08:08Z",
                "merge_commit_sha": "merge-sha-39",
                "body": f"- backend -> `ghcr.io/wlodzimierrr/homelab-api:sha-{source_sha}`",
                "user": {"login": "github-actions[bot]"},
                "head": {"ref": f"automation/dev-image-bump-{source_sha}"},
            }
        ]

    monkeypatch.setattr(
        deployment_reconciler,
        "get_deployment_record_by_request_key",
        lambda *_args, **_kwargs: {
            "status": "live",
            "requestedBy": "github-actions[bot]",
            "requestedAt": "2026-03-10T15:55:57+00:00",
            "prUrl": "https://github.com/wlodzimierrr/homelab-workloads/pull/39",
            "prNumber": 39,
            "mergeSha": "merge-sha-39",
            "targetImage": f"ghcr.io/wlodzimierrr/homelab-api:sha-{source_sha}",
            "argoApp": "homelab-api-dev",
            "startedAt": "2026-03-10T16:08:08+00:00",
            "finishedAt": "2026-03-10T16:12:37+00:00",
            "deployWindowStart": "2026-03-10T16:08:08+00:00",
            "deployWindowEnd": "2026-03-10T16:12:37+00:00",
            "deployReason": "GitOps deploy via PR #39",
            "compareUrl": "https://github.com/wlodzimierrr/homelab-portal/compare/aaa...bbb",
            "gitRef": f"automation/dev-image-bump-{source_sha}",
            "metadata": {
                "source": "deployment-reconciler",
            },
        },
    )
    monkeypatch.setattr(
        deployment_reconciler,
        "get_latest_deployment_record_for_service",
        lambda *_args, **_kwargs: {
            "requestedAt": "2026-03-10T16:32:44+00:00",
            "status": "live",
        },
    )

    def _fake_upsert(_conn, **kwargs):
        captured.append(kwargs)
        return {"status": kwargs["status"]}

    monkeypatch.setattr(deployment_reconciler, "upsert_deployment_record", _fake_upsert)

    summary = deployment_reconciler.reconcile_recent_gitops_deployments(
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
                "last_synced_at": "2026-03-10T16:37:00Z",
            }
        ],
        select_preferred_service_row=lambda _service_id, rows, _env: rows[0],
        load_live_argo_status=lambda _row: {
            "syncStatus": "synced",
            "healthStatus": "healthy",
            "revision": "merge-sha-43",
            "deployedAt": "2026-03-10T16:37:20Z",
        },
        list_live_deployments=lambda _row: [{"kind": "Deployment"}],
        extract_live_image_ref=lambda _deployment: "ghcr.io/wlodzimierrr/homelab-api:sha-new",
        service_id="homelab-api",
        github_fetch_json=_fake_fetch,
        now=datetime(2026, 3, 10, 16, 37, 21, tzinfo=timezone.utc),
    )

    assert summary["statusCounts"]["live"] == 1
    assert captured[0]["status"] == "live"
    assert captured[0]["started_at"] == "2026-03-10T16:08:08+00:00"
    assert captured[0]["finished_at"] == "2026-03-10T16:12:37+00:00"
