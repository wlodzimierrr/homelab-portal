from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import logging
import os
import re
from typing import Any, Callable, TypedDict
from urllib import request as urlrequest

import psycopg

from app.deployment_records import (
    get_deployment_record_by_request_key,
    get_latest_deployment_record_for_service,
    upsert_deployment_record,
)
from app.deployment_locks import sync_deployment_lock_for_deployment_row


logger = logging.getLogger("homelab.backend.deployment_reconciler")

DEFAULT_GITHUB_OWNER = "wlodzimierrr"
DEFAULT_GITOPS_REPO = "homelab-workloads"
DEFAULT_PORTAL_REPO = "homelab-portal"
DEFAULT_PULL_REQUEST_LIMIT = 30
DEFAULT_VERIFICATION_TIMEOUT_SECONDS = 15 * 60

DEV_AUTOBUMP_HEAD_RE = re.compile(r"^automation/dev-image-bump-([0-9a-f]{40})$")
MANUAL_DEV_DEPLOY_HEAD_RE = re.compile(
    r"^automation/dev-deploy-(homelab-api|homelab-web)-([a-z0-9][a-z0-9.-]*)-[0-9]{14}$"
)
MANUAL_PROD_PROMOTE_HEAD_RE = re.compile(
    r"^automation/prod-promote-(homelab-api|homelab-web)-([a-z0-9][a-z0-9.-]*)-[0-9]{14}$"
)
MANUAL_ROLLBACK_HEAD_RE = re.compile(
    r"^automation/(dev|prod)-rollback-(homelab-api|homelab-web)-([a-z0-9][a-z0-9.-]*)-[0-9]{14}$"
)
ENV_MUTATION_HEAD_RE = re.compile(r"^automation/([a-z0-9-]+)-(promote|rollback)-image-update-.+$")
CONFIG_CHANGE_HEAD_RE = re.compile(
    r"^automation/([a-z0-9-]+)-config-change-(homelab-api|homelab-web)-replicas-.+$"
)
DEV_AUTOBUMP_TITLE_RE = re.compile(r"^chore\(dev\): bump portal images to (sha-[0-9a-f]{40})$")
MANUAL_DEV_DEPLOY_TITLE_RE = re.compile(
    r"^Deploy (homelab-api|homelab-web): (sha-[0-9a-f]{40}|v?[0-9]+(?:\.[0-9]+){2}(?:[.-][0-9A-Za-z.-]+)?) to dev$"
)
MANUAL_PROD_PROMOTE_TITLE_RE = re.compile(
    r"^Promote (homelab-api|homelab-web): (sha-[0-9a-f]{40}|v?[0-9]+(?:\.[0-9]+){2}(?:[.-][0-9A-Za-z.-]+)?) to prod$"
)
MANUAL_ROLLBACK_TITLE_RE = re.compile(
    r"^Rollback (homelab-api|homelab-web): (sha-[0-9a-f]{40}|v?[0-9]+(?:\.[0-9]+){2}(?:[.-][0-9A-Za-z.-]+)?) in (dev|prod)$"
)
PROMOTE_TITLE_RE = re.compile(r"^chore\(([a-z0-9-]+)\): promote portal images from dev \((sha-[0-9a-f]{40})\)$")
ROLLBACK_TITLE_RE = re.compile(r"^chore\(([a-z0-9-]+)\): rollback portal images to requested tags$")
CONFIG_CHANGE_TITLE_RE = re.compile(
    r"^chore\(([a-z0-9-]+)\): set (homelab-api|homelab-web) replicas to ([0-9]+)$"
)
IMAGE_REF_RE = re.compile(r"(ghcr\.io/[^/\s]+/(homelab-api|homelab-web):([^\s`]+))")
REASON_LINE_RE = re.compile(r"^\s*-\s+Reason:\s+(.+?)\s*$", re.MULTILINE)


class DeploymentReconcileSummary(TypedDict):
    pullRequestsScanned: int
    recordsUpserted: int
    statusCounts: dict[str, int]
    generatedAt: str


@dataclass(frozen=True)
class GitOpsDeploymentEvent:
    request_key: str
    service_id: str
    env: str
    action: str
    target_image: str
    requested_by: str | None
    requested_at: datetime
    pr_url: str
    pr_number: int
    pr_state: str
    git_ref: str | None
    merge_sha: str | None
    merged_at: datetime | None
    closed_at: datetime | None
    source_commit_sha: str | None
    compare_url: str | None
    metadata: dict[str, Any]


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _serialize_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat()


