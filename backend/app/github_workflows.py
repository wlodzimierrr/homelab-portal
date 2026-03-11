from __future__ import annotations

from dataclasses import dataclass
import json
import os
from urllib import error as urlerror
from urllib import request as urlrequest


DEFAULT_GITHUB_API_BASE_URL = "https://api.github.com"
DEFAULT_GITHUB_OWNER = "wlodzimierrr"
DEFAULT_PORTAL_REPO = "homelab-portal"
DEFAULT_GATED_PROMOTION_WORKFLOW = "gated-promotion.yml"
DEFAULT_GATED_PROMOTION_REF = "main"


class GitHubWorkflowDispatchError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class GitHubWorkflowDispatchResult:
    repository: str
    workflow_file: str
    workflow_ref: str
    workflow_url: str


def _github_actions_token() -> str:
    for name in (
        "PORTAL_GITHUB_ACTIONS_TOKEN",
        "GITHUB_API_TOKEN",
        "GITHUB_TOKEN",
    ):
        token = os.getenv(name, "").strip()
        if token:
            return token
    raise GitHubWorkflowDispatchError(
        "GitHub Actions dispatch token is not configured. Set PORTAL_GITHUB_ACTIONS_TOKEN or GITHUB_API_TOKEN."
    )


def _portal_repo_slug() -> str:
    raw = os.getenv("PORTAL_GITHUB_ACTIONS_REPO", "").strip()
    if raw:
        return raw
    return f"{DEFAULT_GITHUB_OWNER}/{DEFAULT_PORTAL_REPO}"


def _workflow_file() -> str:
    return os.getenv("PORTAL_GITHUB_ACTIONS_WORKFLOW_FILE", DEFAULT_GATED_PROMOTION_WORKFLOW).strip() or DEFAULT_GATED_PROMOTION_WORKFLOW


def _workflow_ref() -> str:
    return os.getenv("PORTAL_GITHUB_ACTIONS_WORKFLOW_REF", DEFAULT_GATED_PROMOTION_REF).strip() or DEFAULT_GATED_PROMOTION_REF


def _github_api_base_url() -> str:
    return os.getenv("GITHUB_API_BASE_URL", DEFAULT_GITHUB_API_BASE_URL).rstrip("/")


def dispatch_portal_rollback_workflow(
    *,
    rollback_api_tag: str,
    rollback_web_tag: str,
    operator_reason: str,
    target_environment: str = "prod",
    timeout_seconds: float = 10.0,
) -> GitHubWorkflowDispatchResult:
    repo_slug = _portal_repo_slug()
    workflow_file = _workflow_file()
    workflow_ref = _workflow_ref()
    workflow_url = f"https://github.com/{repo_slug}/actions/workflows/{workflow_file}"

    payload = {
        "ref": workflow_ref,
        "inputs": {
            "target_environment": target_environment,
            "action_mode": "rollback",
            "rollback_api_tag": rollback_api_tag,
            "rollback_web_tag": rollback_web_tag,
            "operator_reason": operator_reason,
        },
    }
    request = urlrequest.Request(
        f"{_github_api_base_url()}/repos/{repo_slug}/actions/workflows/{workflow_file}/dispatches",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {_github_actions_token()}",
            "Content-Type": "application/json",
            "User-Agent": "homelab-portal-backend",
        },
    )

    try:
        with urlrequest.urlopen(request, timeout=timeout_seconds):
            return GitHubWorkflowDispatchResult(
                repository=repo_slug,
                workflow_file=workflow_file,
                workflow_ref=workflow_ref,
                workflow_url=workflow_url,
            )
    except urlerror.HTTPError as exc:  # pragma: no cover - exercised via endpoint monkeypatch tests
        body = exc.read().decode("utf-8", errors="replace").strip()
        message = body or exc.reason or "GitHub workflow dispatch failed"
        raise GitHubWorkflowDispatchError(message, status_code=exc.code) from exc
    except urlerror.URLError as exc:  # pragma: no cover - exercised in live env only
        raise GitHubWorkflowDispatchError(f"GitHub workflow dispatch failed: {exc.reason}") from exc
