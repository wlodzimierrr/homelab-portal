from datetime import datetime, timedelta, timezone
import logging
import math
import os
from threading import Event, Thread
import time
from typing import Any, Literal
from uuid import uuid4
from urllib import parse as urlparse

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
from app.release_traceability import (
    build_release_join_diagnostics,
    build_release_traceability_rows,
    compute_is_drifted,
    load_argo_metadata_rows,
    load_ci_metadata_rows,
)
from app.service_identity import is_canonical_service_id
from app.service_identity_validation import build_service_identity_diagnostics
from app.service_registry_sync import _kube_get_json, sync_service_registry_from_cluster
from app.observability_cache import TTLCache
from app.observability_config import (
    escape_promql_regex_literal,
    load_observability_config,
    parse_duration_token,
    render_query_template,
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
                    SELECT project_id, project_name, env, owner, repo_url, runbook_url
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
                    SELECT project_id, project_name, env, owner, repo_url, runbook_url
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
                SELECT project_id, project_name, env, namespace, app_label, source_ref
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
            "source_ref": row[5],
        }
        for row in rows
    ]


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


def _resolve_service_monitoring_metadata(service_id: str) -> tuple[str, str]:
    preferred_env = os.getenv("PORTAL_ENV", "dev")
    rows = _load_service_rows(service_id=service_id, env=preferred_env)
    if not rows:
        rows = _load_service_rows(service_id=service_id)
    if not rows:
        return "default", service_id

    selected = _select_preferred_service_row(service_id, rows, preferred_env) or rows[0]
    namespace = str(selected.get("namespace") or "").strip() or "default"
    app_label = str(selected.get("app_label") or "").strip() or service_id
    return namespace, app_label


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


def _load_deployment_metric_snapshots(
    service_row: dict[str, str | None] | None,
    release_row: dict[str, object],
) -> dict[str, dict[str, float]]:
    if not service_row:
        return {}

    namespace = str(service_row.get("namespace") or "").strip()
    app_label = str(service_row.get("app_label") or "").strip()
    service_id = str(service_row.get("service_id") or "").strip()
    env = str(service_row.get("env") or "").strip()
    deployed_at = _parse_iso_datetime(release_row.get("deployedAt"))
    if not namespace or not app_label or not service_id or deployed_at is None:
        return {}

    comparison_window_token = _deployment_comparison_window_token()
    comparison_window = parse_duration_token(comparison_window_token)
    comparison_end = deployed_at + comparison_window
    if comparison_end > now_utc():
        return {}

    cache_key = (
        "service_deployment_metrics",
        service_id,
        env,
        namespace,
        app_label,
        deployed_at.isoformat(),
        comparison_window_token,
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
        step_seconds = int(comparison_window.total_seconds())
        snapshots = {
            "errorRatePct": _query_prometheus_comparison_snapshot(
                queries=queries["errorRatePct"],
                metric_name="deployment_error_rate_pct",
                start=deployed_at,
                end=comparison_end,
                step_seconds=step_seconds,
                correlation_id=correlation_id,
            ),
            "p95LatencyMs": _query_prometheus_comparison_snapshot(
                queries=queries["p95LatencyMs"],
                metric_name="deployment_p95_latency_ms",
                start=deployed_at,
                end=comparison_end,
                step_seconds=step_seconds,
                correlation_id=correlation_id,
            ),
            "availabilityPct": _query_prometheus_comparison_snapshot(
                queries=queries["uptimePct"],
                metric_name="deployment_availability_pct",
                start=deployed_at,
                end=comparison_end,
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
    metric_snapshots = (
        _load_deployment_metric_snapshots(service_row, {"deployedAt": observed_at})
        if observed_at
        else {}
    )
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


def _build_health_timeline_queries(*, namespace: str, app_label: str, config) -> dict[str, str]:
    deployment_name = app_label
    values = {
        "namespace": namespace,
        "app_label": app_label,
        "deployment_name": deployment_name,
    }
    return {
        "availability": render_query_template(
            config.timeline_query_availability_template,
            values,
            "timeline.availability",
        ),
        "errorRatePct": render_query_template(
            config.timeline_query_error_rate_template,
            values,
            "timeline.error_rate",
        ),
        "readiness": render_query_template(
            config.timeline_query_readiness_template,
            values,
            "timeline.readiness",
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
    namespace, app_label = _resolve_service_monitoring_metadata(service_id)

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
        )

    return metrics_summary_cache.get_or_set(
        key=("metrics-summary", service_id, safe_range),
        ttl_seconds=config.metrics_cache_ttl_seconds,
        loader=_load_summary,
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

        namespace, app_label = _resolve_service_monitoring_metadata(service_id)
        queries = _build_health_timeline_queries(
            namespace=namespace,
            app_label=app_label,
            config=config,
        )

        availability_points = _query_prometheus_range(
            queries["availability"],
            "availability",
            start=start,
            end=end,
            step_seconds=step_seconds,
            correlation_id=correlation_id,
        )
        error_points = _query_prometheus_range(
            queries["errorRatePct"],
            "errorRatePct",
            start=start,
            end=end,
            step_seconds=step_seconds,
            correlation_id=correlation_id,
        )
        readiness_points = _query_prometheus_range(
            queries["readiness"],
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
    preset: str = Query(default="errors"),
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
