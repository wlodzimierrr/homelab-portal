from __future__ import annotations

from datetime import datetime
from typing import Callable

import psycopg

from app.deployment_locks import sync_deployment_lock_for_deployment_row
from app.deployment_records import (
    get_deployment_record_by_request_key,
    get_latest_deployment_record_for_service,
    upsert_deployment_record,
)
from app.deployments.reconciler.comments import (  # noqa: F401
    build_pr_comment_body,
    make_pr_comment_poster,
)
from app.deployments.reconciler.event_parsing import (
    action_context_from_pull_request,
    github_get_json,
    github_post_json,
    load_recent_gitops_deployment_events,
    parse_datetime,
    serialize_datetime,
)
from app.deployments.reconciler.live_state import (
    build_compare_url,
    build_reconciled_record,
    canonical_argo_app,
    coalesce_datetime,
    failed_operation_reason,
    live_rollout_verified,
    load_primary_live_image,
    max_datetime,
    stalled_reason,
    terminal_status,
)
from app.deployments.reconciler.models import (  # noqa: F401
    DEFAULT_GITHUB_OWNER,
    DEFAULT_GITOPS_REPO,
    DEFAULT_PORTAL_REPO,
    DEFAULT_PULL_REQUEST_LIMIT,
    DEFAULT_VERIFICATION_TIMEOUT_SECONDS,
    DeploymentReconcileSummary,
    GitOpsDeploymentEvent,
    logger,
)
from app.deployments.reconciler.orchestration import (
    reconcile_recent_gitops_deployments as _reconcile_recent_gitops_deployments,
)

# Keep the historical module-level names available so the rest of the app and the
# existing monkeypatch-heavy tests do not need to know about the new package split.
_parse_datetime = parse_datetime
_serialize_datetime = serialize_datetime
_github_get_json = github_get_json
_github_post_json = github_post_json
_action_context_from_pull_request = action_context_from_pull_request
_build_compare_url = build_compare_url
_coalesce_datetime = coalesce_datetime
_max_datetime = max_datetime
_failed_operation_reason = failed_operation_reason
_live_rollout_verified = live_rollout_verified
_terminal_status = terminal_status
_stalled_reason = stalled_reason
_canonical_argo_app = canonical_argo_app
_load_primary_live_image = load_primary_live_image
_build_reconciled_record = build_reconciled_record
_build_pr_comment_body = build_pr_comment_body


def reconcile_recent_gitops_deployments(
    conn: psycopg.Connection,
    *,
    load_service_rows: Callable[..., list[dict[str, str | None]]],
    select_preferred_service_row: Callable[[str, list[dict[str, str | None]], str], dict[str, str | None] | None],
    load_live_argo_status: Callable[[dict[str, str | None]], dict[str, str | None]],
    list_live_deployments: Callable[[dict[str, str | None]], list[dict[str, object]]],
    extract_live_image_ref: Callable[[dict[str, object]], str | None],
    service_id: str | None = None,
    env: str | None = None,
    limit_pull_requests: int = DEFAULT_PULL_REQUEST_LIMIT,
    github_fetch_json: Callable[[str], object] = _github_get_json,
    post_pr_comment: Callable[[int, str], None] | None = None,
    now: datetime | None = None,
) -> DeploymentReconcileSummary:
    # The wrapper keeps dependency injection rooted in this module so tests can
    # monkeypatch DB/lock helpers here while orchestration lives in a smaller unit.
    return _reconcile_recent_gitops_deployments(
        conn,
        load_service_rows=load_service_rows,
        select_preferred_service_row=select_preferred_service_row,
        load_live_argo_status=load_live_argo_status,
        list_live_deployments=list_live_deployments,
        extract_live_image_ref=extract_live_image_ref,
        get_deployment_record_by_request_key_fn=get_deployment_record_by_request_key,
        get_latest_deployment_record_for_service_fn=get_latest_deployment_record_for_service,
        upsert_deployment_record_fn=upsert_deployment_record,
        sync_deployment_lock_for_deployment_row_fn=sync_deployment_lock_for_deployment_row,
        load_recent_gitops_deployment_events_fn=load_recent_gitops_deployment_events,
        build_pr_comment_body_fn=_build_pr_comment_body,
        service_id=service_id,
        env=env,
        limit_pull_requests=limit_pull_requests,
        github_fetch_json=github_fetch_json,
        post_pr_comment=post_pr_comment,
        now=now,
    )
