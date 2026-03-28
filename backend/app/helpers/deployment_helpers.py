import json
from datetime import datetime, timedelta, timezone
import logging
import math
import os
import re
from typing import Literal
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest
from uuid import uuid4

import psycopg
from fastapi import status

from app.db import get_psycopg_database_url
from app.deployment_records import (
    get_deployment_record,
    list_deployment_records_for_service,
    store_observability_snapshot,
)
from app.deployment_locks import (
    DeploymentLockRow,
    cleanup_stale_deployment_locks,
    get_deployment_lock,
)
from app.health_timeline import now_utc
from app.lib import GitProvider
from app.monitoring_providers import (
    get_monitoring_timeout_seconds,
    get_prometheus_base_url,
    load_json_from_provider,
    raise_provider_bad_payload_error,
)
from app.observability_config import (
    escape_promql_regex_literal,
    load_observability_config,
    parse_duration_token,
    render_query_template,
)
from app.release_traceability import (
    build_release_traceability_rows,
    compute_is_drifted,
    load_argo_metadata_rows,
    load_ci_metadata_rows,
)
from app.runtime_config import (
    BRANCH_SAFE_FRAGMENT_RE,
    DEFAULT_PORTAL_IMAGES_LOOKBACK,
    SHA_IMAGE_TAG_RE,
    dev_deploy_target as _dev_deploy_target,
    ghcr_token as _ghcr_token,
    github_api_base_url as _github_api_base_url,
    github_api_token_for_path as _github_api_token_for_path,
    portal_images_workflow_file as _portal_images_workflow_file,
    portal_images_workflow_ref as _portal_images_workflow_ref,
    portal_repo_slug as _portal_repo_slug,
    promote_to_prod_target as _promote_to_prod_target,
    rollback_target as _rollback_target,
)
from app.api.schemas.deployments import ROLLBACK_TAG_RE
from app.service_registry_sync import _kube_get_json
from app.api.schemas.catalog import DeploymentLockResponse
from app.api.schemas.deployments import DeploymentRecordResponse

logger = logging.getLogger("homelab.backend.monitoring")

# Set by app.main after cache creation. Never None during normal operation.
_deployment_history_cache = None


class PortalDeployToDevError(Exception):
    def __init__(self, message: str, *, status_code: int = status.HTTP_502_BAD_GATEWAY):
        super().__init__(message)
        self.status_code = status_code


class PortalPromoteToProdError(Exception):
    def __init__(self, message: str, *, status_code: int = status.HTTP_502_BAD_GATEWAY):
        super().__init__(message)
        self.status_code = status_code


class PortalServiceRollbackError(Exception):
    def __init__(self, message: str, *, status_code: int = status.HTTP_502_BAD_GATEWAY):
        super().__init__(message)
        self.status_code = status_code


def _with_connection() -> psycopg.Connection:
    return psycopg.connect(get_psycopg_database_url())


from app.helpers.catalog_helpers import (  # noqa: E402
    _load_project_rows,
    _load_service_rows,
)


def _list_deployment_records_for_service(
    service_id: str,
    env: str | None = None,
    limit: int = 20,
) -> list[dict[str, object]]:
    with _with_connection() as conn:
        return list_deployment_records_for_service(
            conn,
            service_id=service_id,
            env=env,
            limit=limit,
        )


def _get_deployment_record_by_id(deployment_id: str) -> dict[str, object] | None:
    with _with_connection() as conn:
        return get_deployment_record(conn, deployment_id)


def _get_active_deployment_lock(
    service_id: str,
    env: str,
) -> DeploymentLockRow | None:
    with _with_connection() as conn:
        cleanup_stale_deployment_locks(conn, service_id=service_id, env=env)
        return get_deployment_lock(conn, service_id=service_id, env=env)


def _extract_version_from_image_ref(image_ref: str | None) -> str | None:
    if not image_ref:
        return None
    trimmed = image_ref.strip()
    if not trimmed:
        return None

    last_slash = trimmed.rfind("/")
    last_colon = trimmed.rfind(":")
    if last_colon > last_slash:
        return trimmed[last_colon + 1 :] or trimmed
    return trimmed


