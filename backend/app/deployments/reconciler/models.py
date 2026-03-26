from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from datetime import datetime
from typing import Any, TypedDict


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
