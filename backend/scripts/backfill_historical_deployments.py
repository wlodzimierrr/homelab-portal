"""Backfill historical deployment records from GitOps PR history and Argo CD app history.

Evidence sources (written into metadata.source):
  git_pr        — All merged/closed GitOps PRs whose branch name or title matches an
                  automation pattern.  Pages through the full PR history on the GitOps
                  repo up to --max-pages pages (100 PRs / page).
  argo_history  — Argo CD application status.history[] fetched via the Argo CD REST API.
                  Typically limited to the last ~20 sync operations per app.

Idempotency:
  Every record is keyed by request_key.  The script checks for an existing record before
  inserting and skips if one is found.  Safe to rerun without duplicating records.

  request_key formats:
    git_pr        →  gitops-pr:<pr_number>:<service_id>:<env>:<action>
                     (same key used by the live reconciler — existing reconciler records
                      will be detected and skipped cleanly)
    argo_history  →  backfill:argo-history:<argo_app>:<history_id>

Confidence notes:
  git_pr merged PRs are marked confidence=medium because the script cannot verify that
  the rollout actually completed (the live state has changed since then).  Failed/closed
  PRs are marked confidence=high since the failure is certain.
  Argo history entries are marked confidence=high because Argo records the actual sync.

  Argo history records may overlap with git_pr records for the same deployment event.
  The metadata.source field allows filtering by provenance.  Prefer git_pr records where
  both exist, as they carry richer context (PR link, actor, reason).

Usage:
    python scripts/backfill_historical_deployments.py [--dry-run] \\
        [--service homelab-api] \\
        [--source git_pr | argo_history | all] \\
        [--max-pages 10]

Environment variables:
    GITHUB_API_TOKEN / GITHUB_READ_TOKEN / HOMELAB_WORKLOADS_REPO_TOKEN
    ARGO_CD_URL      Base URL for the Argo CD API  (e.g. https://argocd.homelab.local)
    ARGO_CD_TOKEN    Argo CD API bearer token
    DATABASE_URL / HOMELAB_DATABASE_URL
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any
from urllib import request as urlrequest

sys.path.insert(0, ".")

import psycopg

from app.db import get_psycopg_database_url
from app.deployment_records import (
    get_deployment_record_by_request_key,
    upsert_deployment_record,
)
from app.deployment_reconciler import (
    DEFAULT_GITHUB_OWNER,
    DEFAULT_GITOPS_REPO,
    _action_context_from_pull_request,
    _extract_images_from_body,
    _extract_requested_reason_from_body,
    _github_api_token,
    _parse_datetime,
    _serialize_datetime,
)

logger = logging.getLogger(__name__)

BACKFILL_SCRIPT_VERSION = "1"
GITHUB_PRS_PER_PAGE = 100
DEFAULT_MAX_GITHUB_PAGES = 10


# ---------------------------------------------------------------------------
# GitHub helpers
# ---------------------------------------------------------------------------


def _github_get_json(path: str, timeout_seconds: float = 10.0) -> object:
    base_url = os.getenv("GITHUB_API_BASE_URL", "https://api.github.com").rstrip("/")
    req = urlrequest.Request(
        f"{base_url}/{path.lstrip('/')}",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "homelab-portal-backfill",
        },
    )
    token = _github_api_token()
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urlrequest.urlopen(req, timeout=timeout_seconds) as resp:
        return json.loads(resp.read())


def _fetch_all_gitops_prs(*, max_pages: int = DEFAULT_MAX_GITHUB_PAGES) -> list[dict[str, object]]:
    owner = os.getenv("DEPLOYMENT_RECONCILER_GITHUB_OWNER", DEFAULT_GITHUB_OWNER).strip() or DEFAULT_GITHUB_OWNER
    gitops_repo = (
        os.getenv("DEPLOYMENT_RECONCILER_GITOPS_REPO", DEFAULT_GITOPS_REPO).strip() or DEFAULT_GITOPS_REPO
    )
    all_prs: list[dict[str, object]] = []
    for page in range(1, max_pages + 1):
        path = (
            f"repos/{owner}/{gitops_repo}/pulls"
            f"?state=all&sort=updated&direction=desc"
            f"&per_page={GITHUB_PRS_PER_PAGE}&page={page}"
        )
        try:
            payload = _github_get_json(path)
        except Exception as exc:
            logger.warning("github_fetch_failed page=%d error=%s", page, exc)
            break
        if not isinstance(payload, list) or not payload:
            break
        for item in payload:
            if isinstance(item, dict):
                all_prs.append(item)
        if len(payload) < GITHUB_PRS_PER_PAGE:
            break  # last page reached
    return all_prs


# ---------------------------------------------------------------------------
# Argo CD helpers
# ---------------------------------------------------------------------------


def _argo_cd_url() -> str | None:
    url = os.getenv("ARGO_CD_URL", "").strip().rstrip("/")
    return url or None


def _argo_cd_token() -> str | None:
    for name in ("ARGO_CD_TOKEN", "ARGOCD_TOKEN", "ARGOCD_AUTH_TOKEN"):
        token = os.getenv(name, "").strip()
        if token:
            return token
    return None


def _argo_get_json(path: str, timeout_seconds: float = 10.0) -> object:
    base_url = _argo_cd_url()
    if not base_url:
        raise RuntimeError("ARGO_CD_URL is not set")
    req = urlrequest.Request(
        f"{base_url}/{path.lstrip('/')}",
        headers={"User-Agent": "homelab-portal-backfill"},
    )
    token = _argo_cd_token()
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urlrequest.urlopen(req, timeout=timeout_seconds) as resp:
        return json.loads(resp.read())


def _list_argo_apps(service_filter: str | None) -> list[dict[str, object]]:
    try:
        payload = _argo_get_json(
            "api/v1/applications?fields=items.metadata.name,items.status.history"
        )
    except Exception as exc:
        logger.error("argo_list_apps_failed error=%s", exc)
        return []
    if not isinstance(payload, dict):
        return []
    items = payload.get("items")
    if not isinstance(items, list):
        return []
    result: list[dict[str, object]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if service_filter:
            meta = item.get("metadata")
            name = str(meta.get("name") or "") if isinstance(meta, dict) else ""
            if not name.startswith(f"{service_filter}-"):
                continue
        result.append(item)
    return result


def _parse_argo_app_name(app_name: str) -> tuple[str, str] | None:
    """Split '<service_id>-<env>' into (service_id, env).

    Handles multi-hyphen service IDs by matching only the known environment
    suffixes (-dev, -prod).
    """
    for suffix in ("-prod", "-dev"):
        if app_name.endswith(suffix):
            return (app_name[: -len(suffix)], suffix[1:])
    return None


# ---------------------------------------------------------------------------
# Record builders
# ---------------------------------------------------------------------------


def _build_git_pr_record(
    pr: dict[str, object],
    *,
    service_id: str,
    env: str,
    action: str,
    target_image: str,
    source_commit_sha: str | None,
    owner: str,
    gitops_repo: str,
    backfilled_at: str,
) -> dict[str, Any]:
    pr_number = int(pr["number"])  # type: ignore[arg-type]
    pr_url = str(pr["html_url"])
    created_at_dt = _parse_datetime(pr.get("created_at")) or datetime.now(tz=timezone.utc)
    merged_at_dt = _parse_datetime(pr.get("merged_at"))
    closed_at_dt = _parse_datetime(pr.get("closed_at"))
    state = str(pr.get("state") or "").strip().lower()

    user = pr.get("user")
    requested_by: str | None = (
        str(user["login"]) if isinstance(user, dict) and isinstance(user.get("login"), str) else None
    )
    head = pr.get("head")
    git_ref: str | None = (
        str(head["ref"]) if isinstance(head, dict) and isinstance(head.get("ref"), str) else None
    )
    merge_sha: str | None = (
        str(pr["merge_commit_sha"]) if isinstance(pr.get("merge_commit_sha"), str) else None
    )
    deploy_reason = (
        _extract_requested_reason_from_body(pr.get("body"))
        or f"GitOps {action} via PR #{pr_number}"
    )

    if merged_at_dt is not None:
        status = "live"
        result = "success"
        result_reason: str | None = None
        finished_at = merged_at_dt
    else:
        status = "failed"
        result = "failure"
        result_reason = "GitOps pull request was closed without merge."
        finished_at = closed_at_dt

    # Confidence: merged PRs are medium (cannot re-verify the rollout happened);
    # closed-without-merge is high (failure is certain).
    confidence = "high" if status == "failed" else "medium"

    return {
        "service_id": service_id,
        "env": env,
        "action": action,
        "status": status,
        "requested_by": requested_by,
        "requested_at": created_at_dt,
        "pr_url": pr_url,
        "pr_number": pr_number,
        "merge_sha": merge_sha,
        "target_image": target_image,
        "git_ref": git_ref,
        "argo_app": f"{service_id}-{env}",
        "started_at": merged_at_dt,
        "finished_at": _serialize_datetime(finished_at),
        "deploy_window_start": _serialize_datetime(merged_at_dt or created_at_dt),
        "deploy_window_end": _serialize_datetime(finished_at),
        "deploy_reason": deploy_reason,
        "compare_url": f"{pr_url}/files",
        "request_key": f"gitops-pr:{pr_number}:{service_id}:{env}:{action}",
        "result": result,
        "result_reason": result_reason,
        "metadata": {
            "source": "git_pr",
            "confidence": confidence,
            "backfilled": True,
            "backfilledAt": backfilled_at,
            "backfillScriptVersion": BACKFILL_SCRIPT_VERSION,
            "gitopsRepo": f"{owner}/{gitops_repo}",
            "pullRequestState": state,
            "pullRequestHeadRef": git_ref,
            "pullRequestMergedAt": _serialize_datetime(merged_at_dt),
            "pullRequestClosedAt": _serialize_datetime(closed_at_dt),
            "sourceCommitSha": source_commit_sha,
        },
    }


def _build_argo_history_record(
    app_name: str,
    service_id: str,
    env: str,
    history_entry: dict[str, object],
    backfilled_at: str,
) -> dict[str, Any]:
    history_id = history_entry.get("id")
    revision = str(history_entry.get("revision") or "") or None
    deployed_at_dt = _parse_datetime(history_entry.get("deployedAt"))
    deploy_started_at_dt = _parse_datetime(history_entry.get("deployStartedAt"))
    source = history_entry.get("source")
    source_path: str | None = (
        str(source["path"]) if isinstance(source, dict) and isinstance(source.get("path"), str) else None
    )
    timestamp = deployed_at_dt or deploy_started_at_dt or datetime.now(tz=timezone.utc)

    return {
        "service_id": service_id,
        "env": env,
        "action": "deploy",
        "status": "live",
        "result": "success",
        "requested_at": timestamp,
        "argo_app": app_name,
        "merge_sha": revision,
        "git_ref": revision,
        "started_at": _serialize_datetime(deploy_started_at_dt),
        "finished_at": _serialize_datetime(deployed_at_dt),
        "deploy_window_start": _serialize_datetime(deploy_started_at_dt or deployed_at_dt),
        "deploy_window_end": _serialize_datetime(deployed_at_dt),
        "deploy_reason": f"Argo CD sync #{history_id} (backfilled from app history)",
        "request_key": f"backfill:argo-history:{app_name}:{history_id}",
        "metadata": {
            "source": "argo_history",
            "confidence": "high",
            "backfilled": True,
            "backfilledAt": backfilled_at,
            "backfillScriptVersion": BACKFILL_SCRIPT_VERSION,
            "argoApp": app_name,
            "argoHistoryId": history_id,
            "argoRevision": revision,
            "argoSourcePath": source_path,
        },
    }


# ---------------------------------------------------------------------------
# Backfill runners
# ---------------------------------------------------------------------------


def _backfill_from_git_prs(
    conn: psycopg.Connection,
    *,
    dry_run: bool,
    service_filter: str | None,
    max_pages: int,
    backfilled_at: str,
) -> tuple[int, int, int]:
    """Return (events_found, records_created, records_skipped)."""
    owner = (
        os.getenv("DEPLOYMENT_RECONCILER_GITHUB_OWNER", DEFAULT_GITHUB_OWNER).strip()
        or DEFAULT_GITHUB_OWNER
    )
    gitops_repo = (
        os.getenv("DEPLOYMENT_RECONCILER_GITOPS_REPO", DEFAULT_GITOPS_REPO).strip()
        or DEFAULT_GITOPS_REPO
    )
    logger.info(
        "git_pr: fetching up to %d page(s) of PRs from %s/%s",
        max_pages,
        owner,
        gitops_repo,
    )
    prs = _fetch_all_gitops_prs(max_pages=max_pages)
    logger.info("git_pr: fetched %d PRs", len(prs))

    events_found = records_created = records_skipped = 0

    for pr in prs:
        context = _action_context_from_pull_request(pr)
        if context is None:
            continue
        pr_env, action, source_commit_sha = context

        images = _extract_images_from_body(pr.get("body"))
        if not images:
            continue

        for current_service_id, image_ref in (
            ("homelab-api", images.get("homelab-api")),
            ("homelab-web", images.get("homelab-web")),
        ):
            if not image_ref:
                continue
            if service_filter and current_service_id != service_filter:
                continue

            events_found += 1
            pr_number = int(pr["number"])  # type: ignore[arg-type]
            request_key = f"gitops-pr:{pr_number}:{current_service_id}:{pr_env}:{action}"

            if get_deployment_record_by_request_key(conn, request_key) is not None:
                records_skipped += 1
                logger.debug("git_pr: skip existing key=%s", request_key)
                continue

            payload = _build_git_pr_record(
                pr,
                service_id=current_service_id,
                env=pr_env,
                action=action,
                target_image=str(image_ref),
                source_commit_sha=source_commit_sha,
                owner=owner,
                gitops_repo=gitops_repo,
                backfilled_at=backfilled_at,
            )
            logger.info(
                "git_pr: %s key=%s service=%s env=%s action=%s merged=%s",
                "DRY-RUN" if dry_run else "INSERT",
                request_key,
                current_service_id,
                pr_env,
                action,
                pr.get("merged_at"),
            )
            if not dry_run:
                upsert_deployment_record(conn, **payload)
            records_created += 1

    if not dry_run:
        conn.commit()

    return events_found, records_created, records_skipped


def _backfill_from_argo_history(
    conn: psycopg.Connection,
    *,
    dry_run: bool,
    service_filter: str | None,
    backfilled_at: str,
) -> tuple[int, int, int]:
    """Return (events_found, records_created, records_skipped)."""
    if not _argo_cd_url():
        logger.warning("argo_history: ARGO_CD_URL not set — skipping Argo source")
        return 0, 0, 0

    apps = _list_argo_apps(service_filter)
    logger.info("argo_history: %d app(s) to scan", len(apps))

    events_found = records_created = records_skipped = 0

    for app in apps:
        meta = app.get("metadata")
        app_name = str(meta.get("name") or "") if isinstance(meta, dict) else ""
        if not app_name:
            continue

        parsed = _parse_argo_app_name(app_name)
        if parsed is None:
            logger.debug("argo_history: cannot parse app name %r — skipping", app_name)
            continue
        service_id, env = parsed

        status_block = app.get("status")
        history = status_block.get("history") if isinstance(status_block, dict) else None
        if not isinstance(history, list) or not history:
            logger.debug("argo_history: app=%s has no history", app_name)
            continue

        for entry in history:
            if not isinstance(entry, dict):
                continue
            events_found += 1
            history_id = entry.get("id")
            request_key = f"backfill:argo-history:{app_name}:{history_id}"

            if get_deployment_record_by_request_key(conn, request_key) is not None:
                records_skipped += 1
                logger.debug("argo_history: skip existing key=%s", request_key)
                continue

            payload = _build_argo_history_record(
                app_name, service_id, env, entry, backfilled_at
            )
            logger.info(
                "argo_history: %s key=%s service=%s env=%s deployedAt=%s",
                "DRY-RUN" if dry_run else "INSERT",
                request_key,
                service_id,
                env,
                entry.get("deployedAt"),
            )
            if not dry_run:
                upsert_deployment_record(conn, **payload)
            records_created += 1

    if not dry_run:
        conn.commit()

    return events_found, records_created, records_skipped


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _run(
    *,
    dry_run: bool,
    service_filter: str | None,
    source: str,
    max_pages: int,
) -> None:
    backfilled_at = datetime.now(tz=timezone.utc).isoformat()
    conn_str = get_psycopg_database_url()

    with psycopg.connect(conn_str) as conn:
        git_pr_events = git_pr_created = git_pr_skipped = 0
        argo_events = argo_created = argo_skipped = 0

        if source in ("git_pr", "all"):
            git_pr_events, git_pr_created, git_pr_skipped = _backfill_from_git_prs(
                conn,
                dry_run=dry_run,
                service_filter=service_filter,
                max_pages=max_pages,
                backfilled_at=backfilled_at,
            )

        if source in ("argo_history", "all"):
            argo_events, argo_created, argo_skipped = _backfill_from_argo_history(
                conn,
                dry_run=dry_run,
                service_filter=service_filter,
                backfilled_at=backfilled_at,
            )

    verb = "would create" if dry_run else "created"
    print(f"\n{'DRY RUN — ' if dry_run else ''}Backfill complete")
    print(
        f"  git_pr:       {git_pr_events:>4} events | "
        f"{git_pr_created:>4} {verb} | {git_pr_skipped:>4} skipped (existing)"
    )
    print(
        f"  argo_history: {argo_events:>4} events | "
        f"{argo_created:>4} {verb} | {argo_skipped:>4} skipped (existing)"
    )
    print(f"  Total new:    {git_pr_created + argo_created:>4}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without writing to the database.",
    )
    parser.add_argument(
        "--service",
        default="",
        help="Limit backfill to a single service ID (e.g. homelab-api).",
    )
    parser.add_argument(
        "--source",
        choices=("git_pr", "argo_history", "all"),
        default="all",
        help="Evidence source to use (default: all).",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=DEFAULT_MAX_GITHUB_PAGES,
        help=(
            f"Maximum GitHub PR pages to fetch (100 PRs per page, "
            f"default {DEFAULT_MAX_GITHUB_PAGES} = last 1000 PRs)."
        ),
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _run(
        dry_run=args.dry_run,
        service_filter=args.service.strip() or None,
        source=args.source,
        max_pages=args.max_pages,
    )


if __name__ == "__main__":
    main()
