from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Callable

from app.deployments.reconciler.event_parsing import parse_datetime, serialize_datetime
from app.deployments.reconciler.models import (
    DEFAULT_GITHUB_OWNER,
    DEFAULT_GITOPS_REPO,
    DEFAULT_PORTAL_REPO,
    DEFAULT_VERIFICATION_TIMEOUT_SECONDS,
    GitOpsDeploymentEvent,
)


def canonical_argo_app(service_id: str, env: str) -> str:
    return f"{service_id}-{env}"


def load_primary_live_image(
    service_row: dict[str, str | None],
    *,
    list_live_deployments: Callable[[dict[str, str | None]], list[dict[str, object]]],
    extract_live_image_ref: Callable[[dict[str, object]], str | None],
) -> str | None:
    for deployment in list_live_deployments(service_row):
        image_ref = extract_live_image_ref(deployment)
        if image_ref:
            return image_ref
    return None


def failed_operation_reason(live_argo: dict[str, str | None]) -> str | None:
    phase = str(live_argo.get("operationPhase") or "").strip().lower()
    if phase not in {"failed", "error"}:
        return None
    message = live_argo.get("operationMessage")
    if isinstance(message, str) and message.strip():
        return message.strip()
    return f"Argo operation phase is {phase}"


def coalesce_datetime(*values: object) -> datetime | None:
    for value in values:
        parsed = parse_datetime(value)
        if parsed is not None:
            return parsed
    return None


def max_datetime(*values: object) -> datetime | None:
    parsed_values = [parsed for value in values if (parsed := parse_datetime(value)) is not None]
    if not parsed_values:
        return None
    return max(parsed_values)


def live_rollout_verified(
    *,
    event: GitOpsDeploymentEvent,
    live_argo: dict[str, str | None],
    live_image: str | None,
) -> bool:
    sync_status = str(live_argo.get("syncStatus") or "").strip().lower()
    health_status = str(live_argo.get("healthStatus") or "").strip().lower()
    if sync_status != "synced" or health_status != "healthy":
        return False

    if event.merge_sha:
        live_revision = str(live_argo.get("revision") or "").strip()
        if live_revision != event.merge_sha:
            return False

    if event.action == "config-change":
        return True

    return live_image == event.target_image


def terminal_status(record: dict[str, object] | None) -> str | None:
    if not record:
        return None
    status = record.get("status")
    if isinstance(status, str) and status in {"live", "failed"}:
        return status
    return None


def stalled_reason(
    *,
    target_image: str,
    live_image: str | None,
    live_argo: dict[str, str | None],
) -> str:
    sync_status = str(live_argo.get("syncStatus") or "").strip().lower() or "unknown"
    health_status = str(live_argo.get("healthStatus") or "").strip().lower() or "unknown"
    if live_image and live_image != target_image:
        return (
            "Deployment did not reach the requested image within the verification window "
            f"(expected {target_image}, live {live_image}, sync={sync_status}, health={health_status})."
        )
    return (
        "Deployment did not reach a healthy synced state within the verification window "
        f"(sync={sync_status}, health={health_status})."
    )


def build_compare_url(
    *,
    event: GitOpsDeploymentEvent,
    previous_record: dict[str, object] | None,
) -> str | None:
    owner = os.getenv("DEPLOYMENT_RECONCILER_GITHUB_OWNER", DEFAULT_GITHUB_OWNER).strip() or DEFAULT_GITHUB_OWNER
    portal_repo = os.getenv("DEPLOYMENT_RECONCILER_PORTAL_REPO", DEFAULT_PORTAL_REPO).strip() or DEFAULT_PORTAL_REPO
    gitops_repo = os.getenv("DEPLOYMENT_RECONCILER_GITOPS_REPO", DEFAULT_GITOPS_REPO).strip() or DEFAULT_GITOPS_REPO

    previous_metadata = previous_record.get("metadata") if previous_record else None
    previous_source_sha = None
    if isinstance(previous_metadata, dict) and isinstance(previous_metadata.get("sourceCommitSha"), str):
        previous_source_sha = str(previous_metadata.get("sourceCommitSha"))

    if previous_source_sha and event.source_commit_sha and previous_source_sha != event.source_commit_sha:
        return f"https://github.com/{owner}/{portal_repo}/compare/{previous_source_sha}...{event.source_commit_sha}"

    previous_merge_sha = previous_record.get("mergeSha") if previous_record else None
    if isinstance(previous_merge_sha, str) and previous_merge_sha and event.merge_sha and previous_merge_sha != event.merge_sha:
        return f"https://github.com/{owner}/{gitops_repo}/compare/{previous_merge_sha}...{event.merge_sha}"

    return event.compare_url


