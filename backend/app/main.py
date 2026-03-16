import json
from datetime import datetime, timedelta, timezone
import logging
import math
import os
import re
from threading import Event, Thread
import time
from typing import Any, Literal
from uuid import uuid4
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from app.alerts_feed import (
    get_alertmanager_base_url,
    normalize_active_alerts,
)
from app.catalog_reconciliation import build_catalog_join
from app.config_editing import (
    ALLOWED_CONFIG_VALUES,
    ConfigEditingError,
    compute_config_checksum_from_manifest,
    enforce_config_edit_rate_limit,
    get_config_edit_target,
    normalize_config_value,
    parse_config_map_data,
    resolve_config_edit_target,
    update_config_map_manifest_document,
    update_deployment_patch_checksum,
)
from app.db import get_psycopg_database_url
from app.deployment_records import (
    get_deployment_record,
    list_deployment_records_for_service,
    upsert_deployment_record,
)
from app.deployment_locks import (
    DeploymentLockConflictError,
    DeploymentLockRow,
    cleanup_stale_deployment_locks,
    get_deployment_lock,
    sync_deployment_lock_for_deployment_row,
)
from app.deployment_reconciler import reconcile_recent_gitops_deployments
from app.health_timeline import (
    TimelinePoint,
    classify_timeline_status,
    compact_timeline_points,
    load_timeline_thresholds,
    now_utc,
    parse_range,
    parse_step,
)
from app.logs_quickview import (
    build_preset_query,
    build_time_window,
    encode_cursor_ns,
    enforce_logs_rate_limit,
    get_logs_default_namespace,
    validate_preset,
)
from app.monitoring_providers import (
    build_provider_status,
    get_loki_base_url,
    get_monitoring_timeout_seconds,
    get_prometheus_base_url,
    load_json_from_provider,
    probe_monitoring_provider,
    raise_provider_bad_payload_error,
)
from app.gitops_project_sync import sync_project_registry_from_gitops
from app.github_workflows import (
    GitHubWorkflowDispatchError,
    dispatch_portal_rollback_workflow,
)
from app.lib import (
    GitProvider,
    GitServiceAuthError,
    GitServiceConfigurationError,
    GitServiceConflictError,
    GitServiceError,
    build_default_git_provider,
)
from app.release_traceability import (
    build_release_join_diagnostics,
    build_release_traceability_rows,
    compute_is_drifted,
    load_argo_metadata_rows,
    load_ci_metadata_rows,
)
from app.service_identity import is_canonical_service_id
from app.service_observability import (
    build_service_metrics_observability_diagnostics,
    normalize_observability_mode,
)
from app.service_identity_validation import build_service_identity_diagnostics
from app.service_registry_sync import _kube_get_json, sync_service_registry_from_cluster
from app.observability_cache import TTLCache
from app.observability_config import (
    escape_promql_regex_literal,
    load_observability_config,
    parse_duration_token,
    render_query_template,
)
from app.scaffold_service import (
    ScaffoldError,
    ScaffoldServiceInput,
    build_appproject_addition,
    build_catalog_entry_addition,
    generate_gitops_new_files,
    update_kustomization_resources,
    validate_service_name,
)
from app.secret_editing import (
    SecretEditingError,
    decrypt_secret_manifest,
    encrypt_secret_manifest,
    enforce_secret_edit_rate_limit,
    resolve_secret_edit_target,
    update_secret_manifest_document,
)

app = FastAPI(title="Homelab Backend API", version="0.1.0")
logger = logging.getLogger("homelab.backend.monitoring")

bearer_auth = HTTPBearer(auto_error=False)
metrics_summary_cache = TTLCache()
timeline_cache = TTLCache()
logs_quickview_cache = TTLCache()
alerts_cache = TTLCache()
deployment_history_cache = TTLCache()
deployment_reconcile_cache = TTLCache()
metrics_namespace_label = os.getenv("OBS_METRICS_NAMESPACE", os.getenv("POD_NAMESPACE", "default"))
metrics_app_label = os.getenv("OBS_METRICS_APP_LABEL", "homelab-api")
deployment_reconciler_stop = Event()
deployment_reconciler_thread: Thread | None = None
http_requests_total = Counter(
    "http_requests_total",
    "Total HTTP requests handled by the homelab API.",
    labelnames=("namespace", "app", "method", "path", "status"),
)
http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency for the homelab API.",
    labelnames=("namespace", "app", "method", "path"),
)

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


def clear_observability_caches_for_tests() -> None:
    metrics_summary_cache.clear()
    timeline_cache.clear()
    logs_quickview_cache.clear()
    alerts_cache.clear()
    deployment_history_cache.clear()
    deployment_reconcile_cache.clear()


@app.middleware("http")
async def observe_http_requests(request, call_next):
    if request.url.path == "/metrics":
        return await call_next(request)

    started = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        labels = {
            "namespace": metrics_namespace_label,
            "app": metrics_app_label,
            "method": request.method,
            "path": request.url.path,
        }
        http_request_duration_seconds.labels(**labels).observe(time.perf_counter() - started)
        http_requests_total.labels(
            **labels,
            status=str(status_code),
        ).inc()


@app.on_event("startup")
def start_deployment_reconciler_loop() -> None:
    global deployment_reconciler_thread

    if not _deployment_reconciler_enabled():
        return
    if deployment_reconciler_thread and deployment_reconciler_thread.is_alive():
        return

    deployment_reconciler_stop.clear()

    def _run() -> None:
        logger.info(
            "deployment_reconciler_started interval_seconds=%s",
            _deployment_reconciler_interval_seconds(),
        )
        while not deployment_reconciler_stop.is_set():
            try:
                _reconcile_recent_deployment_activity()
            except Exception as exc:  # pragma: no cover - live background loop only
                logger.warning("deployment_reconciler_iteration_failed error=%s", exc)
            deployment_reconciler_stop.wait(_deployment_reconciler_interval_seconds())

    deployment_reconciler_thread = Thread(
        target=_run,
        name="deployment-reconciler",
        daemon=True,
    )
    deployment_reconciler_thread.start()


@app.on_event("shutdown")
def stop_deployment_reconciler_loop() -> None:
    global deployment_reconciler_thread

    deployment_reconciler_stop.set()
    if deployment_reconciler_thread and deployment_reconciler_thread.is_alive():
        deployment_reconciler_thread.join(timeout=1.0)
    deployment_reconciler_thread = None


class MonitoringProviderStatusResponse(BaseModel):
    provider: str
    base_url: str = Field(alias="baseUrl")
    status: str
    reachable: bool
    checked_at: str = Field(alias="checkedAt")
    correlation_id: str | None = Field(default=None, alias="correlationId")
    latency_ms: int | None = Field(default=None, alias="latencyMs")
    http_status: int | None = Field(default=None, alias="httpStatus")
    error: str | None = None
    probe_path: str | None = Field(default=None, alias="probePath")

    model_config = ConfigDict(populate_by_name=True)


class HealthResponse(BaseModel):
    status: str = "ok"
    providers: list[MonitoringProviderStatusResponse] | None = None


class MonitoringProviderErrorDetailResponse(BaseModel):
    message: str
    correlation_id: str | None = Field(default=None, alias="correlationId")
    provider_status: MonitoringProviderStatusResponse = Field(alias="providerStatus")

    model_config = ConfigDict(populate_by_name=True)


class MonitoringProvidersDiagnosticsResponse(BaseModel):
    generated_at: str = Field(alias="generatedAt")
    overall_status: str = Field(alias="overallStatus")
    providers: list[MonitoringProviderStatusResponse]

    model_config = ConfigDict(populate_by_name=True)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: str


class Project(BaseModel):
    id: str
    name: str
    environment: str
    owner: str | None = None
    repo_url: str | None = Field(default=None, alias="repoUrl")
    runbook_url: str | None = Field(default=None, alias="runbookUrl")
    observability_mode: str | None = Field(default=None, alias="observabilityMode")

    model_config = ConfigDict(populate_by_name=True)


class ProjectsResponse(BaseModel):
    projects: list[Project]