def _github_api_token() -> str | None:
    for name in (
        "GITHUB_API_TOKEN",
        "GITHUB_READ_TOKEN",
        "HOMELAB_WORKLOADS_REPO_TOKEN",
        "GITHUB_TOKEN",
    ):
        token = os.getenv(name, "").strip()
        if token:
            return token
    return None


def _github_get_json(path: str, timeout_seconds: float = 8.0) -> object:
    base_url = os.getenv("GITHUB_API_BASE_URL", "https://api.github.com").rstrip("/")
    request = urlrequest.Request(
        f"{base_url}/{path.lstrip('/')}",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "homelab-portal-backend",
        },
    )
    token = _github_api_token()
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urlrequest.urlopen(request, timeout=timeout_seconds) as response:
        return json.loads(response.read())


def _extract_images_from_body(body: object) -> dict[str, str]:
    if not isinstance(body, str):
        return {}
    images: dict[str, str] = {}
    for match in IMAGE_REF_RE.finditer(body):
        full_ref = match.group(1)
        image_name = match.group(2)
        images[image_name] = full_ref
    return images


def _extract_requested_reason_from_body(body: object) -> str | None:
    if not isinstance(body, str):
        return None
    match = REASON_LINE_RE.search(body)
    if match is None:
        return None
    reason = match.group(1).strip()
    return reason or None


def _action_context_from_pull_request(pr: dict[str, object]) -> tuple[str, str, str | None] | None:
    title = str(pr.get("title") or "").strip()
    head = pr.get("head")
    head_ref = None
    if isinstance(head, dict) and isinstance(head.get("ref"), str):
        head_ref = str(head.get("ref"))

    dev_title = DEV_AUTOBUMP_TITLE_RE.match(title)
    if dev_title:
        source_sha = None
        if head_ref:
            head_match = DEV_AUTOBUMP_HEAD_RE.match(head_ref)
            if head_match:
                source_sha = head_match.group(1)
        return ("dev", "deploy", source_sha)

    manual_dev_title = MANUAL_DEV_DEPLOY_TITLE_RE.match(title)
    if manual_dev_title:
        target_tag = manual_dev_title.group(2)
        source_sha = None
        if target_tag.startswith("sha-") and len(target_tag) == 44:
            source_sha = target_tag[4:]
        return ("dev", "deploy", source_sha)

    manual_prod_promote_title = MANUAL_PROD_PROMOTE_TITLE_RE.match(title)
    if manual_prod_promote_title:
        target_tag = manual_prod_promote_title.group(2)
        source_sha = None
        if target_tag.startswith("sha-") and len(target_tag) == 44:
            source_sha = target_tag[4:]
        return ("prod", "promote", source_sha)

    manual_rollback_title = MANUAL_ROLLBACK_TITLE_RE.match(title)
    if manual_rollback_title:
        target_tag = manual_rollback_title.group(2)
        source_sha = None
        if target_tag.startswith("sha-") and len(target_tag) == 44:
            source_sha = target_tag[4:]
        return (manual_rollback_title.group(3), "rollback", source_sha)

    if head_ref:
        head_match = DEV_AUTOBUMP_HEAD_RE.match(head_ref)
        if head_match:
            return ("dev", "deploy", head_match.group(1))
        manual_head_match = MANUAL_DEV_DEPLOY_HEAD_RE.match(head_ref)
        if manual_head_match:
            target_tag = manual_head_match.group(2)
            if target_tag.startswith("sha-") and len(target_tag) == 44:
                return ("dev", "deploy", target_tag[4:])
            return ("dev", "deploy", None)
        manual_promote_head_match = MANUAL_PROD_PROMOTE_HEAD_RE.match(head_ref)
        if manual_promote_head_match:
            target_tag = manual_promote_head_match.group(2)
            if target_tag.startswith("sha-") and len(target_tag) == 44:
                return ("prod", "promote", target_tag[4:])
            return ("prod", "promote", None)
        manual_rollback_head_match = MANUAL_ROLLBACK_HEAD_RE.match(head_ref)
        if manual_rollback_head_match:
            target_tag = manual_rollback_head_match.group(3)
            if target_tag.startswith("sha-") and len(target_tag) == 44:
                return (manual_rollback_head_match.group(1), "rollback", target_tag[4:])
            return (manual_rollback_head_match.group(1), "rollback", None)

    promote = PROMOTE_TITLE_RE.match(title)
    if promote:
        return (promote.group(1), "promote", None)

    rollback = ROLLBACK_TITLE_RE.match(title)
    if rollback:
        return (rollback.group(1), "rollback", None)

    config_change = CONFIG_CHANGE_TITLE_RE.match(title)
    if config_change:
        return (config_change.group(1), "config-change", None)

    if head_ref:
        config_head = CONFIG_CHANGE_HEAD_RE.match(head_ref)
        if config_head:
            return (config_head.group(1), "config-change", None)

        env_head = ENV_MUTATION_HEAD_RE.match(head_ref)
        if env_head:
            return (env_head.group(1), env_head.group(2), None)

    return None