def build_reconciled_record(
    *,
    event: GitOpsDeploymentEvent,
    existing_record: dict[str, object] | None,
    previous_record: dict[str, object] | None,
    selected_service_row: dict[str, str | None] | None,
    live_argo: dict[str, str | None],
    live_image: str | None,
    now: datetime,
) -> dict[str, object]:
    timeout_seconds = max(
        60,
        int(os.getenv("DEPLOYMENT_RECONCILER_TIMEOUT_SECONDS", str(DEFAULT_VERIFICATION_TIMEOUT_SECONDS))),
    )
    merged_at = event.merged_at
    existing_started_at = existing_record.get("startedAt") if existing_record else None
    existing_finished_at = existing_record.get("finishedAt") if existing_record else None
    existing_window_start = existing_record.get("deployWindowStart") if existing_record else None
    existing_window_end = existing_record.get("deployWindowEnd") if existing_record else None
    previous_image = existing_record.get("previousImage") if existing_record else None
    if not isinstance(previous_image, str) or not previous_image:
        if previous_record and isinstance(previous_record.get("targetImage"), str):
            previous_image = str(previous_record.get("targetImage"))
        elif live_image and live_image != event.target_image:
            previous_image = live_image
        else:
            previous_image = None

    current_terminal_status = terminal_status(existing_record)
    existing_metadata = existing_record.get("metadata") if existing_record else None
    if current_terminal_status is not None:
        metadata = dict(existing_metadata) if isinstance(existing_metadata, dict) else {}
        metadata.update(
            {
                "lastVerifiedAt": serialize_datetime(now),
                "liveImageRef": live_image,
                "argoRevision": live_argo.get("revision"),
                "argoSyncStatus": live_argo.get("syncStatus"),
                "argoHealthStatus": live_argo.get("healthStatus"),
                "argoOperationPhase": live_argo.get("operationPhase"),
                "argoOperationMessage": live_argo.get("operationMessage"),
            }
        )
        if current_terminal_status == "live":
            metadata.pop("failureReason", None)
        failure_reason = metadata.get("failureReason") if isinstance(metadata.get("failureReason"), str) else None
        existing_result = existing_record.get("result") if existing_record else None
        existing_result_reason = existing_record.get("resultReason") if existing_record else None
        if current_terminal_status == "live":
            reconciled_result: str | None = existing_result if isinstance(existing_result, str) else "success"
            reconciled_result_reason: str | None = None
        else:
            reconciled_result = existing_result if isinstance(existing_result, str) else "failure"
            reconciled_result_reason = existing_result_reason if isinstance(existing_result_reason, str) else failure_reason
        return {
            "service_id": event.service_id,
            "env": event.env,
            "action": event.action,
            "status": current_terminal_status,
            "requested_by": existing_record.get("requestedBy") if isinstance(existing_record.get("requestedBy"), str) else event.requested_by,
            "requested_at": coalesce_datetime(existing_record.get("requestedAt") if existing_record else None, event.requested_at),
            "pr_url": existing_record.get("prUrl") if isinstance(existing_record.get("prUrl"), str) else event.pr_url,
            "pr_number": existing_record.get("prNumber") if isinstance(existing_record.get("prNumber"), int) else event.pr_number,
            "merge_sha": existing_record.get("mergeSha") if isinstance(existing_record.get("mergeSha"), str) else event.merge_sha,
            "target_image": existing_record.get("targetImage") if isinstance(existing_record.get("targetImage"), str) else event.target_image,
            "previous_image": previous_image,
            "argo_app": (
                selected_service_row.get("argo_app_name")
                if selected_service_row and isinstance(selected_service_row.get("argo_app_name"), str)
                else existing_record.get("argoApp")
                if existing_record and isinstance(existing_record.get("argoApp"), str)
                else canonical_argo_app(event.service_id, event.env)
            ),
            "sync_status": live_argo.get("syncStatus") if isinstance(live_argo.get("syncStatus"), str) else None,
            "health_status": live_argo.get("healthStatus") if isinstance(live_argo.get("healthStatus"), str) else None,
            "started_at": existing_started_at,
            "finished_at": existing_finished_at,
            "deploy_window_start": existing_window_start,
            "deploy_window_end": existing_window_end,
            "deploy_reason": (
                existing_record.get("deployReason")
                if existing_record and isinstance(existing_record.get("deployReason"), str)
                else metadata.get("operatorReason")
                if isinstance(metadata.get("operatorReason"), str)
                else f"GitOps {event.action} via PR #{event.pr_number}"
            ),
            "compare_url": (
                existing_record.get("compareUrl")
                if existing_record and isinstance(existing_record.get("compareUrl"), str)
                else build_compare_url(event=event, previous_record=previous_record)
            ),
            "git_ref": existing_record.get("gitRef") if existing_record and isinstance(existing_record.get("gitRef"), str) else event.git_ref,
            "request_key": event.request_key,
            "metadata": metadata,
            "result": reconciled_result,
            "result_reason": reconciled_result_reason,
        }

    failure_reason: str | None = None
    status = "pending"
    started_at_dt = coalesce_datetime(existing_started_at)
    finished_at_dt = coalesce_datetime(existing_finished_at)
    deploy_window_start_dt = coalesce_datetime(existing_window_start)
    deploy_window_end_dt = coalesce_datetime(existing_window_end)

    if event.pr_state == "closed" and merged_at is None:
        status = "failed"
        failure_reason = "GitOps pull request was closed without merge."
        finished_at_dt = max_datetime(finished_at_dt, event.closed_at, now)
        deploy_window_end_dt = max_datetime(deploy_window_end_dt, finished_at_dt)
    elif merged_at is None:
        status = "pending"
    else:
        started_at_dt = max_datetime(started_at_dt, merged_at) or merged_at
        deploy_window_start_dt = max_datetime(deploy_window_start_dt, merged_at) or merged_at
        merged_age = now - merged_at

        immediate_failure = failed_operation_reason(live_argo)
        if immediate_failure:
            status = "failed"
            failure_reason = immediate_failure
            finished_at_dt = max_datetime(finished_at_dt, now, started_at_dt)
            deploy_window_end_dt = max_datetime(deploy_window_end_dt, finished_at_dt)
        elif live_rollout_verified(event=event, live_argo=live_argo, live_image=live_image):
            status = "live"
            finished_at_dt = max_datetime(finished_at_dt, live_argo.get("deployedAt"), now, started_at_dt)
            deploy_window_end_dt = max_datetime(deploy_window_end_dt, finished_at_dt)
        elif selected_service_row is None and merged_age > timedelta(seconds=timeout_seconds):
            status = "failed"
            failure_reason = "No matching service registry row exists for this deployment target."
            finished_at_dt = max_datetime(finished_at_dt, now, started_at_dt)
            deploy_window_end_dt = max_datetime(deploy_window_end_dt, finished_at_dt)
        elif merged_age > timedelta(seconds=timeout_seconds):
            status = "failed"
            failure_reason = stalled_reason(target_image=event.target_image, live_image=live_image, live_argo=live_argo)
            finished_at_dt = max_datetime(finished_at_dt, now, started_at_dt)
            deploy_window_end_dt = max_datetime(deploy_window_end_dt, finished_at_dt)
        else:
            status = "deploying"

    metadata = dict(event.metadata)
    metadata.update(
        {
            "lastVerifiedAt": serialize_datetime(now),
            "liveImageRef": live_image,
            "argoRevision": live_argo.get("revision"),
            "argoSyncStatus": live_argo.get("syncStatus"),
            "argoHealthStatus": live_argo.get("healthStatus"),
            "argoOperationPhase": live_argo.get("operationPhase"),
            "argoOperationMessage": live_argo.get("operationMessage"),
        }
    )
    if failure_reason:
        metadata["failureReason"] = failure_reason
    else:
        metadata.pop("failureReason", None)

    if status == "live":
        computed_result: str | None = "success"
        computed_result_reason: str | None = None
    elif status == "failed":
        computed_result = "failure"
        computed_result_reason = failure_reason
    else:
        computed_result = None
        computed_result_reason = None

    return {
        "service_id": event.service_id,
        "env": event.env,
        "action": event.action,
        "status": status,
        "requested_by": event.requested_by,
        "requested_at": event.requested_at,
        "pr_url": event.pr_url,
        "pr_number": event.pr_number,
        "merge_sha": event.merge_sha,
        "target_image": event.target_image,
        "previous_image": previous_image,
        "argo_app": (
            selected_service_row.get("argo_app_name")
            if selected_service_row and isinstance(selected_service_row.get("argo_app_name"), str)
            else existing_record.get("argoApp")
            if existing_record and isinstance(existing_record.get("argoApp"), str)
            else canonical_argo_app(event.service_id, event.env)
        ),
        "sync_status": live_argo.get("syncStatus") if isinstance(live_argo.get("syncStatus"), str) else None,
        "health_status": live_argo.get("healthStatus") if isinstance(live_argo.get("healthStatus"), str) else None,
        "started_at": serialize_datetime(started_at_dt),
        "finished_at": serialize_datetime(finished_at_dt),
        "deploy_window_start": serialize_datetime(deploy_window_start_dt),
        "deploy_window_end": serialize_datetime(deploy_window_end_dt),
        "deploy_reason": (
            existing_record.get("deployReason")
            if existing_record and isinstance(existing_record.get("deployReason"), str)
            else metadata.get("operatorReason")
            if isinstance(metadata.get("operatorReason"), str)
            else f"GitOps {event.action} via PR #{event.pr_number}"
        ),
        "compare_url": build_compare_url(event=event, previous_record=previous_record),
        "git_ref": event.git_ref,
        "request_key": event.request_key,
        "metadata": metadata,
        "result": computed_result,
        "result_reason": computed_result_reason,
    }