class CreateProjectRequest(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    environment: str = Field(min_length=1)


class ServiceRow(BaseModel):
    service_id: str = Field(alias="serviceId")
    service_name: str = Field(alias="serviceName")
    env: str
    namespace: str
    app_label: str = Field(alias="appLabel")
    argo_app_name: str | None = Field(default=None, alias="argoAppName")
    source: str
    source_ref: str | None = Field(default=None, alias="sourceRef")
    last_synced_at: str | None = Field(default=None, alias="lastSyncedAt")
    observability_mode: str | None = Field(default=None, alias="observabilityMode")

    model_config = ConfigDict(populate_by_name=True)


class ServicesResponse(BaseModel):
    services: list[ServiceRow]


class ServiceDetailResponse(BaseModel):
    id: str
    name: str
    namespace: str
    env: str
    app_label: str = Field(alias="appLabel")
    argo_app_name: str | None = Field(default=None, alias="argoAppName")
    version: str | None = None
    health: str | None = None
    sync: str | None = None
    source: str
    source_ref: str | None = Field(default=None, alias="sourceRef")
    last_synced_at: str | None = Field(default=None, alias="lastSyncedAt")
    observability_mode: str | None = Field(default=None, alias="observabilityMode")
    public_host: str | None = Field(default=None, alias="publicHost")
    deployment_lock: "DeploymentLockResponse | None" = Field(default=None, alias="deploymentLock")

    model_config = ConfigDict(populate_by_name=True)


class DeploymentLockResponse(BaseModel):
    service_id: str = Field(..., alias="serviceId")
    env: str
    deployment_id: str = Field(..., alias="deploymentId")
    request_key: str = Field(..., alias="requestKey")
    action: str
    status: str
    argo_app: str | None = Field(default=None, alias="argoApp")
    requested_by: str | None = Field(default=None, alias="requestedBy")
    requested_at: str | None = Field(default=None, alias="requestedAt")
    git_pr_url: str | None = Field(default=None, alias="gitPrUrl")
    git_pr_number: int | None = Field(default=None, alias="gitPrNumber")
    git_ref: str | None = Field(default=None, alias="gitRef")
    deploy_reason: str | None = Field(default=None, alias="deployReason")
    locked_at: str | None = Field(default=None, alias="lockedAt")
    expires_at: str | None = Field(default=None, alias="expiresAt")
    metadata: dict[str, Any] | None = None

    model_config = ConfigDict(populate_by_name=True)


class DeploymentRecordResponse(BaseModel):
    id: str
    service_id: str = Field(alias="serviceId")
    env: str
    action: str
    version: str | None = None
    status: str | None = None
    requested_at: str | None = Field(default=None, alias="requestedAt")
    requested_by: str | None = Field(default=None, alias="requestedBy")
    deployed_at: str | None = Field(default=None, alias="deployedAt")
    commit_sha: str | None = Field(default=None, alias="commitSha")
    image_ref: str | None = Field(default=None, alias="imageRef")
    previous_image_ref: str | None = Field(default=None, alias="previousImageRef")
    git_ref: str | None = Field(default=None, alias="gitRef")
    git_pr_url: str | None = Field(default=None, alias="gitPrUrl")
    git_pr_number: int | None = Field(default=None, alias="gitPrNumber")
    compare_url: str | None = Field(default=None, alias="compareUrl")
    merge_sha: str | None = Field(default=None, alias="mergeSha")
    argo_app: str | None = Field(default=None, alias="argoApp")
    sync_status: str | None = Field(default=None, alias="syncStatus")
    health_status: str | None = Field(default=None, alias="healthStatus")
    deploy_reason: str | None = Field(default=None, alias="deployReason")
    started_at: str | None = Field(default=None, alias="startedAt")
    finished_at: str | None = Field(default=None, alias="finishedAt")
    deploy_window_start: str | None = Field(default=None, alias="deployWindowStart")
    deploy_window_end: str | None = Field(default=None, alias="deployWindowEnd")
    failure_reason: str | None = Field(default=None, alias="failureReason")
    error_rate_pct: dict[str, float] | None = Field(default=None, alias="errorRatePct")
    p95_latency_ms: dict[str, float] | None = Field(default=None, alias="p95LatencyMs")
    availability_pct: dict[str, float] | None = Field(default=None, alias="availabilityPct")
    metadata: dict[str, Any] | None = None

    model_config = ConfigDict(populate_by_name=True)


class ServiceDeploymentsResponse(BaseModel):
    deployments: list[DeploymentRecordResponse]


class ServiceDeploymentInfoResponse(BaseModel):
    deployment_id: str | None = Field(default=None, alias="deploymentId")
    service_id: str = Field(alias="serviceId")
    env: str | None = None
    action: str | None = None
    deployed_image: str | None = Field(default=None, alias="deployedImage")
    previous_image: str | None = Field(default=None, alias="previousImage")
    image_digest: str | None = Field(default=None, alias="imageDigest")
    git_commit: str | None = Field(default=None, alias="gitCommit")
    deployed_timestamp: str | None = Field(default=None, alias="deployedTimestamp")
    git_pr_url: str | None = Field(default=None, alias="gitPrUrl")
    git_pr_number: int | None = Field(default=None, alias="gitPrNumber")
    compare_url: str | None = Field(default=None, alias="compareUrl")
    deploy_reason: str | None = Field(default=None, alias="deployReason")
    result: str | None = None
    result_reason: str | None = Field(default=None, alias="resultReason")
    commit_url: str | None = Field(default=None, alias="commitUrl")
    image_url: str | None = Field(default=None, alias="imageUrl")
    argo_app: str | None = Field(default=None, alias="argoApp")
    sync_status: str | None = Field(default=None, alias="syncStatus")
    health_status: str | None = Field(default=None, alias="healthStatus")

    model_config = ConfigDict(populate_by_name=True)


class DeploymentReconcileResponse(BaseModel):
    pull_requests_scanned: int = Field(alias="pullRequestsScanned")
    records_upserted: int = Field(alias="recordsUpserted")
    status_counts: dict[str, int] = Field(alias="statusCounts")
    generated_at: str = Field(alias="generatedAt")

    model_config = ConfigDict(populate_by_name=True)


class CreateDeploymentRecordRequest(BaseModel):
    service_id: str = Field(..., alias="serviceId", min_length=1)
    env: str = Field(min_length=1)
    action: Literal["deploy", "promote", "rollback", "config-change"]
    status: Literal["pending", "deploying", "live", "failed"] = "pending"
    requested_at: datetime | None = Field(default=None, alias="requestedAt")
    requested_by: str | None = Field(default=None, alias="requestedBy")
    pr_url: str | None = Field(default=None, alias="gitPrUrl")
    pr_number: int | None = Field(default=None, alias="gitPrNumber")
    merge_sha: str | None = Field(default=None, alias="mergeSha")
    target_image: str | None = Field(default=None, alias="imageRef")
    previous_image: str | None = Field(default=None, alias="previousImageRef")
    argo_app: str | None = Field(default=None, alias="argoApp")
    sync_status: str | None = Field(default=None, alias="syncStatus")
    health_status: str | None = Field(default=None, alias="healthStatus")
    started_at: datetime | None = Field(default=None, alias="startedAt")
    finished_at: datetime | None = Field(default=None, alias="finishedAt")
    deploy_window_start: datetime | None = Field(default=None, alias="deployWindowStart")
    deploy_window_end: datetime | None = Field(default=None, alias="deployWindowEnd")
    deploy_reason: str | None = Field(default=None, alias="deployReason")
    compare_url: str | None = Field(default=None, alias="compareUrl")
    git_ref: str | None = Field(default=None, alias="gitRef")
    request_key: str | None = Field(default=None, alias="requestKey")
    metadata: dict[str, Any] | None = None

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("service_id")
    @classmethod
    def validate_canonical_service_id(cls, value: str) -> str:
        if not is_canonical_service_id(value):
            raise ValueError("serviceId must use canonical lowercase-hyphen identity")
        return value


ROLLBACK_TAG_RE = re.compile(r"^(sha-[0-9a-f]{40}|v?[0-9]+(\.[0-9]+){2}([.-][0-9A-Za-z.-]+)?)$")


class PortalRollbackRequest(BaseModel):
    target_environment: Literal["prod"] = Field(default="prod", alias="targetEnvironment")
    rollback_api_tag: str = Field(..., alias="rollbackApiTag", min_length=1)
    rollback_web_tag: str = Field(..., alias="rollbackWebTag", min_length=1)
    reason: str = Field(..., min_length=5, max_length=500)

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("rollback_api_tag", "rollback_web_tag")
    @classmethod
    def validate_rollback_tag(cls, value: str) -> str:
        normalized = value.strip()
        if not ROLLBACK_TAG_RE.fullmatch(normalized):
            raise ValueError("rollback tags must use sha-<40 hex> or semver format")
        return normalized

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 5:
            raise ValueError("reason must be at least 5 characters long")
        return normalized


class PortalRollbackResponse(BaseModel):
    status: Literal["accepted"]
    action: Literal["rollback"]
    target_environment: str = Field(alias="targetEnvironment")
    rollback_api_tag: str = Field(alias="rollbackApiTag")
    rollback_web_tag: str = Field(alias="rollbackWebTag")
    reason: str
    requested_by: str = Field(alias="requestedBy")
    repository: str
    workflow_file: str = Field(alias="workflowFile")
    workflow_ref: str = Field(alias="workflowRef")
    workflow_url: str = Field(alias="workflowUrl")
    initiated_at: str = Field(alias="initiatedAt")

    model_config = ConfigDict(populate_by_name=True)


class PortalDeployToDevError(Exception):
    def __init__(self, message: str, *, status_code: int = status.HTTP_502_BAD_GATEWAY):
        super().__init__(message)
        self.status_code = status_code


class PortalDeployToDevRequest(BaseModel):
    deploy_reason: str = Field(..., alias="deployReason", min_length=5, max_length=500)

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("deploy_reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 5:
            raise ValueError("deployReason must be at least 5 characters long")
        return normalized


class PortalDeployToDevResponse(BaseModel):
    status: Literal["accepted", "noop"]
    action: Literal["deploy"]
    service_id: str = Field(alias="serviceId")
    target_environment: str = Field(alias="targetEnvironment")
    requested_by: str = Field(alias="requestedBy")
    repository: str
    base_branch: str = Field(alias="baseBranch")
    branch_name: str | None = Field(default=None, alias="branchName")
    deployment_id: str | None = Field(default=None, alias="deploymentId")
    git_pr_url: str | None = Field(default=None, alias="gitPrUrl")
    git_pr_number: int | None = Field(default=None, alias="gitPrNumber")
    previous_tag: str | None = Field(default=None, alias="previousTag")
    new_tag: str | None = Field(default=None, alias="newTag")
    previous_image_ref: str | None = Field(default=None, alias="previousImageRef")
    new_image_ref: str | None = Field(default=None, alias="newImageRef")
    compare_url: str | None = Field(default=None, alias="compareUrl")
    source_commit_sha: str | None = Field(default=None, alias="sourceCommitSha")
    source_workflow_run_url: str | None = Field(default=None, alias="sourceWorkflowRunUrl")
    message: str | None = None
    initiated_at: str = Field(alias="initiatedAt")

    model_config = ConfigDict(populate_by_name=True)


class PortalPromoteToProdError(Exception):
    def __init__(self, message: str, *, status_code: int = status.HTTP_502_BAD_GATEWAY):
        super().__init__(message)
        self.status_code = status_code


class PortalPromoteToProdRequest(BaseModel):
    deploy_reason: str = Field(..., alias="deployReason", min_length=5, max_length=500)

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("deploy_reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 5:
            raise ValueError("deployReason must be at least 5 characters long")
        return normalized


class PortalPromoteToProdResponse(BaseModel):
    status: Literal["accepted", "noop"]
    action: Literal["promote"]
    service_id: str = Field(alias="serviceId")
    target_environment: Literal["prod"] = Field(alias="targetEnvironment")
    requested_by: str = Field(alias="requestedBy")
    repository: str
    base_branch: str = Field(alias="baseBranch")
    branch_name: str | None = Field(default=None, alias="branchName")
    deployment_id: str | None = Field(default=None, alias="deploymentId")
    git_pr_url: str | None = Field(default=None, alias="gitPrUrl")
    git_pr_number: int | None = Field(default=None, alias="gitPrNumber")
    previous_tag: str | None = Field(default=None, alias="previousTag")
    new_tag: str | None = Field(default=None, alias="newTag")
    previous_image_ref: str | None = Field(default=None, alias="previousImageRef")
    new_image_ref: str | None = Field(default=None, alias="newImageRef")
    compare_url: str | None = Field(default=None, alias="compareUrl")
    source_commit_sha: str | None = Field(default=None, alias="sourceCommitSha")
    message: str | None = None
    initiated_at: str = Field(alias="initiatedAt")

    model_config = ConfigDict(populate_by_name=True)


class PortalSetSecretRequest(BaseModel):
    env: Literal["dev", "prod"]
    secret_key: str = Field(..., alias="secretKey", min_length=1, max_length=128)
    secret_value: str = Field(..., alias="secretValue", min_length=1, max_length=10_000)

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("secret_key")
    @classmethod
    def validate_secret_key(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("secretKey must not be empty")
        return normalized

    @field_validator("secret_value")
    @classmethod
    def validate_secret_value(cls, value: str) -> str:
        if value == "":
            raise ValueError("secretValue must not be empty")
        return value


class PortalSetSecretResponse(BaseModel):
    status: Literal["accepted"]
    service_id: str = Field(alias="serviceId")
    env: Literal["dev", "prod"]
    secret_key: str = Field(alias="secretKey")
    requested_by: str = Field(alias="requestedBy")
    repository: str
    base_branch: str = Field(alias="baseBranch")
    branch_name: str = Field(alias="branchName")
    git_pr_url: str = Field(alias="gitPrUrl")
    git_pr_number: int = Field(alias="gitPrNumber")
    secret_file_path: str = Field(alias="secretFilePath")
    message: str | None = None
    initiated_at: str = Field(alias="initiatedAt")

    model_config = ConfigDict(populate_by_name=True)


class PortalSetConfigRequest(BaseModel):
    env: Literal["dev", "prod"]
    config_key: str = Field(..., alias="configKey", min_length=1, max_length=128)
    config_value: str = Field(..., alias="configValue", min_length=1, max_length=10_000)

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("config_key")
    @classmethod
    def validate_config_key(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("configKey must not be empty")
        return normalized

    @field_validator("config_value")
    @classmethod
    def validate_config_value(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("configValue must not be empty")
        return normalized


class PortalSetConfigResponse(BaseModel):
    status: Literal["accepted", "noop"]
    service_id: str = Field(alias="serviceId")
    env: Literal["dev", "prod"]
    config_key: str = Field(alias="configKey")
    previous_value: str = Field(alias="previousValue")
    config_value: str = Field(alias="configValue")
    requested_by: str = Field(alias="requestedBy")
    repository: str
    base_branch: str = Field(alias="baseBranch")
    branch_name: str | None = Field(default=None, alias="branchName")
    git_pr_url: str | None = Field(default=None, alias="gitPrUrl")
    git_pr_number: int | None = Field(default=None, alias="gitPrNumber")
    config_file_path: str = Field(alias="configFilePath")
    message: str | None = None
    initiated_at: str = Field(alias="initiatedAt")

    model_config = ConfigDict(populate_by_name=True)


class ServiceConfigEntry(BaseModel):
    key: str
    value: str
    allowed_values: list[str] = Field(alias="allowedValues")

    model_config = ConfigDict(populate_by_name=True)


class ServiceConfigResponse(BaseModel):
    service_id: str = Field(alias="serviceId")
    env: Literal["dev", "prod"]
    entries: list[ServiceConfigEntry]

    model_config = ConfigDict(populate_by_name=True)


class PortalServiceRollbackError(Exception):
    def __init__(self, message: str, *, status_code: int = status.HTTP_502_BAD_GATEWAY):
        super().__init__(message)
        self.status_code = status_code


class PortalServiceRollbackCandidate(BaseModel):
    tag: str
    image_ref: str = Field(alias="imageRef")
    compare_url: str | None = Field(default=None, alias="compareUrl")
    source_commit_sha: str | None = Field(default=None, alias="sourceCommitSha")
    published_at: str | None = Field(default=None, alias="publishedAt")

    model_config = ConfigDict(populate_by_name=True)


class PortalServiceRollbackCandidatesResponse(BaseModel):
    service_id: str = Field(alias="serviceId")
    target_environment: Literal["dev", "prod"] = Field(alias="targetEnvironment")
    current_tag: str | None = Field(default=None, alias="currentTag")
    current_image_ref: str | None = Field(default=None, alias="currentImageRef")
    candidates: list[PortalServiceRollbackCandidate]
    generated_at: str = Field(alias="generatedAt")

    model_config = ConfigDict(populate_by_name=True)


class PortalServiceRollbackRequest(BaseModel):
    target_environment: Literal["dev", "prod"] = Field(default="dev", alias="targetEnvironment")
    rollback_tag: str = Field(..., alias="rollbackTag", min_length=1)
    deploy_reason: str = Field(..., alias="deployReason", min_length=5, max_length=500)

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("rollback_tag")
    @classmethod
    def validate_rollback_tag(cls, value: str) -> str:
        normalized = value.strip()
        if not ROLLBACK_TAG_RE.fullmatch(normalized):
            raise ValueError("rollbackTag must use sha-<40 hex> or semver format")
        return normalized

    @field_validator("deploy_reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 5:
            raise ValueError("deployReason must be at least 5 characters long")
        return normalized


class PortalServiceRollbackResponse(BaseModel):
    status: Literal["accepted", "noop"]
    action: Literal["rollback"]
    service_id: str = Field(alias="serviceId")
    target_environment: Literal["dev", "prod"] = Field(alias="targetEnvironment")
    requested_by: str = Field(alias="requestedBy")
    repository: str
    base_branch: str = Field(alias="baseBranch")
    branch_name: str | None = Field(default=None, alias="branchName")
    deployment_id: str | None = Field(default=None, alias="deploymentId")
    git_pr_url: str | None = Field(default=None, alias="gitPrUrl")
    git_pr_number: int | None = Field(default=None, alias="gitPrNumber")
    previous_tag: str | None = Field(default=None, alias="previousTag")
    new_tag: str | None = Field(default=None, alias="newTag")
    previous_image_ref: str | None = Field(default=None, alias="previousImageRef")
    new_image_ref: str | None = Field(default=None, alias="newImageRef")
    compare_url: str | None = Field(default=None, alias="compareUrl")
    source_commit_sha: str | None = Field(default=None, alias="sourceCommitSha")
    message: str | None = None
    initiated_at: str = Field(alias="initiatedAt")

    model_config = ConfigDict(populate_by_name=True)


class ServiceRegistrySyncFailure(BaseModel):
    source: str
    scope: str
    error: str


class ServiceRegistrySyncResponse(BaseModel):
    correlation_id: str = Field(alias="correlationId")
    source: str
    env: str
    namespaces: list[str]
    discovered: int
    upserted: int
    inserted: int
    updated: int
    deleted: int = 0
    source_failures: list[ServiceRegistrySyncFailure] = Field(alias="sourceFailures")
    generated_at: str = Field(alias="generatedAt")
    duration_ms: int = Field(alias="durationMs")

    model_config = ConfigDict(populate_by_name=True)


class ServiceRegistryFreshnessResponse(BaseModel):
    row_count: int = Field(alias="rowCount")
    last_synced_at: str | None = Field(alias="lastSyncedAt")
    warning_after_minutes: int = Field(alias="warningAfterMinutes")
    stale_after_minutes: int = Field(alias="staleAfterMinutes")
    is_empty: bool = Field(alias="isEmpty")
    is_warning: bool = Field(alias="isWarning")
    is_stale: bool = Field(alias="isStale")
    state: str

    model_config = ConfigDict(populate_by_name=True)


class ServiceRegistryJoinMismatchResponse(BaseModel):
    ci_unmatched_count: int = Field(alias="ciUnmatchedCount")
    argo_unmatched_count: int = Field(alias="argoUnmatchedCount")
    ci_unmatched_keys: list[str] = Field(alias="ciUnmatchedKeys")
    argo_unmatched_keys: list[str] = Field(alias="argoUnmatchedKeys")

    model_config = ConfigDict(populate_by_name=True)


class CatalogJoinServiceRefResponse(BaseModel):
    service_id: str = Field(alias="serviceId")
    service_name: str = Field(alias="serviceName")
    namespace: str
    app_label: str = Field(alias="appLabel")
    argo_app_name: str | None = Field(default=None, alias="argoAppName")

    model_config = ConfigDict(populate_by_name=True)


class CatalogJoinRowResponse(BaseModel):
    project_id: str = Field(alias="projectId")
    project_name: str = Field(alias="projectName")
    env: str
    namespace: str
    app_label: str = Field(alias="appLabel")
    join_source: str = Field(alias="joinSource")
    primary_service_id: str | None = Field(default=None, alias="primaryServiceId")
    service_count: int = Field(alias="serviceCount")
    service_ids: list[str] = Field(alias="serviceIds")
    services: list[CatalogJoinServiceRefResponse]

    model_config = ConfigDict(populate_by_name=True)


class CatalogJoinDiagnosticsResponse(BaseModel):
    project_only_count: int = Field(alias="projectOnlyCount")
    service_only_count: int = Field(alias="serviceOnlyCount")
    one_to_many_count: int = Field(alias="oneToManyCount")
    ambiguous_join_count: int = Field(alias="ambiguousJoinCount")
    project_only_keys: list[str] = Field(alias="projectOnlyKeys")
    service_only_keys: list[str] = Field(alias="serviceOnlyKeys")
    one_to_many_keys: list[str] = Field(alias="oneToManyKeys")
    ambiguous_join_keys: list[str] = Field(alias="ambiguousJoinKeys")

    model_config = ConfigDict(populate_by_name=True)


class ProjectCatalogDiagnosticsResponse(BaseModel):
    generated_at: str = Field(alias="generatedAt")
    env: str | None = None
    freshness: ServiceRegistryFreshnessResponse
    catalog_join: CatalogJoinDiagnosticsResponse = Field(alias="catalogJoin")

    model_config = ConfigDict(populate_by_name=True)


class CatalogJoinResponse(BaseModel):
    generated_at: str = Field(alias="generatedAt")
    env: str | None = None
    rows: list[CatalogJoinRowResponse]
    diagnostics: CatalogJoinDiagnosticsResponse

    model_config = ConfigDict(populate_by_name=True)


class ServiceIdentityMonitoringSelectorResponse(BaseModel):
    namespace: str
    app_label: str = Field(alias="appLabel")

    model_config = ConfigDict(populate_by_name=True)


class ServiceIdentityDriftRowResponse(BaseModel):
    service_id: str = Field(alias="serviceId")
    env: str
    project_id: str | None = Field(default=None, alias="projectId")
    catalog_linked: bool = Field(alias="catalogLinked")
    namespace: str
    expected_namespace: str | None = Field(default=None, alias="expectedNamespace")
    app_label: str = Field(alias="appLabel")
    expected_app_label: str | None = Field(default=None, alias="expectedAppLabel")
    argo_app_name: str | None = Field(default=None, alias="argoAppName")
    expected_argo_app_name: str | None = Field(default=None, alias="expectedArgoAppName")
    release_argo_app_name: str | None = Field(default=None, alias="releaseArgoAppName")
    observability_mode: str | None = Field(default=None, alias="observabilityMode")
    gitops_path: str | None = Field(default=None, alias="gitopsPath")
    expected_gitops_path: str | None = Field(default=None, alias="expectedGitopsPath")
    monitoring_selector: ServiceIdentityMonitoringSelectorResponse = Field(alias="monitoringSelector")
    violations: list[str]

    model_config = ConfigDict(populate_by_name=True)


class ServiceIdentityDiagnosticsResponse(BaseModel):
    drift_count: int = Field(alias="driftCount")
    ok_count: int = Field(alias="okCount")
    drift_keys: list[str] = Field(alias="driftKeys")
    rows: list[ServiceIdentityDriftRowResponse]

    model_config = ConfigDict(populate_by_name=True)


class ServiceRegistryDiagnosticsResponse(BaseModel):
    generated_at: str = Field(alias="generatedAt")
    env: str | None = None
    freshness: ServiceRegistryFreshnessResponse
    join_mismatch: ServiceRegistryJoinMismatchResponse = Field(alias="joinMismatch")
    catalog_join: CatalogJoinDiagnosticsResponse = Field(alias="catalogJoin")
    identity_drift: ServiceIdentityDiagnosticsResponse = Field(alias="identityDrift")

    model_config = ConfigDict(populate_by_name=True)


class ServiceMetricsSummaryResponse(BaseModel):
    service_id: str = Field(alias="serviceId")
    uptime_pct: float | None = Field(default=None, alias="uptimePct")
    p95_latency_ms: float | None = Field(default=None, alias="p95LatencyMs")
    error_rate_pct: float | None = Field(default=None, alias="errorRatePct")
    restart_count: float | None = Field(default=None, alias="restartCount")
    window_start: str = Field(alias="windowStart")
    window_end: str = Field(alias="windowEnd")
    generated_at: str = Field(alias="generatedAt")
    no_data: dict[str, bool] = Field(alias="noData")
    provider_status: MonitoringProviderStatusResponse = Field(alias="providerStatus")
    observability_diagnostics: "ServiceMetricsObservabilityDiagnosticsResponse" = Field(
        alias="observabilityDiagnostics"
    )

    model_config = ConfigDict(populate_by_name=True)


class ServiceMetricTrendPointResponse(BaseModel):
    timestamp: str
    value: float


class ServiceMetricTrendSeriesResponse(BaseModel):
    query_status: Literal["ok", "no_data"] = Field(alias="queryStatus")
    query_message: str | None = Field(default=None, alias="queryMessage")
    query_source: Literal["app_metrics", "traefik_fallback"] | None = Field(
        default=None,
        alias="querySource",
    )
    latest_value: float | None = Field(default=None, alias="latestValue")
    point_count: int = Field(alias="pointCount")
    points: list[ServiceMetricTrendPointResponse]

    model_config = ConfigDict(populate_by_name=True)


class ServiceMetricsTrendsResponse(BaseModel):
    service_id: str = Field(alias="serviceId")
    range_value: str = Field(alias="range")
    window_start: str = Field(alias="windowStart")
    window_end: str = Field(alias="windowEnd")
    generated_at: str = Field(alias="generatedAt")
    provider_status: MonitoringProviderStatusResponse = Field(alias="providerStatus")
    p95_latency_ms: ServiceMetricTrendSeriesResponse = Field(alias="p95LatencyMs")
    error_rate_pct: ServiceMetricTrendSeriesResponse = Field(alias="errorRatePct")
    observability_diagnostics: "ServiceMetricsObservabilityDiagnosticsResponse" = Field(
        alias="observabilityDiagnostics"
    )

    model_config = ConfigDict(populate_by_name=True)


class ServiceMetricsObservabilityDiagnosticsResponse(BaseModel):
    mode: Literal["app-native", "ingress-derived", "no-http"] | None = None
    authority: Literal["app", "ingress", "none"] | None = None
    status: Literal["ok", "unsupported", "no_retained_data", "misconfigured", "unknown"]
    reason: str
    message: str
    missing_metrics: list[str] = Field(alias="missingMetrics")
    source_available: bool | None = Field(default=None, alias="sourceAvailable")
    service_series_available: bool | None = Field(default=None, alias="serviceSeriesAvailable")

    model_config = ConfigDict(populate_by_name=True)


class ServiceHealthTimelineSegmentResponse(BaseModel):
    start: str
    end: str
    status: str
    reason: str | None = None


class QuickViewLogLineResponse(BaseModel):
    timestamp: str
    message: str
    labels: dict[str, str]


class LogsQuickViewResponse(BaseModel):
    service_id: str = Field(alias="serviceId")
    preset: str
    range_value: str = Field(alias="range")
    generated_at: str = Field(alias="generatedAt")
    limit: int
    returned: int
    more_available: bool = Field(alias="moreAvailable")
    next_cursor: str | None = Field(default=None, alias="nextCursor")
    lines: list[QuickViewLogLineResponse]
    provider_status: MonitoringProviderStatusResponse = Field(alias="providerStatus")

    model_config = ConfigDict(populate_by_name=True)


class DeploymentObservabilityContextResponse(BaseModel):
    service_id: str = Field(alias="serviceId")
    env: str | None = None
    deployment_id: str | None = Field(default=None, alias="deploymentId")
    action: str | None = None
    status: str | None = None
    window_start: str | None = Field(default=None, alias="windowStart")
    window_end: str | None = Field(default=None, alias="windowEnd")
    window_source: Literal["deployment_record", "explicit_window"] = Field(alias="windowSource")
    evidence_status: Literal["resolved", "missing"] = Field(alias="evidenceStatus")
    evidence_message: str | None = Field(default=None, alias="evidenceMessage")
    compare_url: str | None = Field(default=None, alias="compareUrl")
    git_pr_url: str | None = Field(default=None, alias="gitPrUrl")
    git_pr_number: int | None = Field(default=None, alias="gitPrNumber")
    deploy_reason: str | None = Field(default=None, alias="deployReason")

    model_config = ConfigDict(populate_by_name=True)


class DeploymentObservabilityMetricSnapshotResponse(BaseModel):
    before: float | None = None
    after: float | None = None
    delta: float | None = None


class DeploymentObservabilityMetricsResponse(BaseModel):
    query_status: Literal["ok", "no_data", "no_deployment_window"] = Field(alias="queryStatus")
    query_message: str | None = Field(default=None, alias="queryMessage")
    window_start: str | None = Field(default=None, alias="windowStart")
    window_end: str | None = Field(default=None, alias="windowEnd")
    generated_at: str | None = Field(default=None, alias="generatedAt")
    error_rate_pct: DeploymentObservabilityMetricSnapshotResponse | None = Field(
        default=None,
        alias="errorRatePct",
    )
    p95_latency_ms: DeploymentObservabilityMetricSnapshotResponse | None = Field(
        default=None,
        alias="p95LatencyMs",
    )
    availability_pct: DeploymentObservabilityMetricSnapshotResponse | None = Field(
        default=None,
        alias="availabilityPct",
    )
    no_data: dict[str, bool] = Field(alias="noData")
    provider_status: MonitoringProviderStatusResponse | None = Field(default=None, alias="providerStatus")

    model_config = ConfigDict(populate_by_name=True)


class DeploymentObservabilityTimelineResponse(BaseModel):
    query_status: Literal["ok", "no_data", "no_deployment_window"] = Field(alias="queryStatus")
    query_message: str | None = Field(default=None, alias="queryMessage")
    service_id: str = Field(alias="serviceId")
    window_start: str | None = Field(default=None, alias="windowStart")
    window_end: str | None = Field(default=None, alias="windowEnd")
    generated_at: str | None = Field(default=None, alias="generatedAt")
    provider_status: MonitoringProviderStatusResponse | None = Field(default=None, alias="providerStatus")
    segments: list[ServiceHealthTimelineSegmentResponse]

    model_config = ConfigDict(populate_by_name=True)


class DeploymentObservabilityLogsResponse(BaseModel):
    query_status: Literal["ok", "no_data", "no_deployment_window"] = Field(alias="queryStatus")
    query_message: str | None = Field(default=None, alias="queryMessage")
    service_id: str = Field(alias="serviceId")
    preset: str
    generated_at: str | None = Field(default=None, alias="generatedAt")
    window_start: str | None = Field(default=None, alias="windowStart")
    window_end: str | None = Field(default=None, alias="windowEnd")
    limit: int
    returned: int
    more_available: bool = Field(alias="moreAvailable")
    lines: list[QuickViewLogLineResponse]
    provider_status: MonitoringProviderStatusResponse | None = Field(default=None, alias="providerStatus")

    model_config = ConfigDict(populate_by_name=True)


class DeploymentObservabilityResponse(BaseModel):
    service_id: str = Field(alias="serviceId")
    context: DeploymentObservabilityContextResponse
    metrics: DeploymentObservabilityMetricsResponse
    health_timeline: DeploymentObservabilityTimelineResponse = Field(alias="healthTimeline")
    logs_quick_view: DeploymentObservabilityLogsResponse = Field(alias="logsQuickView")

    model_config = ConfigDict(populate_by_name=True)


class ActiveAlertResponse(BaseModel):
    id: str
    severity: str
    title: str
    description: str | None = None
    starts_at: str = Field(alias="startsAt")
    labels: dict[str, str]
    service_id: str | None = Field(default=None, alias="serviceId")
    env: str | None = None

    model_config = ConfigDict(populate_by_name=True)


class ActiveAlertsResponse(BaseModel):
    alerts: list[ActiveAlertResponse]
    provider_status: MonitoringProviderStatusResponse = Field(alias="providerStatus")

    model_config = ConfigDict(populate_by_name=True)


class MonitoringIncidentCompatResponse(BaseModel):
    id: str
    severity: str
    title: str
    status: str = "active"
    started_at: str = Field(alias="startedAt")
    source: str = "alertmanager"
    service_id: str | None = Field(default=None, alias="serviceId")

    model_config = ConfigDict(populate_by_name=True)


class MonitoringIncidentsCompatEnvelope(BaseModel):
    incidents: list[MonitoringIncidentCompatResponse]
    provider_status: MonitoringProviderStatusResponse | None = Field(
        default=None,
        alias="providerStatus",
    )

    model_config = ConfigDict(populate_by_name=True)


class ReleaseArgoStateResponse(BaseModel):
    app_name: str = Field(alias="appName")
    sync_status: str = Field(alias="syncStatus")
    health_status: str = Field(alias="healthStatus")
    revision: str | None = None

    model_config = ConfigDict(populate_by_name=True)


class ReleaseDriftStateResponse(BaseModel):
    is_drifted: bool = Field(alias="isDrifted")
    expected_revision: str | None = Field(default=None, alias="expectedRevision")
    live_revision: str | None = Field(default=None, alias="liveRevision")

    model_config = ConfigDict(populate_by_name=True)


class ReleaseTraceabilityResponse(BaseModel):
    service_id: str = Field(alias="serviceId")
    env: str
    commit_sha: str | None = Field(default=None, alias="commitSha")
    image_ref: str | None = Field(default=None, alias="imageRef")
    deployed_at: str | None = Field(default=None, alias="deployedAt")
    argo: ReleaseArgoStateResponse
    drift: ReleaseDriftStateResponse

    model_config = ConfigDict(populate_by_name=True)


class ReleaseDashboardCompatRow(BaseModel):
    service_id: str = Field(alias="serviceId")
    service_name: str = Field(alias="serviceName")
    environment: str
    commit_sha: str | None = Field(default=None, alias="commitSha")
    image: str | None = None
    sync: str
    health: str
    drift: bool
    deployed_at: str | None = Field(default=None, alias="deployedAt")

    model_config = ConfigDict(populate_by_name=True)


class ReleaseDashboardCompatResponse(BaseModel):
    releases: list[ReleaseDashboardCompatRow]

def require_bearer_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_auth),
) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
        )

    if credentials.credentials != "dev-static-token":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    return credentials.credentials


def _parse_csv_header(value: str | None) -> set[str]:
    if not value:
        return set()
    return {item.strip() for item in value.split(",") if item.strip()}


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_auth),
    x_auth_user: str | None = Header(None, alias="X-Auth-Request-User"),
    x_auth_groups: str | None = Header(None, alias="X-Auth-Request-Groups"),
) -> tuple[str, set[str]]:
    if x_auth_user:
        return x_auth_user, _parse_csv_header(x_auth_groups)
    return require_bearer_token(credentials), set()


def require_admin(
    identity: tuple[str, set[str]] = Depends(get_current_user),
) -> str:
    user, groups = identity
    if user == "dev-static-token":
        return user

    admin_users = _parse_csv_header(os.getenv("PORTAL_ADMIN_USERS", "admin"))
    admin_groups = _parse_csv_header(
        os.getenv("PORTAL_ADMIN_GROUPS", "team-admins")
    )
    if user in admin_users or groups.intersection(admin_groups):
        return user

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="User is not authorized for admin actions",
    )


def _with_connection() -> psycopg.Connection:
    return psycopg.connect(get_psycopg_database_url())


def _deployment_lock_stale_timeout_seconds() -> int:
    return max(60, int(os.getenv("DEPLOYMENT_LOCK_STALE_TIMEOUT_SECONDS", "1800")))


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


def _upsert_deployment_record_row(
    payload: CreateDeploymentRecordRequest,
    *,
    requested_by: str | None = None,
) -> dict[str, object]:
    service_rows = _load_service_rows(service_id=payload.service_id, env=payload.env)
    selected = _select_preferred_service_row(
        payload.service_id,
        service_rows,
        payload.env,
    )
    inferred_argo_app = (
        selected["argo_app_name"]
        if selected and isinstance(selected.get("argo_app_name"), str)
        else None
    )

    with _with_connection() as conn:
        cleanup_stale_deployment_locks(
            conn,
            service_id=payload.service_id,
            env=payload.env,
        )
        row = upsert_deployment_record(
            conn,
            service_id=payload.service_id,
            env=payload.env,
            action=payload.action,
            status=payload.status,
            requested_by=payload.requested_by or requested_by,
            requested_at=payload.requested_at,
            pr_url=payload.pr_url,
            pr_number=payload.pr_number,
            merge_sha=payload.merge_sha,
            target_image=payload.target_image,
            previous_image=payload.previous_image,
            argo_app=payload.argo_app or inferred_argo_app,
            sync_status=payload.sync_status,
            health_status=payload.health_status,
            started_at=payload.started_at,
            finished_at=payload.finished_at,
            deploy_window_start=payload.deploy_window_start,
            deploy_window_end=payload.deploy_window_end,
            deploy_reason=payload.deploy_reason,
            compare_url=payload.compare_url,
            git_ref=payload.git_ref,
            request_key=payload.request_key,
            metadata=payload.metadata,
        )
        sync_deployment_lock_for_deployment_row(
            conn,
            row,
            stale_after_seconds=_deployment_lock_stale_timeout_seconds(),
            enforce_conflict=True,
        )
    deployment_history_cache.clear()
    deployment_reconcile_cache.clear()
    return row


def _parse_bool_env(var_name: str, default: bool) -> bool:
    raw = os.getenv(var_name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _deployment_reconciler_enabled() -> bool:
    default_enabled = bool(os.getenv("KUBERNETES_SERVICE_HOST"))
    return _parse_bool_env("DEPLOYMENT_RECONCILER_ENABLED", default_enabled)


def _deployment_reconciler_readthrough_enabled() -> bool:
    return _parse_bool_env(
        "DEPLOYMENT_RECONCILER_READTHROUGH_ENABLED",
        _deployment_reconciler_enabled(),
    )


def _deployment_reconciler_interval_seconds() -> int:
    return max(15, int(os.getenv("DEPLOYMENT_RECONCILER_INTERVAL_SECONDS", "60")))


def _deployment_reconciler_read_ttl_seconds() -> int:
    return max(0, int(os.getenv("DEPLOYMENT_RECONCILER_READ_TTL_SECONDS", "30")))


def _reconcile_recent_deployment_activity(
    *,
    service_id: str | None = None,
    env: str | None = None,
) -> DeploymentReconcileResponse:
    with _with_connection() as conn:
        cleanup_stale_deployment_locks(conn)
        result = reconcile_recent_gitops_deployments(
            conn,
            load_service_rows=_load_service_rows,
            select_preferred_service_row=_select_preferred_service_row,
            load_live_argo_status=_load_live_argo_status_for_service,
            list_live_deployments=_list_live_deployments_for_service,
            extract_live_image_ref=_extract_live_deployment_image_ref,
            service_id=service_id,
            env=env,
        )
    deployment_history_cache.clear()
    return DeploymentReconcileResponse(**result)


def _maybe_reconcile_recent_deployments(
    *,
    service_id: str | None = None,
    env: str | None = None,
) -> DeploymentReconcileResponse | None:
    if not _deployment_reconciler_readthrough_enabled():
        return None

    cache_key = ("deployment-reconcile", service_id or "*", env or "*")
    ttl_seconds = _deployment_reconciler_read_ttl_seconds()

    def _load() -> DeploymentReconcileResponse | None:
        try:
            return _reconcile_recent_deployment_activity(service_id=service_id, env=env)
        except Exception as exc:  # pragma: no cover - live fallback only
            logger.warning(
                "deployment_reconcile_readthrough_failed service_id=%s env=%s error=%s",
                service_id,
                env,
                exc,
            )
            return None

    return deployment_reconcile_cache.get_or_set(
        key=cache_key,
        ttl_seconds=ttl_seconds,
        loader=_load,
    )


def _load_project_rows(env: str | None = None) -> list[dict[str, str | None]]:
    with _with_connection() as conn:
        with conn.cursor() as cur:
            if env:
                cur.execute(
                    """
                    SELECT project_id, project_name, env, owner, repo_url, runbook_url, observability_mode
                    FROM project_registry
                    WHERE source = %s
                      AND env = %s
                    ORDER BY project_id ASC, env ASC
                    """,
                    ("gitops_apps", env),
                )
            else:
                cur.execute(
                    """
                    SELECT project_id, project_name, env, owner, repo_url, runbook_url, observability_mode
                    FROM project_registry
                    WHERE source = %s
                    ORDER BY project_id ASC, env ASC
                    """,
                    ("gitops_apps",),
                )
            rows = cur.fetchall()

    return [
        {
            "service_id": row[0],
            "service_name": row[1],
            "env": row[2],
            "owner": row[3],
            "repo_url": row[4],
            "runbook_url": row[5],
            "observability_mode": row[6] if len(row) > 6 else None,
        }
        for row in rows
    ]


def _load_project_catalog_rows(
    *,
    env: str | None = None,
    project_id: str | None = None,
) -> list[dict[str, str]]:
    conditions = ["source = %s"]
    params: list[str] = ["gitops_apps"]
    if env:
        conditions.append("env = %s")
        params.append(env)
    if project_id:
        conditions.append("project_id = %s")
        params.append(project_id)

    where_clause = " AND ".join(conditions)
    with _with_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT project_id, project_name, env, namespace, app_label, source_ref, observability_mode, public_host
                FROM project_registry
                WHERE {where_clause}
                ORDER BY project_id ASC, env ASC
                """,
                tuple(params),
            )
            rows = cur.fetchall()

    return [
        {
            "project_id": row[0],
            "project_name": row[1],
            "env": row[2],
            "namespace": row[3],
            "app_label": row[4],
            "source_ref": row[5] if len(row) > 5 else None,
            "observability_mode": normalize_observability_mode(row[6] if len(row) > 6 else None),
            "public_host": row[7] if len(row) > 7 else None,
        }
        for row in rows
    ]


def _project_catalog_index(
    rows: list[dict[str, str | None]],
) -> dict[tuple[str, str], dict[str, str | None]]:
    return {
        (
            str(row.get("project_id") or "").strip(),
            str(row.get("env") or "").strip(),
        ): row
        for row in rows
    }


def _load_service_rows(
    *,
    env: str | None = None,
    namespace: str | None = None,
    service_id: str | None = None,
) -> list[dict[str, str | None]]:
    conditions = ["source = %s"]
    params: list[str] = ["cluster_services"]
    if env:
        conditions.append("env = %s")
        params.append(env)
    if namespace:
        conditions.append("namespace = %s")
        params.append(namespace)
    if service_id:
        conditions.append("service_id = %s")
        params.append(service_id)

    where_clause = " AND ".join(conditions)
    with _with_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT service_id, service_name, env, namespace, app_label, argo_app_name, source, source_ref, last_synced_at
                FROM service_registry
                WHERE {where_clause}
                ORDER BY service_id ASC, env ASC
                """,
                tuple(params),
            )
            rows = cur.fetchall()

    return [
        {
            "service_id": row[0],
            "service_name": row[1],
            "env": row[2],
            "namespace": row[3],
            "app_label": row[4],
            "argo_app_name": row[5],
            "source": row[6],
            "source_ref": row[7],
            "last_synced_at": row[8].isoformat() if row[8] else None,
        }
        for row in rows
    ]


def _load_service_catalog_rows(
    *,
    env: str | None = None,
    service_id: str | None = None,
) -> list[dict[str, str | None]]:
    return _load_service_rows(env=env, service_id=service_id)


def _resolve_service_monitoring_context(
    service_id: str,
) -> tuple[str, str, str | None]:
    preferred_env = os.getenv("PORTAL_ENV", "dev")
    rows = _load_service_rows(service_id=service_id, env=preferred_env)
    if not rows:
        rows = _load_service_rows(service_id=service_id)
    if not rows:
        project_rows = _load_project_catalog_rows(project_id=service_id)
        observability_mode = (
            normalize_observability_mode(project_rows[0].get("observability_mode"))
            if project_rows
            else None
        )
        return "default", service_id, observability_mode

    selected = _select_preferred_service_row(service_id, rows, preferred_env) or rows[0]
    namespace = str(selected.get("namespace") or "").strip() or "default"
    app_label = str(selected.get("app_label") or "").strip() or service_id
    selected_env = str(selected.get("env") or "").strip() or preferred_env
    project_rows = _load_project_catalog_rows(env=selected_env, project_id=service_id)
    if not project_rows:
        project_rows = _load_project_catalog_rows(project_id=service_id)
    observability_mode = (
        normalize_observability_mode(project_rows[0].get("observability_mode"))
        if project_rows
        else None
    )
    return namespace, app_label, observability_mode


def _resolve_service_monitoring_metadata(service_id: str) -> tuple[str, str]:
    namespace, app_label, _ = _resolve_service_monitoring_context(service_id)
    return namespace, app_label


def _build_service_metrics_probe_queries(
    *,
    namespace: str,
    app_label: str,
) -> dict[str, str]:
    ingress_service_pattern = f".*{escape_promql_regex_literal(app_label)}.*"
    return {
        "app_request_series": (
            f'count(http_requests_total{{namespace="{namespace}", app="{app_label}"}}) or vector(0)'
        ),
        "app_latency_series": (
            f'count(http_request_duration_seconds_bucket{{namespace="{namespace}", app="{app_label}"}}) or vector(0)'
        ),
        "ingress_request_source": "count(traefik_service_requests_total) or vector(0)",
        "ingress_latency_source": "count(traefik_service_request_duration_seconds_bucket) or vector(0)",
        "ingress_request_series": (
            f'count(traefik_service_requests_total{{service=~"{ingress_service_pattern}"}}) or vector(0)'
        ),
        "ingress_latency_series": (
            f'count(traefik_service_request_duration_seconds_bucket{{service=~"{ingress_service_pattern}"}}) or vector(0)'
        ),
    }


def _query_prometheus_series_present(
    query: str,
    label: str,
    *,
    correlation_id: str,
) -> bool:
    value = _query_prometheus_scalar(query, label, correlation_id=correlation_id)
    return bool(value and value > 0)


def _build_metrics_observability_diagnostics(
    *,
    service_id: str,
    namespace: str,
    app_label: str,
    observability_mode: str | None,
    missing_metrics: list[str],
    correlation_id: str,
) -> ServiceMetricsObservabilityDiagnosticsResponse:
    probe_queries = _build_service_metrics_probe_queries(namespace=namespace, app_label=app_label)
    normalized_mode = normalize_observability_mode(observability_mode)

    source_available: bool | None = None
    service_series_available: bool | None = None
    if normalized_mode == "app-native":
        request_series = _query_prometheus_series_present(
            probe_queries["app_request_series"],
            f"{service_id}_app_request_series",
            correlation_id=correlation_id,
        )
        latency_series = _query_prometheus_series_present(
            probe_queries["app_latency_series"],
            f"{service_id}_app_latency_series",
            correlation_id=correlation_id,
        )
        source_available = request_series or latency_series
        service_series_available = request_series and latency_series
    elif normalized_mode == "ingress-derived":
        request_source = _query_prometheus_series_present(
            probe_queries["ingress_request_source"],
            f"{service_id}_ingress_request_source",
            correlation_id=correlation_id,
        )
        latency_source = _query_prometheus_series_present(
            probe_queries["ingress_latency_source"],
            f"{service_id}_ingress_latency_source",
            correlation_id=correlation_id,
        )
        request_series = _query_prometheus_series_present(
            probe_queries["ingress_request_series"],
            f"{service_id}_ingress_request_series",
            correlation_id=correlation_id,
        )
        latency_series = _query_prometheus_series_present(
            probe_queries["ingress_latency_series"],
            f"{service_id}_ingress_latency_series",
            correlation_id=correlation_id,
        )
        source_available = request_source and latency_source
        service_series_available = request_series and latency_series

    diagnostics = build_service_metrics_observability_diagnostics(
        mode=observability_mode,
        missing_metrics=missing_metrics,
        source_available=source_available,
        service_series_available=service_series_available,
    )
    return ServiceMetricsObservabilityDiagnosticsResponse(**diagnostics)


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


def _portal_repo_slug() -> str:
    configured = os.getenv("PORTAL_DEPLOY_PORTAL_REPO", "").strip()
    if configured:
        return configured
    actions_repo = os.getenv("PORTAL_GITHUB_ACTIONS_REPO", "").strip()
    if actions_repo:
        return actions_repo
    return f"{DEFAULT_GITHUB_OWNER}/{DEFAULT_PORTAL_REPO}"


def _workloads_repo_slug() -> str:
    configured = os.getenv("PORTAL_DEPLOY_GITOPS_REPO", "").strip()
    if configured:
        return configured
    reconciler_repo = os.getenv("DEPLOYMENT_RECONCILER_GITOPS_REPO", "").strip()
    if reconciler_repo:
        owner = os.getenv("DEPLOYMENT_RECONCILER_GITHUB_OWNER", DEFAULT_GITHUB_OWNER).strip() or DEFAULT_GITHUB_OWNER
        return f"{owner}/{reconciler_repo}"
    return f"{DEFAULT_GITHUB_OWNER}/{DEFAULT_WORKLOADS_REPO}"


def _workloads_base_branch() -> str:
    configured = os.getenv("PORTAL_DEPLOY_GITOPS_BASE_BRANCH", "").strip()
    return configured or DEFAULT_PORTAL_IMAGES_WORKFLOW_REF


def _portal_images_workflow_file() -> str:
    configured = os.getenv("PORTAL_DEPLOY_WORKFLOW_FILE", "").strip()
    return configured or DEFAULT_PORTAL_IMAGES_WORKFLOW_FILE


def _portal_images_workflow_ref() -> str:
    configured = os.getenv("PORTAL_DEPLOY_WORKFLOW_REF", "").strip()
    return configured or DEFAULT_PORTAL_IMAGES_WORKFLOW_REF


def _github_metadata_token() -> str | None:
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


def _github_api_token_for_path(path: str) -> str | None:
    normalized = path.lstrip("/")
    if "/packages/container/" in normalized:
        return _ghcr_token()
    return _github_metadata_token()


def _github_api_base_url() -> str:
    return os.getenv("GITHUB_API_BASE_URL", "https://api.github.com").rstrip("/")


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


def _dev_deploy_target(service_id: str) -> dict[str, object]:
    target = DEV_DEPLOY_TARGETS.get(service_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Service {service_id!r} does not support deploy-to-dev.",
        )
    return target


def _promote_to_prod_target(service_id: str) -> dict[str, object]:
    target = PROMOTE_TO_PROD_TARGETS.get(service_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Service {service_id!r} does not support promote-to-prod.",
        )
    return target


def _rollback_target(service_id: str, target_environment: str) -> dict[str, object]:
    if target_environment == "dev":
        target = _dev_deploy_target(service_id)
        return {
            "image_repo": str(target["image_repo"]),
            "patch_files": [str(path) for path in target["patch_files"]],
            "argo_app": str(target["argo_app"]),
            "target_environment": "dev",
        }

    if target_environment == "prod":
        target = _promote_to_prod_target(service_id)
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


def _ghcr_token() -> str | None:
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

    return deployment_history_cache.get_or_set(
        key=cache_key,
        ttl_seconds=_deployment_history_cache_ttl_seconds(),
        loader=_load,
    )


def _load_deployment_metric_snapshots(
    service_row: dict[str, str | None] | None,
    release_row: dict[str, object],
) -> dict[str, dict[str, float]]:
    window_start, window_end = _resolve_record_window(release_row)
    if window_start is None or window_end is None:
        return {}
    return _load_metric_snapshots_for_window(
        service_row,
        window_start=window_start,
        window_end=window_end,
    )


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
    metric_snapshots = _load_deployment_metric_snapshots(service_row, record)
    image_ref = record.get("targetImage") if isinstance(record.get("targetImage"), str) else None
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else None
    failure_reason = (
        metadata.get("failureReason")
        if isinstance(metadata, dict) and isinstance(metadata.get("failureReason"), str)
        else None
    )

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
        errorRatePct=metric_snapshots.get("errorRatePct"),
        p95LatencyMs=metric_snapshots.get("p95LatencyMs"),
        availabilityPct=metric_snapshots.get("availabilityPct"),
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


def _query_prometheus_scalar(
    query: str,
    metric_name: str,
    *,
    correlation_id: str,
) -> float | None:
    encoded = urlparse.urlencode({"query": query})
    endpoint = f"{get_prometheus_base_url()}/api/v1/query?{encoded}"
    payload, _provider_status = load_json_from_provider(
        provider="prometheus",
        endpoint=endpoint,
        correlation_id=correlation_id,
        timeout_seconds=get_monitoring_timeout_seconds(),
        message="Monitoring provider query failed.",
    )

    if not isinstance(payload, dict) or payload.get("status") != "success":
        logger.error(
            "prometheus_bad_payload correlation_id=%s metric=%s payload_status=%s",
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
        return None

    sample = results[0].get("value")
    if (
        not isinstance(sample, list)
        or len(sample) < 2
        or not isinstance(sample[1], str)
    ):
        return None

    try:
        value = float(sample[1])
    except ValueError:
        return None

    if not math.isfinite(value):
        return None
    return value


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


def _query_loki_range(
    *,
    query: str,
    start: datetime,
    end: datetime,
    limit: int,
    correlation_id: str,
) -> list[tuple[int, str, dict[str, str]]]:
    encoded = urlparse.urlencode(
        {
            "query": query,
            "start": str(int(start.timestamp() * 1_000_000_000)),
            "end": str(int(end.timestamp() * 1_000_000_000)),
            "limit": str(limit),
            "direction": "backward",
        }
    )
    endpoint = f"{get_loki_base_url()}/loki/api/v1/query_range?{encoded}"
    payload, _provider_status = load_json_from_provider(
        provider="loki",
        endpoint=endpoint,
        correlation_id=correlation_id,
        timeout_seconds=get_monitoring_timeout_seconds(),
        message="Monitoring provider query failed.",
    )

    if not isinstance(payload, dict) or payload.get("status") != "success":
        logger.error(
            "loki_bad_payload correlation_id=%s payload_status=%s",
            correlation_id,
            payload.get("status") if isinstance(payload, dict) else type(payload).__name__,
        )
        raise_provider_bad_payload_error(
            provider="loki",
            base_url=get_loki_base_url(),
            correlation_id=correlation_id,
            checked_at=datetime.now(tz=timezone.utc).isoformat(),
            error=(
                f"unexpected payload status="
                f"{payload.get('status') if isinstance(payload, dict) else type(payload).__name__}"
            ),
            message="Monitoring provider query failed.",
        )

    result = payload.get("data", {}).get("result", [])
    if not isinstance(result, list):
        return []

    lines: list[tuple[int, str, dict[str, str]]] = []
    for stream in result:
        labels = stream.get("stream")
        values = stream.get("values")
        if not isinstance(labels, dict) or not isinstance(values, list):
            continue
        safe_labels = {str(k): str(v) for k, v in labels.items()}
        for value in values:
            if (
                not isinstance(value, list)
                or len(value) < 2
                or not isinstance(value[0], str)
                or not isinstance(value[1], str)
            ):
                continue
            try:
                ts_ns = int(value[0])
            except ValueError:
                continue
            lines.append((ts_ns, value[1], safe_labels))

    lines.sort(key=lambda item: item[0], reverse=True)
    return lines


def _query_alertmanager_active_alerts(
    *,
    correlation_id: str,
) -> tuple[list[dict], dict[str, object]]:
    endpoint = f"{get_alertmanager_base_url()}/api/v2/alerts"
    payload, provider_status = load_json_from_provider(
        provider="alertmanager",
        endpoint=endpoint,
        correlation_id=correlation_id,
        timeout_seconds=get_monitoring_timeout_seconds(),
        message="Monitoring provider query failed.",
    )

    if not isinstance(payload, list):
        logger.error(
            "alertmanager_bad_payload correlation_id=%s payload_type=%s",
            correlation_id,
            type(payload).__name__,
        )
        raise_provider_bad_payload_error(
            provider="alertmanager",
            base_url=get_alertmanager_base_url(),
            correlation_id=correlation_id,
            checked_at=datetime.now(tz=timezone.utc).isoformat(),
            error=f"unexpected payload type={type(payload).__name__}",
            message="Monitoring provider query failed.",
        )

    return payload, provider_status


def _validate_selected_range(
    *,
    selected_range: str,
    allowed_ranges: tuple[str, ...],
    field_name: str,
) -> str:
    if selected_range not in allowed_ranges:
        allowed = ",".join(allowed_ranges)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{field_name} must be one of: {allowed}",
        )
    return selected_range


def _effective_limit(requested: int, configured_max: int) -> int:
    return min(max(1, requested), max(1, configured_max))


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


def _serialize_metric_trend_points(points: dict[int, float]) -> list[ServiceMetricTrendPointResponse]:
    return [
        ServiceMetricTrendPointResponse(
            timestamp=datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(),
            value=value,
        )
        for timestamp, value in sorted(points.items())
    ]


def _build_metric_trend_series(
    *,
    field_name: str,
    query_candidates: tuple[str, ...],
    start: datetime,
    end: datetime,
    step_seconds: int,
    correlation_id: str,
) -> ServiceMetricTrendSeriesResponse:
    sources: tuple[Literal["app_metrics", "traefik_fallback"], ...] = (
        "app_metrics",
        "traefik_fallback",
    )

    for index, query in enumerate(query_candidates):
        points = _query_prometheus_range(
            query,
            f"{field_name}_{index}",
            start=start,
            end=end,
            step_seconds=step_seconds,
            correlation_id=correlation_id,
        )
        if not points:
            continue

        serialized_points = _serialize_metric_trend_points(points)
        latest_value = serialized_points[-1].value if serialized_points else None
        source = sources[index] if index < len(sources) else sources[-1]
        return ServiceMetricTrendSeriesResponse(
            queryStatus="ok",
            queryMessage=None,
            querySource=source,
            latestValue=latest_value,
            pointCount=len(serialized_points),
            points=serialized_points,
        )

    return ServiceMetricTrendSeriesResponse(
        queryStatus="no_data",
        queryMessage="Prometheus returned no retained samples for this metric and time window.",
        querySource=None,
        latestValue=None,
        pointCount=0,
        points=[],
    )


def _build_health_timeline_queries(
    *,
    namespace: str,
    app_label: str,
    config,
) -> dict[str, tuple[str, ...]]:
    deployment_name = app_label
    ingress_service_pattern = f".*{escape_promql_regex_literal(app_label)}.*"
    values = {
        "namespace": namespace,
        "app_label": app_label,
        "deployment_name": deployment_name,
        "ingress_service_pattern": ingress_service_pattern,
    }
    return {
        "availability": (
            render_query_template(
                config.timeline_query_availability_template,
                values,
                "timeline.availability",
            ),
        ),
        "errorRatePct": (
            render_query_template(
                config.timeline_query_error_rate_template,
                values,
                "timeline.error_rate",
            ),
            render_query_template(
                config.timeline_query_error_rate_fallback_template,
                values,
                "timeline.error_rate_fallback",
            ),
        ),
        "readiness": (
            render_query_template(
                config.timeline_query_readiness_template,
                values,
                "timeline.readiness",
            ),
        ),
    }


def _validate_step_for_range(*, range_value: str, step_value: str) -> int:
    config = load_observability_config()
    try:
        step_delta = parse_step(step_value)
        window_delta = parse_range(range_value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    min_step = config.timeline_step_min
    max_step = config.timeline_step_max
    if step_delta < min_step or step_delta > max_step:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"step must be between "
                f"{int(min_step.total_seconds() // 60)}m and {int(max_step.total_seconds() // 60)}m"
            ),
        )

    points = int(window_delta.total_seconds() / step_delta.total_seconds())
    if points > config.timeline_max_points:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="step produces too many samples for selected range",
        )

    return int(step_delta.total_seconds())