def _github_api_json(path: str, *, timeout_seconds: float = 10.0) -> object:
    request = urlrequest.Request(
        f"{_github_api_base_url()}/{path.lstrip('/')}",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "homelab-portal-backend",
        },
    )
    token = _github_api_token_for_path(path)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urlrequest.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read()
    except urlerror.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace").strip()
        message = body or exc.reason or "GitHub API request failed"
        raise PortalDeployToDevError(message, status_code=status.HTTP_502_BAD_GATEWAY) from exc
    except urlerror.URLError as exc:
        raise PortalDeployToDevError(
            f"GitHub API request failed: {exc.reason}",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc

    if not raw:
        return {}
    return json.loads(raw)


def _build_service_image_ref(service_id: str, tag: str) -> str:
    target = _dev_deploy_target(service_id)
    image_repo = str(target["image_repo"])
    return f"{image_repo}:{tag}"


def _build_prod_service_image_ref(service_id: str, tag: str) -> str:
    target = _promote_to_prod_target(service_id)
    image_repo = str(target["image_repo"])
    return f"{image_repo}:{tag}"


def _extract_sha_from_tag(tag: str | None) -> str | None:
    if not isinstance(tag, str):
        return None
    match = SHA_IMAGE_TAG_RE.fullmatch(tag.strip())
    if match is None:
        return None
    return match.group(1)


def _build_compare_url_for_portal_tags(previous_tag: str | None, new_tag: str | None) -> str | None:
    previous_sha = _extract_sha_from_tag(previous_tag)
    new_sha = _extract_sha_from_tag(new_tag)
    if not previous_sha or not new_sha or previous_sha == new_sha:
        return None
    return f"https://github.com/{_portal_repo_slug()}/compare/{previous_sha}...{new_sha}"


def _build_commit_url(commit_sha: str | None) -> str | None:
    if not isinstance(commit_sha, str) or not commit_sha.strip():
        return None
    return f"https://github.com/{_portal_repo_slug()}/commit/{commit_sha.strip()}"


def _parse_ghcr_image_repo(image_repo: str) -> tuple[str, str]:
    trimmed = image_repo.strip()
    if not trimmed.startswith("ghcr.io/"):
        raise PortalPromoteToProdError(
            f"Image repository {image_repo!r} is not a valid GHCR image reference.",
            status_code=status.HTTP_409_CONFLICT,
        )
    parts = trimmed.split("/")
    if len(parts) != 3 or not parts[1] or not parts[2]:
        raise PortalPromoteToProdError(
            f"Image repository {image_repo!r} is not a valid GHCR image reference.",
            status_code=status.HTTP_409_CONFLICT,
        )
    return parts[1], parts[2]


def _build_package_url_from_image_ref(image_ref: str | None) -> str | None:
    if not isinstance(image_ref, str) or not image_ref.strip():
        return None
    repo = image_ref.strip().split("@", 1)[0]
    repo = repo.rsplit(":", 1)[0]
    try:
        owner, package_name = _parse_ghcr_image_repo(repo)
    except PortalPromoteToProdError:
        return None
    encoded_owner = urlparse.quote(owner, safe="")
    encoded_package = urlparse.quote(package_name, safe="")
    return f"https://github.com/users/{encoded_owner}/packages/container/{encoded_package}"


def _extract_image_digest(record: dict[str, object]) -> str | None:
    image_ref = record.get("imageRef")
    if isinstance(image_ref, str) and "@" in image_ref:
        return image_ref.split("@", 1)[1].strip() or None
    metadata = record.get("metadata")
    if isinstance(metadata, dict):
        value = metadata.get("imageDigest")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _deployment_record_timestamp(record: dict[str, object]) -> str | None:
    for key in ("deployWindowEnd", "finishedAt", "deployedAt", "startedAt", "requestedAt"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _select_latest_deployment_info_record(
    records: list[dict[str, object]],
) -> dict[str, object] | None:
    if not records:
        return None

    ordered = sorted(
        records,
        key=lambda record: _deployment_record_timestamp(record) or "",
        reverse=True,
    )
    for record in ordered:
        if str(record.get("status") or "").strip().lower() == "live":
            return record
    return ordered[0]


def _github_package_version_paths(image_repo: str, *, page: int) -> list[str]:
    owner, package_name = _parse_ghcr_image_repo(image_repo)
    encoded_owner = urlparse.quote(owner, safe="")
    encoded_package = urlparse.quote(package_name, safe="")
    query = f"packages/container/{encoded_package}/versions?per_page=100&page={page}"
    return [
        f"users/{encoded_owner}/{query}",
        f"orgs/{encoded_owner}/{query}",
    ]


def _package_version_has_tag(payload: object, expected_tag: str) -> bool:
    if not isinstance(payload, list):
        return False
    for item in payload:
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata")
        if not isinstance(metadata, dict):
            continue
        container = metadata.get("container")
        if not isinstance(container, dict):
            continue
        tags = container.get("tags")
        if isinstance(tags, list) and expected_tag in tags:
            return True
    return False


def _ensure_ghcr_tag_exists(
    image_repo: str,
    tag: str,
    *,
    purpose: str = "Requested image tag",
    timeout_seconds: float = 10.0,
) -> None:
    token = _ghcr_token()
    github_api_base = _github_api_base_url()
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "homelab-portal-backend",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    for page in range(1, 11):
        found_on_page = False
        exhausted = True
        for path in _github_package_version_paths(image_repo, page=page):
            request = urlrequest.Request(
                f"{github_api_base}/{path.lstrip('/')}",
                headers=headers,
            )
            try:
                with urlrequest.urlopen(request, timeout=timeout_seconds) as response:
                    raw = response.read()
            except urlerror.HTTPError as exc:
                if exc.code == 404:
                    continue
                body = exc.read().decode("utf-8", errors="replace").strip()
                message = body or exc.reason or "GitHub Packages lookup failed"
                raise PortalPromoteToProdError(message, status_code=status.HTTP_502_BAD_GATEWAY) from exc
            except urlerror.URLError as exc:
                raise PortalPromoteToProdError(
                    f"GitHub Packages lookup failed: {exc.reason}",
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                ) from exc

            payload = json.loads(raw) if raw else []
            if isinstance(payload, list) and payload:
                exhausted = False
            if _package_version_has_tag(payload, tag):
                found_on_page = True
                break
        if found_on_page:
            return
        if exhausted:
            break

    raise PortalPromoteToProdError(
        f"{purpose} {tag!r} was not found in GitHub Packages for {image_repo}.",
        status_code=status.HTTP_409_CONFLICT,
    )


def _safe_branch_fragment(value: str) -> str:
    normalized = BRANCH_SAFE_FRAGMENT_RE.sub("-", value.strip().lower()).strip(".-")
    return normalized or "deploy"


def _build_dev_deploy_branch_name(service_id: str, tag: str, requested_at: datetime) -> str:
    tag_fragment = tag
    if tag.startswith("sha-") and len(tag) > 20:
        tag_fragment = f"sha-{tag[4:16]}"
    return (
        f"automation/dev-deploy-{service_id}-"
        f"{_safe_branch_fragment(tag_fragment)}-"
        f"{requested_at.strftime('%Y%m%d%H%M%S')}"
    )


def _build_prod_promote_branch_name(service_id: str, tag: str, requested_at: datetime) -> str:
    tag_fragment = tag
    if tag.startswith("sha-") and len(tag) > 20:
        tag_fragment = f"sha-{tag[4:16]}"
    return (
        f"automation/prod-promote-{service_id}-"
        f"{_safe_branch_fragment(tag_fragment)}-"
        f"{requested_at.strftime('%Y%m%d%H%M%S')}"
    )


def _build_service_rollback_branch_name(
    service_id: str,
    target_environment: str,
    tag: str,
    requested_at: datetime,
) -> str:
    tag_fragment = tag
    if tag.startswith("sha-") and len(tag) > 20:
        tag_fragment = f"sha-{tag[4:16]}"
    return (
        f"automation/{target_environment}-rollback-{service_id}-"
        f"{_safe_branch_fragment(tag_fragment)}-"
        f"{requested_at.strftime('%Y%m%d%H%M%S')}"
    )


def _extract_image_ref_from_overlay(
    content: str,
    *,
    image_repo: str,
    file_path: str,
) -> str:
    pattern = re.compile(rf"(?m)^\s*image:\s*({re.escape(image_repo)}:[^\s#]+)")
    match = pattern.search(content)
    if match is None:
        raise PortalDeployToDevError(
            f"GitOps overlay file {file_path} does not contain an image ref for {image_repo}.",
            status_code=status.HTTP_409_CONFLICT,
        )
    return match.group(1)


def _replace_image_ref_in_overlay(
    content: str,
    *,
    image_repo: str,
    new_image_ref: str,
    file_path: str,
) -> str:
    pattern = re.compile(rf"(?m)^(\s*image:\s*){re.escape(image_repo)}:[^\s#]+(\s*(?:#.*)?)$")

    def _replace(match: re.Match[str]) -> str:
        trailing = match.group(2) or ""
        return f"{match.group(1)}{new_image_ref}{trailing}"

    updated, count = pattern.subn(_replace, content)
    if count == 0:
        raise PortalDeployToDevError(
            f"GitOps overlay file {file_path} does not contain a replaceable image ref for {image_repo}.",
            status_code=status.HTTP_409_CONFLICT,
        )
    return updated


def _resolve_latest_portal_image_candidate(service_id: str) -> dict[str, object]:
    repo_slug = _portal_repo_slug()
    workflow_file = _portal_images_workflow_file()
    branch = _portal_images_workflow_ref()
    payload = _github_api_json(
        f"repos/{repo_slug}/actions/workflows/{workflow_file}/runs"
        f"?branch={urlparse.quote(branch)}&event=push&status=completed&per_page={DEFAULT_PORTAL_IMAGES_LOOKBACK}"
    )
    workflow_runs = payload.get("workflow_runs") if isinstance(payload, dict) else None
    if not isinstance(workflow_runs, list):
        raise PortalDeployToDevError(
            "GitHub Actions did not return workflow run data for portal-images.yml.",
            status_code=status.HTTP_502_BAD_GATEWAY,
        )

    for run in workflow_runs:
        if not isinstance(run, dict):
            continue
        if str(run.get("conclusion") or "").strip().lower() != "success":
            continue
        head_sha = run.get("head_sha")
        if not isinstance(head_sha, str) or len(head_sha.strip()) != 40:
            continue
        normalized_sha = head_sha.strip()
        tag = f"sha-{normalized_sha}"
        return {
            "tag": tag,
            "imageRef": _build_service_image_ref(service_id, tag),
            "sourceCommitSha": normalized_sha,
            "workflowRunId": run.get("id") if isinstance(run.get("id"), int) else None,
            "workflowRunUrl": run.get("html_url") if isinstance(run.get("html_url"), str) else None,
        }

    raise PortalDeployToDevError(
        "No successful portal-images workflow run was found on the portal repository main branch.",
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


def _load_dev_overlay_update_plan(
    git_provider: GitProvider,
    *,
    service_id: str,
    repo_slug: str,
    branch: str,
    new_image_ref: str,
) -> tuple[str, str | None, dict[str, str]]:
    target = _dev_deploy_target(service_id)
    image_repo = str(target["image_repo"])
    patch_files = [str(path) for path in target["patch_files"]]
    previous_image_ref: str | None = None
    updated_files: dict[str, str] = {}

    for file_path in patch_files:
        current_content = git_provider.read_file(repo_slug, branch, file_path)
        file_image_ref = _extract_image_ref_from_overlay(
            current_content,
            image_repo=image_repo,
            file_path=file_path,
        )
        if previous_image_ref is None:
            previous_image_ref = file_image_ref
        elif previous_image_ref != file_image_ref:
            raise PortalDeployToDevError(
                f"GitOps dev overlay for {service_id} is inconsistent across image patch files.",
                status_code=status.HTTP_409_CONFLICT,
            )
        updated_files[file_path] = _replace_image_ref_in_overlay(
            current_content,
            image_repo=image_repo,
            new_image_ref=new_image_ref,
            file_path=file_path,
        )

    return image_repo, previous_image_ref, updated_files


def _load_promote_to_prod_update_plan(
    git_provider: GitProvider,
    *,
    service_id: str,
    repo_slug: str,
    branch: str,
) -> tuple[str, str | None, str, str | None, dict[str, str]]:
    target = _promote_to_prod_target(service_id)
    image_repo = str(target["image_repo"])
    source_file = str(target["source_file"])
    patch_files = [str(path) for path in target["patch_files"]]

    source_content = git_provider.read_file(repo_slug, branch, source_file)
    source_image_ref = _extract_image_ref_from_overlay(
        source_content,
        image_repo=image_repo,
        file_path=source_file,
    )
    new_tag = _extract_version_from_image_ref(source_image_ref)
    if not new_tag:
        raise PortalPromoteToProdError(
            f"GitOps dev overlay for {service_id} does not contain a deployable image tag.",
            status_code=status.HTTP_409_CONFLICT,
        )

    previous_image_ref: str | None = None
    updated_files: dict[str, str] = {}
    for file_path in patch_files:
        current_content = git_provider.read_file(repo_slug, branch, file_path)
        file_image_ref = _extract_image_ref_from_overlay(
            current_content,
            image_repo=image_repo,
            file_path=file_path,
        )
        if previous_image_ref is None:
            previous_image_ref = file_image_ref
        elif previous_image_ref != file_image_ref:
            raise PortalPromoteToProdError(
                f"GitOps prod overlay for {service_id} is inconsistent across image patch files.",
                status_code=status.HTTP_409_CONFLICT,
            )
        updated_files[file_path] = _replace_image_ref_in_overlay(
            current_content,
            image_repo=image_repo,
            new_image_ref=source_image_ref,
            file_path=file_path,
        )

    return image_repo, previous_image_ref, source_image_ref, new_tag, updated_files


def _load_service_rollback_update_plan(
    git_provider: GitProvider,
    *,
    service_id: str,
    repo_slug: str,
    branch: str,
    target_environment: str,
    rollback_tag: str,
) -> tuple[str, str | None, str, dict[str, str]]:
    target = _rollback_target(service_id, target_environment)
    image_repo = str(target["image_repo"])
    patch_files = [str(path) for path in target["patch_files"]]
    rollback_image_ref = f"{image_repo}:{rollback_tag}"

    previous_image_ref: str | None = None
    updated_files: dict[str, str] = {}
    for file_path in patch_files:
        current_content = git_provider.read_file(repo_slug, branch, file_path)
        file_image_ref = _extract_image_ref_from_overlay(
            current_content,
            image_repo=image_repo,
            file_path=file_path,
        )
        if previous_image_ref is None:
            previous_image_ref = file_image_ref
        elif previous_image_ref != file_image_ref:
            raise PortalServiceRollbackError(
                f"GitOps {target_environment} overlay for {service_id} is inconsistent across image patch files.",
                status_code=status.HTTP_409_CONFLICT,
            )
        updated_files[file_path] = _replace_image_ref_in_overlay(
            current_content,
            image_repo=image_repo,
            new_image_ref=rollback_image_ref,
            file_path=file_path,
        )

    return image_repo, previous_image_ref, rollback_image_ref, updated_files


def _list_service_rollback_candidates(
    *,
    image_repo: str,
    current_tag: str | None,
    limit: int = 5,
) -> list[dict[str, object]]:
    excluded_tags = {current_tag} if isinstance(current_tag, str) and current_tag else set()
    seen: set[str] = set()
    candidates: list[dict[str, object]] = []

    for page in range(1, 6):
        exhausted = True
        for path in _github_package_version_paths(image_repo, page=page):
            payload = _github_api_json(path)
            if isinstance(payload, list) and payload:
                exhausted = False
            if not isinstance(payload, list):
                continue
            for item in payload:
                if not isinstance(item, dict):
                    continue
                metadata = item.get("metadata")
                container = metadata.get("container") if isinstance(metadata, dict) else None
                tags = container.get("tags") if isinstance(container, dict) else None
                if not isinstance(tags, list):
                    continue
                published_at = item.get("created_at") if isinstance(item.get("created_at"), str) else None
                for raw_tag in tags:
                    if not isinstance(raw_tag, str):
                        continue
                    tag = raw_tag.strip()
                    if not tag or tag in excluded_tags or tag in seen:
                        continue
                    if not ROLLBACK_TAG_RE.fullmatch(tag):
                        continue
                    seen.add(tag)
                    candidates.append(
                        {
                            "tag": tag,
                            "imageRef": f"{image_repo}:{tag}",
                            "compareUrl": _build_compare_url_for_portal_tags(current_tag, tag),
                            "sourceCommitSha": _extract_sha_from_tag(tag),
                            "publishedAt": published_at,
                        }
                    )
                    if len(candidates) >= limit:
                        return candidates
        if exhausted:
            break

    return candidates


def _build_dev_deploy_pr_body(
    *,
    service_id: str,
    requested_by: str,
    deploy_reason: str,
    previous_tag: str | None,
    new_tag: str,
    new_image_ref: str,
    compare_url: str | None,
    source_commit_sha: str | None,
    workflow_run_url: str | None,
) -> str:
    lines = [
        "Portal-requested dev deploy.",
        "",
        f"- Service: `{service_id}`",
        "- Environment: `dev`",
        f"- Requested by: `{requested_by}`",
        f"- Reason: {deploy_reason}",
        f"- Previous tag: `{previous_tag or 'unknown'}`",
        f"- Target tag: `{new_tag}`",
        f"- Target image: `{new_image_ref}`",
    ]
    if compare_url:
        lines.append(f"- Compare: {compare_url}")
    if source_commit_sha:
        lines.append(f"- Source commit: `{source_commit_sha}`")
    if workflow_run_url:
        lines.append(f"- Source workflow run: {workflow_run_url}")
    lines.extend(
        [
            "",
            "This pull request updates only the dev overlay image reference(s) for the selected service.",
        ]
    )
    return "\n".join(lines)


def _build_promote_to_prod_pr_body(
    *,
    service_id: str,
    requested_by: str,
    deploy_reason: str,
    previous_tag: str | None,
    new_tag: str,
    new_image_ref: str,
    compare_url: str | None,
) -> str:
    lines = [
        "Portal-requested promote-to-prod.",
        "",
        f"- Service: `{service_id}`",
        "- Source environment: `dev`",
        "- Target environment: `prod`",
        f"- Requested by: `{requested_by}`",
        f"- Reason: {deploy_reason}",
        f"- Previous prod tag: `{previous_tag or 'unknown'}`",
        f"- Promoted tag: `{new_tag}`",
        f"- Target image: `{new_image_ref}`",
    ]
    if compare_url:
        lines.append(f"- Compare: {compare_url}")
    lines.extend(
        [
            "",
            "This pull request updates only the prod overlay image reference(s) for the selected service to match dev.",
        ]
    )
    return "\n".join(lines)


def _build_service_rollback_pr_body(
    *,
    service_id: str,
    target_environment: str,
    requested_by: str,
    deploy_reason: str,
    previous_tag: str | None,
    rollback_tag: str,
    rollback_image_ref: str,
    compare_url: str | None,
) -> str:
    lines = [
        "Portal-requested rollback.",
        "",
        f"- Service: `{service_id}`",
        f"- Target environment: `{target_environment}`",
        f"- Requested by: `{requested_by}`",
        f"- Reason: {deploy_reason}",
        f"- Current tag: `{previous_tag or 'unknown'}`",
        f"- Rollback tag: `{rollback_tag}`",
        f"- Target image: `{rollback_image_ref}`",
    ]
    if compare_url:
        lines.append(f"- Compare: {compare_url}")
    lines.extend(
        [
            "",
            "This pull request updates only the selected service image reference(s) for the chosen environment.",
        ]
    )
    return "\n".join(lines)


def _build_secret_edit_branch_name(
    service_id: str,
    env: str,
    secret_key: str,
    requested_at: datetime,
) -> str:
    return (
        f"automation/{env}-secret-{_safe_branch_fragment(service_id)}-"
        f"{_safe_branch_fragment(secret_key)}-{requested_at.strftime('%Y%m%d%H%M%S')}"
    )


def _build_config_edit_branch_name(
    service_id: str,
    env: str,
    config_key: str,
    requested_at: datetime,
) -> str:
    return (
        f"automation/{env}-config-{_safe_branch_fragment(service_id)}-"
        f"{_safe_branch_fragment(config_key)}-{requested_at.strftime('%Y%m%d%H%M%S')}"
    )


def _build_config_edit_pr_body(
    *,
    service_id: str,
    env: str,
    config_key: str,
    config_value: str,
    previous_value: str,
    requested_by: str,
    config_file_path: str,
) -> str:
    return "\n".join(
        [
            "Portal-requested config update.",
            "",
            f"- Service: `{service_id}`",
            f"- Environment: `{env}`",
            f"- Config key: `{config_key}`",
            f"- Previous value: `{previous_value or 'unset'}`",
            f"- New value: `{config_value}`",
            f"- Requested by: `{requested_by}`",
            f"- Config manifest: `{config_file_path}`",
            "",
            "This pull request updates only the selected ConfigMap-backed runtime setting.",
        ]
    )


def _build_secret_edit_pr_body(
    *,
    service_id: str,
    env: str,
    secret_key: str,
    requested_by: str,
    secret_file_path: str,
) -> str:
    return "\n".join(
        [
            "Portal-requested secret update.",
            "",
            f"- Service: `{service_id}`",
            f"- Environment: `{env}`",
            f"- Secret key: `{secret_key}`",
            f"- Requested by: `{requested_by}`",
            f"- Secret manifest: `{secret_file_path}`",
            "",
            "This pull request updates only the encrypted secret manifest for the selected service and environment.",
            "The secret value is intentionally not included in the pull request body.",
        ]
    )


def _select_preferred_service_row(
    service_id: str,
    rows: list[dict[str, str | None]],
    preferred_env: str | None,
) -> dict[str, str | None] | None:
    if not rows:
        return None

    effective_env = preferred_env or os.getenv("PORTAL_ENV", "dev")

    def _rank(row: dict[str, str | None]) -> tuple[int, int, int, str]:
        row_env = str(row.get("env") or "").strip()
        service_name = str(row.get("service_name") or "").strip()
        app_label = str(row.get("app_label") or "").strip()
        return (
            0 if row_env == effective_env else 1,
            0 if service_name == service_id or app_label == service_id else 1,
            1 if "postgres" in service_name.lower() else 0,
            service_name,
        )

    return sorted(rows, key=_rank)[0]


def _normalize_live_sync_status(value: object) -> str:
    if not isinstance(value, str):
        return "unknown"
    normalized = value.strip().lower().replace("-", "_")
    if normalized == "outofsync":
        normalized = "out_of_sync"
    if normalized in {"synced", "out_of_sync"}:
        return normalized
    return "unknown"


def _normalize_live_health_status(value: object) -> str:
    if not isinstance(value, str):
        return "unknown"
    normalized = value.strip().lower()
    if normalized == "healthy":
        return "healthy"
    if normalized in {"degraded", "progressing"}:
        return "degraded"
    return "unknown"


def _release_row_has_meaningful_metadata(row: dict) -> bool:
    for key in ("commitSha", "imageRef", "deployedAt"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return True

    argo = row.get("argo")
    if not isinstance(argo, dict):
        return False

    for key in ("syncStatus", "healthStatus", "revision", "liveRevision", "imageRef"):
        value = argo.get(key)
        if isinstance(value, str) and value.strip() and value.strip().lower() != "unknown":
            return True

    return False


def _coalesce_service_status(primary: object, fallback: object) -> str | None:
    primary_value = primary.strip() if isinstance(primary, str) else None
    fallback_value = fallback.strip() if isinstance(fallback, str) else None
    if primary_value and primary_value.lower() != "unknown":
        return primary_value
    if fallback_value:
        return fallback_value
    return primary_value or fallback_value


# Live-runtime helpers backfill service details when release traceability data
# is missing or stale. They query Kubernetes/Argo directly and return best-effort
# snapshots rather than failing the whole request path.
def _list_live_deployments_for_service(
    service_row: dict[str, str | None],
) -> list[dict[str, object]]:
    namespace = str(service_row.get("namespace") or "").strip()
    app_label = str(service_row.get("app_label") or "").strip()
    service_id = str(service_row.get("service_id") or "").strip()
    if not namespace or not app_label:
        return []

    try:
        payload = _kube_get_json(f"/apis/apps/v1/namespaces/{namespace}/deployments")
    except Exception as exc:  # pragma: no cover - live fallback only
        logger.warning(
            "service_runtime_deployments_unavailable namespace=%s service_id=%s error=%s",
            namespace,
            service_id,
            exc,
        )
        return []

    items = payload.get("items", [])
    if not isinstance(items, list):
        return []

    matched: list[dict[str, object]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata", {})
        if not isinstance(metadata, dict):
            continue
        labels = metadata.get("labels", {})
        if not isinstance(labels, dict):
            labels = {}
        deployment_name = str(metadata.get("name") or "").strip()
        deployment_app = str(
            labels.get("app.kubernetes.io/name")
            or labels.get("app")
            or ""
        ).strip()
        component = str(labels.get("app.kubernetes.io/component") or "").strip().lower()
        if component == "postgres":
            continue
        if deployment_name in {service_id, app_label} or deployment_app in {service_id, app_label}:
            matched.append(item)

    return sorted(
        matched,
        key=lambda item: str(item.get("metadata", {}).get("creationTimestamp") or ""),
        reverse=True,
    )


def _load_live_argo_status_for_service(
    service_row: dict[str, str | None],
) -> dict[str, str | None]:
    app_name = str(service_row.get("argo_app_name") or "").strip()
    if not app_name:
        return {}

    argo_namespace = os.getenv("ARGOCD_NAMESPACE", "argocd")
    try:
        payload = _kube_get_json(
            f"/apis/argoproj.io/v1alpha1/namespaces/{argo_namespace}/applications/{app_name}"
        )
    except Exception as exc:  # pragma: no cover - live fallback only
        logger.warning(
            "service_runtime_argo_unavailable app=%s namespace=%s error=%s",
            app_name,
            argo_namespace,
            exc,
        )
        return {}

    status_payload = payload.get("status", {})
    if not isinstance(status_payload, dict):
        status_payload = {}
    sync_payload = status_payload.get("sync", {})
    if not isinstance(sync_payload, dict):
        sync_payload = {}
    health_payload = status_payload.get("health", {})
    if not isinstance(health_payload, dict):
        health_payload = {}
    operation_state = status_payload.get("operationState", {})
    if not isinstance(operation_state, dict):
        operation_state = {}
    operation_message = operation_state.get("message")
    if not isinstance(operation_message, str):
        operation_message = None

    return {
        "appName": app_name,
        "syncStatus": _normalize_live_sync_status(sync_payload.get("status")),
        "healthStatus": _normalize_live_health_status(health_payload.get("status")),
        "revision": sync_payload.get("revision")
        if isinstance(sync_payload.get("revision"), str)
        else None,
        "deployedAt": operation_state.get("finishedAt")
        if isinstance(operation_state.get("finishedAt"), str)
        else status_payload.get("reconciledAt")
        if isinstance(status_payload.get("reconciledAt"), str)
        else None,
        "operationPhase": operation_state.get("phase")
        if isinstance(operation_state.get("phase"), str)
        else None,
        "operationMessage": operation_message,
    }


def _extract_live_deployment_image_ref(deployment: dict[str, object]) -> str | None:
    spec = deployment.get("spec", {})
    if not isinstance(spec, dict):
        return None
    template = spec.get("template", {})
    if not isinstance(template, dict):
        return None
    template_spec = template.get("spec", {})
    if not isinstance(template_spec, dict):
        return None
    containers = template_spec.get("containers", [])
    if not isinstance(containers, list):
        return None

    preferred: str | None = None
    fallback: str | None = None
    for container in containers:
        if not isinstance(container, dict):
            continue
        image = container.get("image")
        if not isinstance(image, str) or not image.strip():
            continue
        if fallback is None:
            fallback = image.strip()
        name = str(container.get("name") or "").strip()
        if name == "api":
            preferred = image.strip()
            break
    return preferred or fallback


def _extract_live_deployment_health(deployment: dict[str, object]) -> str:
    spec = deployment.get("spec", {})
    if not isinstance(spec, dict):
        spec = {}
    status_payload = deployment.get("status", {})
    if not isinstance(status_payload, dict):
        status_payload = {}

    desired = int(spec.get("replicas") or 0)
    ready = int(status_payload.get("readyReplicas") or 0)
    available = int(status_payload.get("availableReplicas") or 0)

    if desired > 0 and ready >= desired and available >= desired:
        return "healthy"
    if ready > 0 or available > 0:
        return "degraded"
    return "unknown"


def _extract_live_deployment_timestamp(deployment: dict[str, object]) -> str | None:
    status_payload = deployment.get("status", {})
    if not isinstance(status_payload, dict):
        status_payload = {}
    conditions = status_payload.get("conditions", [])
    if isinstance(conditions, list):
        for condition in conditions:
            if not isinstance(condition, dict):
                continue
            updated = condition.get("lastUpdateTime")
            if isinstance(updated, str) and updated.strip():
                return updated
            transitioned = condition.get("lastTransitionTime")
            if isinstance(transitioned, str) and transitioned.strip():
                return transitioned

    metadata = deployment.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    created_at = metadata.get("creationTimestamp")
    if isinstance(created_at, str) and created_at.strip():
        return created_at
    return None


# Normalize live deployment and Argo state into the same row shape used by
# release history so downstream detail/history endpoints can enrich records without
# branching on data source.
def _load_live_service_runtime_rows(
    service_row: dict[str, str | None],
) -> list[dict[str, object]]:
    deployments = _list_live_deployments_for_service(service_row)
    argo = _load_live_argo_status_for_service(service_row)
    service_id = str(service_row.get("service_id") or "").strip()
    env = str(service_row.get("env") or "").strip()

    rows: list[dict[str, object]] = []
    for deployment in deployments:
        deployment_health = _extract_live_deployment_health(deployment)
        rows.append(
            {
                "serviceId": service_id,
                "env": env,
                "commitSha": None,
                "imageRef": _extract_live_deployment_image_ref(deployment),
                "deployedAt": _extract_live_deployment_timestamp(deployment) or argo.get("deployedAt"),
                "argo": {
                    "appName": argo.get("appName"),
                    "syncStatus": argo.get("syncStatus"),
                    "healthStatus": _coalesce_service_status(
                        argo.get("healthStatus"),
                        deployment_health,
                    ),
                    "revision": argo.get("revision"),
                },
                "drift": {
                    "isDrifted": False,
                    "expectedRevision": None,
                    "liveRevision": argo.get("revision"),
                    "expectedImageRef": None,
                    "liveImageRef": _extract_live_deployment_image_ref(deployment),
                },
            }
        )

    if rows:
        return rows

    if argo:
        return [
            {
                "serviceId": service_id,
                "env": env,
                "commitSha": None,
                "imageRef": None,
                "deployedAt": argo.get("deployedAt"),
                "argo": {
                    "appName": argo.get("appName"),
                    "syncStatus": argo.get("syncStatus"),
                    "healthStatus": argo.get("healthStatus"),
                    "revision": argo.get("revision"),
                },
                "drift": {
                    "isDrifted": False,
                    "expectedRevision": None,
                    "liveRevision": argo.get("revision"),
                    "expectedImageRef": None,
                    "liveImageRef": None,
                },
            }
        ]

    return []


def _load_release_rows_for_service(service_id: str, env: str | None = None) -> list[dict]:
    preferred_env = env or os.getenv("PORTAL_ENV", "dev")
    rows = build_release_traceability_rows(
        project_rows=_load_project_rows(),
        ci_rows=load_ci_metadata_rows(),
        argo_rows=load_argo_metadata_rows(),
        env_filter=preferred_env,
        service_id_filter=service_id,
        limit=20,
    )
    if rows or env:
        return rows

    return build_release_traceability_rows(
        project_rows=_load_project_rows(),
        ci_rows=load_ci_metadata_rows(),
        argo_rows=load_argo_metadata_rows(),
        env_filter=None,
        service_id_filter=service_id,
        limit=20,
    )


def _sort_release_rows_by_deployed_at(rows: list[dict]) -> list[dict]:
    return sorted(
        rows,
        key=lambda row: str(row.get("deployedAt") or ""),
        reverse=True,
    )


def _coalesce_release_string(
    primary: object,
    fallback: object,
    *,
    ignore_unknown: bool = False,
) -> str | None:
    for value in (primary, fallback):
        if not isinstance(value, str):
            continue
        candidate = value.strip()
        if not candidate:
            continue
        if ignore_unknown and candidate.lower() == "unknown":
            continue
        return candidate
    return None


# Release rows come from CI/Argo traceability joins, but those sources can lag
# behind the actual cluster. This merge prefers explicit release metadata first, then
# fills gaps from the live runtime snapshot to keep the UI informative during drift.
def _enrich_release_row_with_live_runtime(
    row: dict[str, object],
    service_row: dict[str, str | None] | None,
) -> dict[str, object]:
    base_argo = row.get("argo") if isinstance(row.get("argo"), dict) else {}
    base_drift = row.get("drift") if isinstance(row.get("drift"), dict) else {}

    live_release: dict[str, object] = {}
    if service_row:
        live_rows = _sort_release_rows_by_deployed_at(_load_live_service_runtime_rows(service_row))
        live_release = next((item for item in live_rows if _release_row_has_meaningful_metadata(item)), {})

    live_argo = live_release.get("argo") if isinstance(live_release.get("argo"), dict) else {}
    live_drift = live_release.get("drift") if isinstance(live_release.get("drift"), dict) else {}

    revision = _coalesce_release_string(
        base_argo.get("revision"),
        _coalesce_release_string(
            live_argo.get("revision"),
            live_drift.get("liveRevision"),
            ignore_unknown=True,
        ),
        ignore_unknown=True,
    )
    commit_sha = _coalesce_release_string(row.get("commitSha"), revision, ignore_unknown=True)
    image_ref = _coalesce_release_string(
        row.get("imageRef"),
        live_release.get("imageRef"),
        ignore_unknown=True,
    )
    deployed_at = _coalesce_release_string(
        row.get("deployedAt"),
        live_release.get("deployedAt"),
        ignore_unknown=True,
    )
    app_name = _coalesce_release_string(
        base_argo.get("appName"),
        live_argo.get("appName"),
        ignore_unknown=True,
    ) or "unknown"
    sync_status = _coalesce_service_status(base_argo.get("syncStatus"), live_argo.get("syncStatus")) or "unknown"
    health_status = _coalesce_service_status(base_argo.get("healthStatus"), live_argo.get("healthStatus")) or "unknown"
    expected_revision = _coalesce_release_string(
        base_drift.get("expectedRevision"),
        live_drift.get("expectedRevision"),
        ignore_unknown=True,
    )
    live_revision = _coalesce_release_string(
        base_drift.get("liveRevision"),
        revision,
        ignore_unknown=True,
    )

    return {
        **row,
        "commitSha": commit_sha,
        "imageRef": image_ref,
        "deployedAt": deployed_at,
        "argo": {
            "appName": app_name,
            "syncStatus": sync_status,
            "healthStatus": health_status,
            "revision": revision,
        },
        "drift": {
            **base_drift,
            "isDrifted": bool(base_drift.get("isDrifted"))
            or compute_is_drifted(
                sync_status=sync_status,
                expected_revision=expected_revision,
                live_revision=live_revision,
                expected_image_ref=None,
                live_image_ref=image_ref,
            ),
            "expectedRevision": expected_revision,
            "liveRevision": live_revision,
        },
    }


# Batch enrichment resolves the best matching service row once, then overlays live
# runtime details per release row so the history endpoint can return consistent data
# for mixed environments and partially synced registry rows.
def _enrich_release_rows_with_live_runtime(
    rows: list[dict[str, object]],
    *,
    env: str | None,
) -> list[dict[str, object]]:
    if not rows:
        return rows

    service_rows = _load_service_rows(env=env)
    rows_by_key: dict[tuple[str, str], list[dict[str, str | None]]] = {}
    rows_by_id: dict[str, list[dict[str, str | None]]] = {}
    for service_row in service_rows:
        service_id = str(service_row.get("service_id") or "").strip()
        service_env = str(service_row.get("env") or "").strip()
        if not service_id:
            continue
        rows_by_id.setdefault(service_id, []).append(service_row)
        if service_env:
            rows_by_key.setdefault((service_id, service_env), []).append(service_row)

    enriched: list[dict[str, object]] = []
    for row in rows:
        service_id = str(row.get("serviceId") or "").strip()
        row_env = str(row.get("env") or "").strip()
        candidates = rows_by_key.get((service_id, row_env), [])
        if not candidates:
            candidates = rows_by_id.get(service_id, [])
        selected = _select_preferred_service_row(service_id, candidates, row_env)
        enriched.append(_enrich_release_row_with_live_runtime(row, selected))

    return enriched


def _registry_stale_after_minutes() -> int:
    raw = os.getenv("REGISTRY_STALE_AFTER_MINUTES", "30")
    try:
        value = int(raw)
    except ValueError:
        return 30
    return value if value > 0 else 30


def _registry_warning_after_minutes(stale_after_minutes: int) -> int:
    raw = os.getenv("REGISTRY_WARN_AFTER_MINUTES")
    if raw is not None:
        try:
            value = int(raw)
        except ValueError:
            value = 0
        if 0 < value < stale_after_minutes:
            return value

    default_warning = max(1, int(stale_after_minutes * 0.66))
    return min(default_warning, max(1, stale_after_minutes - 1))


def _deployment_history_cache_ttl_seconds() -> int:
    raw = os.getenv("OBS_DEPLOYMENT_HISTORY_CACHE_TTL_SECONDS", "60")
    try:
        value = int(raw)
    except ValueError:
        return 60
    if value < 0:
        return 60
    return min(value, 300)


# Observability range helpers normalize user/env configuration into bounded query
# windows so Prometheus/Loki requests stay stable even when deployment records are
# sparse or timestamps need padding.
def _deployment_comparison_window_token() -> str:
    raw = str(os.getenv("OBS_DEPLOYMENT_COMPARISON_WINDOW", "1h") or "").strip()
    if not raw:
        return "1h"
    try:
        parse_duration_token(raw)
    except ValueError:
        return "1h"
    return raw


def _parse_iso_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _build_metric_snapshot(before: float | None, after: float | None) -> dict[str, float] | None:
    snapshot: dict[str, float] = {}
    if before is not None:
        snapshot["before"] = round(before, 3)
    if after is not None:
        snapshot["after"] = round(after, 3)
    if before is not None and after is not None:
        snapshot["delta"] = round(after - before, 3)
    return snapshot or None


def _format_duration_token(value: timedelta) -> str:
    total_seconds = max(int(math.ceil(value.total_seconds())), 60)
    if total_seconds % 86_400 == 0:
        return f"{max(1, total_seconds // 86_400)}d"
    if total_seconds % 3_600 == 0:
        return f"{max(1, total_seconds // 3_600)}h"
    return f"{max(1, math.ceil(total_seconds / 60))}m"


def _resolve_window_end(start: datetime, end: datetime | None) -> datetime:
    effective_end = end or now_utc()
    if effective_end > now_utc():
        effective_end = now_utc()
    if effective_end <= start:
        effective_end = start + timedelta(minutes=1)
    return effective_end


def _expand_observability_query_window(
    start: datetime,
    end: datetime,
    *,
    minimum_window: timedelta = timedelta(minutes=10),
    padding: timedelta = timedelta(minutes=5),
) -> tuple[datetime, datetime]:
    effective_end = _resolve_window_end(start, end)
    if effective_end - start >= minimum_window:
        return start, effective_end
    return start - padding, effective_end + padding


def _resolve_record_window(
    record: dict[str, object],
) -> tuple[datetime | None, datetime | None]:
    start = (
        _parse_iso_datetime(record.get("deployWindowStart"))
        or _parse_iso_datetime(record.get("startedAt"))
        or _parse_iso_datetime(record.get("requestedAt"))
    )
    end = (
        _parse_iso_datetime(record.get("deployWindowEnd"))
        or _parse_iso_datetime(record.get("finishedAt"))
    )
    if start is None:
        return None, None
    return start, _resolve_window_end(start, end)


def _query_prometheus_comparison_snapshot(
    *,
    queries: tuple[str, ...],
    metric_name: str,
    start: datetime,
    end: datetime,
    step_seconds: int,
    correlation_id: str,
) -> dict[str, float] | None:
    for index, query in enumerate(queries):
        try:
            points = _query_prometheus_range(
                query,
                f"{metric_name}_{index}",
                start=start,
                end=end,
                step_seconds=step_seconds,
                correlation_id=correlation_id,
            )
        except Exception as exc:  # pragma: no cover - optional comparison only
            logger.warning(
                "deployment_history_metric_unavailable metric=%s correlation_id=%s query_index=%s error=%s",
                metric_name,
                correlation_id,
                index,
                exc,
            )
            continue
        if not points:
            continue

        ordered_values = [value for _timestamp, value in sorted(points.items())]
        if not ordered_values:
            continue
        before = ordered_values[0]
        after = ordered_values[-1] if len(ordered_values) > 1 else None
        snapshot = _build_metric_snapshot(before, after)
        if snapshot is not None:
            return snapshot
    return None


# Deployment metric comparisons are cached per exact service/window tuple because
# the same deployment details view often re-requests snapshots while operators page
# around related data. Missing namespace/app metadata short-circuits to no data.
def _load_metric_snapshots_for_window(
    service_row: dict[str, str | None] | None,
    *,
    window_start: datetime,
    window_end: datetime,
) -> dict[str, dict[str, float]]:
    if not service_row:
        return {}

    namespace = str(service_row.get("namespace") or "").strip()
    app_label = str(service_row.get("app_label") or "").strip()
    service_id = str(service_row.get("service_id") or "").strip()
    env = str(service_row.get("env") or "").strip()
    if not namespace or not app_label or not service_id:
        return {}

    comparison_window = window_end - window_start
    if comparison_window <= timedelta(0):
        return {}
    comparison_window_token = _format_duration_token(comparison_window)

    cache_key = (
        "service_deployment_metrics",
        service_id,
        env,
        namespace,
        app_label,
        window_start.isoformat(),
        window_end.isoformat(),
    )

    def _load() -> dict[str, dict[str, float]]:
        config = load_observability_config()
        queries = _build_service_metrics_queries(
            namespace=namespace,
            app_label=app_label,
            selected_range=comparison_window_token,
            config=config,
        )
        correlation_id = str(uuid4())
        step_seconds = max(60, int(comparison_window.total_seconds()))
        snapshots = {
            "errorRatePct": _query_prometheus_comparison_snapshot(
                queries=queries["errorRatePct"],
                metric_name="deployment_error_rate_pct",
                start=window_start,
                end=window_end,
                step_seconds=step_seconds,
                correlation_id=correlation_id,
            ),
            "p95LatencyMs": _query_prometheus_comparison_snapshot(
                queries=queries["p95LatencyMs"],
                metric_name="deployment_p95_latency_ms",
                start=window_start,
                end=window_end,
                step_seconds=step_seconds,
                correlation_id=correlation_id,
            ),
            "availabilityPct": _query_prometheus_comparison_snapshot(
                queries=queries["uptimePct"],
                metric_name="deployment_availability_pct",
                start=window_start,
                end=window_end,
                step_seconds=step_seconds,
                correlation_id=correlation_id,
            ),
        }
        return {
            key: value
            for key, value in snapshots.items()
            if value is not None
        }

    return _deployment_history_cache.get_or_set(
        key=cache_key,
        ttl_seconds=_deployment_history_cache_ttl_seconds(),
        loader=_load,
    )


def _load_deployment_metric_snapshots(
    service_row: dict[str, str | None] | None,
    release_row: dict[str, object],
) -> tuple[dict[str, dict[str, float]], Literal["live_query", "stored_snapshot", "none"]]:
    """Returns (snapshots, source).

    Priority:
    1. Live Prometheus query — used when the deploy window is within retention.
    2. Stored snapshot from metadata.observabilitySnapshot — used when Prometheus
       no longer has samples for the window (retention expired).
    3. Empty — no metrics available from either source.
    """
    window_start, window_end = _resolve_record_window(release_row)
    if window_start is not None and window_end is not None:
        live = _load_metric_snapshots_for_window(service_row, window_start=window_start, window_end=window_end)
        if live:
            return live, "live_query"

    metadata = release_row.get("metadata")
    stored = metadata.get("observabilitySnapshot") if isinstance(metadata, dict) else None
    if isinstance(stored, dict) and stored:
        return stored, "stored_snapshot"

    return {}, "none"


def _persist_observability_snapshot_safe(
    deployment_id: str,
    snapshots: dict[str, dict[str, float]],
) -> None:
    """Write observability snapshots into deployment metadata.

    Called lazily when live Prometheus data is available for a terminal
    deployment that has no snapshot yet.  Errors are swallowed so that a
    DB write failure never breaks the API response.
    """
    try:
        with _with_connection() as conn:
            store_observability_snapshot(conn, deployment_id, snapshots)
    except Exception:
        pass  # non-critical; snapshot will be captured on the next request


def _deployment_record_sort_timestamp(record: dict[str, object]) -> str | None:
    for key in ("finishedAt", "deployWindowStart", "startedAt", "requestedAt"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _build_deployment_record_response(
    record: dict[str, object],
    service_row: dict[str, str | None] | None,
) -> DeploymentRecordResponse:
    observed_at = _deployment_record_sort_timestamp(record)
    metric_snapshots, metrics_source = _load_deployment_metric_snapshots(service_row, record)
    image_ref = record.get("targetImage") if isinstance(record.get("targetImage"), str) else None
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else None
    failure_reason = (
        metadata.get("failureReason")
        if isinstance(metadata, dict) and isinstance(metadata.get("failureReason"), str)
        else None
    )

    # Lazily persist a snapshot the first time live Prometheus data is available
    # for a terminal deployment that has no stored snapshot yet.
    deployment_id = record.get("deploymentId")
    status = record.get("status")
    has_stored_snapshot = isinstance(metadata, dict) and isinstance(metadata.get("observabilitySnapshot"), dict)
    if (
        metrics_source == "live_query"
        and isinstance(deployment_id, str)
        and status in {"live", "failed"}
        and not has_stored_snapshot
        and metric_snapshots
    ):
        _persist_observability_snapshot_safe(deployment_id, metric_snapshots)

    return DeploymentRecordResponse(
        id=str(record.get("deploymentId") or ""),
        serviceId=str(record.get("serviceId") or ""),
        env=str(record.get("env") or ""),
        action=str(record.get("action") or ""),
        version=_extract_version_from_image_ref(image_ref),
        status=record.get("status") if isinstance(record.get("status"), str) else None,
        requestedAt=record.get("requestedAt") if isinstance(record.get("requestedAt"), str) else None,
        requestedBy=record.get("requestedBy") if isinstance(record.get("requestedBy"), str) else None,
        deployedAt=observed_at,
        commitSha=record.get("mergeSha") if isinstance(record.get("mergeSha"), str) else None,
        imageRef=image_ref,
        previousImageRef=(
            record.get("previousImage")
            if isinstance(record.get("previousImage"), str)
            else None
        ),
        gitRef=record.get("gitRef") if isinstance(record.get("gitRef"), str) else None,
        gitPrUrl=record.get("prUrl") if isinstance(record.get("prUrl"), str) else None,
        gitPrNumber=record.get("prNumber") if isinstance(record.get("prNumber"), int) else None,
        compareUrl=record.get("compareUrl") if isinstance(record.get("compareUrl"), str) else None,
        mergeSha=record.get("mergeSha") if isinstance(record.get("mergeSha"), str) else None,
        argoApp=record.get("argoApp") if isinstance(record.get("argoApp"), str) else None,
        syncStatus=record.get("syncStatus") if isinstance(record.get("syncStatus"), str) else None,
        healthStatus=(
            record.get("healthStatus")
            if isinstance(record.get("healthStatus"), str)
            else None
        ),
        deployReason=(
            record.get("deployReason")
            if isinstance(record.get("deployReason"), str)
            else None
        ),
        startedAt=record.get("startedAt") if isinstance(record.get("startedAt"), str) else None,
        finishedAt=record.get("finishedAt") if isinstance(record.get("finishedAt"), str) else None,
        deployWindowStart=(
            record.get("deployWindowStart")
            if isinstance(record.get("deployWindowStart"), str)
            else None
        ),
        deployWindowEnd=(
            record.get("deployWindowEnd")
            if isinstance(record.get("deployWindowEnd"), str)
            else None
        ),
        failureReason=failure_reason,
        result=record.get("result") if isinstance(record.get("result"), str) else None,
        resultReason=record.get("resultReason") if isinstance(record.get("resultReason"), str) else None,
        errorRatePct=metric_snapshots.get("errorRatePct"),
        p95LatencyMs=metric_snapshots.get("p95LatencyMs"),
        availabilityPct=metric_snapshots.get("availabilityPct"),
        metricsSource=metrics_source,
        metadata=metadata,
    )


def _build_deployment_lock_response(lock_row: DeploymentLockRow | None) -> DeploymentLockResponse | None:
    if lock_row is None:
        return None
    return DeploymentLockResponse(
        serviceId=str(lock_row.get("serviceId") or ""),
        env=str(lock_row.get("env") or ""),
        deploymentId=str(lock_row.get("deploymentId") or ""),
        requestKey=str(lock_row.get("requestKey") or ""),
        action=str(lock_row.get("action") or ""),
        status=str(lock_row.get("status") or ""),
        argoApp=lock_row.get("argoApp") if isinstance(lock_row.get("argoApp"), str) else None,
        requestedBy=(
            lock_row.get("requestedBy")
            if isinstance(lock_row.get("requestedBy"), str)
            else None
        ),
        requestedAt=(
            lock_row.get("requestedAt")
            if isinstance(lock_row.get("requestedAt"), str)
            else None
        ),
        gitPrUrl=lock_row.get("prUrl") if isinstance(lock_row.get("prUrl"), str) else None,
        gitPrNumber=(
            lock_row.get("prNumber")
            if isinstance(lock_row.get("prNumber"), int)
            else None
        ),
        gitRef=lock_row.get("gitRef") if isinstance(lock_row.get("gitRef"), str) else None,
        deployReason=(
            lock_row.get("deployReason")
            if isinstance(lock_row.get("deployReason"), str)
            else None
        ),
        lockedAt=lock_row.get("lockedAt") if isinstance(lock_row.get("lockedAt"), str) else None,
        expiresAt=(
            lock_row.get("expiresAt")
            if isinstance(lock_row.get("expiresAt"), str)
            else None
        ),
        metadata=lock_row.get("metadata") if isinstance(lock_row.get("metadata"), dict) else None,
    )


def _query_prometheus_range(
    query: str,
    metric_name: str,
    *,
    start: datetime,
    end: datetime,
    step_seconds: int,
    correlation_id: str,
) -> dict[int, float]:
    encoded = urlparse.urlencode(
        {
            "query": query,
            "start": f"{start.timestamp():.3f}",
            "end": f"{end.timestamp():.3f}",
            "step": str(step_seconds),
        }
    )
    endpoint = f"{get_prometheus_base_url()}/api/v1/query_range?{encoded}"
    payload, _provider_status = load_json_from_provider(
        provider="prometheus",
        endpoint=endpoint,
        correlation_id=correlation_id,
        timeout_seconds=get_monitoring_timeout_seconds(),
        message="Monitoring provider query failed.",
    )

    if not isinstance(payload, dict) or payload.get("status") != "success":
        logger.error(
            "prometheus_range_bad_payload correlation_id=%s metric=%s payload_status=%s",
            correlation_id,
            metric_name,
            payload.get("status") if isinstance(payload, dict) else type(payload).__name__,
        )
        raise_provider_bad_payload_error(
            provider="prometheus",
            base_url=get_prometheus_base_url(),
            correlation_id=correlation_id,
            checked_at=datetime.now(tz=timezone.utc).isoformat(),
            error=(
                f"unexpected payload status="
                f"{payload.get('status') if isinstance(payload, dict) else type(payload).__name__}"
            ),
            message="Monitoring provider query failed.",
        )

    results = payload.get("data", {}).get("result", [])
    if not results:
        return {}

    # Use first series because query should be pre-aggregated.
    series_values = results[0].get("values")
    if not isinstance(series_values, list):
        return {}

    points: dict[int, float] = {}
    for sample in series_values:
        if (
            not isinstance(sample, list)
            or len(sample) < 2
            or not isinstance(sample[0], (int, float))
            or not isinstance(sample[1], str)
        ):
            continue
        try:
            value = float(sample[1])
        except ValueError:
            continue
        if not math.isfinite(value):
            continue
        points[int(sample[0])] = value
    return points


def _build_service_metrics_queries(
    *,
    namespace: str,
    app_label: str,
    selected_range: str,
    config,
) -> dict[str, tuple[str, ...]]:
    pod_pattern = escape_promql_regex_literal(app_label)
    ingress_service_pattern = f".*{escape_promql_regex_literal(app_label)}.*"
    deployment_name = app_label
    values = {
        "namespace": namespace,
        "app_label": app_label,
        "deployment_name": deployment_name,
        "selected_range": selected_range,
        "pod_pattern": pod_pattern,
        "ingress_service_pattern": ingress_service_pattern,
    }
    return {
        "uptimePct": (
            render_query_template(
                config.metrics_query_uptime_template,
                values,
                "metrics.uptime",
            ),
        ),
        "p95LatencyMs": (
            render_query_template(
                config.metrics_query_p95_latency_template,
                values,
                "metrics.p95_latency",
            ),
            render_query_template(
                config.metrics_query_p95_latency_fallback_template,
                values,
                "metrics.p95_latency_fallback",
            ),
        ),
        "errorRatePct": (
            render_query_template(
                config.metrics_query_error_rate_template,
                values,
                "metrics.error_rate",
            ),
            render_query_template(
                config.metrics_query_error_rate_fallback_template,
                values,
                "metrics.error_rate_fallback",
            ),
        ),
        "restartCount": (
            render_query_template(
                config.metrics_query_restart_count_template,
                values,
                "metrics.restart_count",
            ),
        ),
    }