def _build_compare_url(
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
    if (
        isinstance(previous_merge_sha, str)
        and previous_merge_sha
        and event.merge_sha
        and previous_merge_sha != event.merge_sha
    ):
        return f"https://github.com/{owner}/{gitops_repo}/compare/{previous_merge_sha}...{event.merge_sha}"

    return event.compare_url


def load_recent_gitops_deployment_events(
    *,
    service_id: str | None = None,
    env: str | None = None,
    limit_pull_requests: int = DEFAULT_PULL_REQUEST_LIMIT,
    github_fetch_json: Callable[[str], object] = _github_get_json,
) -> list[GitOpsDeploymentEvent]:
    owner = os.getenv("DEPLOYMENT_RECONCILER_GITHUB_OWNER", DEFAULT_GITHUB_OWNER).strip() or DEFAULT_GITHUB_OWNER
    gitops_repo = os.getenv("DEPLOYMENT_RECONCILER_GITOPS_REPO", DEFAULT_GITOPS_REPO).strip() or DEFAULT_GITOPS_REPO
    per_page = max(1, min(limit_pull_requests, 100))
    payload = github_fetch_json(
        f"repos/{owner}/{gitops_repo}/pulls?state=all&sort=updated&direction=desc&per_page={per_page}"
    )
    if not isinstance(payload, list):
        return []

    events: list[GitOpsDeploymentEvent] = []
    for pr in payload:
        if not isinstance(pr, dict):
            continue

        context = _action_context_from_pull_request(pr)
        if context is None:
            continue
        pr_env, action, source_commit_sha = context
        if env and pr_env != env:
            continue

        pr_number = pr.get("number")
        pr_url = pr.get("html_url")
        created_at = _parse_datetime(pr.get("created_at"))
        if not isinstance(pr_number, int) or not isinstance(pr_url, str) or created_at is None:
            continue

        images = _extract_images_from_body(pr.get("body"))
        if not images:
            continue

        state = str(pr.get("state") or "").strip().lower() or "unknown"
        merged_at = _parse_datetime(pr.get("merged_at"))
        closed_at = _parse_datetime(pr.get("closed_at"))
        user = pr.get("user")
        requested_by = user.get("login") if isinstance(user, dict) and isinstance(user.get("login"), str) else None
        head = pr.get("head")
        git_ref = head.get("ref") if isinstance(head, dict) and isinstance(head.get("ref"), str) else None
        merge_sha = pr.get("merge_commit_sha") if isinstance(pr.get("merge_commit_sha"), str) else None
        requested_reason = _extract_requested_reason_from_body(pr.get("body"))

        for current_service_id, image_ref in (
            ("homelab-api", images.get("homelab-api")),
            ("homelab-web", images.get("homelab-web")),
        ):
            if current_service_id == "homelab-api" and not image_ref:
                continue
            if current_service_id == "homelab-web" and not image_ref:
                continue
            if service_id and current_service_id != service_id:
                continue

            events.append(
                GitOpsDeploymentEvent(
                    request_key=f"gitops-pr:{pr_number}:{current_service_id}:{pr_env}:{action}",
                    service_id=current_service_id,
                    env=pr_env,
                    action=action,
                    target_image=str(image_ref),
                    requested_by=requested_by,
                    requested_at=created_at,
                    pr_url=pr_url,
                    pr_number=pr_number,
                    pr_state=state,
                    git_ref=git_ref,
                    merge_sha=merge_sha,
                    merged_at=merged_at,
                    closed_at=closed_at,
                    source_commit_sha=source_commit_sha,
                    compare_url=f"{pr_url}/files",
                    metadata={
                        "source": "deployment-reconciler",
                        "gitopsRepo": f"{owner}/{gitops_repo}",
                        "pullRequestState": state,
                        "pullRequestHeadRef": git_ref,
                        "pullRequestMergedAt": _serialize_datetime(merged_at),
                        "pullRequestClosedAt": _serialize_datetime(closed_at),
                        "sourceCommitSha": source_commit_sha,
                        "operatorReason": requested_reason,
                    },
                )
            )

    events.sort(key=lambda item: item.requested_at, reverse=True)
    return events


def _canonical_argo_app(service_id: str, env: str) -> str:
    return f"{service_id}-{env}"


def _load_primary_live_image(
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


def _failed_operation_reason(live_argo: dict[str, str | None]) -> str | None:
    phase = str(live_argo.get("operationPhase") or "").strip().lower()
    if phase not in {"failed", "error"}:
        return None
    message = live_argo.get("operationMessage")
    if isinstance(message, str) and message.strip():
        return message.strip()
    return f"Argo operation phase is {phase}"


def _coalesce_datetime(*values: object) -> datetime | None:
    for value in values:
        parsed = _parse_datetime(value)
        if parsed is not None:
            return parsed
    return None


def _max_datetime(*values: object) -> datetime | None:
    parsed_values = [parsed for value in values if (parsed := _parse_datetime(value)) is not None]
    if not parsed_values:
        return None
    return max(parsed_values)


def _live_rollout_verified(
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


def _terminal_status(record: dict[str, object] | None) -> str | None:
    if not record:
        return None
    status = record.get("status")
    if isinstance(status, str) and status in {"live", "failed"}:
        return status
    return None


def _stalled_reason(
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


def _build_reconciled_record(
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

    terminal_status = _terminal_status(existing_record)
    existing_metadata = existing_record.get("metadata") if existing_record else None
    if terminal_status is not None:
        metadata = dict(existing_metadata) if isinstance(existing_metadata, dict) else {}
        metadata.update(
            {
                "lastVerifiedAt": _serialize_datetime(now),
                "liveImageRef": live_image,
                "argoRevision": live_argo.get("revision"),
                "argoSyncStatus": live_argo.get("syncStatus"),
                "argoHealthStatus": live_argo.get("healthStatus"),
                "argoOperationPhase": live_argo.get("operationPhase"),
                "argoOperationMessage": live_argo.get("operationMessage"),
            }
        )
        if terminal_status == "live":
            metadata.pop("failureReason", None)
        failure_reason = (
            metadata.get("failureReason")
            if isinstance(metadata.get("failureReason"), str)
            else None
        )
        return {
            "service_id": event.service_id,
            "env": event.env,
            "action": event.action,
            "status": terminal_status,
            "requested_by": (
                existing_record.get("requestedBy")
                if isinstance(existing_record.get("requestedBy"), str)
                else event.requested_by
            ),
            "requested_at": _coalesce_datetime(existing_record.get("requestedAt") if existing_record else None, event.requested_at),
            "pr_url": (
                existing_record.get("prUrl")
                if isinstance(existing_record.get("prUrl"), str)
                else event.pr_url
            ),
            "pr_number": (
                existing_record.get("prNumber")
                if isinstance(existing_record.get("prNumber"), int)
                else event.pr_number
            ),
            "merge_sha": (
                existing_record.get("mergeSha")
                if isinstance(existing_record.get("mergeSha"), str)
                else event.merge_sha
            ),
            "target_image": (
                existing_record.get("targetImage")
                if isinstance(existing_record.get("targetImage"), str)
                else event.target_image
            ),
            "previous_image": previous_image,
            "argo_app": (
                selected_service_row.get("argo_app_name")
                if selected_service_row and isinstance(selected_service_row.get("argo_app_name"), str)
                else existing_record.get("argoApp")
                if existing_record and isinstance(existing_record.get("argoApp"), str)
                else _canonical_argo_app(event.service_id, event.env)
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
                else _build_compare_url(event=event, previous_record=previous_record)
            ),
            "git_ref": (
                existing_record.get("gitRef")
                if existing_record and isinstance(existing_record.get("gitRef"), str)
                else event.git_ref
            ),
            "request_key": event.request_key,
            "metadata": metadata,
        }

    failure_reason: str | None = None
    status = "pending"
    started_at_dt = _coalesce_datetime(existing_started_at)
    finished_at_dt = _coalesce_datetime(existing_finished_at)
    deploy_window_start_dt = _coalesce_datetime(existing_window_start)
    deploy_window_end_dt = _coalesce_datetime(existing_window_end)

    if event.pr_state == "closed" and merged_at is None:
        status = "failed"
        failure_reason = "GitOps pull request was closed without merge."
        finished_at_dt = _max_datetime(finished_at_dt, event.closed_at, now)
        deploy_window_end_dt = _max_datetime(deploy_window_end_dt, finished_at_dt)
    elif merged_at is None:
        status = "pending"
    else:
        started_at_dt = _max_datetime(started_at_dt, merged_at) or merged_at
        deploy_window_start_dt = _max_datetime(deploy_window_start_dt, merged_at) or merged_at
        merged_age = now - merged_at

        immediate_failure = _failed_operation_reason(live_argo)
        if immediate_failure:
            status = "failed"
            failure_reason = immediate_failure
            finished_at_dt = _max_datetime(finished_at_dt, now, started_at_dt)
            deploy_window_end_dt = _max_datetime(deploy_window_end_dt, finished_at_dt)
        elif _live_rollout_verified(event=event, live_argo=live_argo, live_image=live_image):
            status = "live"
            finished_at_dt = _max_datetime(finished_at_dt, live_argo.get("deployedAt"), now, started_at_dt)
            deploy_window_end_dt = _max_datetime(deploy_window_end_dt, finished_at_dt)
        elif selected_service_row is None and merged_age > timedelta(seconds=timeout_seconds):
            status = "failed"
            failure_reason = "No matching service registry row exists for this deployment target."
            finished_at_dt = _max_datetime(finished_at_dt, now, started_at_dt)
            deploy_window_end_dt = _max_datetime(deploy_window_end_dt, finished_at_dt)
        elif merged_age > timedelta(seconds=timeout_seconds):
            status = "failed"
            failure_reason = _stalled_reason(
                target_image=event.target_image,
                live_image=live_image,
                live_argo=live_argo,
            )
            finished_at_dt = _max_datetime(finished_at_dt, now, started_at_dt)
            deploy_window_end_dt = _max_datetime(deploy_window_end_dt, finished_at_dt)
        else:
            status = "deploying"

    metadata = dict(event.metadata)
    metadata.update(
        {
            "lastVerifiedAt": _serialize_datetime(now),
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
            else _canonical_argo_app(event.service_id, event.env)
        ),
        "sync_status": live_argo.get("syncStatus") if isinstance(live_argo.get("syncStatus"), str) else None,
        "health_status": live_argo.get("healthStatus") if isinstance(live_argo.get("healthStatus"), str) else None,
        "started_at": _serialize_datetime(started_at_dt),
        "finished_at": _serialize_datetime(finished_at_dt),
        "deploy_window_start": _serialize_datetime(deploy_window_start_dt),
        "deploy_window_end": _serialize_datetime(deploy_window_end_dt),
        "deploy_reason": (
            existing_record.get("deployReason")
            if existing_record and isinstance(existing_record.get("deployReason"), str)
            else metadata.get("operatorReason")
            if isinstance(metadata.get("operatorReason"), str)
            else f"GitOps {event.action} via PR #{event.pr_number}"
        ),
        "compare_url": _build_compare_url(event=event, previous_record=previous_record),
        "git_ref": event.git_ref,
        "request_key": event.request_key,
        "metadata": metadata,
    }


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
    now: datetime | None = None,
) -> DeploymentReconcileSummary:
    current_time = now or datetime.now(tz=timezone.utc)
    events = load_recent_gitops_deployment_events(
        service_id=service_id,
        env=env,
        limit_pull_requests=limit_pull_requests,
        github_fetch_json=github_fetch_json,
    )
    status_counts = {status: 0 for status in ("pending", "deploying", "live", "failed")}
    records_upserted = 0
    lock_managed_pairs: set[tuple[str, str]] = set()

    for event in events:
        existing_record = get_deployment_record_by_request_key(conn, event.request_key)
        previous_record = get_latest_deployment_record_for_service(
            conn,
            service_id=event.service_id,
            env=event.env,
            exclude_request_key=event.request_key,
        )
        service_rows = load_service_rows(service_id=event.service_id, env=event.env)
        selected_service_row = select_preferred_service_row(event.service_id, service_rows, event.env)
        live_argo = load_live_argo_status(selected_service_row) if selected_service_row else {}
        live_image = (
            _load_primary_live_image(
                selected_service_row,
                list_live_deployments=list_live_deployments,
                extract_live_image_ref=extract_live_image_ref,
            )
            if selected_service_row
            else None
        )
        payload = _build_reconciled_record(
            event=event,
            existing_record=existing_record,
            previous_record=previous_record,
            selected_service_row=selected_service_row,
            live_argo=live_argo,
            live_image=live_image,
            now=current_time,
        )
        row = upsert_deployment_record(conn, **payload)
        records_upserted += 1
        pair = (event.service_id, event.env)
        if pair not in lock_managed_pairs:
            sync_deployment_lock_for_deployment_row(
                conn,
                row,
                enforce_conflict=False,
                now=current_time,
            )
            lock_managed_pairs.add(pair)
        status = row.get("status")
        if isinstance(status, str) and status in status_counts:
            status_counts[status] += 1

    return {
        "pullRequestsScanned": len(events),
        "recordsUpserted": records_upserted,
        "statusCounts": status_counts,
        "generatedAt": current_time.astimezone(timezone.utc).isoformat(),
    }