def _serialize_metric_snapshot(
    snapshot: dict[str, float] | None,
) -> DeploymentObservabilityMetricSnapshotResponse | None:
    if not snapshot:
        return None
    return DeploymentObservabilityMetricSnapshotResponse(
        before=snapshot.get("before"),
        after=snapshot.get("after"),
        delta=snapshot.get("delta"),
    )


def _select_timeline_step_seconds(window: timedelta, config) -> int:
    minimum = int(config.timeline_step_min.total_seconds())
    maximum = int(config.timeline_step_max.total_seconds())
    target = max(1, math.ceil(window.total_seconds() / max(1, config.timeline_max_points // 2)))
    return max(minimum, min(maximum, target))


def _resolve_deployment_observability_context(
    *,
    service_id: str,
    deployment_id: str | None,
    window_start_value: str | None,
    window_end_value: str | None,
) -> tuple[
    DeploymentObservabilityContextResponse,
    dict[str, str | None] | None,
    datetime | None,
    datetime | None,
]:
    if deployment_id and (window_start_value or window_end_value):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Use either deploymentId or windowStart/windowEnd, not both.",
        )

    if not deployment_id and not (window_start_value and window_end_value):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="deploymentId or both windowStart and windowEnd are required.",
        )

    if deployment_id:
        record = _get_deployment_record_by_id(deployment_id)
        if record is None or str(record.get("serviceId") or "") != service_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Deployment record not found for this service.",
            )
        record_env = str(record.get("env") or "").strip() or os.getenv("PORTAL_ENV", "dev")
        service_rows = _load_service_rows(service_id=service_id, env=record_env)
        selected = _select_preferred_service_row(service_id, service_rows, record_env)
        window_start, window_end = _resolve_record_window(record)
        evidence_status: Literal["resolved", "missing"] = "resolved" if window_start and window_end else "missing"
        evidence_message = None
        if evidence_status == "missing":
            evidence_message = "Deployment record does not have a usable deploy window yet."
        resolved_start = window_start
        resolved_end = window_end
        if window_start is not None and window_end is not None:
            resolved_start, resolved_end = _expand_observability_query_window(window_start, window_end)
        return (
            DeploymentObservabilityContextResponse(
                serviceId=service_id,
                env=record_env,
                deploymentId=deployment_id,
                action=record.get("action") if isinstance(record.get("action"), str) else None,
                status=record.get("status") if isinstance(record.get("status"), str) else None,
                windowStart=window_start.isoformat() if window_start else None,
                windowEnd=window_end.isoformat() if window_end else None,
                windowSource="deployment_record",
                evidenceStatus=evidence_status,
                evidenceMessage=evidence_message,
                compareUrl=record.get("compareUrl") if isinstance(record.get("compareUrl"), str) else None,
                gitPrUrl=record.get("prUrl") if isinstance(record.get("prUrl"), str) else None,
                gitPrNumber=record.get("prNumber") if isinstance(record.get("prNumber"), int) else None,
                deployReason=record.get("deployReason") if isinstance(record.get("deployReason"), str) else None,
            ),
            selected,
            resolved_start,
            resolved_end,
        )

    explicit_start = _parse_iso_datetime(window_start_value)
    explicit_end = _parse_iso_datetime(window_end_value)
    if explicit_start is None or explicit_end is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="windowStart and windowEnd must be valid ISO timestamps.",
        )
    if explicit_end <= explicit_start:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="windowEnd must be after windowStart.",
        )
    preferred_env = os.getenv("PORTAL_ENV", "dev")
    service_rows = _load_service_rows(service_id=service_id, env=preferred_env)
    selected = _select_preferred_service_row(service_id, service_rows, preferred_env)
    return (
        DeploymentObservabilityContextResponse(
            serviceId=service_id,
            env=preferred_env,
            deploymentId=None,
            action=None,
            status=None,
            windowStart=explicit_start.isoformat(),
            windowEnd=explicit_end.isoformat(),
            windowSource="explicit_window",
            evidenceStatus="resolved",
            evidenceMessage=None,
            compareUrl=None,
            gitPrUrl=None,
            gitPrNumber=None,
            deployReason=None,
        ),
        selected,
        explicit_start,
        explicit_end,
    )


