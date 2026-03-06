from datetime import datetime, timedelta, timezone
import logging
import math
import os
import re
from uuid import uuid4
from urllib import parse as urlparse

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, ConfigDict

from app.alerts_feed import (
    get_alertmanager_base_url,
    normalize_active_alerts,
)
from app.catalog_reconciliation import build_catalog_join
from app.db import get_psycopg_database_url
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
    load_argo_metadata_rows,
    load_ci_metadata_rows,
)
from app.service_registry_sync import sync_service_registry_from_cluster
from app.observability_cache import TTLCache
from app.observability_config import (
    load_observability_config,
    render_query_template,
)

app = FastAPI(title="Homelab Backend API", version="0.1.0")
logger = logging.getLogger("homelab.backend.monitoring")

bearer_auth = HTTPBearer(auto_error=False)
metrics_summary_cache = TTLCache()
timeline_cache = TTLCache()
logs_quickview_cache = TTLCache()
alerts_cache = TTLCache()


def clear_observability_caches_for_tests() -> None:
    metrics_summary_cache.clear()
    timeline_cache.clear()
    logs_quickview_cache.clear()
    alerts_cache.clear()


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
    source: str
    source_ref: str | None = Field(default=None, alias="sourceRef")
    last_synced_at: str | None = Field(default=None, alias="lastSyncedAt")

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
    stale_after_minutes: int = Field(alias="staleAfterMinutes")
    is_empty: bool = Field(alias="isEmpty")
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


class ServiceRegistryDiagnosticsResponse(BaseModel):
    generated_at: str = Field(alias="generatedAt")
    env: str | None = None
    freshness: ServiceRegistryFreshnessResponse
    join_mismatch: ServiceRegistryJoinMismatchResponse = Field(alias="joinMismatch")
    catalog_join: CatalogJoinDiagnosticsResponse = Field(alias="catalogJoin")

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


def _load_project_rows(env: str | None = None) -> list[dict[str, str]]:
    with _with_connection() as conn:
        with conn.cursor() as cur:
            if env:
                cur.execute(
                    """
                    SELECT project_id, project_name, env
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
                    SELECT project_id, project_name, env
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
                SELECT project_id, project_name, env, namespace, app_label
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


def _registry_stale_after_minutes() -> int:
    raw = os.getenv("REGISTRY_STALE_AFTER_MINUTES", "30")
    try:
        value = int(raw)
    except ValueError:
        return 30
    return value if value > 0 else 30


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
) -> dict[str, str]:
    pod_pattern = re.escape(app_label)
    values = {
        "namespace": namespace,
        "app_label": app_label,
        "selected_range": selected_range,
        "pod_pattern": pod_pattern,
    }
    return {
        "uptimePct": render_query_template(
            config.metrics_query_uptime_template,
            values,
            "metrics.uptime",
        ),
        "p95LatencyMs": render_query_template(
            config.metrics_query_p95_latency_template,
            values,
            "metrics.p95_latency",
        ),
        "errorRatePct": render_query_template(
            config.metrics_query_error_rate_template,
            values,
            "metrics.error_rate",
        ),
        "restartCount": render_query_template(
            config.metrics_query_restart_count_template,
            values,
            "metrics.restart_count",
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


@app.get("/projects", response_model=ProjectsResponse, tags=["metadata"])
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
    now = datetime.now(tz=timezone.utc)

    is_empty = row_count == 0
    if is_empty:
        is_stale = False
        state = "empty"
    else:
        if last_synced_at is None:
            is_stale = True
        else:
            is_stale = (now - last_synced_at) > timedelta(minutes=stale_after_minutes)
        state = "stale" if is_stale else "fresh"

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
            staleAfterMinutes=stale_after_minutes,
            isEmpty=is_empty,
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
    rows = _load_service_rows(service_id=service_id, env=env)
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Service not found",
        )

    preferred_env = env or os.getenv("PORTAL_ENV", "dev")
    selected = next(
        (row for row in rows if row["env"] == preferred_env),
        rows[0],
    )
    return ServiceDetailResponse(
        id=str(selected["service_id"]),
        name=str(selected["service_name"]),
        namespace=str(selected["namespace"]),
        env=str(selected["env"]),
        appLabel=str(selected["app_label"]),
        argoAppName=selected["argo_app_name"] if isinstance(selected["argo_app_name"], str) else None,
        source=str(selected["source"]),
        sourceRef=selected["source_ref"] if isinstance(selected["source_ref"], str) else None,
        lastSyncedAt=selected["last_synced_at"] if isinstance(selected["last_synced_at"], str) else None,
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
    now = datetime.now(tz=timezone.utc)

    is_empty = row_count == 0
    if is_empty:
        is_stale = False
        state = "empty"
    else:
        if last_synced_at is None:
            is_stale = True
        else:
            is_stale = (now - last_synced_at) > timedelta(minutes=stale_after_minutes)
        state = "stale" if is_stale else "fresh"

    project_rows = _load_project_rows()
    mismatches = build_release_join_diagnostics(
        project_rows=project_rows,
        ci_rows=load_ci_metadata_rows(),
        argo_rows=load_argo_metadata_rows(),
        env_filter=env,
        service_id_filter=None,
    )
    catalog_join = build_catalog_join(
        project_rows=_load_project_catalog_rows(env=env),
        service_rows=_load_service_catalog_rows(env=env),
        env_filter=env,
        project_id_filter=None,
        service_id_filter=None,
    )

    return ServiceRegistryDiagnosticsResponse(
        generatedAt=now.isoformat(),
        env=env,
        freshness=ServiceRegistryFreshnessResponse(
            rowCount=row_count,
            lastSyncedAt=last_synced_at.isoformat() if last_synced_at else None,
            staleAfterMinutes=stale_after_minutes,
            isEmpty=is_empty,
            isStale=is_stale,
            state=state,
        ),
        joinMismatch=ServiceRegistryJoinMismatchResponse(**mismatches),
        catalogJoin=CatalogJoinDiagnosticsResponse(**catalog_join["diagnostics"]),
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
    namespace = "default"
    app_label = service_id

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

        for field_name, query in queries.items():
            value = _query_prometheus_scalar(
                query,
                field_name,
                correlation_id=correlation_id,
            )
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

        namespace = "default"
        app_label = service_id
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

    safe_namespace = namespace.strip() if namespace and namespace.strip() else get_logs_default_namespace()
    query = build_preset_query(
        service_id=service_id,
        namespace=safe_namespace,
        preset=safe_preset,
    )
    correlation_id = str(uuid4())

    fetch_limit = min(safe_limit + 1, max(2, config.logs_max_lines + 1))
    cache_key = (
        "logs-quickview",
        service_id,
        safe_namespace,
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
