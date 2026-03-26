from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Callable
from urllib import request as urlrequest

from app.deployments.reconciler.models import (
    CONFIG_CHANGE_HEAD_RE,
    CONFIG_CHANGE_TITLE_RE,
    DEFAULT_GITHUB_OWNER,
    DEFAULT_GITOPS_REPO,
    DEFAULT_PULL_REQUEST_LIMIT,
    DEV_AUTOBUMP_HEAD_RE,
    DEV_AUTOBUMP_TITLE_RE,
    ENV_MUTATION_HEAD_RE,
    GitOpsDeploymentEvent,
    IMAGE_REF_RE,
    MANUAL_DEV_DEPLOY_HEAD_RE,
    MANUAL_DEV_DEPLOY_TITLE_RE,
    MANUAL_PROD_PROMOTE_HEAD_RE,
    MANUAL_PROD_PROMOTE_TITLE_RE,
    MANUAL_ROLLBACK_HEAD_RE,
    MANUAL_ROLLBACK_TITLE_RE,
    PROMOTE_TITLE_RE,
    REASON_LINE_RE,
    ROLLBACK_TITLE_RE,
)


def parse_datetime(value: object) -> datetime | None:
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


def serialize_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat()


def github_api_token() -> str | None:
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


def github_get_json(path: str, timeout_seconds: float = 8.0) -> object:
    base_url = os.getenv("GITHUB_API_BASE_URL", "https://api.github.com").rstrip("/")
    request = urlrequest.Request(
        f"{base_url}/{path.lstrip('/')}",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "homelab-portal-backend",
        },
    )
    token = github_api_token()
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urlrequest.urlopen(request, timeout=timeout_seconds) as response:
        return json.loads(response.read())


def github_post_json(path: str, payload: object, timeout_seconds: float = 8.0) -> None:
    base_url = os.getenv("GITHUB_API_BASE_URL", "https://api.github.com").rstrip("/")
    data = json.dumps(payload).encode()
    request = urlrequest.Request(
        f"{base_url}/{path.lstrip('/')}",
        data=data,
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "homelab-portal-backend",
        },
    )
    token = github_api_token()
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urlrequest.urlopen(request, timeout=timeout_seconds) as _response:
        pass


def extract_images_from_body(body: object) -> dict[str, str]:
    if not isinstance(body, str):
        return {}
    images: dict[str, str] = {}
    for match in IMAGE_REF_RE.finditer(body):
        full_ref = match.group(1)
        image_name = match.group(2)
        images[image_name] = full_ref
    return images


def extract_requested_reason_from_body(body: object) -> str | None:
    if not isinstance(body, str):
        return None
    match = REASON_LINE_RE.search(body)
    if match is None:
        return None
    reason = match.group(1).strip()
    return reason or None


def action_context_from_pull_request(pr: dict[str, object]) -> tuple[str, str, str | None] | None:
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
        source_sha = target_tag[4:] if target_tag.startswith("sha-") and len(target_tag) == 44 else None
        return ("dev", "deploy", source_sha)

    manual_prod_promote_title = MANUAL_PROD_PROMOTE_TITLE_RE.match(title)
    if manual_prod_promote_title:
        target_tag = manual_prod_promote_title.group(2)
        source_sha = target_tag[4:] if target_tag.startswith("sha-") and len(target_tag) == 44 else None
        return ("prod", "promote", source_sha)

    manual_rollback_title = MANUAL_ROLLBACK_TITLE_RE.match(title)
    if manual_rollback_title:
        target_tag = manual_rollback_title.group(2)
        source_sha = target_tag[4:] if target_tag.startswith("sha-") and len(target_tag) == 44 else None
        return (manual_rollback_title.group(3), "rollback", source_sha)

    if head_ref:
        head_match = DEV_AUTOBUMP_HEAD_RE.match(head_ref)
        if head_match:
            return ("dev", "deploy", head_match.group(1))
        manual_head_match = MANUAL_DEV_DEPLOY_HEAD_RE.match(head_ref)
        if manual_head_match:
            target_tag = manual_head_match.group(2)
            return ("dev", "deploy", target_tag[4:] if target_tag.startswith("sha-") and len(target_tag) == 44 else None)
        manual_promote_head_match = MANUAL_PROD_PROMOTE_HEAD_RE.match(head_ref)
        if manual_promote_head_match:
            target_tag = manual_promote_head_match.group(2)
            return ("prod", "promote", target_tag[4:] if target_tag.startswith("sha-") and len(target_tag) == 44 else None)
        manual_rollback_head_match = MANUAL_ROLLBACK_HEAD_RE.match(head_ref)
        if manual_rollback_head_match:
            target_tag = manual_rollback_head_match.group(3)
            return (
                manual_rollback_head_match.group(1),
                "rollback",
                target_tag[4:] if target_tag.startswith("sha-") and len(target_tag) == 44 else None,
            )

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


def load_recent_gitops_deployment_events(
    *,
    service_id: str | None = None,
    env: str | None = None,
    limit_pull_requests: int = DEFAULT_PULL_REQUEST_LIMIT,
    github_fetch_json: Callable[[str], object] = github_get_json,
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

        context = action_context_from_pull_request(pr)
        if context is None:
            continue
        pr_env, action, source_commit_sha = context
        if env and pr_env != env:
            continue

        pr_number = pr.get("number")
        pr_url = pr.get("html_url")
        created_at = parse_datetime(pr.get("created_at"))
        if not isinstance(pr_number, int) or not isinstance(pr_url, str) or created_at is None:
            continue

        images = extract_images_from_body(pr.get("body"))
        if not images:
            continue

        state = str(pr.get("state") or "").strip().lower() or "unknown"
        merged_at = parse_datetime(pr.get("merged_at"))
        closed_at = parse_datetime(pr.get("closed_at"))
        user = pr.get("user")
        requested_by = user.get("login") if isinstance(user, dict) and isinstance(user.get("login"), str) else None
        head = pr.get("head")
        git_ref = head.get("ref") if isinstance(head, dict) and isinstance(head.get("ref"), str) else None
        merge_sha = pr.get("merge_commit_sha") if isinstance(pr.get("merge_commit_sha"), str) else None
        requested_reason = extract_requested_reason_from_body(pr.get("body"))

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
                        "pullRequestMergedAt": serialize_datetime(merged_at),
                        "pullRequestClosedAt": serialize_datetime(closed_at),
                        "sourceCommitSha": source_commit_sha,
                        "operatorReason": requested_reason,
                    },
                )
            )

    events.sort(key=lambda item: item.requested_at, reverse=True)
    return events