def _build_no_window_metrics_response(
    *,
    message: str,
    window_start: datetime | None,
    window_end: datetime | None,
) -> DeploymentObservabilityMetricsResponse:
    return DeploymentObservabilityMetricsResponse(
        queryStatus="no_deployment_window",
        queryMessage=message,
        windowStart=window_start.isoformat() if window_start else None,
        windowEnd=window_end.isoformat() if window_end else None,
        generatedAt=now_utc().isoformat(),
        errorRatePct=None,
        p95LatencyMs=None,
        availabilityPct=None,
        noData={
            "errorRatePct": True,
            "p95LatencyMs": True,
            "availabilityPct": True,
        },
        providerStatus=None,
    )


def _build_no_window_timeline_response(
    *,
    service_id: str,
    message: str,
    window_start: datetime | None,
    window_end: datetime | None,
) -> DeploymentObservabilityTimelineResponse:
    return DeploymentObservabilityTimelineResponse(
        queryStatus="no_deployment_window",
        queryMessage=message,
        serviceId=service_id,
        windowStart=window_start.isoformat() if window_start else None,
        windowEnd=window_end.isoformat() if window_end else None,
        generatedAt=now_utc().isoformat(),
        providerStatus=None,
        segments=[],
    )


def _build_no_window_logs_response(
    *,
    service_id: str,
    preset: str,
    limit: int,
    message: str,
    window_start: datetime | None,
    window_end: datetime | None,
) -> DeploymentObservabilityLogsResponse:
    return DeploymentObservabilityLogsResponse(
        queryStatus="no_deployment_window",
        queryMessage=message,
        serviceId=service_id,
        preset=preset,
        generatedAt=now_utc().isoformat(),
        windowStart=window_start.isoformat() if window_start else None,
        windowEnd=window_end.isoformat() if window_end else None,
        limit=limit,
        returned=0,
        moreAvailable=False,
        lines=[],
        providerStatus=None,
    )


def _extract_provider_failure(exc: HTTPException) -> tuple[str, dict[str, object] | None]:
    detail = exc.detail
    if isinstance(detail, dict):
        message = detail.get("message")
        provider_status = detail.get("providerStatus")
        safe_message = message if isinstance(message, str) and message.strip() else "Monitoring provider query failed."
        safe_status = provider_status if isinstance(provider_status, dict) else None
        return safe_message, safe_status
    return "Monitoring provider query failed.", None


def _build_provider_error_metrics_response(
    *,
    message: str,
    provider_status: dict[str, object] | None,
    window_start: datetime,
    window_end: datetime,
) -> DeploymentObservabilityMetricsResponse:
    return DeploymentObservabilityMetricsResponse(
        queryStatus="no_data",
        queryMessage=message,
        windowStart=window_start.isoformat(),
        windowEnd=window_end.isoformat(),
        generatedAt=now_utc().isoformat(),
        errorRatePct=None,
        p95LatencyMs=None,
        availabilityPct=None,
        noData={
            "errorRatePct": True,
            "p95LatencyMs": True,
            "availabilityPct": True,
        },
        providerStatus=(
            MonitoringProviderStatusResponse(**provider_status)
            if isinstance(provider_status, dict)
            else None
        ),
    )


def _build_provider_error_timeline_response(
    *,
    service_id: str,
    message: str,
    provider_status: dict[str, object] | None,
    window_start: datetime,
    window_end: datetime,
) -> DeploymentObservabilityTimelineResponse:
    return DeploymentObservabilityTimelineResponse(
        queryStatus="no_data",
        queryMessage=message,
        serviceId=service_id,
        windowStart=window_start.isoformat(),
        windowEnd=window_end.isoformat(),
        generatedAt=now_utc().isoformat(),
        providerStatus=(
            MonitoringProviderStatusResponse(**provider_status)
            if isinstance(provider_status, dict)
            else None
        ),
        segments=[],
    )


def _build_provider_error_logs_response(
    *,
    service_id: str,
    preset: str,
    limit: int,
    message: str,
    provider_status: dict[str, object] | None,
    window_start: datetime,
    window_end: datetime,
) -> DeploymentObservabilityLogsResponse:
    return DeploymentObservabilityLogsResponse(
        queryStatus="no_data",
        queryMessage=message,
        serviceId=service_id,
        preset=preset,
        generatedAt=now_utc().isoformat(),
        windowStart=window_start.isoformat(),
        windowEnd=window_end.isoformat(),
        limit=limit,
        returned=0,
        moreAvailable=False,
        lines=[],
        providerStatus=(
            MonitoringProviderStatusResponse(**provider_status)
            if isinstance(provider_status, dict)
            else None
        ),
    )


def _build_deployment_metrics_response(
    *,
    service_id: str,
    service_row: dict[str, str | None] | None,
    window_start: datetime,
    window_end: datetime,
) -> DeploymentObservabilityMetricsResponse:
    now = datetime.now(tz=timezone.utc)
    snapshots = _load_metric_snapshots_for_window(
        service_row,
        window_start=window_start,
        window_end=window_end,
    )
    no_data = {
        "errorRatePct": "errorRatePct" not in snapshots,
        "p95LatencyMs": "p95LatencyMs" not in snapshots,
        "availabilityPct": "availabilityPct" not in snapshots,
    }
    query_status: Literal["ok", "no_data", "no_deployment_window"] = (
        "no_data" if all(no_data.values()) else "ok"
    )
    query_message = (
        "Prometheus returned no retained samples for this deployment window."
        if query_status == "no_data"
        else None
    )
    return DeploymentObservabilityMetricsResponse(
        queryStatus=query_status,
        queryMessage=query_message,
        windowStart=window_start.isoformat(),
        windowEnd=window_end.isoformat(),
        generatedAt=now.isoformat(),
        errorRatePct=_serialize_metric_snapshot(snapshots.get("errorRatePct")),
        p95LatencyMs=_serialize_metric_snapshot(snapshots.get("p95LatencyMs")),
        availabilityPct=_serialize_metric_snapshot(snapshots.get("availabilityPct")),
        noData=no_data,
        providerStatus=MonitoringProviderStatusResponse(
            **build_provider_status(
                provider="prometheus",
                base_url=get_prometheus_base_url(),
                status_value="healthy",
                reachable=True,
                checked_at=now.isoformat(),
                correlation_id=str(uuid4()),
            )
        ),
    )


def _build_deployment_timeline_response(
    *,
    service_id: str,
    service_row: dict[str, str | None] | None,
    window_start: datetime,
    window_end: datetime,
) -> DeploymentObservabilityTimelineResponse:
    now = datetime.now(tz=timezone.utc)
    correlation_id = str(uuid4())
    namespace = (
        str(service_row.get("namespace") or "").strip()
        if service_row
        else _resolve_service_monitoring_metadata(service_id)[0]
    )
    app_label = (
        str(service_row.get("app_label") or "").strip()
        if service_row
        else _resolve_service_monitoring_metadata(service_id)[1]
    )
    if not namespace or not app_label:
        namespace, app_label = _resolve_service_monitoring_metadata(service_id)
    config = load_observability_config()
    step_seconds = _select_timeline_step_seconds(window_end - window_start, config)
    cache_key = (
        "deployment-observability-timeline",
        service_id,
        namespace,
        app_label,
        window_start.isoformat(),
        window_end.isoformat(),
        step_seconds,
    )

    def _load_segments() -> list[ServiceHealthTimelineSegmentResponse]:
        queries = _build_health_timeline_queries(
            namespace=namespace,
            app_label=app_label,
            config=config,
        )
        availability_points = _query_prometheus_range(
            queries["availability"],
            "deployment_availability",
            start=window_start,
            end=window_end,
            step_seconds=step_seconds,
            correlation_id=correlation_id,
        )
        error_points = _query_prometheus_range(
            queries["errorRatePct"],
            "deployment_error_rate",
            start=window_start,
            end=window_end,
            step_seconds=step_seconds,
            correlation_id=correlation_id,
        )
        readiness_points = _query_prometheus_range(
            queries["readiness"],
            "deployment_readiness",
            start=window_start,
            end=window_end,
            step_seconds=step_seconds,
            correlation_id=correlation_id,
        )
        all_timestamps = sorted(
            set(availability_points.keys())
            .union(error_points.keys())
            .union(readiness_points.keys())
        )
        thresholds = load_timeline_thresholds()
        points: list[TimelinePoint] = []
        for ts in all_timestamps:
            status_label, reason = classify_timeline_status(
                availability=availability_points.get(ts),
                error_rate_pct=error_points.get(ts),
                readiness=readiness_points.get(ts),
                thresholds=thresholds,
            )
            points.append(
                TimelinePoint(
                    timestamp=datetime.fromtimestamp(ts, tz=timezone.utc),
                    status=status_label,
                    reason=reason,
                )
            )
        segments = compact_timeline_points(
            points,
            window_start=window_start,
            window_end=window_end,
            step=timedelta(seconds=step_seconds),
        )
        return [
            ServiceHealthTimelineSegmentResponse(
                start=segment.start.isoformat(),
                end=segment.end.isoformat(),
                status=segment.status,
                reason=segment.reason,
            )
            for segment in segments
        ]

    segments = timeline_cache.get_or_set(
        key=cache_key,
        ttl_seconds=config.timeline_cache_ttl_seconds,
        loader=_load_segments,
    )
    query_status: Literal["ok", "no_data", "no_deployment_window"] = "ok" if segments else "no_data"
    query_message = (
        None if segments else "Prometheus returned no retained health timeline data for this deployment window."
    )
    return DeploymentObservabilityTimelineResponse(
        queryStatus=query_status,
        queryMessage=query_message,
        serviceId=service_id,
        windowStart=window_start.isoformat(),
        windowEnd=window_end.isoformat(),
        generatedAt=now.isoformat(),
        providerStatus=MonitoringProviderStatusResponse(
            **build_provider_status(
                provider="prometheus",
                base_url=get_prometheus_base_url(),
                status_value="healthy",
                reachable=True,
                checked_at=now.isoformat(),
                correlation_id=correlation_id,
            )
        ),
        segments=segments,
    )


def _build_deployment_logs_response(
    *,
    service_id: str,
    service_row: dict[str, str | None] | None,
    preset: str,
    limit: int,
    window_start: datetime,
    window_end: datetime,
    identity_key: str,
) -> DeploymentObservabilityLogsResponse:
    now = datetime.now(tz=timezone.utc)
    enforce_logs_rate_limit(identity_key=identity_key, now=now)
    safe_preset = validate_preset(preset)
    config = load_observability_config()
    safe_limit = _effective_limit(limit, config.logs_max_lines)
    namespace = (
        str(service_row.get("namespace") or "").strip()
        if service_row
        else _resolve_service_monitoring_metadata(service_id)[0]
    )
    app_label = (
        str(service_row.get("app_label") or "").strip()
        if service_row
        else _resolve_service_monitoring_metadata(service_id)[1]
    )
    if not namespace or not app_label:
        namespace, app_label = _resolve_service_monitoring_metadata(service_id)
    query = build_preset_query(
        app_label=app_label,
        namespace=namespace or get_logs_default_namespace(),
        preset=safe_preset,
    )
    correlation_id = str(uuid4())
    cache_key = (
        "deployment-observability-logs",
        service_id,
        namespace,
        app_label,
        safe_preset,
        window_start.isoformat(),
        window_end.isoformat(),
        safe_limit,
    )
    lines = logs_quickview_cache.get_or_set(
        key=cache_key,
        ttl_seconds=config.logs_cache_ttl_seconds,
        loader=lambda: _query_loki_range(
            query=query,
            start=window_start,
            end=window_end,
            limit=safe_limit,
            correlation_id=correlation_id,
        ),
    )
    query_status: Literal["ok", "no_data", "no_deployment_window"] = "ok" if lines else "no_data"
    query_message = (
        None if lines else "Loki returned no retained log lines for this deployment window and preset."
    )
    return DeploymentObservabilityLogsResponse(
        queryStatus=query_status,
        queryMessage=query_message,
        serviceId=service_id,
        preset=safe_preset,
        generatedAt=now.isoformat(),
        windowStart=window_start.isoformat(),
        windowEnd=window_end.isoformat(),
        limit=safe_limit,
        returned=len(lines),
        moreAvailable=False,
        lines=[
            QuickViewLogLineResponse(
                timestamp=datetime.fromtimestamp(item[0] / 1_000_000_000, tz=timezone.utc).isoformat(),
                message=item[1],
                labels=item[2],
            )
            for item in lines
        ],
        providerStatus=MonitoringProviderStatusResponse(
            **build_provider_status(
                provider="loki",
                base_url=get_loki_base_url(),
                status_value="healthy",
                reachable=True,
                checked_at=now.isoformat(),
                correlation_id=correlation_id,
            )
        ),
    )


@app.get(
    "/health",
    response_model=HealthResponse,
    response_model_exclude_none=True,
    tags=["system"],
)
def health(
    include_providers: bool = Query(default=False, alias="includeProviders"),
) -> HealthResponse:
    if not include_providers:
        return HealthResponse(status="ok")

    providers = [
        probe_monitoring_provider("prometheus", correlation_id=str(uuid4())),
        probe_monitoring_provider("loki", correlation_id=str(uuid4())),
        probe_monitoring_provider("alertmanager", correlation_id=str(uuid4())),
    ]
    overall = "ok" if all(item["status"] == "healthy" for item in providers) else "degraded"
    return HealthResponse(
        status=overall,
        providers=[MonitoringProviderStatusResponse(**item) for item in providers],
    )


@app.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/auth/login", response_model=LoginResponse, tags=["auth"])
def login(payload: LoginRequest) -> LoginResponse:
    if payload.username != "admin" or payload.password != "changeme":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    expires_at = datetime.now(tz=timezone.utc) + timedelta(hours=1)
    return LoginResponse(
        access_token="dev-static-token",
        expires_at=expires_at.isoformat(),
    )


