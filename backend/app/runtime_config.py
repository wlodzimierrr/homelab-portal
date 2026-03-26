"""Environment-driven runtime and composition helpers for the backend."""

from __future__ import annotations

import os
import re
from typing import Any

from fastapi import HTTPException, status


DEFAULT_GITHUB_OWNER = "wlodzimierrr"
DEFAULT_PORTAL_REPO = "homelab-portal"
DEFAULT_WORKLOADS_REPO = "homelab-workloads"
DEFAULT_PORTAL_IMAGES_WORKFLOW_FILE = "portal-images.yml"
DEFAULT_PORTAL_IMAGES_WORKFLOW_REF = "main"
DEFAULT_PORTAL_IMAGES_LOOKBACK = 20
SHA_IMAGE_TAG_RE = re.compile(r"^sha-([0-9a-f]{40})$")
BRANCH_SAFE_FRAGMENT_RE = re.compile(r"[^a-z0-9.-]+")

DEV_DEPLOY_TARGETS: dict[str, dict[str, object]] = {
    "homelab-api": {
        "image_repo": "ghcr.io/wlodzimierrr/homelab-api",
        "argo_app": "homelab-api-dev",
        "patch_files": [
            "apps/homelab-api/envs/dev/patch-deployment.yaml",
            "apps/homelab-api/envs/dev/patch-migration-job.yaml",
            "apps/homelab-api/envs/dev/patch-catalog-sync-cronjob.yaml",
        ],
    },
    "homelab-web": {
        "image_repo": "ghcr.io/wlodzimierrr/homelab-web",
        "argo_app": "homelab-web-dev",
        "patch_files": [
            "apps/homelab-web/envs/dev/patch-deployment.yaml",
        ],
    },
}

PROMOTE_TO_PROD_TARGETS: dict[str, dict[str, object]] = {
    "homelab-api": {
        "image_repo": "ghcr.io/wlodzimierrr/homelab-api",
        "source_file": "apps/homelab-api/envs/dev/patch-deployment.yaml",
        "argo_app": "homelab-api-prod",
        "patch_files": [
            "apps/homelab-api/envs/prod/patch-deployment.yaml",
            "apps/homelab-api/envs/prod/patch-migration-job.yaml",
            "apps/homelab-api/envs/prod/patch-catalog-sync-cronjob.yaml",
        ],
    },
    "homelab-web": {
        "image_repo": "ghcr.io/wlodzimierrr/homelab-web",
        "source_file": "apps/homelab-web/envs/dev/patch-deployment.yaml",
        "argo_app": "homelab-web-prod",
        "patch_files": [
            "apps/homelab-web/envs/prod/patch-deployment.yaml",
        ],
    },
}


