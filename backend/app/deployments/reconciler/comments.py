from __future__ import annotations

import os
from typing import Callable

from app.deployments.reconciler.event_parsing import github_post_json
from app.deployments.reconciler.models import GitOpsDeploymentEvent


def build_pr_comment_body(*, row: dict[str, object], event: GitOpsDeploymentEvent) -> str:
    deployment_status = str(row.get("status") or "")
    result = str(row.get("result") or "")
    result_reason = row.get("resultReason")
    deployment_id = str(row.get("deploymentId") or "")
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    failure_reason = (
        result_reason
        if isinstance(result_reason, str) and result_reason
        else metadata.get("failureReason")
        if isinstance(metadata, dict) and isinstance(metadata.get("failureReason"), str)
        else None
    )

    portal_base_url = os.getenv("PORTAL_BASE_URL", "").rstrip("/")
    portal_link = f"{portal_base_url}/services/{event.service_id}" if portal_base_url else None

    if deployment_status == "live":
        icon = "\u2705"
        headline = "Deployment **live** (`success`)"
    else:
        icon = "\u274c"
        headline = f"Deployment **failed** (`{result or 'failure'}`)"

    lines = [f"{icon} {headline}", ""]
    lines.append("| Field | Value |")
    lines.append("| --- | --- |")
    lines.append(f"| **Service** | `{event.service_id}` |")
    lines.append(f"| **Environment** | `{event.env}` |")
    lines.append(f"| **Action** | `{event.action}` |")
    lines.append(f"| **Image** | `{event.target_image}` |")
    if deployment_id:
        lines.append(f"| **Deployment ID** | `{deployment_id}` |")
    if failure_reason:
        lines.append(f"| **Reason** | {failure_reason} |")
    if portal_link:
        lines.append(f"| **Portal** | [{event.service_id}]({portal_link}) |")

    lines.append("")
    lines.append("_Posted by homelab portal deployment reconciler._")
    return "\n".join(lines)


def make_pr_comment_poster(
    gitops_repo_path: str,
    *,
    github_post_json_fn: Callable[[str, object], None] = github_post_json,
) -> Callable[[int, str], None]:
    """Return a callable that posts a comment on the given PR number."""

    def _post(pr_number: int, body: str) -> None:
        github_post_json_fn(
            f"repos/{gitops_repo_path}/issues/{pr_number}/comments",
            {"body": body},
        )

    return _post