@app.get(
    "/projects",
    response_model=ProjectsResponse,
    response_model_exclude_none=True,
    tags=["metadata"],
)
def list_projects(
    env: str | None = Query(default=None),
    _: tuple[str, set[str]] = Depends(get_current_user),
) -> ProjectsResponse:
    rows = _load_project_rows(env=env)
    return ProjectsResponse(
        projects=[
            Project(
                id=row["service_id"],
                name=row["service_name"],
                environment=row["env"],
                owner=row["owner"] if isinstance(row.get("owner"), str) else None,
                repoUrl=row["repo_url"] if isinstance(row.get("repo_url"), str) else None,
                runbookUrl=row["runbook_url"] if isinstance(row.get("runbook_url"), str) else None,
                observabilityMode=(
                    row["observability_mode"] if isinstance(row.get("observability_mode"), str) else None
                ),
            )
            for row in rows
        ]
    )


@app.get(
    "/projects/diagnostics",
    response_model=ProjectCatalogDiagnosticsResponse,
    tags=["metadata"],
)
def get_project_catalog_diagnostics(
    env: str | None = Query(default=None),
    _: tuple[str, set[str]] = Depends(get_current_user),
) -> ProjectCatalogDiagnosticsResponse:
    with _with_connection() as conn:
        with conn.cursor() as cur:
            if env:
                cur.execute(
                    """
                    SELECT COUNT(*), MAX(last_synced_at)
                    FROM project_registry
                    WHERE source = %s
                      AND env = %s
                    """,
                    ("gitops_apps", env),
                )
            else:
                cur.execute(
                    """
                    SELECT COUNT(*), MAX(last_synced_at)
                    FROM project_registry
                    WHERE source = %s
                    """,
                    ("gitops_apps",),
                )
            count_row = cur.fetchone()

    row_count = int(count_row[0] or 0)
    last_synced_at = count_row[1]
    stale_after_minutes = _registry_stale_after_minutes()
    warning_after_minutes = _registry_warning_after_minutes(stale_after_minutes)
    now = datetime.now(tz=timezone.utc)

    is_empty = row_count == 0
    if is_empty:
        is_warning = False
        is_stale = False
        state = "empty"
    else:
        age = None if last_synced_at is None else now - last_synced_at
        if last_synced_at is None:
            is_warning = True
            is_stale = True
        else:
            is_warning = age > timedelta(minutes=warning_after_minutes)
            is_stale = age > timedelta(minutes=stale_after_minutes)
        if is_stale:
            state = "stale"
        elif is_warning:
            state = "warning"
        else:
            state = "fresh"

    catalog_join = build_catalog_join(
        project_rows=_load_project_catalog_rows(env=env),
        service_rows=_load_service_catalog_rows(env=env),
        env_filter=env,
        project_id_filter=None,
        service_id_filter=None,
    )

    return ProjectCatalogDiagnosticsResponse(
        generatedAt=now.isoformat(),
        env=env,
        freshness=ServiceRegistryFreshnessResponse(
            rowCount=row_count,
            lastSyncedAt=last_synced_at.isoformat() if last_synced_at else None,
            warningAfterMinutes=warning_after_minutes,
            staleAfterMinutes=stale_after_minutes,
            isEmpty=is_empty,
            isWarning=is_warning,
            isStale=is_stale,
            state=state,
        ),
        catalogJoin=CatalogJoinDiagnosticsResponse(**catalog_join["diagnostics"]),
    )


@app.post(
    "/projects",
    response_model=Project,
    status_code=status.HTTP_201_CREATED,
    tags=["metadata"],
)
def create_project(
    payload: CreateProjectRequest,
    admin_user: str = Depends(require_admin),
) -> Project:
    del payload, admin_user
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            "Projects are sourced from GitOps app definitions; "
            "manual project creation is not allowed."
        ),
    )


@app.get("/services", response_model=ServicesResponse, tags=["metadata"])
def list_services(
    env: str | None = Query(default=None),
    namespace: str | None = Query(default=None),
    _: tuple[str, set[str]] = Depends(get_current_user),
) -> ServicesResponse:
    rows = _load_service_rows(env=env, namespace=namespace)
    project_index = _project_catalog_index(_load_project_catalog_rows(env=env))
    return ServicesResponse(
        services=[
            ServiceRow(
                serviceId=str(row["service_id"]),
                serviceName=str(row["service_name"]),
                env=str(row["env"]),
                namespace=str(row["namespace"]),
                appLabel=str(row["app_label"]),
                argoAppName=row["argo_app_name"] if isinstance(row["argo_app_name"], str) else None,
                source=str(row["source"]),
                sourceRef=row["source_ref"] if isinstance(row["source_ref"], str) else None,
                lastSyncedAt=row["last_synced_at"] if isinstance(row["last_synced_at"], str) else None,
                observabilityMode=(
                    project_index.get((str(row["service_id"]), str(row["env"])), {}).get(
                        "observability_mode"
                    )
                ),
            )
            for row in rows
        ]
    )


@app.get("/services/{service_id}", response_model=ServiceDetailResponse, tags=["metadata"])
def get_service(
    service_id: str,
    env: str | None = Query(default=None),
    _: tuple[str, set[str]] = Depends(get_current_user),
) -> ServiceDetailResponse:
    preferred_env = env or os.getenv("PORTAL_ENV", "dev")
    _maybe_reconcile_recent_deployments(service_id=service_id, env=preferred_env)
    rows = _load_service_rows(service_id=service_id, env=env)
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Service not found",
        )

    selected = _select_preferred_service_row(service_id, rows, preferred_env) or rows[0]
    release_rows = _sort_release_rows_by_deployed_at(_load_release_rows_for_service(service_id, env))
    release = next((row for row in release_rows if _release_row_has_meaningful_metadata(row)), {})
    live_rows = _sort_release_rows_by_deployed_at(_load_live_service_runtime_rows(selected))
    live_release = next((row for row in live_rows if _release_row_has_meaningful_metadata(row)), {})
    argo = release.get("argo") if isinstance(release.get("argo"), dict) else {}
    live_argo = live_release.get("argo") if isinstance(live_release.get("argo"), dict) else {}
    image_ref = (
        release.get("imageRef")
        if isinstance(release.get("imageRef"), str) and release.get("imageRef")
        else live_release.get("imageRef")
        if isinstance(live_release.get("imageRef"), str)
        else None
    )
    active_lock = _get_active_deployment_lock(str(selected["service_id"]), str(selected["env"]))
    project_rows = _load_project_catalog_rows(
        env=str(selected["env"]),
        project_id=str(selected["service_id"]),
    )
    observability_mode = project_rows[0].get("observability_mode") if project_rows else None

    catalog_public_host = project_rows[0].get("public_host") if project_rows else None
    return ServiceDetailResponse(
        id=str(selected["service_id"]),
        name=str(selected["service_name"]),
        namespace=str(selected["namespace"]),
        env=str(selected["env"]),
        appLabel=str(selected["app_label"]),
        argoAppName=selected["argo_app_name"] if isinstance(selected["argo_app_name"], str) else None,
        version=_extract_version_from_image_ref(image_ref if isinstance(image_ref, str) else None),
        health=_coalesce_service_status(argo.get("healthStatus"), live_argo.get("healthStatus")),
        sync=_coalesce_service_status(argo.get("syncStatus"), live_argo.get("syncStatus")),
        source=str(selected["source"]),
        sourceRef=selected["source_ref"] if isinstance(selected["source_ref"], str) else None,
        lastSyncedAt=selected["last_synced_at"] if isinstance(selected["last_synced_at"], str) else None,
        observabilityMode=observability_mode if isinstance(observability_mode, str) else None,
        publicHost=catalog_public_host if isinstance(catalog_public_host, str) else None,
        deploymentLock=_build_deployment_lock_response(active_lock),
    )


@app.post(
    "/deployments/reconcile",
    response_model=DeploymentReconcileResponse,
    tags=["metadata"],
)
def reconcile_deployments(
    service_id: str | None = Query(default=None, alias="serviceId"),
    env: str | None = Query(default=None),
    _: str = Depends(require_admin),
) -> DeploymentReconcileResponse:
    deployment_reconcile_cache.clear()
    return _reconcile_recent_deployment_activity(service_id=service_id, env=env)


@app.get(
    "/deployments/{deployment_id}",
    response_model=DeploymentRecordResponse,
    tags=["metadata"],
)
def get_deployment(
    deployment_id: str,
    _: tuple[str, set[str]] = Depends(get_current_user),
) -> DeploymentRecordResponse:
    record = _get_deployment_record_by_id(deployment_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Deployment record not found",
        )

    service_id = str(record.get("serviceId") or "")
    env = record.get("env") if isinstance(record.get("env"), str) else None
    if service_id:
        _maybe_reconcile_recent_deployments(service_id=service_id, env=env)
        refreshed = _get_deployment_record_by_id(deployment_id)
        if refreshed is not None:
            record = refreshed

    service_rows = _load_service_rows(service_id=service_id or None, env=env)
    selected = _select_preferred_service_row(service_id, service_rows, env) if service_id else None
    return _build_deployment_record_response(record, selected)


@app.post(
    "/deployments",
    response_model=DeploymentRecordResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["metadata"],
)
def create_deployment_record(
    payload: CreateDeploymentRecordRequest,
    admin_user: str = Depends(require_admin),
) -> DeploymentRecordResponse:
    try:
        record = _upsert_deployment_record_row(payload, requested_by=admin_user)
    except DeploymentLockConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": (
                    f"Active deployment lock already exists for {payload.service_id}/{payload.env}. "
                    "Wait for the in-flight mutation to finish or clear its stale lock."
                ),
                "activeLock": _build_deployment_lock_response(exc.active_lock).model_dump(by_alias=True),
            },
        ) from exc
    service_rows = _load_service_rows(service_id=payload.service_id, env=payload.env)
    selected = _select_preferred_service_row(payload.service_id, service_rows, payload.env)
    return _build_deployment_record_response(record, selected)


@app.post(
    "/services/{service_id}/deploy-to-dev",
    response_model=PortalDeployToDevResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["metadata"],
)
def request_portal_deploy_to_dev(
    service_id: str,
    payload: PortalDeployToDevRequest,
    response: Response,
    identity: tuple[str, set[str]] = Depends(get_current_user),
) -> PortalDeployToDevResponse:
    requested_by, _groups = identity
    target = _dev_deploy_target(service_id)
    initiated_at = datetime.now(tz=timezone.utc)
    workloads_repo = _workloads_repo_slug()
    base_branch = _workloads_base_branch()

    latest_candidate = _resolve_latest_portal_image_candidate(service_id)
    new_tag = str(latest_candidate["tag"])
    new_image_ref = str(latest_candidate["imageRef"])

    try:
        git_provider = build_default_git_provider()
        _image_repo, previous_image_ref, updated_files = _load_dev_overlay_update_plan(
            git_provider,
            service_id=service_id,
            repo_slug=workloads_repo,
            branch=base_branch,
            new_image_ref=new_image_ref,
        )
    except GitServiceConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except GitServiceAuthError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except GitServiceConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except GitServiceError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except PortalDeployToDevError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    previous_tag = _extract_version_from_image_ref(previous_image_ref)
    compare_url = _build_compare_url_for_portal_tags(previous_tag, new_tag)
    if previous_image_ref == new_image_ref:
        response.status_code = status.HTTP_200_OK
        return PortalDeployToDevResponse(
            status="noop",
            action="deploy",
            serviceId=service_id,
            targetEnvironment="dev",
            requestedBy=requested_by,
            repository=workloads_repo,
            baseBranch=base_branch,
            branchName=None,
            deploymentId=None,
            gitPrUrl=None,
            gitPrNumber=None,
            previousTag=previous_tag,
            newTag=new_tag,
            previousImageRef=previous_image_ref,
            newImageRef=new_image_ref,
            compareUrl=compare_url,
            sourceCommitSha=latest_candidate.get("sourceCommitSha"),
            sourceWorkflowRunUrl=latest_candidate.get("workflowRunUrl"),
            message="Dev overlay already points at the latest deployable image tag.",
            initiatedAt=initiated_at.isoformat(),
        )

    active_lock = _get_active_deployment_lock(service_id, "dev")
    if active_lock is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": (
                    f"Active deployment lock already exists for {service_id}/dev. "
                    "Wait for the in-flight mutation to finish or clear its stale lock."
                ),
                "activeLock": _build_deployment_lock_response(active_lock).model_dump(by_alias=True),
            },
        )

    branch_name = _build_dev_deploy_branch_name(service_id, new_tag, initiated_at)
    pr_title = f"Deploy {service_id}: {new_tag} to dev"
    pr_body = _build_dev_deploy_pr_body(
        service_id=service_id,
        requested_by=requested_by,
        deploy_reason=payload.deploy_reason,
        previous_tag=previous_tag,
        new_tag=new_tag,
        new_image_ref=new_image_ref,
        compare_url=compare_url,
        source_commit_sha=latest_candidate.get("sourceCommitSha") if isinstance(latest_candidate.get("sourceCommitSha"), str) else None,
        workflow_run_url=latest_candidate.get("workflowRunUrl") if isinstance(latest_candidate.get("workflowRunUrl"), str) else None,
    )

    try:
        git_provider.create_branch(workloads_repo, base_branch, branch_name)
        git_provider.commit_to_branch(
            workloads_repo,
            branch_name,
            updated_files,
            pr_title,
        )
        pr = git_provider.open_pr(
            workloads_repo,
            branch_name,
            base_branch,
            pr_title,
            pr_body,
        )
    except GitServiceConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except GitServiceAuthError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except GitServiceConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except GitServiceError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    request_key = f"gitops-pr:{pr['number']}:{service_id}:dev:deploy"
    record_payload = CreateDeploymentRecordRequest(
        serviceId=service_id,
        env="dev",
        action="deploy",
        status="pending",
        requestedAt=initiated_at,
        requestedBy=requested_by,
        gitPrUrl=pr["url"],
        gitPrNumber=pr["number"],
        imageRef=new_image_ref,
        previousImageRef=previous_image_ref,
        argoApp=str(target["argo_app"]),
        gitRef=branch_name,
        deployReason=payload.deploy_reason,
        compareUrl=compare_url,
        requestKey=request_key,
        metadata={
            "source": "portal-deploy-to-dev",
            "sourceCommitSha": latest_candidate.get("sourceCommitSha"),
            "previousTag": previous_tag,
            "newTag": new_tag,
            "workflowRunId": latest_candidate.get("workflowRunId"),
            "workflowRunUrl": latest_candidate.get("workflowRunUrl"),
            "patchFiles": sorted(updated_files),
        },
    )

    try:
        record = _upsert_deployment_record_row(record_payload, requested_by=requested_by)
    except DeploymentLockConflictError as exc:
        try:
            git_provider.close_pr(workloads_repo, pr["number"])
        except Exception as close_exc:  # pragma: no cover - cleanup fallback only
            logger.warning(
                "deploy_to_dev_failed_to_close_pr service_id=%s pr_number=%s error=%s",
                service_id,
                pr["number"],
                close_exc,
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": (
                    f"Active deployment lock already exists for {service_id}/dev. "
                    "Wait for the in-flight mutation to finish or clear its stale lock."
                ),
                "activeLock": _build_deployment_lock_response(exc.active_lock).model_dump(by_alias=True),
            },
        ) from exc
    except Exception:
        try:
            git_provider.close_pr(workloads_repo, pr["number"])
        except Exception as close_exc:  # pragma: no cover - cleanup fallback only
            logger.warning(
                "deploy_to_dev_failed_to_close_pr service_id=%s pr_number=%s error=%s",
                service_id,
                pr["number"],
                close_exc,
            )
        raise

    return PortalDeployToDevResponse(
        status="accepted",
        action="deploy",
        serviceId=service_id,
        targetEnvironment="dev",
        requestedBy=requested_by,
        repository=workloads_repo,
        baseBranch=base_branch,
        branchName=branch_name,
        deploymentId=record.get("deploymentId") if isinstance(record.get("deploymentId"), str) else None,
        gitPrUrl=pr["url"],
        gitPrNumber=pr["number"],
        previousTag=previous_tag,
        newTag=new_tag,
        previousImageRef=previous_image_ref,
        newImageRef=new_image_ref,
        compareUrl=compare_url,
        sourceCommitSha=latest_candidate.get("sourceCommitSha"),
        sourceWorkflowRunUrl=latest_candidate.get("workflowRunUrl"),
        message=None,
        initiatedAt=initiated_at.isoformat(),
    )


@app.post(
    "/services/{service_id}/promote-to-prod",
    response_model=PortalPromoteToProdResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["metadata"],
)
def request_portal_promote_to_prod(
    service_id: str,
    payload: PortalPromoteToProdRequest,
    response: Response,
    identity: tuple[str, set[str]] = Depends(get_current_user),
) -> PortalPromoteToProdResponse:
    requested_by, _groups = identity
    target = _promote_to_prod_target(service_id)
    initiated_at = datetime.now(tz=timezone.utc)
    workloads_repo = _workloads_repo_slug()
    base_branch = _workloads_base_branch()

    try:
        git_provider = build_default_git_provider()
        image_repo, previous_image_ref, new_image_ref, new_tag, updated_files = _load_promote_to_prod_update_plan(
            git_provider,
            service_id=service_id,
            repo_slug=workloads_repo,
            branch=base_branch,
        )
        _ensure_ghcr_tag_exists(image_repo, new_tag)
    except GitServiceConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except GitServiceAuthError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except GitServiceConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except GitServiceError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except PortalPromoteToProdError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    previous_tag = _extract_version_from_image_ref(previous_image_ref)
    compare_url = _build_compare_url_for_portal_tags(previous_tag, new_tag)
    source_commit_sha = _extract_sha_from_tag(new_tag)

    if previous_image_ref == new_image_ref:
        response.status_code = status.HTTP_200_OK
        return PortalPromoteToProdResponse(
            status="noop",
            action="promote",
            serviceId=service_id,
            targetEnvironment="prod",
            requestedBy=requested_by,
            repository=workloads_repo,
            baseBranch=base_branch,
            branchName=None,
            deploymentId=None,
            gitPrUrl=None,
            gitPrNumber=None,
            previousTag=previous_tag,
            newTag=new_tag,
            previousImageRef=previous_image_ref,
            newImageRef=new_image_ref,
            compareUrl=compare_url,
            sourceCommitSha=source_commit_sha,
            message="Prod overlay already matches the current dev image tag.",
            initiatedAt=initiated_at.isoformat(),
        )

    active_lock = _get_active_deployment_lock(service_id, "prod")
    if active_lock is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": (
                    f"Active deployment lock already exists for {service_id}/prod. "
                    "Wait for the in-flight mutation to finish or clear its stale lock."
                ),
                "activeLock": _build_deployment_lock_response(active_lock).model_dump(by_alias=True),
            },
        )

    branch_name = _build_prod_promote_branch_name(service_id, new_tag, initiated_at)
    pr_title = f"Promote {service_id}: {new_tag} to prod"
    pr_body = _build_promote_to_prod_pr_body(
        service_id=service_id,
        requested_by=requested_by,
        deploy_reason=payload.deploy_reason,
        previous_tag=previous_tag,
        new_tag=new_tag,
        new_image_ref=new_image_ref,
        compare_url=compare_url,
    )

    try:
        git_provider.create_branch(workloads_repo, base_branch, branch_name)
        git_provider.commit_to_branch(
            workloads_repo,
            branch_name,
            updated_files,
            pr_title,
        )
        pr = git_provider.open_pr(
            workloads_repo,
            branch_name,
            base_branch,
            pr_title,
            pr_body,
        )
    except GitServiceConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except GitServiceAuthError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except GitServiceConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except GitServiceError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    request_key = f"gitops-pr:{pr['number']}:{service_id}:prod:promote"
    record_payload = CreateDeploymentRecordRequest(
        serviceId=service_id,
        env="prod",
        action="promote",
        status="pending",
        requestedAt=initiated_at,
        requestedBy=requested_by,
        gitPrUrl=pr["url"],
        gitPrNumber=pr["number"],
        imageRef=new_image_ref,
        previousImageRef=previous_image_ref,
        argoApp=str(target["argo_app"]),
        gitRef=branch_name,
        deployReason=payload.deploy_reason,
        compareUrl=compare_url,
        requestKey=request_key,
        metadata={
            "source": "portal-promote-to-prod",
            "previousTag": previous_tag,
            "newTag": new_tag,
            "patchFiles": sorted(updated_files),
            "sourceEnvironment": "dev",
            "targetEnvironment": "prod",
        },
    )

    try:
        record = _upsert_deployment_record_row(record_payload, requested_by=requested_by)
    except DeploymentLockConflictError as exc:
        try:
            git_provider.close_pr(workloads_repo, pr["number"])
        except Exception as close_exc:  # pragma: no cover - cleanup fallback only
            logger.warning(
                "promote_to_prod_failed_to_close_pr service_id=%s pr_number=%s error=%s",
                service_id,
                pr["number"],
                close_exc,
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": (
                    f"Active deployment lock already exists for {service_id}/prod. "
                    "Wait for the in-flight mutation to finish or clear its stale lock."
                ),
                "activeLock": _build_deployment_lock_response(exc.active_lock).model_dump(by_alias=True),
            },
        ) from exc
    except Exception:
        try:
            git_provider.close_pr(workloads_repo, pr["number"])
        except Exception as close_exc:  # pragma: no cover - cleanup fallback only
            logger.warning(
                "promote_to_prod_failed_to_close_pr service_id=%s pr_number=%s error=%s",
                service_id,
                pr["number"],
                close_exc,
            )
        raise

    return PortalPromoteToProdResponse(
        status="accepted",
        action="promote",
        serviceId=service_id,
        targetEnvironment="prod",
        requestedBy=requested_by,
        repository=workloads_repo,
        baseBranch=base_branch,
        branchName=branch_name,
        deploymentId=record.get("deploymentId") if isinstance(record.get("deploymentId"), str) else None,
        gitPrUrl=pr["url"],
        gitPrNumber=pr["number"],
        previousTag=previous_tag,
        newTag=new_tag,
        previousImageRef=previous_image_ref,
        newImageRef=new_image_ref,
        compareUrl=compare_url,
        sourceCommitSha=source_commit_sha,
        message=None,
        initiatedAt=initiated_at.isoformat(),
    )


