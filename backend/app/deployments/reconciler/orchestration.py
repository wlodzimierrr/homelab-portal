from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

import psycopg

from app.deployments.reconciler.live_state import build_reconciled_record, load_primary_live_image, terminal_status
from app.deployments.reconciler.models import DEFAULT_PULL_REQUEST_LIMIT, DeploymentReconcileSummary, GitOpsDeploymentEvent, logger


def reconcile_recent_gitops_deployments(
    conn: psycopg.Connection,
    *,
    load_service_rows: Callable[..., list[dict[str, str | None]]],
    select_preferred_service_row: Callable[[str, list[dict[str, str | None]], str], dict[str, str | None] | None],
    load_live_argo_status: Callable[[dict[str, str | None]], dict[str, str | None]],
    list_live_deployments: Callable[[dict[str, str | None]], list[dict[str, object]]],
    extract_live_image_ref: Callable[[dict[str, object]], str | None],
    get_deployment_record_by_request_key_fn: Callable[[psycopg.Connection, str], dict[str, object] | None],
    get_latest_deployment_record_for_service_fn: Callable[..., dict[str, object] | None],
    upsert_deployment_record_fn: Callable[..., dict[str, object]],
    sync_deployment_lock_for_deployment_row_fn: Callable[..., object],
    load_recent_gitops_deployment_events_fn: Callable[..., list[GitOpsDeploymentEvent]],
    build_pr_comment_body_fn: Callable[..., str],
    service_id: str | None = None,
    env: str | None = None,
    limit_pull_requests: int = DEFAULT_PULL_REQUEST_LIMIT,
    github_fetch_json: Callable[[str], object] | None = None,
    post_pr_comment: Callable[[int, str], None] | None = None,
    now: datetime | None = None,
) -> DeploymentReconcileSummary:
    current_time = now or datetime.now(tz=timezone.utc)
    event_loader_kwargs = {
        "service_id": service_id,
        "env": env,
        "limit_pull_requests": limit_pull_requests,
    }
    if github_fetch_json is not None:
        event_loader_kwargs["github_fetch_json"] = github_fetch_json
    events = load_recent_gitops_deployment_events_fn(**event_loader_kwargs)
    status_counts = {status: 0 for status in ("pending", "deploying", "live", "failed")}
    records_upserted = 0
    lock_managed_pairs: set[tuple[str, str]] = set()

    for event in events:
        existing_record = get_deployment_record_by_request_key_fn(conn, event.request_key)
        was_terminal = terminal_status(existing_record) is not None
        previous_record = get_latest_deployment_record_for_service_fn(
            conn,
            service_id=event.service_id,
            env=event.env,
            exclude_request_key=event.request_key,
        )
        service_rows = load_service_rows(service_id=event.service_id, env=event.env)
        selected_service_row = select_preferred_service_row(event.service_id, service_rows, event.env)
        live_argo = load_live_argo_status(selected_service_row) if selected_service_row else {}
        live_image = (
            load_primary_live_image(
                selected_service_row,
                list_live_deployments=list_live_deployments,
                extract_live_image_ref=extract_live_image_ref,
            )
            if selected_service_row
            else None
        )
        payload = build_reconciled_record(
            event=event,
            existing_record=existing_record,
            previous_record=previous_record,
            selected_service_row=selected_service_row,
            live_argo=live_argo,
            live_image=live_image,
            now=current_time,
        )
        row = upsert_deployment_record_fn(conn, **payload)
        records_upserted += 1
        pair = (event.service_id, event.env)
        if pair not in lock_managed_pairs:
            sync_deployment_lock_for_deployment_row_fn(
                conn,
                row,
                enforce_conflict=False,
                now=current_time,
            )
            lock_managed_pairs.add(pair)
        new_status = row.get("status")
        if isinstance(new_status, str) and new_status in status_counts:
            status_counts[new_status] += 1

        if not was_terminal and new_status in {"live", "failed"} and post_pr_comment is not None:
            comment_body = build_pr_comment_body_fn(row=row, event=event)
            try:
                post_pr_comment(event.pr_number, comment_body)
            except Exception as exc:
                logger.warning(
                    "deployment_reconciler_pr_comment_failed pr=%s service=%s env=%s error=%s",
                    event.pr_number,
                    event.service_id,
                    event.env,
                    exc,
                )

    return {
        "pullRequestsScanned": len(events),
        "recordsUpserted": records_upserted,
        "statusCounts": status_counts,
        "generatedAt": current_time.astimezone(timezone.utc).isoformat(),
    }