def parse_bool_env(var_name: str, default: bool) -> bool:
    raw = os.getenv(var_name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def deployment_lock_stale_timeout_seconds() -> int:
    return max(60, int(os.getenv("DEPLOYMENT_LOCK_STALE_TIMEOUT_SECONDS", "1800")))


def deployment_reconciler_enabled() -> bool:
    default_enabled = bool(os.getenv("KUBERNETES_SERVICE_HOST"))
    return parse_bool_env("DEPLOYMENT_RECONCILER_ENABLED", default_enabled)


def deployment_reconciler_readthrough_enabled() -> bool:
    return parse_bool_env(
        "DEPLOYMENT_RECONCILER_READTHROUGH_ENABLED",
        deployment_reconciler_enabled(),
    )


def deployment_reconciler_interval_seconds() -> int:
    return max(15, int(os.getenv("DEPLOYMENT_RECONCILER_INTERVAL_SECONDS", "60")))


def deployment_reconciler_read_ttl_seconds() -> int:
    return max(0, int(os.getenv("DEPLOYMENT_RECONCILER_READ_TTL_SECONDS", "30")))


def deployment_reconciler_pr_comments_enabled() -> bool:
    return parse_bool_env("DEPLOYMENT_RECONCILER_PR_COMMENTS_ENABLED", False)


def deployment_reconciler_gitops_repo_slug() -> str:
    owner = os.getenv("DEPLOYMENT_RECONCILER_GITHUB_OWNER", DEFAULT_GITHUB_OWNER).strip()
    repo = os.getenv("DEPLOYMENT_RECONCILER_GITOPS_REPO", DEFAULT_WORKLOADS_REPO).strip()
    normalized_owner = owner or DEFAULT_GITHUB_OWNER
    normalized_repo = repo or DEFAULT_WORKLOADS_REPO
    return f"{normalized_owner}/{normalized_repo}"


def portal_repo_slug() -> str:
    configured = os.getenv("PORTAL_DEPLOY_PORTAL_REPO", "").strip()
    if configured:
        return configured
    actions_repo = os.getenv("PORTAL_GITHUB_ACTIONS_REPO", "").strip()
    if actions_repo:
        return actions_repo
    return f"{DEFAULT_GITHUB_OWNER}/{DEFAULT_PORTAL_REPO}"


def workloads_repo_slug() -> str:
    configured = os.getenv("PORTAL_DEPLOY_GITOPS_REPO", "").strip()
    if configured:
        return configured
    reconciler_repo = os.getenv("DEPLOYMENT_RECONCILER_GITOPS_REPO", "").strip()
    if reconciler_repo:
        owner = os.getenv("DEPLOYMENT_RECONCILER_GITHUB_OWNER", DEFAULT_GITHUB_OWNER).strip()
        normalized_owner = owner or DEFAULT_GITHUB_OWNER
        return f"{normalized_owner}/{reconciler_repo}"
    return f"{DEFAULT_GITHUB_OWNER}/{DEFAULT_WORKLOADS_REPO}"


def workloads_base_branch() -> str:
    configured = os.getenv("PORTAL_DEPLOY_GITOPS_BASE_BRANCH", "").strip()
    return configured or DEFAULT_PORTAL_IMAGES_WORKFLOW_REF


def portal_images_workflow_file() -> str:
    configured = os.getenv("PORTAL_DEPLOY_WORKFLOW_FILE", "").strip()
    return configured or DEFAULT_PORTAL_IMAGES_WORKFLOW_FILE


def portal_images_workflow_ref() -> str:
    configured = os.getenv("PORTAL_DEPLOY_WORKFLOW_REF", "").strip()
    return configured or DEFAULT_PORTAL_IMAGES_WORKFLOW_REF


def github_metadata_token() -> str | None:
    for name in (
        "PORTAL_GITHUB_ACTIONS_TOKEN",
        "GITHUB_API_TOKEN",
        "GITHUB_READ_TOKEN",
        "GITHUB_TOKEN",
    ):
        token = os.getenv(name, "").strip()
        if token:
            return token
    return None


def ghcr_token() -> str | None:
    for name in (
        "GHCR_READ_TOKEN",
        "GITHUB_API_TOKEN",
        "GITHUB_READ_TOKEN",
        "PORTAL_GITHUB_ACTIONS_TOKEN",
        "GITHUB_TOKEN",
    ):
        token = os.getenv(name, "").strip()
        if token:
            return token
    return None


def github_api_token_for_path(path: str) -> str | None:
    normalized = path.lstrip("/")
    if "/packages/container/" in normalized:
        return ghcr_token()
    return github_metadata_token()


def github_api_base_url() -> str:
    return os.getenv("GITHUB_API_BASE_URL", "https://api.github.com").rstrip("/")


def dev_deploy_target(service_id: str) -> dict[str, object]:
    target = DEV_DEPLOY_TARGETS.get(service_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Service {service_id!r} does not support deploy-to-dev.",
        )
    return target


def promote_to_prod_target(service_id: str) -> dict[str, object]:
    target = PROMOTE_TO_PROD_TARGETS.get(service_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Service {service_id!r} does not support promote-to-prod.",
        )
    return target


def rollback_target(service_id: str, target_environment: str) -> dict[str, Any]:
    if target_environment == "dev":
        target = dev_deploy_target(service_id)
        return {
            "image_repo": str(target["image_repo"]),
            "patch_files": [str(path) for path in target["patch_files"]],
            "argo_app": str(target["argo_app"]),
            "target_environment": "dev",
        }

    if target_environment == "prod":
        target = promote_to_prod_target(service_id)
        return {
            "image_repo": str(target["image_repo"]),
            "patch_files": [str(path) for path in target["patch_files"]],
            "argo_app": str(target["argo_app"]),
            "target_environment": "prod",
        }

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Unsupported rollback target environment {target_environment!r}.",
    )


def workloads_gitops_repo_url(repo_slug: str) -> str:
    return f"https://github.com/{repo_slug}.git"