@app.get(
    "/services/{service_id}/config",
    response_model=ServiceConfigResponse,
    tags=["metadata"],
)
def get_service_config(
    service_id: str,
    env: Literal["dev", "prod"],
    current_user: tuple[str, set[str]] = Depends(get_current_user),
) -> ServiceConfigResponse:
    workloads_repo = _workloads_repo_slug()
    base_branch = _workloads_base_branch()
    try:
        target = get_config_edit_target(service_id, env)
        git_provider = build_default_git_provider()
        config_contents = git_provider.read_file(workloads_repo, base_branch, target.file_path)
        data = parse_config_map_data(config_contents)
    except ConfigEditingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except GitServiceConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except GitServiceAuthError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except GitServiceError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    entries = [
        ServiceConfigEntry(
            key=key,
            value=data.get(key, ""),
            allowedValues=list(ALLOWED_CONFIG_VALUES.get(key, ())),
        )
        for key in target.allowed_keys
    ]
    return ServiceConfigResponse(serviceId=service_id, env=env, entries=entries)


@app.post(
    "/services/{service_id}/config/set",
    response_model=PortalSetConfigResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["metadata"],
)
def request_portal_set_config(
    service_id: str,
    payload: PortalSetConfigRequest,
    admin_user: str = Depends(require_admin),
) -> PortalSetConfigResponse:
    initiated_at = datetime.now(tz=timezone.utc)
    workloads_repo = _workloads_repo_slug()
    base_branch = _workloads_base_branch()

    try:
        enforce_config_edit_rate_limit(
            identity_key=f"config-edit:{admin_user}",
            now=initiated_at,
        )
        target = resolve_config_edit_target(service_id, payload.env, payload.config_key)
        normalized_value = normalize_config_value(payload.config_key, payload.config_value)
        git_provider = build_default_git_provider()
        config_contents = git_provider.read_file(workloads_repo, base_branch, target.file_path)
        updated_contents, previous_value = update_config_map_manifest_document(
            config_contents,
            target=target,
            config_key=payload.config_key,
            config_value=normalized_value,
        )
        patch_contents = git_provider.read_file(
            workloads_repo, base_branch, target.deployment_patch_file_path
        )
        checksum = compute_config_checksum_from_manifest(updated_contents)
        updated_patch_contents = update_deployment_patch_checksum(patch_contents, checksum)
    except ConfigEditingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except GitServiceConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except GitServiceAuthError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except GitServiceConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except GitServiceError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    if previous_value == normalized_value:
        return PortalSetConfigResponse(
            status="noop",
            serviceId=service_id,
            env=payload.env,
            configKey=payload.config_key,
            previousValue=previous_value,
            configValue=normalized_value,
            requestedBy=admin_user,
            repository=workloads_repo,
            baseBranch=base_branch,
            branchName=None,
            gitPrUrl=None,
            gitPrNumber=None,
            configFilePath=target.file_path,
            message="ConfigMap already contains the requested value.",
            initiatedAt=initiated_at.isoformat(),
        )

    branch_name = _build_config_edit_branch_name(service_id, payload.env, payload.config_key, initiated_at)
    pr_title = f"Config: {service_id} {payload.env} {payload.config_key} updated"
    pr_body = _build_config_edit_pr_body(
        service_id=service_id,
        env=payload.env,
        config_key=payload.config_key,
        config_value=normalized_value,
        previous_value=previous_value,
        requested_by=admin_user,
        config_file_path=target.file_path,
    )

    try:
        git_provider.create_branch(workloads_repo, base_branch, branch_name)
        git_provider.commit_to_branch(
            workloads_repo,
            branch_name,
            {
                target.file_path: updated_contents,
                target.deployment_patch_file_path: updated_patch_contents,
            },
            pr_title,
        )
        pr = git_provider.open_pr(
            workloads_repo,
            branch_name,
            base_branch,
            pr_title,
            pr_body,
        )
    except GitServiceConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except GitServiceAuthError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except GitServiceConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except GitServiceError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return PortalSetConfigResponse(
        status="accepted",
        serviceId=service_id,
        env=payload.env,
        configKey=payload.config_key,
        previousValue=previous_value,
        configValue=normalized_value,
        requestedBy=admin_user,
        repository=workloads_repo,
        baseBranch=base_branch,
        branchName=branch_name,
        gitPrUrl=pr["url"],
        gitPrNumber=pr["number"],
        configFilePath=target.file_path,
        message="Config update pull request created.",
        initiatedAt=initiated_at.isoformat(),
    )


@app.post(
    "/services/{service_id}/config/set-secret",
    response_model=PortalSetSecretResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["metadata"],
)
def request_portal_set_secret(
    service_id: str,
    payload: PortalSetSecretRequest,
    admin_user: str = Depends(require_admin),
) -> PortalSetSecretResponse:
    initiated_at = datetime.now(tz=timezone.utc)
    workloads_repo = _workloads_repo_slug()
    base_branch = _workloads_base_branch()

    try:
        enforce_secret_edit_rate_limit(
            identity_key=f"secret-edit:{admin_user}",
            now=initiated_at,
        )
        target = resolve_secret_edit_target(service_id, payload.env, payload.secret_key)
        git_provider = build_default_git_provider()
        encrypted_contents = git_provider.read_file(workloads_repo, base_branch, target.file_path)
        sops_config_contents = git_provider.read_file(workloads_repo, base_branch, ".sops.yaml")
        decrypted_manifest = decrypt_secret_manifest(encrypted_contents)
        updated_manifest = update_secret_manifest_document(
            decrypted_manifest,
            target=target,
            secret_key=payload.secret_key,
            secret_value=payload.secret_value,
        )
        encrypted_manifest = encrypt_secret_manifest(
            updated_manifest,
            target_file_path=target.file_path,
            sops_config_contents=sops_config_contents,
        )
    except SecretEditingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except GitServiceConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except GitServiceAuthError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except GitServiceConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except GitServiceError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    branch_name = _build_secret_edit_branch_name(service_id, payload.env, payload.secret_key, initiated_at)
    pr_title = f"Secret: {service_id} {payload.env} {payload.secret_key} updated"
    pr_body = _build_secret_edit_pr_body(
        service_id=service_id,
        env=payload.env,
        secret_key=payload.secret_key,
        requested_by=admin_user,
        secret_file_path=target.file_path,
    )

    try:
        git_provider.create_branch(workloads_repo, base_branch, branch_name)
        git_provider.commit_to_branch(
            workloads_repo,
            branch_name,
            {target.file_path: encrypted_manifest},
            pr_title,
        )
        pr = git_provider.open_pr(
            workloads_repo,
            branch_name,
            base_branch,
            pr_title,
            pr_body,
        )
    except GitServiceConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except GitServiceAuthError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except GitServiceConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except GitServiceError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return PortalSetSecretResponse(
        status="accepted",
        serviceId=service_id,
        env=payload.env,
        secretKey=payload.secret_key,
        requestedBy=admin_user,
        repository=workloads_repo,
        baseBranch=base_branch,
        branchName=branch_name,
        gitPrUrl=pr["url"],
        gitPrNumber=pr["number"],
        secretFilePath=target.file_path,
        message="Encrypted secret update pull request created.",
        initiatedAt=initiated_at.isoformat(),
    )


@app.get(
    "/services/{service_id}/rollback-candidates",
    response_model=PortalServiceRollbackCandidatesResponse,
    tags=["metadata"],
)
def list_service_rollback_candidates(
    service_id: str,
    target_environment: Literal["dev", "prod"] = Query(default="dev", alias="targetEnvironment"),
    identity: tuple[str, set[str]] = Depends(get_current_user),
) -> PortalServiceRollbackCandidatesResponse:
    _requested_by, _groups = identity
    initiated_at = datetime.now(tz=timezone.utc)
    workloads_repo = _workloads_repo_slug()
    base_branch = _workloads_base_branch()

    try:
        git_provider = build_default_git_provider()
        target = _rollback_target(service_id, target_environment)
        image_repo = str(target["image_repo"])
        patch_files = [str(path) for path in target["patch_files"]]
        previous_image_ref: str | None = None
        for file_path in patch_files:
            current_content = git_provider.read_file(workloads_repo, base_branch, file_path)
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
        current_tag = _extract_version_from_image_ref(previous_image_ref)
        candidates = _list_service_rollback_candidates(image_repo=image_repo, current_tag=current_tag)
    except GitServiceConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except GitServiceAuthError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except GitServiceConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except GitServiceError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except (PortalDeployToDevError, PortalPromoteToProdError, PortalServiceRollbackError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    return PortalServiceRollbackCandidatesResponse(
        serviceId=service_id,
        targetEnvironment=target_environment,
        currentTag=current_tag,
        currentImageRef=previous_image_ref,
        candidates=candidates,
        generatedAt=initiated_at.isoformat(),
    )


@app.post(
    "/services/{service_id}/rollback",
    response_model=PortalServiceRollbackResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["metadata"],
)
def request_service_rollback(
    service_id: str,
    payload: PortalServiceRollbackRequest,
    response: Response,
    identity: tuple[str, set[str]] = Depends(get_current_user),
) -> PortalServiceRollbackResponse:
    requested_by, _groups = identity
    initiated_at = datetime.now(tz=timezone.utc)
    workloads_repo = _workloads_repo_slug()
    base_branch = _workloads_base_branch()

    try:
        git_provider = build_default_git_provider()
        target = _rollback_target(service_id, payload.target_environment)
        image_repo, previous_image_ref, new_image_ref, updated_files = _load_service_rollback_update_plan(
            git_provider,
            service_id=service_id,
            repo_slug=workloads_repo,
            branch=base_branch,
            target_environment=payload.target_environment,
            rollback_tag=payload.rollback_tag,
        )
        _ensure_ghcr_tag_exists(image_repo, payload.rollback_tag, purpose="Rollback image tag")
    except GitServiceConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except GitServiceAuthError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except GitServiceConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except GitServiceError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except (PortalPromoteToProdError, PortalServiceRollbackError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    previous_tag = _extract_version_from_image_ref(previous_image_ref)
    compare_url = _build_compare_url_for_portal_tags(previous_tag, payload.rollback_tag)
    source_commit_sha = _extract_sha_from_tag(payload.rollback_tag)

    if previous_image_ref == new_image_ref:
        response.status_code = status.HTTP_200_OK
        return PortalServiceRollbackResponse(
            status="noop",
            action="rollback",
            serviceId=service_id,
            targetEnvironment=payload.target_environment,
            requestedBy=requested_by,
            repository=workloads_repo,
            baseBranch=base_branch,
            branchName=None,
            deploymentId=None,
            gitPrUrl=None,
            gitPrNumber=None,
            previousTag=previous_tag,
            newTag=payload.rollback_tag,
            previousImageRef=previous_image_ref,
            newImageRef=new_image_ref,
            compareUrl=compare_url,
            sourceCommitSha=source_commit_sha,
            message=f"{payload.target_environment.title()} overlay already matches the requested rollback tag.",
            initiatedAt=initiated_at.isoformat(),
        )

    active_lock = _get_active_deployment_lock(service_id, payload.target_environment)
    if active_lock is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": (
                    f"Active deployment lock already exists for {service_id}/{payload.target_environment}. "
                    "Wait for the in-flight mutation to finish or clear its stale lock."
                ),
                "activeLock": _build_deployment_lock_response(active_lock).model_dump(by_alias=True),
            },
        )

    branch_name = _build_service_rollback_branch_name(
        service_id,
        payload.target_environment,
        payload.rollback_tag,
        initiated_at,
    )
    pr_title = f"Rollback {service_id}: {payload.rollback_tag} in {payload.target_environment}"
    pr_body = _build_service_rollback_pr_body(
        service_id=service_id,
        target_environment=payload.target_environment,
        requested_by=requested_by,
        deploy_reason=payload.deploy_reason,
        previous_tag=previous_tag,
        rollback_tag=payload.rollback_tag,
        rollback_image_ref=new_image_ref,
        compare_url=compare_url,
    )

    try:
        git_provider.create_branch(workloads_repo, base_branch, branch_name)
        git_provider.commit_to_branch(
            workloads_repo,
            branch_name,
            updated_files,
            pr_title,
        )
        pr = git_provider.open_pr(
            workloads_repo,
            branch_name,
            base_branch,
            pr_title,
            pr_body,
        )
    except GitServiceConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except GitServiceAuthError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except GitServiceConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except GitServiceError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    request_key = f"gitops-pr:{pr['number']}:{service_id}:{payload.target_environment}:rollback"
    record_payload = CreateDeploymentRecordRequest(
        serviceId=service_id,
        env=payload.target_environment,
        action="rollback",
        status="pending",
        requestedAt=initiated_at,
        requestedBy=requested_by,
        gitPrUrl=pr["url"],
        gitPrNumber=pr["number"],
        imageRef=new_image_ref,
        previousImageRef=previous_image_ref,
        argoApp=str(target["argo_app"]),
        gitRef=branch_name,
        deployReason=payload.deploy_reason,
        compareUrl=compare_url,
        requestKey=request_key,
        metadata={
            "source": "portal-service-rollback",
            "previousTag": previous_tag,
            "newTag": payload.rollback_tag,
            "patchFiles": sorted(updated_files),
            "targetEnvironment": payload.target_environment,
        },
    )

    try:
        record = _upsert_deployment_record_row(record_payload, requested_by=requested_by)
    except DeploymentLockConflictError as exc:
        try:
            git_provider.close_pr(workloads_repo, pr["number"])
        except Exception as close_exc:  # pragma: no cover - cleanup fallback only
            logger.warning(
                "service_rollback_failed_to_close_pr service_id=%s pr_number=%s error=%s",
                service_id,
                pr["number"],
                close_exc,
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": (
                    f"Active deployment lock already exists for {service_id}/{payload.target_environment}. "
                    "Wait for the in-flight mutation to finish or clear its stale lock."
                ),
                "activeLock": _build_deployment_lock_response(exc.active_lock).model_dump(by_alias=True),
            },
        ) from exc
    except Exception:
        try:
            git_provider.close_pr(workloads_repo, pr["number"])
        except Exception as close_exc:  # pragma: no cover - cleanup fallback only
            logger.warning(
                "service_rollback_failed_to_close_pr service_id=%s pr_number=%s error=%s",
                service_id,
                pr["number"],
                close_exc,
            )
        raise

    return PortalServiceRollbackResponse(
        status="accepted",
        action="rollback",
        serviceId=service_id,
        targetEnvironment=payload.target_environment,
        requestedBy=requested_by,
        repository=workloads_repo,
        baseBranch=base_branch,
        branchName=branch_name,
        deploymentId=record.get("deploymentId") if isinstance(record.get("deploymentId"), str) else None,
        gitPrUrl=pr["url"],
        gitPrNumber=pr["number"],
        previousTag=previous_tag,
        newTag=payload.rollback_tag,
        previousImageRef=previous_image_ref,
        newImageRef=new_image_ref,
        compareUrl=compare_url,
        sourceCommitSha=source_commit_sha,
        message=None,
        initiatedAt=initiated_at.isoformat(),
    )


@app.post(
    "/rollbacks",
    response_model=PortalRollbackResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["metadata"],
)
def request_portal_rollback(
    payload: PortalRollbackRequest,
    admin_user: str = Depends(require_admin),
) -> PortalRollbackResponse:
    try:
        result = dispatch_portal_rollback_workflow(
            rollback_api_tag=payload.rollback_api_tag,
            rollback_web_tag=payload.rollback_web_tag,
            operator_reason=payload.reason,
            target_environment=payload.target_environment,
        )
    except GitHubWorkflowDispatchError as exc:
        status_code = (
            status.HTTP_503_SERVICE_UNAVAILABLE
            if exc.status_code is None or exc.status_code >= 500
            else status.HTTP_502_BAD_GATEWAY
        )
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    return PortalRollbackResponse(
        status="accepted",
        action="rollback",
        targetEnvironment=payload.target_environment,
        rollbackApiTag=payload.rollback_api_tag,
        rollbackWebTag=payload.rollback_web_tag,
        reason=payload.reason,
        requestedBy=admin_user,
        repository=result.repository,
        workflowFile=result.workflow_file,
        workflowRef=result.workflow_ref,
        workflowUrl=result.workflow_url,
        initiatedAt=datetime.now(tz=timezone.utc).isoformat(),
    )


@app.get(
    "/services/{service_id}/deployments",
    response_model=ServiceDeploymentsResponse,
    tags=["metadata"],
)
def get_service_deployments(
    service_id: str,
    env: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    _: tuple[str, set[str]] = Depends(get_current_user),
) -> ServiceDeploymentsResponse:
    selected_env = env or os.getenv("PORTAL_ENV", "dev")
    _maybe_reconcile_recent_deployments(service_id=service_id, env=selected_env)
    service_rows = _load_service_rows(service_id=service_id, env=env)
    selected = _select_preferred_service_row(
        service_id,
        service_rows,
        selected_env,
    )
    rows = _list_deployment_records_for_service(service_id, env=env, limit=limit)
    deployments = [_build_deployment_record_response(row, selected) for row in rows]

    return ServiceDeploymentsResponse(deployments=deployments)


@app.get(
    "/services/{service_id}/deployment-info",
    response_model=ServiceDeploymentInfoResponse,
    tags=["metadata"],
)
def get_service_deployment_info(
    service_id: str,
    env: str | None = Query(default=None),
    _: tuple[str, set[str]] = Depends(get_current_user),
) -> ServiceDeploymentInfoResponse:
    selected_env = env or os.getenv("PORTAL_ENV", "dev")
    _maybe_reconcile_recent_deployments(service_id=service_id, env=selected_env)
    records = _list_deployment_records_for_service(service_id, env=env, limit=50)
    record = _select_latest_deployment_info_record(records)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Deployment info not found",
        )

    resolved_env = record.get("env") if isinstance(record.get("env"), str) else selected_env
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else None
    deployed_image = record.get("targetImage") if isinstance(record.get("targetImage"), str) else None
    previous_image = record.get("previousImage") if isinstance(record.get("previousImage"), str) else None
    commit_sha = record.get("mergeSha") if isinstance(record.get("mergeSha"), str) else None
    if commit_sha is None and isinstance(metadata, dict):
        source_commit_sha = metadata.get("sourceCommitSha")
        if isinstance(source_commit_sha, str) and source_commit_sha.strip():
            commit_sha = source_commit_sha.strip()
    result_reason = None
    if isinstance(metadata, dict):
        failure_reason = metadata.get("failureReason")
        if isinstance(failure_reason, str) and failure_reason.strip():
            result_reason = failure_reason.strip()
    if result_reason is None:
        deploy_reason = record.get("deployReason")
        if isinstance(deploy_reason, str) and deploy_reason.strip():
            result_reason = deploy_reason.strip() if str(record.get("status") or "").strip().lower() == "failed" else None

    return ServiceDeploymentInfoResponse(
        deploymentId=record.get("deploymentId") if isinstance(record.get("deploymentId"), str) else None,
        serviceId=service_id,
        env=resolved_env,
        action=record.get("action") if isinstance(record.get("action"), str) else None,
        deployedImage=deployed_image,
        previousImage=previous_image,
        imageDigest=_extract_image_digest(record),
        gitCommit=commit_sha,
        deployedTimestamp=_deployment_record_timestamp(record),
        gitPrUrl=record.get("prUrl") if isinstance(record.get("prUrl"), str) else None,
        gitPrNumber=record.get("prNumber") if isinstance(record.get("prNumber"), int) else None,
        compareUrl=record.get("compareUrl") if isinstance(record.get("compareUrl"), str) else None,
        deployReason=record.get("deployReason") if isinstance(record.get("deployReason"), str) else None,
        result=record.get("status") if isinstance(record.get("status"), str) else None,
        resultReason=result_reason,
        commitUrl=_build_commit_url(commit_sha),
        imageUrl=_build_package_url_from_image_ref(deployed_image),
        argoApp=record.get("argoApp") if isinstance(record.get("argoApp"), str) else None,
        syncStatus=record.get("syncStatus") if isinstance(record.get("syncStatus"), str) else None,
        healthStatus=record.get("healthStatus") if isinstance(record.get("healthStatus"), str) else None,
    )


@app.get(
    "/services/{service_id}/observability/window",
    response_model=DeploymentObservabilityResponse,
    tags=["monitoring"],
)
def get_service_deployment_observability(
    service_id: str,
    deployment_id: str | None = Query(default=None, alias="deploymentId"),
    window_start: str | None = Query(default=None, alias="windowStart"),
    window_end: str | None = Query(default=None, alias="windowEnd"),
    logs_preset: str = Query(default="all", alias="logsPreset"),
    logs_limit: int = Query(default=50, alias="logsLimit", ge=1, le=200),
    identity: tuple[str, set[str]] = Depends(get_current_user),
) -> DeploymentObservabilityResponse:
    user, _groups = identity
    context, service_row, resolved_start, resolved_end = _resolve_deployment_observability_context(
        service_id=service_id,
        deployment_id=deployment_id,
        window_start_value=window_start,
        window_end_value=window_end,
    )
    if context.evidence_status != "resolved" or resolved_start is None or resolved_end is None:
        message = context.evidence_message or "No deployment window was available for this deployment record."
        return DeploymentObservabilityResponse(
            serviceId=service_id,
            context=context,
            metrics=_build_no_window_metrics_response(
                message=message,
                window_start=resolved_start,
                window_end=resolved_end,
            ),
            healthTimeline=_build_no_window_timeline_response(
                service_id=service_id,
                message=message,
                window_start=resolved_start,
                window_end=resolved_end,
            ),
            logsQuickView=_build_no_window_logs_response(
                service_id=service_id,
                preset=logs_preset,
                limit=logs_limit,
                message=message,
                window_start=resolved_start,
                window_end=resolved_end,
            ),
        )

    try:
        metrics = _build_deployment_metrics_response(
            service_id=service_id,
            service_row=service_row,
            window_start=resolved_start,
            window_end=resolved_end,
        )
    except HTTPException as exc:
        if exc.status_code != status.HTTP_502_BAD_GATEWAY:
            raise
        message, provider_status = _extract_provider_failure(exc)
        metrics = _build_provider_error_metrics_response(
            message=message,
            provider_status=provider_status,
            window_start=resolved_start,
            window_end=resolved_end,
        )

    try:
        timeline = _build_deployment_timeline_response(
            service_id=service_id,
            service_row=service_row,
            window_start=resolved_start,
            window_end=resolved_end,
        )
    except HTTPException as exc:
        if exc.status_code != status.HTTP_502_BAD_GATEWAY:
            raise
        message, provider_status = _extract_provider_failure(exc)
        timeline = _build_provider_error_timeline_response(
            service_id=service_id,
            message=message,
            provider_status=provider_status,
            window_start=resolved_start,
            window_end=resolved_end,
        )

    try:
        logs = _build_deployment_logs_response(
            service_id=service_id,
            service_row=service_row,
            preset=logs_preset,
            limit=logs_limit,
            window_start=resolved_start,
            window_end=resolved_end,
            identity_key=user,
        )
    except HTTPException as exc:
        if exc.status_code != status.HTTP_502_BAD_GATEWAY:
            raise
        message, provider_status = _extract_provider_failure(exc)
        logs = _build_provider_error_logs_response(
            service_id=service_id,
            preset=logs_preset,
            limit=logs_limit,
            message=message,
            provider_status=provider_status,
            window_start=resolved_start,
            window_end=resolved_end,
        )
    except ValueError as exc:
        detail = str(exc)
        status_code = (
            status.HTTP_429_TOO_MANY_REQUESTS
            if "rate limit" in detail.lower()
            else status.HTTP_422_UNPROCESSABLE_ENTITY
        )
        raise HTTPException(status_code=status_code, detail=detail) from exc

    return DeploymentObservabilityResponse(
        serviceId=service_id,
        context=context,
        metrics=metrics,
        healthTimeline=timeline,
        logsQuickView=logs,
    )


@app.get("/catalog/reconciliation", response_model=CatalogJoinResponse, tags=["metadata"])
def get_catalog_reconciliation(
    env: str | None = Query(default=None),
    project_id: str | None = Query(default=None, alias="projectId"),
    service_id: str | None = Query(default=None, alias="serviceId"),
    _: tuple[str, set[str]] = Depends(get_current_user),
) -> CatalogJoinResponse:
    now = datetime.now(tz=timezone.utc)
    result = build_catalog_join(
        project_rows=_load_project_catalog_rows(env=env, project_id=project_id),
        service_rows=_load_service_catalog_rows(env=env, service_id=service_id),
        env_filter=env,
        project_id_filter=project_id,
        service_id_filter=service_id,
    )
    return CatalogJoinResponse(
        generatedAt=now.isoformat(),
        env=env,
        rows=[CatalogJoinRowResponse(**row) for row in result["rows"]],
        diagnostics=CatalogJoinDiagnosticsResponse(**result["diagnostics"]),
    )


@app.post(
    "/service-registry/sync",
    response_model=ServiceRegistrySyncResponse,
    tags=["metadata"],
)
def sync_service_registry(
    source: str = Query(default="cluster_services"),
    env: str | None = Query(default=None),
    _: str = Depends(require_admin),
) -> ServiceRegistrySyncResponse:
    with _with_connection() as conn:
        if source == "cluster_services":
            summary = sync_service_registry_from_cluster(conn, env_name=env)
        elif source == "gitops_apps":
            summary = sync_project_registry_from_gitops(conn, env_name=env)
        else:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="source must be one of: cluster_services,gitops_apps",
            )
    return ServiceRegistrySyncResponse(**summary)


@app.get(
    "/service-registry/diagnostics",
    response_model=ServiceRegistryDiagnosticsResponse,
    tags=["metadata"],
)
def get_service_registry_diagnostics(
    env: str | None = Query(default=None),
    _: tuple[str, set[str]] = Depends(get_current_user),
) -> ServiceRegistryDiagnosticsResponse:
    with _with_connection() as conn:
        with conn.cursor() as cur:
            if env:
                cur.execute(
                    """
                    SELECT COUNT(*), MAX(last_synced_at)
                    FROM service_registry
                    WHERE env = %s
                    """,
                    (env,),
                )
            else:
                cur.execute(
                    """
                    SELECT COUNT(*), MAX(last_synced_at)
                    FROM service_registry
                    """
                )
            count_row = cur.fetchone()

    row_count = int(count_row[0] or 0)
    last_synced_at = count_row[1]
    stale_after_minutes = _registry_stale_after_minutes()
    warning_after_minutes = _registry_warning_after_minutes(stale_after_minutes)
    now = datetime.now(tz=timezone.utc)

    is_empty = row_count == 0
    if is_empty:
        is_warning = False
        is_stale = False
        state = "empty"
    else:
        age = None if last_synced_at is None else now - last_synced_at
        if last_synced_at is None:
            is_warning = True
            is_stale = True
        else:
            is_warning = age > timedelta(minutes=warning_after_minutes)
            is_stale = age > timedelta(minutes=stale_after_minutes)
        if is_stale:
            state = "stale"
        elif is_warning:
            state = "warning"
        else:
            state = "fresh"

    project_rows = _load_project_rows()
    project_catalog_rows = _load_project_catalog_rows(env=env)
    service_catalog_rows = _load_service_catalog_rows(env=env)
    ci_rows = load_ci_metadata_rows()
    argo_rows = load_argo_metadata_rows()
    mismatches = build_release_join_diagnostics(
        project_rows=project_rows,
        ci_rows=ci_rows,
        argo_rows=argo_rows,
        env_filter=env,
        service_id_filter=None,
    )
    catalog_join = build_catalog_join(
        project_rows=project_catalog_rows,
        service_rows=service_catalog_rows,
        env_filter=env,
        project_id_filter=None,
        service_id_filter=None,
    )
    identity_drift = build_service_identity_diagnostics(
        project_rows=project_catalog_rows,
        service_rows=service_catalog_rows,
        ci_rows=ci_rows,
        argo_rows=argo_rows,
        env_filter=env,
        service_id_filter=None,
    )

    return ServiceRegistryDiagnosticsResponse(
        generatedAt=now.isoformat(),
        env=env,
        freshness=ServiceRegistryFreshnessResponse(
            rowCount=row_count,
            lastSyncedAt=last_synced_at.isoformat() if last_synced_at else None,
            warningAfterMinutes=warning_after_minutes,
            staleAfterMinutes=stale_after_minutes,
            isEmpty=is_empty,
            isWarning=is_warning,
            isStale=is_stale,
            state=state,
        ),
        joinMismatch=ServiceRegistryJoinMismatchResponse(**mismatches),
        catalogJoin=CatalogJoinDiagnosticsResponse(**catalog_join["diagnostics"]),
        identityDrift=ServiceIdentityDiagnosticsResponse(**identity_drift),
    )


@app.get(
    "/monitoring/providers/diagnostics",
    response_model=MonitoringProvidersDiagnosticsResponse,
    tags=["monitoring"],
)
def get_monitoring_provider_diagnostics(
    _: tuple[str, set[str]] = Depends(get_current_user),
) -> MonitoringProvidersDiagnosticsResponse:
    generated_at = datetime.now(tz=timezone.utc).isoformat()
    providers = [
        probe_monitoring_provider("prometheus", correlation_id=str(uuid4())),
        probe_monitoring_provider("loki", correlation_id=str(uuid4())),
        probe_monitoring_provider("alertmanager", correlation_id=str(uuid4())),
    ]
    overall_status = (
        "healthy" if all(item["status"] == "healthy" for item in providers) else "degraded"
    )
    return MonitoringProvidersDiagnosticsResponse(
        generatedAt=generated_at,
        overallStatus=overall_status,
        providers=[MonitoringProviderStatusResponse(**item) for item in providers],
    )


@app.get(
    "/services/{service_id}/metrics/summary",
    response_model=ServiceMetricsSummaryResponse,
    tags=["monitoring"],
)
def get_service_metrics_summary(
    service_id: str,
    selected_range: str = Query(
        default="24h",
        alias="range",
        pattern="^([1-9][0-9]*)(m|h|d)$",
    ),
    _: tuple[str, set[str]] = Depends(get_current_user),
) -> ServiceMetricsSummaryResponse:
    config = load_observability_config()
    safe_range = _validate_selected_range(
        selected_range=selected_range,
        allowed_ranges=config.metrics_allowed_ranges,
        field_name="range",
    )
    namespace, app_label, observability_mode = _resolve_service_monitoring_context(service_id)

    def _load_summary() -> ServiceMetricsSummaryResponse:
        now = datetime.now(tz=timezone.utc)
        correlation_id = str(uuid4())
        durations = {
            "1h": timedelta(hours=1),
            "24h": timedelta(hours=24),
            "7d": timedelta(days=7),
        }
        window_start = now - durations[safe_range]
        queries = _build_service_metrics_queries(
            namespace=namespace,
            app_label=app_label,
            selected_range=safe_range,
            config=config,
        )
        values: dict[str, float | None] = {}
        no_data: dict[str, bool] = {}

        for field_name, query_candidates in queries.items():
            value = None
            for query in query_candidates:
                value = _query_prometheus_scalar(
                    query,
                    field_name,
                    correlation_id=correlation_id,
                )
                if value is not None:
                    break
            values[field_name] = value
            no_data[field_name] = value is None

        observability_diagnostics = _build_metrics_observability_diagnostics(
            service_id=service_id,
            namespace=namespace,
            app_label=app_label,
            observability_mode=observability_mode,
            missing_metrics=[
                field_name
                for field_name in ("p95LatencyMs", "errorRatePct")
                if no_data.get(field_name, False)
            ],
            correlation_id=correlation_id,
        )

        return ServiceMetricsSummaryResponse(
            serviceId=service_id,
            uptimePct=values["uptimePct"],
            p95LatencyMs=values["p95LatencyMs"],
            errorRatePct=values["errorRatePct"],
            restartCount=values["restartCount"],
            windowStart=window_start.isoformat(),
            windowEnd=now.isoformat(),
            generatedAt=now.isoformat(),
            noData=no_data,
            providerStatus=MonitoringProviderStatusResponse(
                **build_provider_status(
                    provider="prometheus",
                    base_url=get_prometheus_base_url(),
                    status_value="healthy",
                    reachable=True,
                    checked_at=now.isoformat(),
                    correlation_id=correlation_id,
                )
            ),
            observabilityDiagnostics=observability_diagnostics,
        )

    return metrics_summary_cache.get_or_set(
        key=("metrics-summary", service_id, safe_range),
        ttl_seconds=config.metrics_cache_ttl_seconds,
        loader=_load_summary,
    )


@app.get(
    "/services/{service_id}/metrics/trends",
    response_model=ServiceMetricsTrendsResponse,
    tags=["monitoring"],
)
def get_service_metrics_trends(
    service_id: str,
    selected_range: str = Query(
        default="24h",
        alias="range",
        pattern="^([1-9][0-9]*)(m|h|d)$",
    ),
    _: tuple[str, set[str]] = Depends(get_current_user),
) -> ServiceMetricsTrendsResponse:
    config = load_observability_config()
    safe_range = _validate_selected_range(
        selected_range=selected_range,
        allowed_ranges=config.metrics_allowed_ranges,
        field_name="range",
    )
    namespace, app_label, observability_mode = _resolve_service_monitoring_context(service_id)

    def _load_trends() -> ServiceMetricsTrendsResponse:
        now = datetime.now(tz=timezone.utc)
        durations = {
            "1h": timedelta(hours=1),
            "24h": timedelta(hours=24),
            "7d": timedelta(days=7),
        }
        window_start = now - durations[safe_range]
        step_seconds = _select_timeline_step_seconds(now - window_start, config)
        correlation_id = str(uuid4())
        queries = _build_service_metrics_queries(
            namespace=namespace,
            app_label=app_label,
            selected_range=safe_range,
            config=config,
        )

        p95_latency = _build_metric_trend_series(
            field_name="p95LatencyMs",
            query_candidates=queries["p95LatencyMs"],
            start=window_start,
            end=now,
            step_seconds=step_seconds,
            correlation_id=correlation_id,
        )
        error_rate = _build_metric_trend_series(
            field_name="errorRatePct",
            query_candidates=queries["errorRatePct"],
            start=window_start,
            end=now,
            step_seconds=step_seconds,
            correlation_id=correlation_id,
        )
        observability_diagnostics = _build_metrics_observability_diagnostics(
            service_id=service_id,
            namespace=namespace,
            app_label=app_label,
            observability_mode=observability_mode,
            missing_metrics=[
                field_name
                for field_name, series in (
                    ("p95LatencyMs", p95_latency),
                    ("errorRatePct", error_rate),
                )
                if series.query_status == "no_data"
            ],
            correlation_id=correlation_id,
        )

        return ServiceMetricsTrendsResponse(
            serviceId=service_id,
            range=safe_range,
            windowStart=window_start.isoformat(),
            windowEnd=now.isoformat(),
            generatedAt=now.isoformat(),
            providerStatus=MonitoringProviderStatusResponse(
                **build_provider_status(
                    provider="prometheus",
                    base_url=get_prometheus_base_url(),
                    status_value="healthy",
                    reachable=True,
                    checked_at=now.isoformat(),
                    correlation_id=correlation_id,
                )
            ),
            p95LatencyMs=p95_latency,
            errorRatePct=error_rate,
            observabilityDiagnostics=observability_diagnostics,
        )

    return metrics_summary_cache.get_or_set(
        key=("metrics-trends", service_id, safe_range),
        ttl_seconds=config.metrics_cache_ttl_seconds,
        loader=_load_trends,
    )


@app.get(
    "/services/{service_id}/metrics-summary",
    response_model=ServiceMetricsSummaryResponse,
    tags=["monitoring"],
)
def get_service_metrics_summary_legacy(
    service_id: str,
    selected_range: str = Query(
        default="24h",
        alias="range",
        pattern="^([1-9][0-9]*)(m|h|d)$",
    ),
    identity: tuple[str, set[str]] = Depends(get_current_user),
) -> ServiceMetricsSummaryResponse:
    return get_service_metrics_summary(
        service_id=service_id,
        selected_range=selected_range,
        _=identity,
    )


@app.get(
    "/services/{service_id}/health/timeline",
    response_model=list[ServiceHealthTimelineSegmentResponse],
    tags=["monitoring"],
)
def get_service_health_timeline(
    service_id: str,
    selected_range: str = Query(
        default="24h",
        alias="range",
        pattern="^([1-9][0-9]*)(m|h|d)$",
    ),
    step: str = Query(default="5m", pattern="^([1-9][0-9]*)(m|h)$"),
    _: tuple[str, set[str]] = Depends(get_current_user),
) -> list[ServiceHealthTimelineSegmentResponse]:
    config = load_observability_config()
    safe_range = _validate_selected_range(
        selected_range=selected_range,
        allowed_ranges=config.timeline_allowed_ranges,
        field_name="range",
    )
    step_seconds = _validate_step_for_range(range_value=safe_range, step_value=step)

    def _load_timeline() -> list[ServiceHealthTimelineSegmentResponse]:
        end = now_utc()
        window = parse_range(safe_range)
        start = end - window
        correlation_id = str(uuid4())

        namespace, app_label, observability_mode = _resolve_service_monitoring_context(service_id)
        queries = _build_health_timeline_queries(
            namespace=namespace,
            app_label=app_label,
            config=config,
        )

        availability_points = _query_prometheus_range(
            queries["availability"][0],
            "availability",
            start=start,
            end=end,
            step_seconds=step_seconds,
            correlation_id=correlation_id,
        )
        error_points: dict[int, float] = {}
        for index, query in enumerate(queries["errorRatePct"]):
            error_points = _query_prometheus_range(
                query,
                f"errorRatePct_{index}",
                start=start,
                end=end,
                step_seconds=step_seconds,
                correlation_id=correlation_id,
            )
            if error_points:
                break
        readiness_points = _query_prometheus_range(
            queries["readiness"][0],
            "readiness",
            start=start,
            end=end,
            step_seconds=step_seconds,
            correlation_id=correlation_id,
        )

        all_timestamps = sorted(
            set(availability_points.keys())
            .union(error_points.keys())
            .union(readiness_points.keys())
        )

        thresholds = load_timeline_thresholds()
        points: list[TimelinePoint] = []
        for ts in all_timestamps:
            error_rate_value = error_points.get(ts)
            if normalize_observability_mode(observability_mode) == "no-http" and error_rate_value is None:
                error_rate_value = 0.0
            status_label, reason = classify_timeline_status(
                availability=availability_points.get(ts),
                error_rate_pct=error_rate_value,
                readiness=readiness_points.get(ts),
                thresholds=thresholds,
            )
            points.append(
                TimelinePoint(
                    timestamp=datetime.fromtimestamp(ts, tz=timezone.utc),
                    status=status_label,
                    reason=reason,
                )
            )

        segments = compact_timeline_points(
            points,
            window_start=start,
            window_end=end,
            step=timedelta(seconds=step_seconds),
        )
        return [
            ServiceHealthTimelineSegmentResponse(
                start=segment.start.isoformat(),
                end=segment.end.isoformat(),
                status=segment.status,
                reason=segment.reason,
            )
            for segment in segments
        ]

    return timeline_cache.get_or_set(
        key=("health-timeline", service_id, safe_range, step_seconds),
        ttl_seconds=config.timeline_cache_ttl_seconds,
        loader=_load_timeline,
    )


@app.get(
    "/alerts/active",
    response_model=ActiveAlertsResponse,
    tags=["monitoring"],
)
def get_active_alerts(
    env: str | None = Query(default=None),
    service_id: str | None = Query(default=None, alias="serviceId"),
    limit: int = Query(default=100, ge=1, le=500),
    _: tuple[str, set[str]] = Depends(get_current_user),
) -> list[ActiveAlertResponse]:
    config = load_observability_config()
    safe_limit = _effective_limit(limit, config.alerts_max_rows)

    correlation_id = str(uuid4())
    now = datetime.now(tz=timezone.utc).isoformat()

    try:
        raw_alerts, provider_status = _query_alertmanager_active_alerts(
            correlation_id=correlation_id,
        )
        normalized = normalize_active_alerts(raw_alerts)
    except HTTPException as exc:
        # Graceful degradation for dashboard/banner UX: keep API usable with explicit metadata.
        if exc.status_code == status.HTTP_502_BAD_GATEWAY and isinstance(exc.detail, dict):
            logger.warning("alerts_active_degraded detail=%s", exc.detail)
            detail = exc.detail
            provider_detail = detail.get("providerStatus")
            provider_status = (
                provider_detail
                if isinstance(provider_detail, dict)
                else build_provider_status(
                    provider="alertmanager",
                    base_url=get_alertmanager_base_url(),
                    status_value="error",
                    reachable=False,
                    checked_at=now,
                    correlation_id=correlation_id,
                    error="provider failure",
                )
            )
            return ActiveAlertsResponse(
                alerts=[],
                providerStatus=MonitoringProviderStatusResponse(**provider_status),
            )
        raise

    filtered = [
        alert
        for alert in normalized
        if (not env or alert.env == env)
        and (not service_id or alert.service_id == service_id)
    ][:safe_limit]

    return ActiveAlertsResponse(
        alerts=[
            ActiveAlertResponse(
                id=alert.id,
                severity=alert.severity,
                title=alert.title,
                description=alert.description,
                startsAt=alert.starts_at,
                labels=alert.labels,
                serviceId=alert.service_id,
                env=alert.env,
            )
            for alert in filtered
        ],
        providerStatus=MonitoringProviderStatusResponse(**provider_status),
    )


@app.get(
    "/monitoring/incidents",
    response_model=MonitoringIncidentsCompatEnvelope,
    tags=["monitoring"],
)
def get_monitoring_incidents_compat(
    env: str | None = Query(default=None),
    service_id: str | None = Query(default=None, alias="serviceId"),
    limit: int = Query(default=100, ge=1, le=500),
    identity: tuple[str, set[str]] = Depends(get_current_user),
) -> MonitoringIncidentsCompatEnvelope:
    active_alerts = get_active_alerts(
        env=env,
        service_id=service_id,
        limit=limit,
        _=identity,
    )
    return MonitoringIncidentsCompatEnvelope(
        incidents=[
            MonitoringIncidentCompatResponse(
                id=item.id,
                severity=item.severity,
                title=item.title,
                status="active",
                startedAt=item.starts_at,
                source="alertmanager",
                serviceId=item.service_id,
            )
            for item in active_alerts.alerts
        ],
        providerStatus=active_alerts.provider_status,
    )


@app.get(
    "/releases",
    response_model=list[ReleaseTraceabilityResponse],
    tags=["monitoring"],
)
def get_release_traceability(
    env: str | None = Query(default=None),
    service_id: str | None = Query(default=None, alias="serviceId"),
    limit: int = Query(default=50, ge=1, le=200),
    _: tuple[str, set[str]] = Depends(get_current_user),
) -> list[ReleaseTraceabilityResponse]:
    rows = build_release_traceability_rows(
        project_rows=_load_project_rows(),
        ci_rows=load_ci_metadata_rows(),
        argo_rows=load_argo_metadata_rows(),
        env_filter=env,
        service_id_filter=service_id,
        limit=limit,
    )
    rows = _enrich_release_rows_with_live_runtime(rows, env=env)
    return [
        ReleaseTraceabilityResponse(
            serviceId=row["serviceId"],
            env=row["env"],
            commitSha=row["commitSha"],
            imageRef=row["imageRef"],
            deployedAt=row["deployedAt"],
            argo=ReleaseArgoStateResponse(
                appName=str(row["argo"]["appName"]),
                syncStatus=str(row["argo"]["syncStatus"]),
                healthStatus=str(row["argo"]["healthStatus"]),
                revision=row["argo"]["revision"]
                if isinstance(row["argo"]["revision"], str)
                else None,
            ),
            drift=ReleaseDriftStateResponse(
                isDrifted=bool(row["drift"]["isDrifted"]),
                expectedRevision=row["drift"]["expectedRevision"]
                if isinstance(row["drift"]["expectedRevision"], str)
                else None,
                liveRevision=row["drift"]["liveRevision"]
                if isinstance(row["drift"]["liveRevision"], str)
                else None,
            ),
        )
        for row in rows
    ]


@app.get(
    "/release-dashboard",
    response_model=ReleaseDashboardCompatResponse,
    tags=["monitoring"],
)
def get_release_dashboard_compat(
    env: str | None = Query(default=None),
    service_id: str | None = Query(default=None, alias="serviceId"),
    limit: int = Query(default=50, ge=1, le=200),
    identity: tuple[str, set[str]] = Depends(get_current_user),
) -> ReleaseDashboardCompatResponse:
    rows = get_release_traceability(
        env=env,
        service_id=service_id,
        limit=limit,
        _=identity,
    )
    return ReleaseDashboardCompatResponse(
        releases=[
            ReleaseDashboardCompatRow(
                serviceId=row.service_id,
                serviceName=row.service_id,
                environment=row.env,
                commitSha=row.commit_sha,
                image=row.image_ref,
                sync=row.argo.sync_status,
                health=row.argo.health_status,
                drift=row.drift.is_drifted,
                deployedAt=row.deployed_at,
            )
            for row in rows
        ]
    )


@app.get(
    "/services/{service_id}/logs/quickview",
    response_model=LogsQuickViewResponse,
    tags=["monitoring"],
)
def get_service_logs_quickview(
    service_id: str,
    preset: str = Query(default="all"),
    selected_range: str = Query(default="1h", alias="range"),
    limit: int = Query(default=100, ge=1, le=200),
    cursor: str | None = Query(default=None),
    namespace: str | None = Query(default=None),
    app_label: str | None = Query(default=None, alias="appLabel"),
    identity: tuple[str, set[str]] = Depends(get_current_user),
) -> LogsQuickViewResponse:
    config = load_observability_config()
    safe_range = _validate_selected_range(
        selected_range=selected_range,
        allowed_ranges=config.logs_allowed_ranges,
        field_name="range",
    )
    safe_limit = _effective_limit(limit, config.logs_max_lines)
    user, _groups = identity
    now = datetime.now(tz=timezone.utc)

    try:
        enforce_logs_rate_limit(identity_key=user, now=now)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
        ) from exc

    try:
        safe_preset = validate_preset(preset)
        window = build_time_window(
            now=now,
            range_value=safe_range,
            cursor=cursor,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    resolved_namespace, resolved_app_label = _resolve_service_monitoring_metadata(service_id)
    safe_namespace = namespace.strip() if namespace and namespace.strip() else resolved_namespace
    safe_namespace = safe_namespace or get_logs_default_namespace()
    safe_app_label = app_label.strip() if app_label and app_label.strip() else resolved_app_label
    safe_app_label = safe_app_label or service_id
    query = build_preset_query(
        app_label=safe_app_label,
        namespace=safe_namespace,
        preset=safe_preset,
    )
    correlation_id = str(uuid4())

    fetch_limit = min(safe_limit + 1, max(2, config.logs_max_lines + 1))
    cache_key = (
        "logs-quickview",
        service_id,
        safe_namespace,
        safe_app_label,
        safe_preset,
        safe_range,
        cursor or "",
        safe_limit,
    )
    lines = logs_quickview_cache.get_or_set(
        key=cache_key,
        ttl_seconds=config.logs_cache_ttl_seconds,
        loader=lambda: _query_loki_range(
            query=query,
            start=window.start,
            end=window.end,
            limit=fetch_limit,
            correlation_id=correlation_id,
        ),
    )

    more_available = len(lines) > safe_limit
    visible = lines[:safe_limit]
    next_cursor = encode_cursor_ns(visible[-1][0]) if more_available and visible else None

    return LogsQuickViewResponse(
        serviceId=service_id,
        preset=safe_preset,
        range=safe_range,
        generatedAt=now.isoformat(),
        limit=safe_limit,
        returned=len(visible),
        moreAvailable=more_available,
        nextCursor=next_cursor,
        lines=[
            QuickViewLogLineResponse(
                timestamp=datetime.fromtimestamp(item[0] / 1_000_000_000, tz=timezone.utc).isoformat(),
                message=item[1],
                labels=item[2],
            )
            for item in visible
        ],
        providerStatus=MonitoringProviderStatusResponse(
            **build_provider_status(
                provider="loki",
                base_url=get_loki_base_url(),
                status_value="healthy",
                reachable=True,
                checked_at=now.isoformat(),
                correlation_id=correlation_id,
            )
        ),
    )


# ---------------------------------------------------------------------------
# T6.4.3 — Service scaffold endpoints
# ---------------------------------------------------------------------------

_WORKLOADS_KUSTOMIZATION_PATH = "environments/dev/workloads/kustomization.yaml"
_WORKLOADS_APPPROJECT_PATH = "bootstrap/project-homelab.yaml"
_WORKLOADS_CATALOG_PATH = "services.yaml"


class ScaffoldServiceRequest(BaseModel):
    name: str
    description: str
    image_repo: str = Field(alias="imageRepo", default="")
    repo_url: str = Field(alias="repoUrl", default="")
    owner_email: str = Field(alias="ownerEmail")
    owner: str = ""
    template: Literal["python-fastapi", "static-nginx", "postgres", "mysql"] = "python-fastapi"
    namespace: str = ""
    dev_host: str = Field(alias="devHost", default="")
    prod_host: str = Field(alias="prodHost", default="")
    public_host: str = Field(alias="publicHost", default="")
    db_username: str = Field(alias="dbUsername", default="")
    db_password: str = Field(alias="dbPassword", default="")
    db_name: str = Field(alias="dbName", default="")

    model_config = ConfigDict(populate_by_name=True)


class ScaffoldPreviewFile(BaseModel):
    path: str
    content: str
    change_type: str = Field(alias="changeType")

    model_config = ConfigDict(populate_by_name=True)


class ScaffoldPreviewResponse(BaseModel):
    files: list[ScaffoldPreviewFile]


class ScaffoldSubmitResponse(BaseModel):
    pr_url: str = Field(alias="prUrl")
    pr_number: int = Field(alias="prNumber")
    branch_name: str = Field(alias="branchName")
    files_committed: list[str] = Field(alias="filesCommitted")
    initiated_at: str = Field(alias="initiatedAt")

    model_config = ConfigDict(populate_by_name=True)


def _workloads_gitops_repo_url(repo_slug: str) -> str:
    return f"https://github.com/{repo_slug}.git"


def _build_scaffold_input(payload: ScaffoldServiceRequest) -> ScaffoldServiceInput:
    repo_slug = _workloads_repo_slug()
    name = payload.name.strip().lower()
    namespace = payload.namespace.strip() or name
    dev_host = payload.dev_host.strip() or f"{name}.dev.homelab.local"
    prod_host = payload.prod_host.strip() or f"{name}.homelab.local"
    base_domain = os.getenv("PUBLIC_BASE_DOMAIN", "homelab.local").strip() or "homelab.local"
    public_host = payload.public_host.strip() or f"{name}.{base_domain}"
    return ScaffoldServiceInput(
        name=name,
        description=payload.description.strip(),
        image_repo=payload.image_repo.strip(),
        repo_url=payload.repo_url.strip(),
        owner_email=payload.owner_email.strip(),
        owner=payload.owner.strip(),
        template=payload.template,
        namespace=namespace,
        dev_host=dev_host,
        prod_host=prod_host,
        public_host=public_host,
        workloads_repo_url=_workloads_gitops_repo_url(repo_slug),
        db_username=payload.db_username.strip() or "appuser",
        db_password=payload.db_password.strip() or "changeme",
        db_name=payload.db_name.strip() or "appdb",
    )


@app.post("/scaffold/preview", response_model=ScaffoldPreviewResponse, tags=["scaffold"])
def scaffold_preview(
    payload: ScaffoldServiceRequest,
) -> ScaffoldPreviewResponse:
    workloads_repo = _workloads_repo_slug()
    base_branch = _workloads_base_branch()

    try:
        inp = _build_scaffold_input(payload)
        validate_service_name(inp.name)

        git_provider = build_default_git_provider()
        kustomization_raw = git_provider.read_file(workloads_repo, base_branch, _WORKLOADS_KUSTOMIZATION_PATH)
        appproject_raw = git_provider.read_file(workloads_repo, base_branch, _WORKLOADS_APPPROJECT_PATH)
        services_yaml_raw = git_provider.read_file(workloads_repo, base_branch, _WORKLOADS_CATALOG_PATH)

        new_files = generate_gitops_new_files(inp)
        updated_kustomization = update_kustomization_resources(kustomization_raw, f"{inp.name}-app.yaml")
        updated_services_yaml = build_catalog_entry_addition(services_yaml_raw, inp)
        updated_appproject = build_appproject_addition(appproject_raw, inp)
    except ScaffoldError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except GitServiceConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except GitServiceAuthError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except GitServiceError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    preview_files: list[ScaffoldPreviewFile] = [
        ScaffoldPreviewFile(path=path, content=content, changeType="create")
        for path, content in sorted(new_files.items())
    ]
    preview_files += [
        ScaffoldPreviewFile(path=_WORKLOADS_KUSTOMIZATION_PATH, content=updated_kustomization, changeType="modify"),
        ScaffoldPreviewFile(path=_WORKLOADS_APPPROJECT_PATH, content=updated_appproject, changeType="modify"),
        ScaffoldPreviewFile(path=_WORKLOADS_CATALOG_PATH, content=updated_services_yaml, changeType="modify"),
    ]
    return ScaffoldPreviewResponse(files=preview_files)


@app.post(
    "/scaffold/submit",
    response_model=ScaffoldSubmitResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["scaffold"],
)
def scaffold_submit(
    payload: ScaffoldServiceRequest,
) -> ScaffoldSubmitResponse:
    initiated_at = datetime.now(tz=timezone.utc)
    workloads_repo = _workloads_repo_slug()
    base_branch = _workloads_base_branch()

    try:
        inp = _build_scaffold_input(payload)
        validate_service_name(inp.name)

        git_provider = build_default_git_provider()
        kustomization_raw = git_provider.read_file(workloads_repo, base_branch, _WORKLOADS_KUSTOMIZATION_PATH)
        appproject_raw = git_provider.read_file(workloads_repo, base_branch, _WORKLOADS_APPPROJECT_PATH)
        services_yaml_raw = git_provider.read_file(workloads_repo, base_branch, _WORKLOADS_CATALOG_PATH)

        new_files = generate_gitops_new_files(inp)
        updated_kustomization = update_kustomization_resources(kustomization_raw, f"{inp.name}-app.yaml")
        updated_services_yaml = build_catalog_entry_addition(services_yaml_raw, inp)
        updated_appproject = build_appproject_addition(appproject_raw, inp)
    except ScaffoldError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except GitServiceConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except GitServiceAuthError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except GitServiceConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except GitServiceError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    all_files: dict[str, str] = {
        **new_files,
        _WORKLOADS_KUSTOMIZATION_PATH: updated_kustomization,
        _WORKLOADS_APPPROJECT_PATH: updated_appproject,
        _WORKLOADS_CATALOG_PATH: updated_services_yaml,
    }

    timestamp = initiated_at.strftime("%Y%m%d-%H%M%S")
    branch_name = f"scaffold/{inp.name}-{timestamp}"
    pr_title = f"feat(scaffold): add {inp.name} service"
    pr_body = (
        f"## Scaffold: {inp.name}\n\n"
        f"**Description:** {inp.description}\n"
        f"**Template:** {inp.template}\n"
        f"**Namespace:** {inp.namespace}\n"
        f"**Image:** {inp.image_repo}\n"
        f"**Repository:** {inp.repo_url}\n\n"
        f"Generated by the homelab portal scaffold wizard.\n\n"
        f"### Checklist\n"
        f"- [ ] Review generated manifests\n"
        f"- [ ] Create image pull secret in `{inp.namespace}` if using GHCR private images\n"
        f"- [ ] Update `runbook_url` in `services.yaml` once a runbook exists\n"
        f"- [ ] Verify kustomize renders without errors: "
        f"`./scripts/render-kustomize.sh apps/{inp.name}/envs/dev`\n"
    )

    try:
        git_provider.create_branch(workloads_repo, base_branch, branch_name)
        git_provider.commit_to_branch(
            workloads_repo,
            branch_name,
            all_files,
            f"feat(scaffold): add {inp.name} service manifests and catalog entry",
        )
        pr = git_provider.open_pr(workloads_repo, branch_name, base_branch, pr_title, pr_body)
    except GitServiceConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except GitServiceAuthError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except GitServiceConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except GitServiceError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return ScaffoldSubmitResponse(
        prUrl=pr["url"],
        prNumber=pr["number"],
        branchName=branch_name,
        filesCommitted=sorted(all_files),
        initiatedAt=initiated_at.isoformat(),
    )


# ---------------------------------------------------------------------------
# T6.4.7 — Public hostname management
# ---------------------------------------------------------------------------


class UpdatePublicHostnameRequest(BaseModel):
    public_host: str = Field(alias="publicHost")

    model_config = ConfigDict(populate_by_name=True)


class UpdatePublicHostnameResponse(BaseModel):
    pr_url: str = Field(alias="prUrl")
    pr_number: int = Field(alias="prNumber")
    branch_name: str = Field(alias="branchName")

    model_config = ConfigDict(populate_by_name=True)


def _read_current_public_host_from_services_yaml(services_yaml: str, service_id: str) -> str | None:
    """Parse services.yaml and return the current prod public_host for the given service_id, or None."""
    import yaml as _yaml
    try:
        data = _yaml.safe_load(services_yaml)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    for entry in (data.get("services") or []):
        if not isinstance(entry, dict):
            continue
        if entry.get("service_id") != service_id:
            continue
        for env_entry in (entry.get("envs") or []):
            if not isinstance(env_entry, dict):
                continue
            if env_entry.get("name") == "prod":
                val = env_entry.get("public_host")
                return str(val).strip() if val else None
    return None


def _read_current_host_from_patch_ingress(patch_ingress: str) -> str | None:
    """Return the current host value from a patch-ingress.yaml, or None."""
    import re as _re
    match = _re.search(r"^\s*-\s*host:\s*(.+)$", patch_ingress, _re.MULTILINE)
    return match.group(1).strip() if match else None


def _update_services_yaml_public_host(services_yaml: str, service_id: str, new_host: str) -> str:
    """Return services.yaml content with the prod public_host for service_id set to new_host.

    Adds the field if absent; replaces it if present.  The file's existing whitespace
    and ordering are preserved for all other entries.
    """
    from app.scaffold_service import _yaml_string as _ys

    # --- locate the service block ----------------------------------------
    # Pattern: find `  - service_id: <id>` then, within that block, find the
    # prod env entry and update/insert public_host.

    # We work line-by-line to avoid reformatting the entire file.
    lines = services_yaml.splitlines(keepends=True)
    in_service = False
    in_prod_env = False
    service_indent = ""
    prod_env_start = -1  # index of "      - name: prod" line
    public_host_line_idx = -1  # index of existing "        public_host: ..." line

    for i, line in enumerate(lines):
        stripped = line.lstrip()
        indent = line[: len(line) - len(stripped)]

        if stripped.startswith(f"service_id: {service_id}"):
            in_service = True
            service_indent = indent
            in_prod_env = False
            prod_env_start = -1
            public_host_line_idx = -1
            continue

        if in_service:
            # Detect a new top-level service entry (same or lesser indent) — stop
            if stripped.startswith("- service_id:") and indent == service_indent:
                break

            if stripped.startswith("- name: prod"):
                in_prod_env = True
                prod_env_start = i
                continue

            if in_prod_env:
                if stripped.startswith("- name:") and not stripped.startswith("- name: prod"):
                    # moved to next env entry
                    in_prod_env = False
                    continue
                if stripped.startswith("public_host:"):
                    public_host_line_idx = i
                    # don't break — keep scanning so we have the full picture

    new_host_line = f"        public_host: {_ys(new_host)}\n"

    if public_host_line_idx >= 0:
        lines[public_host_line_idx] = new_host_line
    elif prod_env_start >= 0:
        # Insert after the last field of the prod env block.
        # Find the end of the prod env block (next "- name:" or new service entry).
        insert_after = prod_env_start
        for j in range(prod_env_start + 1, len(lines)):
            stripped_j = lines[j].lstrip()
            if stripped_j.startswith("- name:") or stripped_j.startswith("- service_id:"):
                break
            if stripped_j and not stripped_j.startswith("#"):
                insert_after = j
        lines.insert(insert_after + 1, new_host_line)
    else:
        # Fallback: append to end (should not happen for valid catalog)
        lines.append(new_host_line)

    return "".join(lines)


def _update_patch_ingress_host(patch_ingress: str, new_host: str) -> str:
    """Replace the host value in patch-ingress.yaml."""
    import re as _re
    return _re.sub(
        r"^(\s*-\s*host:\s*)(.+)$",
        lambda m: f"{m.group(1)}{new_host}",
        patch_ingress,
        flags=_re.MULTILINE,
    )


@app.put(
    "/services/{service_id}/public-hostname",
    tags=["scaffold"],
    status_code=status.HTTP_202_ACCEPTED,
    response_model=UpdatePublicHostnameResponse,
    responses={204: {"description": "No-op: hostname unchanged"}},
)
def update_service_public_hostname(
    service_id: str,
    payload: UpdatePublicHostnameRequest,
    response: Response,
    _admin: str = Depends(require_admin),
) -> UpdatePublicHostnameResponse | None:
    """Update the production public hostname for an existing service.

    Creates a GitOps PR updating services.yaml and patch-ingress.yaml.
    Returns 204 if the hostname is unchanged.
    """
    new_host = payload.public_host.strip()
    if not new_host:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="publicHost must be non-empty")

    workloads_repo = _workloads_repo_slug()
    base_branch = _workloads_base_branch()
    patch_ingress_path = f"apps/{service_id}/envs/prod/patch-ingress.yaml"

    try:
        git_provider = build_default_git_provider()
        services_yaml_raw = git_provider.read_file(workloads_repo, base_branch, _WORKLOADS_CATALOG_PATH)
        patch_ingress_raw = git_provider.read_file(workloads_repo, base_branch, patch_ingress_path)
    except GitServiceConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except GitServiceAuthError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except GitServiceError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    # No-op check
    current_catalog_host = _read_current_public_host_from_services_yaml(services_yaml_raw, service_id)
    current_ingress_host = _read_current_host_from_patch_ingress(patch_ingress_raw)
    if current_catalog_host == new_host and current_ingress_host == new_host:
        response.status_code = status.HTTP_204_NO_CONTENT
        return None

    updated_services_yaml = _update_services_yaml_public_host(services_yaml_raw, service_id, new_host)
    updated_patch_ingress = _update_patch_ingress_host(patch_ingress_raw, new_host)

    initiated_at = datetime.now(tz=timezone.utc)
    timestamp = initiated_at.strftime("%Y%m%d-%H%M%S")
    branch_name = f"hostname/{service_id}-{timestamp}"
    pr_title = f"feat(hostname): update {service_id} public hostname"
    pr_body = (
        f"## Public hostname update: {service_id}\n\n"
        f"**New hostname:** `{new_host}`\n\n"
        f"### Files changed\n"
        f"- `{_WORKLOADS_CATALOG_PATH}` — updated `envs[prod].public_host`\n"
        f"- `{patch_ingress_path}` — updated Ingress host field\n\n"
        f"> **Note:** DNS record creation is out of scope. Point `{new_host}` at the "
        f"cluster ingress IP after merging.\n"
    )

    all_files = {
        _WORKLOADS_CATALOG_PATH: updated_services_yaml,
        patch_ingress_path: updated_patch_ingress,
    }

    try:
        git_provider.create_branch(workloads_repo, base_branch, branch_name)
        git_provider.commit_to_branch(
            workloads_repo,
            branch_name,
            all_files,
            f"feat(hostname): update {service_id} public hostname to {new_host}",
        )
        pr = git_provider.open_pr(workloads_repo, branch_name, base_branch, pr_title, pr_body)
    except GitServiceConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except GitServiceAuthError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except GitServiceConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except GitServiceError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return UpdatePublicHostnameResponse(
        prUrl=pr["url"],
        prNumber=pr["number"],
        branchName=branch_name,
    )
