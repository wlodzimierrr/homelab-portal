import json
from datetime import datetime, timedelta, timezone
import logging
import math
import os
import re
from typing import Any, Callable, Literal
from uuid import uuid4
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

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
    store_observability_snapshot,
    upsert_deployment_record,
)
from app.deployment_locks import (
    DeploymentLockConflictError,
    DeploymentLockRow,
    cleanup_stale_deployment_locks,
    get_deployment_lock,
    release_deployment_lock,
    sync_deployment_lock_for_deployment_row,
)
from app.deployment_reconciler import make_pr_comment_poster, reconcile_recent_gitops_deployments
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
from app.service_observability import (
    build_service_metrics_observability_diagnostics,
    normalize_observability_mode,
)
from app.service_identity_validation import build_service_identity_diagnostics
from app.service_registry_sync import _kube_get_json, sync_service_registry_from_cluster
from app.observability_config import (
    escape_promql_regex_literal,
    load_observability_config,
    parse_duration_token,
    render_query_template,
)
from app.scaffold_service import (
    ScaffoldAddServiceInput,
    ScaffoldBundleInput,
    ScaffoldError,
    ScaffoldServiceInput,
    build_appproject_addition,
    build_catalog_add_service_entry,
    build_catalog_bundle_entries,
    build_catalog_entry_addition,
    generate_gitops_add_service_files,
    generate_gitops_bundle_files,
    generate_gitops_new_files,
    update_kustomization_resources,
    update_overlay_kustomization_patches,
    validate_add_service,
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
from app.services.deployment_service import DeploymentService
from app.services.builders import (
    build_catalog_service as _compose_catalog_service,
    build_deployment_service as _compose_deployment_service,
    build_observability_service as _compose_observability_service,
    build_scaffold_admin_service as _compose_scaffold_admin_service,
)
from app.services.catalog_service import CatalogService
from app.services.composition import (
    BackendServiceBuilders,
    configure_backend_service_builders as _configure_backend_service_builders,
    get_backend_service_builders,
)
from app.services.observability_service import ObservabilityService
from app.services.scaffold_admin_service import ScaffoldAdminService
from app.services.startup_jobs import register_deployment_reconciler_jobs
from app.api.schemas.auth import LoginRequest, LoginResponse
from app.api.bootstrap import (
    clear_observability_caches,
    create_api_app,
    create_http_metrics_state,
    create_observability_caches,
    install_http_metrics_middleware,
)
from app.api.schemas.catalog import (
    CatalogJoinDiagnosticsResponse,
    CatalogJoinResponse,
    CreateProjectRequest,
    DeploymentLockResponse,
    Project,
    ProjectCatalogDiagnosticsResponse,
    ProjectsResponse,
    ServiceDetailResponse,
    ServiceIdentityDiagnosticsResponse,
    ServiceIdentityDriftRowResponse,
    ServiceIdentityMonitoringSelectorResponse,
    ServiceRegistryDiagnosticsResponse,
    ServiceRegistryFreshnessResponse,
    ServiceRegistryJoinMismatchResponse,
    ServiceRegistrySyncFailure,
    ServiceRegistrySyncResponse,
    ServiceRow,
    ServicesResponse,
    CatalogJoinRowResponse,
    CatalogJoinServiceRefResponse,
)
from app.api.schemas.deployments import (
    CreateDeploymentRecordRequest,
    DeploymentReconcileResponse,
    DeploymentRecordResponse,
    PortalDeployToDevRequest,
    PortalDeployToDevResponse,
    PortalPromoteToProdRequest,
    PortalPromoteToProdResponse,
    PortalRollbackRequest,
    PortalRollbackResponse,
    PortalServiceRollbackCandidatesResponse,
    PortalServiceRollbackCandidate,
    PortalServiceRollbackRequest,
    PortalServiceRollbackResponse,
    PortalSetConfigRequest,
    PortalSetConfigResponse,
    PortalSetSecretRequest,
    PortalSetSecretResponse,
    ROLLBACK_TAG_RE,
    ReleaseArgoStateResponse,
    ReleaseDashboardCompatResponse,
    ReleaseDashboardCompatRow,
    ReleaseDriftStateResponse,
    ReleaseTraceabilityResponse,
    ServiceConfigEntry,
    ServiceConfigResponse,
    ServiceDeploymentInfoResponse,
    ServiceDeploymentsResponse,
    UpdatePublicHostnameRequest,
    UpdatePublicHostnameResponse,
)
from app.api.schemas.observability import (
    ActiveAlertResponse,
    ActiveAlertsResponse,
    DeploymentObservabilityContextResponse,
    DeploymentObservabilityLogsResponse,
    DeploymentObservabilityMetricSnapshotResponse,
    DeploymentObservabilityMetricsResponse,
    DeploymentObservabilityResponse,
    DeploymentObservabilityTimelineResponse,
    HealthResponse,
    LogsQuickViewResponse,
    MonitoringIncidentCompatResponse,
    MonitoringIncidentsCompatEnvelope,
    MonitoringProviderErrorDetailResponse,
    MonitoringProviderStatusResponse,
    MonitoringProvidersDiagnosticsResponse,
    QuickViewLogLineResponse,
    ServiceHealthTimelineSegmentResponse,
    ServiceMetricTrendPointResponse,
    ServiceMetricTrendSeriesResponse,
    ServiceMetricsObservabilityDiagnosticsResponse,
    ServiceMetricsSummaryResponse,
    ServiceMetricsTrendsResponse,
)
from app.api.schemas.scaffold import (
    ScaffoldPreviewFile,
    ScaffoldPreviewResponse,
    ScaffoldProjectInfo,
    ScaffoldServiceRequest,
    ScaffoldSubmitResponse,
)
from app.runtime_config import (
    BRANCH_SAFE_FRAGMENT_RE,
    DEFAULT_PORTAL_IMAGES_LOOKBACK,
    SHA_IMAGE_TAG_RE,
    deployment_lock_stale_timeout_seconds as _deployment_lock_stale_timeout_seconds,
    deployment_reconciler_enabled as _deployment_reconciler_enabled,
    deployment_reconciler_gitops_repo_slug as _deployment_reconciler_gitops_repo_slug,
    deployment_reconciler_interval_seconds as _deployment_reconciler_interval_seconds,
    deployment_reconciler_pr_comments_enabled as _deployment_reconciler_pr_comments_enabled,
    deployment_reconciler_read_ttl_seconds as _deployment_reconciler_read_ttl_seconds,
    deployment_reconciler_readthrough_enabled as _deployment_reconciler_readthrough_enabled,
    dev_deploy_target as _dev_deploy_target,
    ghcr_token as _ghcr_token,
    github_api_base_url as _github_api_base_url,
    github_api_token_for_path as _github_api_token_for_path,
    portal_images_workflow_file as _portal_images_workflow_file,
    portal_images_workflow_ref as _portal_images_workflow_ref,
    portal_repo_slug as _portal_repo_slug,
    promote_to_prod_target as _promote_to_prod_target,
    rollback_target as _rollback_target,
    workloads_base_branch as _workloads_base_branch,
    workloads_gitops_repo_url as _workloads_gitops_repo_url,
    workloads_repo_slug as _workloads_repo_slug,
)

# FastAPI entrypoint for the portal backend.
#
# This module keeps the API surface, auth checks, background reconciliation loop,
# and most integration helpers in one place. Comments here focus on the seams a new
# engineer needs to reason about first: request lifecycle, auth assumptions, and
# the endpoints that other portal layers depend on most heavily.

app = create_api_app()
logger = logging.getLogger("homelab.backend.monitoring")

bearer_auth = HTTPBearer(auto_error=False)
_observability_caches = create_observability_caches()
# These caches reduce repeated observability queries for pages that poll often.
metrics_summary_cache = _observability_caches.metrics_summary_cache
timeline_cache = _observability_caches.timeline_cache
logs_quickview_cache = _observability_caches.logs_quickview_cache
alerts_cache = _observability_caches.alerts_cache
deployment_history_cache = _observability_caches.deployment_history_cache
deployment_reconcile_cache = _observability_caches.deployment_reconcile_cache
_http_metrics_state = create_http_metrics_state()
install_http_metrics_middleware(app, _http_metrics_state)


def clear_observability_caches_for_tests() -> None:
    clear_observability_caches(_observability_caches)


# Backend request/response contracts now live in app.api.schemas.* so route
# handlers here can focus on orchestration instead of schema ownership.

# Request/response schemas now live under app.api.schemas.* so this module can
# focus on route orchestration, auth flow, and integration behavior.

# Portal mutation operations still raise local exception types because route
# handlers map them directly to HTTP responses later in the file.

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


def require_bearer_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_auth),
) -> str:
    # Local development uses a fixed bearer token. In deployed environments the
    # request usually arrives with upstream auth headers instead; see get_current_user.
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
    # Prefer identity forwarded by the auth gateway when present so admin checks
    # can use real usernames/groups. Fall back to the dev bearer token locally.
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


# Internal helpers below are roughly grouped by concern: auth/database access,
# background reconciliation, catalog loading, GitHub/GHCR deploy workflows, live
# runtime enrichment, and observability query helpers.

def _with_connection() -> psycopg.Connection:
    return psycopg.connect(get_psycopg_database_url())


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

def _reconcile_recent_deployment_activity(
    *,
    service_id: str | None = None,
    env: str | None = None,
) -> DeploymentReconcileResponse:
    pr_commenter = None
    if _deployment_reconciler_pr_comments_enabled():
        pr_commenter = make_pr_comment_poster(_deployment_reconciler_gitops_repo_slug())

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
            post_pr_comment=pr_commenter,
        )
    deployment_history_cache.clear()
    return DeploymentReconcileResponse(**result)


# Startup/shutdown hooks stay explicit, but the thread lifecycle now lives in a
# small service module so route composition does not own background job state.
register_deployment_reconciler_jobs(
    app,
    enabled_fn=_deployment_reconciler_enabled,
    interval_seconds_fn=_deployment_reconciler_interval_seconds,
    reconcile_fn=_reconcile_recent_deployment_activity,
    logger=logger,
)


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
                SELECT service_id, service_name, env, namespace, app_label, argo_app_name, source, source_ref, last_synced_at, project_id
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
            "project_id": row[9],
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

    return deployment_history_cache.get_or_set(
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


def _build_deployment_service() -> DeploymentService:
    return _compose_deployment_service(
        get_deployment_record_by_id=_get_deployment_record_by_id,
        maybe_reconcile_recent_deployments=_maybe_reconcile_recent_deployments,
        list_deployment_records_for_service=_list_deployment_records_for_service,
        load_service_rows=_load_service_rows,
        select_preferred_service_row=_select_preferred_service_row,
        build_deployment_record_response=_build_deployment_record_response,
        upsert_deployment_record_row=_upsert_deployment_record_row,
        build_deployment_lock_response=_build_deployment_lock_response,
        get_active_deployment_lock=_get_active_deployment_lock,
        with_connection=_with_connection,
        deployment_history_cache=deployment_history_cache,
        deployment_reconcile_cache=deployment_reconcile_cache,
        logger=logger,
        dev_deploy_target=_dev_deploy_target,
        workloads_repo_slug=_workloads_repo_slug,
        workloads_base_branch=_workloads_base_branch,
        resolve_latest_portal_image_candidate=_resolve_latest_portal_image_candidate,
        load_dev_overlay_update_plan=_load_dev_overlay_update_plan,
        extract_version_from_image_ref=_extract_version_from_image_ref,
        build_compare_url_for_portal_tags=_build_compare_url_for_portal_tags,
        build_dev_deploy_branch_name=_build_dev_deploy_branch_name,
        build_dev_deploy_pr_body=_build_dev_deploy_pr_body,
        promote_to_prod_target=_promote_to_prod_target,
        load_promote_to_prod_update_plan=_load_promote_to_prod_update_plan,
        ensure_ghcr_tag_exists=_ensure_ghcr_tag_exists,
        extract_sha_from_tag=_extract_sha_from_tag,
        build_prod_promote_branch_name=_build_prod_promote_branch_name,
        build_promote_to_prod_pr_body=_build_promote_to_prod_pr_body,
        rollback_target=_rollback_target,
        extract_image_ref_from_overlay=_extract_image_ref_from_overlay,
        list_service_rollback_candidates=_list_service_rollback_candidates,
        load_service_rollback_update_plan=_load_service_rollback_update_plan,
        build_service_rollback_branch_name=_build_service_rollback_branch_name,
        build_service_rollback_pr_body=_build_service_rollback_pr_body,
        select_latest_deployment_info_record=_select_latest_deployment_info_record,
        extract_image_digest=_extract_image_digest,
        deployment_record_timestamp=_deployment_record_timestamp,
        build_commit_url=_build_commit_url,
        build_package_url_from_image_ref=_build_package_url_from_image_ref,
        deploy_to_dev_error_type=PortalDeployToDevError,
        promote_to_prod_error_type=PortalPromoteToProdError,
        service_rollback_error_type=PortalServiceRollbackError,
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


# Monitoring provider failures are downgraded into structured partial responses so
# the frontend can still render the deployment page and show provider health instead
# of failing the entire composite endpoint.
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


# Metrics/timeline/log builders all follow the same contract: query one provider,
# translate empty results into explicit `no_data`, and attach provider status so the
# UI can distinguish retention gaps from provider outages.
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


def _build_observability_service() -> ObservabilityService:
    return _compose_observability_service(
        resolve_deployment_observability_context=_resolve_deployment_observability_context,
        build_no_window_metrics_response=_build_no_window_metrics_response,
        build_no_window_timeline_response=_build_no_window_timeline_response,
        build_no_window_logs_response=_build_no_window_logs_response,
        build_deployment_metrics_response=_build_deployment_metrics_response,
        build_deployment_timeline_response=_build_deployment_timeline_response,
        build_deployment_logs_response=_build_deployment_logs_response,
        extract_provider_failure=_extract_provider_failure,
        build_provider_error_metrics_response=_build_provider_error_metrics_response,
        build_provider_error_timeline_response=_build_provider_error_timeline_response,
        build_provider_error_logs_response=_build_provider_error_logs_response,
        validate_selected_range=_validate_selected_range,
        resolve_service_monitoring_context=_resolve_service_monitoring_context,
        resolve_service_monitoring_metadata=_resolve_service_monitoring_metadata,
        build_service_metrics_queries=_build_service_metrics_queries,
        build_metrics_observability_diagnostics=_build_metrics_observability_diagnostics,
        build_metric_trend_series=_build_metric_trend_series,
        validate_step_for_range=_validate_step_for_range,
        build_health_timeline_queries=_build_health_timeline_queries,
        query_prometheus_scalar=_query_prometheus_scalar,
        query_prometheus_range=_query_prometheus_range,
        query_loki_range=_query_loki_range,
        query_alertmanager_active_alerts=_query_alertmanager_active_alerts,
        select_timeline_step_seconds=_select_timeline_step_seconds,
        effective_limit=_effective_limit,
        enrich_release_rows_with_live_runtime=_enrich_release_rows_with_live_runtime,
        load_project_rows=_load_project_rows,
        metrics_summary_cache=metrics_summary_cache,
        timeline_cache=timeline_cache,
        logs_quickview_cache=logs_quickview_cache,
        logger=logger,
    )


def _build_catalog_service() -> CatalogService:
    return _compose_catalog_service(
        load_project_rows=_load_project_rows,
        load_project_catalog_rows=_load_project_catalog_rows,
        load_service_catalog_rows=_load_service_catalog_rows,
        load_service_rows=_load_service_rows,
        maybe_reconcile_recent_deployments=_maybe_reconcile_recent_deployments,
        select_preferred_service_row=_select_preferred_service_row,
        sort_release_rows_by_deployed_at=_sort_release_rows_by_deployed_at,
        load_release_rows_for_service=_load_release_rows_for_service,
        release_row_has_meaningful_metadata=_release_row_has_meaningful_metadata,
        load_live_service_runtime_rows=_load_live_service_runtime_rows,
        coalesce_service_status=_coalesce_service_status,
        extract_version_from_image_ref=_extract_version_from_image_ref,
        get_active_deployment_lock=_get_active_deployment_lock,
        build_deployment_lock_response=_build_deployment_lock_response,
        with_connection=_with_connection,
        registry_stale_after_minutes=_registry_stale_after_minutes,
        registry_warning_after_minutes=_registry_warning_after_minutes,
    )


def configure_backend_services(
    target_app: FastAPI = app,
    *,
    build_catalog_service: Callable[[], CatalogService] | None = None,
    build_deployment_service: Callable[[], DeploymentService] | None = None,
    build_observability_service: Callable[[], ObservabilityService] | None = None,
    build_scaffold_admin_service: Callable[[], ScaffoldAdminService] | None = None,
) -> BackendServiceBuilders:
    """Register swappable service builders without changing the app entrypoint."""

    return _configure_backend_service_builders(
        target_app,
        BackendServiceBuilders(
            build_catalog_service=build_catalog_service or _build_catalog_service,
            build_deployment_service=build_deployment_service or _build_deployment_service,
            build_observability_service=(
                build_observability_service or _build_observability_service
            ),
            build_scaffold_admin_service=(
                build_scaffold_admin_service or _build_scaffold_admin_service
            ),
        ),
    )


def _get_catalog_service() -> CatalogService:
    return get_backend_service_builders(app).build_catalog_service()


def _get_deployment_service() -> DeploymentService:
    return get_backend_service_builders(app).build_deployment_service()


def _get_observability_service() -> ObservabilityService:
    return get_backend_service_builders(app).build_observability_service()


# Primary API endpoints begin here. The order loosely follows how the frontend
# consumes them: system/auth, metadata, deployment mutations, observability, then
# scaffold/admin maintenance features.

def health(
    include_providers: bool = Query(default=False, alias="includeProviders"),
) -> HealthResponse:
    # Keep the default health check lightweight for liveness/readiness probes, and
    # only fan out to Prometheus/Loki/Alertmanager when diagnostics are requested.
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


def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


def login(payload: LoginRequest) -> LoginResponse:
    # This is a development-only login contract that matches the frontend auth
    # flow. Production auth is expected to be enforced by the ingress/auth proxy.
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


def list_projects(
    env: str | None = Query(default=None),
    _: tuple[str, set[str]] = Depends(get_current_user),
) -> ProjectsResponse:
    return _get_catalog_service().list_projects(env=env)


# Freshness/diagnostic endpoints intentionally compute both source age and join
# mismatch state so operators can tell whether data is old, empty, or structurally off.
def get_project_catalog_diagnostics(
    env: str | None = Query(default=None),
    _: tuple[str, set[str]] = Depends(get_current_user),
) -> ProjectCatalogDiagnosticsResponse:
    return _get_catalog_service().get_project_catalog_diagnostics(env=env)


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


def list_services(
    env: str | None = Query(default=None),
    namespace: str | None = Query(default=None),
    _: tuple[str, set[str]] = Depends(get_current_user),
) -> ServicesResponse:
    return _get_catalog_service().list_services(env=env, namespace=namespace)


def get_service(
    service_id: str,
    env: str | None = Query(default=None),
    _: tuple[str, set[str]] = Depends(get_current_user),
) -> ServiceDetailResponse:
    return _get_catalog_service().get_service(service_id=service_id, env=env)


def reconcile_deployments(
    service_id: str | None = Query(default=None, alias="serviceId"),
    env: str | None = Query(default=None),
    _: str = Depends(require_admin),
) -> DeploymentReconcileResponse:
    deployment_reconcile_cache.clear()
    return _reconcile_recent_deployment_activity(service_id=service_id, env=env)


def get_deployment(
    deployment_id: str,
    _: tuple[str, set[str]] = Depends(get_current_user),
) -> DeploymentRecordResponse:
    return _get_deployment_service().get_deployment(deployment_id)


def create_deployment_record(
    payload: CreateDeploymentRecordRequest,
    admin_user: str = Depends(require_admin),
) -> DeploymentRecordResponse:
    return _get_deployment_service().create_deployment_record(payload, admin_user=admin_user)


def cancel_deployment(
    deployment_id: str,
    admin_user: str = Depends(require_admin),
) -> DeploymentRecordResponse:
    """Soft-cancel a deployment that has not yet reached a terminal state.

    Sets status='failed', result='cancelled', result_reason='Cancelled by operator'
    and releases the deployment lock.  Returns 404 if the deployment is not found,
    409 if it is already in a terminal state.
    """
    return _get_deployment_service().cancel_deployment(deployment_id, admin_user=admin_user)


# Deploy-to-dev is a GitOps mutation endpoint: resolve the newest portal image,
# patch the target overlays in a branch, open a PR, then create a pending deployment
# record/lock so the UI can track the request before Argo applies it.
def request_portal_deploy_to_dev(
    service_id: str,
    payload: PortalDeployToDevRequest,
    response: Response,
    identity: tuple[str, set[str]] = Depends(get_current_user),
) -> PortalDeployToDevResponse:
    requested_by, _groups = identity
    return _get_deployment_service().request_portal_deploy_to_dev(
        service_id,
        payload,
        response=response,
        requested_by=requested_by,
    )


# Promote-to-prod follows the same GitOps pattern as deploy-to-dev, but its source
# of truth is the current dev overlay and it always targets the prod overlays.
def request_portal_promote_to_prod(
    service_id: str,
    payload: PortalPromoteToProdRequest,
    response: Response,
    identity: tuple[str, set[str]] = Depends(get_current_user),
) -> PortalPromoteToProdResponse:
    requested_by, _groups = identity
    return _get_deployment_service().request_portal_promote_to_prod(
        service_id,
        payload,
        response=response,
        requested_by=requested_by,
    )


def get_service_config(
    service_id: str,
    env: Literal["dev", "prod"],
    current_user: tuple[str, set[str]] = Depends(get_current_user),
) -> ServiceConfigResponse:
    del current_user
    return _get_scaffold_admin_service().get_service_config(service_id=service_id, env=env)


def request_portal_set_config(
    service_id: str,
    payload: PortalSetConfigRequest,
    admin_user: str = Depends(require_admin),
) -> PortalSetConfigResponse:
    return _get_scaffold_admin_service().request_portal_set_config(
        service_id=service_id,
        payload=payload,
        admin_user=admin_user,
    )


def request_portal_set_secret(
    service_id: str,
    payload: PortalSetSecretRequest,
    admin_user: str = Depends(require_admin),
) -> PortalSetSecretResponse:
    return _get_scaffold_admin_service().request_portal_set_secret(
        service_id=service_id,
        payload=payload,
        admin_user=admin_user,
    )


def list_service_rollback_candidates(
    service_id: str,
    target_environment: Literal["dev", "prod"] = Query(default="dev", alias="targetEnvironment"),
    identity: tuple[str, set[str]] = Depends(get_current_user),
) -> PortalServiceRollbackCandidatesResponse:
    del identity
    return _get_deployment_service().list_service_rollback_candidates(
        service_id,
        target_environment=target_environment,
    )


# Service rollback is also PR-driven. Candidates are discovered from recent image
# history, then the selected tag is written back into the appropriate overlay files.
def request_service_rollback(
    service_id: str,
    payload: PortalServiceRollbackRequest,
    response: Response,
    identity: tuple[str, set[str]] = Depends(get_current_user),
) -> PortalServiceRollbackResponse:
    requested_by, _groups = identity
    return _get_deployment_service().request_service_rollback(
        service_id,
        payload,
        response=response,
        requested_by=requested_by,
    )


def request_portal_rollback(
    payload: PortalRollbackRequest,
    admin_user: str = Depends(require_admin),
) -> PortalRollbackResponse:
    return _get_deployment_service().request_portal_rollback(payload, admin_user=admin_user)


def get_service_deployments(
    service_id: str,
    env: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    _: tuple[str, set[str]] = Depends(get_current_user),
) -> ServiceDeploymentsResponse:
    return _get_deployment_service().get_service_deployments(service_id, env=env, limit=limit)


def get_service_deployment_info(
    service_id: str,
    env: str | None = Query(default=None),
    _: tuple[str, set[str]] = Depends(get_current_user),
) -> ServiceDeploymentInfoResponse:
    return _get_deployment_service().get_service_deployment_info(service_id, env=env)


# Deployment observability endpoints compose metrics, timeline, and logs around a
# specific deployment window so regressions can be reviewed in one API call.
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
    return _get_observability_service().get_service_deployment_observability(
        service_id=service_id,
        deployment_id=deployment_id,
        window_start=window_start,
        window_end=window_end,
        logs_preset=logs_preset,
        logs_limit=logs_limit,
        user=user,
    )


def get_catalog_reconciliation(
    env: str | None = Query(default=None),
    project_id: str | None = Query(default=None, alias="projectId"),
    service_id: str | None = Query(default=None, alias="serviceId"),
    _: tuple[str, set[str]] = Depends(get_current_user),
) -> CatalogJoinResponse:
    return _get_catalog_service().get_catalog_reconciliation(
        env=env,
        project_id=project_id,
        service_id=service_id,
    )


def sync_service_registry(
    source: str = Query(default="cluster_services"),
    env: str | None = Query(default=None),
    _: str = Depends(require_admin),
) -> ServiceRegistrySyncResponse:
    return _get_catalog_service().sync_service_registry(source=source, env=env)


def get_service_registry_diagnostics(
    env: str | None = Query(default=None),
    _: tuple[str, set[str]] = Depends(get_current_user),
) -> ServiceRegistryDiagnosticsResponse:
    return _get_catalog_service().get_service_registry_diagnostics(env=env)


def get_monitoring_provider_diagnostics(
    _: tuple[str, set[str]] = Depends(get_current_user),
) -> MonitoringProvidersDiagnosticsResponse:
    return _get_observability_service().get_monitoring_provider_diagnostics()


# The service-level observability endpoints below share the same identity resolution
# and provider-error translation patterns, but return progressively richer views.
def get_service_metrics_summary(
    service_id: str,
    selected_range: str = Query(
        default="24h",
        alias="range",
        pattern="^([1-9][0-9]*)(m|h|d)$",
    ),
    _: tuple[str, set[str]] = Depends(get_current_user),
) -> ServiceMetricsSummaryResponse:
    return _get_observability_service().get_service_metrics_summary(
        service_id=service_id,
        selected_range=selected_range,
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
    return _get_observability_service().get_service_metrics_trends(
        service_id=service_id,
        selected_range=selected_range,
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
    del identity
    return _get_observability_service().get_service_metrics_summary(
        service_id=service_id,
        selected_range=selected_range,
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
    return _get_observability_service().get_service_health_timeline(
        service_id=service_id,
        selected_range=selected_range,
        step=step,
    )


def get_active_alerts(
    env: str | None = Query(default=None),
    service_id: str | None = Query(default=None, alias="serviceId"),
    limit: int = Query(default=100, ge=1, le=500),
    _: tuple[str, set[str]] = Depends(get_current_user),
) -> ActiveAlertsResponse:
    return _get_observability_service().get_active_alerts(
        env=env,
        service_id=service_id,
        limit=limit,
    )


def get_monitoring_incidents_compat(
    env: str | None = Query(default=None),
    service_id: str | None = Query(default=None, alias="serviceId"),
    limit: int = Query(default=100, ge=1, le=500),
    identity: tuple[str, set[str]] = Depends(get_current_user),
) -> MonitoringIncidentsCompatEnvelope:
    del identity
    return _get_observability_service().get_monitoring_incidents_compat(
        env=env,
        service_id=service_id,
        limit=limit,
    )


def get_release_traceability(
    env: str | None = Query(default=None),
    service_id: str | None = Query(default=None, alias="serviceId"),
    limit: int = Query(default=50, ge=1, le=200),
    _: tuple[str, set[str]] = Depends(get_current_user),
) -> list[ReleaseTraceabilityResponse]:
    return _get_observability_service().get_release_traceability(
        env=env,
        service_id=service_id,
        limit=limit,
    )


def get_release_dashboard_compat(
    env: str | None = Query(default=None),
    service_id: str | None = Query(default=None, alias="serviceId"),
    limit: int = Query(default=50, ge=1, le=200),
    identity: tuple[str, set[str]] = Depends(get_current_user),
) -> ReleaseDashboardCompatResponse:
    del identity
    return _get_observability_service().get_release_dashboard_compat(
        env=env,
        service_id=service_id,
        limit=limit,
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
    user, _groups = identity
    return _get_observability_service().get_service_logs_quickview(
        service_id=service_id,
        preset=preset,
        selected_range=selected_range,
        limit=limit,
        cursor=cursor,
        namespace=namespace,
        app_label=app_label,
        user=user,
    )


# Admin feature sections that follow are intentionally grouped by ticket/feature
# lineage because they evolved incrementally and still share helper assumptions.

# ---------------------------------------------------------------------------
# T6.4.3 — Service scaffold endpoints
# ---------------------------------------------------------------------------

_WORKLOADS_KUSTOMIZATION_PATH = "environments/dev/workloads/kustomization.yaml"
_WORKLOADS_PROD_KUSTOMIZATION_PATH = "environments/prod/workloads/kustomization.yaml"
_WORKLOADS_APPPROJECT_PATH = "bootstrap/project-homelab.yaml"
_WORKLOADS_CATALOG_PATH = "services.yaml"


def _kustomization_path_for(inp: "ScaffoldServiceInput") -> str:
    return _WORKLOADS_PROD_KUSTOMIZATION_PATH if inp.template in ("postgres", "mysql") else _WORKLOADS_KUSTOMIZATION_PATH

def _build_scaffold_input(payload: ScaffoldServiceRequest) -> ScaffoldServiceInput:
    repo_slug = _workloads_repo_slug()
    name = payload.name.strip().lower()
    namespace = payload.namespace.strip() or name
    dev_host = payload.dev_host.strip() or f"{name}.dev.homelab.local"
    prod_host = payload.prod_host.strip() or f"{name}.homelab.local"
    base_domain = os.getenv("PUBLIC_BASE_DOMAIN", "homelab.local").strip() or "homelab.local"
    public_host = payload.public_host.strip() or f"{name}.{base_domain}"
    image_repo = payload.image_repo.strip()
    if payload.template == "wordpress" and not image_repo:
        image_repo = "wordpress:latest"
    return ScaffoldServiceInput(
        name=name,
        description=payload.description.strip(),
        image_repo=image_repo,
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


def _build_scaffold_bundle_input(payload: ScaffoldServiceRequest) -> ScaffoldBundleInput:
    repo_slug = _workloads_repo_slug()
    name = payload.name.strip().lower()
    namespace = payload.namespace.strip() or name
    dev_host = payload.dev_host.strip() or f"{name}.dev.homelab.local"
    prod_host = payload.prod_host.strip() or f"{name}.homelab.local"
    base_domain = os.getenv("PUBLIC_BASE_DOMAIN", "homelab.local").strip() or "homelab.local"
    public_host = payload.public_host.strip() or f"{name}.{base_domain}"

    if not payload.frontend_template:
        raise ScaffoldError("frontendTemplate is required for bundle topologies.", status_code=422)
    if not payload.backend_template:
        raise ScaffoldError("backendTemplate is required for bundle topologies.", status_code=422)
    if not payload.frontend_image_repo.strip():
        raise ScaffoldError("frontendImageRepo is required for bundle topologies.", status_code=422)
    if not payload.backend_image_repo.strip():
        raise ScaffoldError("backendImageRepo is required for bundle topologies.", status_code=422)
    if payload.topology == "frontend-backend-db" and not payload.db_template:
        raise ScaffoldError("dbTemplate is required for frontend-backend-db topology.", status_code=422)

    return ScaffoldBundleInput(
        name=name,
        description=payload.description.strip(),
        owner_email=payload.owner_email.strip(),
        owner=payload.owner.strip(),
        namespace=namespace,
        dev_host=dev_host,
        prod_host=prod_host,
        public_host=public_host,
        workloads_repo_url=_workloads_gitops_repo_url(repo_slug),
        repo_url=payload.repo_url.strip(),
        topology=payload.topology,
        frontend_template=payload.frontend_template,
        frontend_image_repo=payload.frontend_image_repo.strip(),
        backend_template=payload.backend_template,
        backend_image_repo=payload.backend_image_repo.strip(),
        db_template=payload.db_template,
        db_username=payload.db_username.strip() or "appuser",
        db_password=payload.db_password.strip() or "changeme",
        db_name=payload.db_name.strip() or "appdb",
    )


def _build_scaffold_add_service_input(payload: ScaffoldServiceRequest) -> ScaffoldAddServiceInput:
    repo_slug = _workloads_repo_slug()
    project_id = payload.project_id.strip().lower()
    service_name = payload.service_name.strip().lower()
    if not project_id:
        raise ScaffoldError("projectId is required for add-to-project mode.", status_code=422)
    if not service_name:
        raise ScaffoldError("serviceName is required for add-to-project mode.", status_code=422)

    namespace = payload.namespace.strip() or project_id
    dev_host = payload.dev_host.strip() or f"{project_id}.dev.homelab.local"
    prod_host = payload.prod_host.strip() or f"{project_id}.homelab.local"
    base_domain = os.getenv("PUBLIC_BASE_DOMAIN", "homelab.local").strip() or "homelab.local"
    public_host = payload.public_host.strip() or f"{project_id}.{base_domain}"
    return ScaffoldAddServiceInput(
        project_id=project_id,
        service_name=service_name,
        description=payload.description.strip(),
        owner_email=payload.owner_email.strip(),
        owner=payload.owner.strip(),
        namespace=namespace,
        template=payload.template,
        image_repo=payload.image_repo.strip(),
        repo_url=payload.repo_url.strip(),
        dev_host=dev_host,
        prod_host=prod_host,
        public_host=public_host,
        workloads_repo_url=_workloads_gitops_repo_url(repo_slug),
        db_username=payload.db_username.strip() or "appuser",
        db_password=payload.db_password.strip() or "changeme",
        db_name=payload.db_name.strip() or "appdb",
    )


def _generate_scaffold_files_and_updates(
    payload: ScaffoldServiceRequest,
    workloads_repo: str,
    base_branch: str,
    git_provider: object,
) -> tuple[dict[str, str], dict[str, str]]:
    """Shared logic for preview and submit: generate files and catalog updates.

    Returns (new_files, modified_files) where new_files are created and
    modified_files are existing files with updated content.
    """
    is_add_to_project = payload.mode == "add-to-project"
    is_bundle = not is_add_to_project and payload.topology != "single-service"

    if is_add_to_project:
        inp = _build_scaffold_add_service_input(payload)

        base_kust_path = f"apps/{inp.project_id}/base/kustomization.yaml"
        base_kustomization_raw = git_provider.read_file(workloads_repo, base_branch, base_kust_path)  # type: ignore[union-attr]
        services_yaml_raw = git_provider.read_file(workloads_repo, base_branch, _WORKLOADS_CATALOG_PATH)  # type: ignore[union-attr]

        validate_add_service(inp, base_kustomization_raw, services_yaml_raw)

        new_files, new_resources = generate_gitops_add_service_files(inp)
        modified_files: dict[str, str] = {}

        # Update the project's base kustomization with new resources
        updated_base_kust = base_kustomization_raw
        for resource in new_resources:
            updated_base_kust = update_kustomization_resources(updated_base_kust, resource)
        modified_files[base_kust_path] = updated_base_kust

        # Update overlay kustomizations with new patch references
        is_db = inp.template in ("postgres", "mysql")
        if not is_db:
            patch_filename = f"patch-{inp.service_name}-deployment.yaml"
            for env_name in ("dev", "prod"):
                overlay_kust_path = f"apps/{inp.project_id}/envs/{env_name}/kustomization.yaml"
                overlay_kust_raw = git_provider.read_file(workloads_repo, base_branch, overlay_kust_path)  # type: ignore[union-attr]
                modified_files[overlay_kust_path] = update_overlay_kustomization_patches(
                    overlay_kust_raw, patch_filename,
                )

        modified_files[_WORKLOADS_CATALOG_PATH] = build_catalog_add_service_entry(
            services_yaml_raw, inp,
        )
        return new_files, modified_files

    if is_bundle:
        inp = _build_scaffold_bundle_input(payload)
        validate_service_name(inp.name)
        kustomization_path = _WORKLOADS_KUSTOMIZATION_PATH
        kustomization_raw = git_provider.read_file(workloads_repo, base_branch, kustomization_path)  # type: ignore[union-attr]
        appproject_raw = git_provider.read_file(workloads_repo, base_branch, _WORKLOADS_APPPROJECT_PATH)  # type: ignore[union-attr]
        services_yaml_raw = git_provider.read_file(workloads_repo, base_branch, _WORKLOADS_CATALOG_PATH)  # type: ignore[union-attr]

        new_files = generate_gitops_bundle_files(inp)
        # Reuse single-service AppProject builder — same shape, just needs a dummy ScaffoldServiceInput
        single_inp = ScaffoldServiceInput(
            name=inp.name, description=inp.description, image_repo="",
            repo_url=inp.repo_url, owner_email=inp.owner_email, owner=inp.owner,
            template="python-fastapi", namespace=inp.namespace,
            dev_host=inp.dev_host, prod_host=inp.prod_host, public_host=inp.public_host,
            workloads_repo_url=inp.workloads_repo_url,
        )
        modified_files = {
            kustomization_path: update_kustomization_resources(kustomization_raw, f"{inp.name}-app.yaml"),
            _WORKLOADS_APPPROJECT_PATH: build_appproject_addition(appproject_raw, single_inp),
            _WORKLOADS_CATALOG_PATH: build_catalog_bundle_entries(services_yaml_raw, inp),
        }
    else:
        inp_single = _build_scaffold_input(payload)
        validate_service_name(inp_single.name)
        kustomization_path = _kustomization_path_for(inp_single)
        kustomization_raw = git_provider.read_file(workloads_repo, base_branch, kustomization_path)  # type: ignore[union-attr]
        appproject_raw = git_provider.read_file(workloads_repo, base_branch, _WORKLOADS_APPPROJECT_PATH)  # type: ignore[union-attr]
        services_yaml_raw = git_provider.read_file(workloads_repo, base_branch, _WORKLOADS_CATALOG_PATH)  # type: ignore[union-attr]

        new_files = generate_gitops_new_files(inp_single)
        modified_files = {
            kustomization_path: update_kustomization_resources(kustomization_raw, f"{inp_single.name}-app.yaml"),
            _WORKLOADS_APPPROJECT_PATH: build_appproject_addition(appproject_raw, inp_single),
            _WORKLOADS_CATALOG_PATH: build_catalog_entry_addition(services_yaml_raw, inp_single),
        }

    return new_files, modified_files


def scaffold_preview(
    payload: ScaffoldServiceRequest,
) -> ScaffoldPreviewResponse:
    return _get_scaffold_admin_service().scaffold_preview(payload=payload)


def scaffold_submit(
    payload: ScaffoldServiceRequest,
) -> ScaffoldSubmitResponse:
    return _get_scaffold_admin_service().scaffold_submit(payload=payload)


def scaffold_list_projects() -> list[ScaffoldProjectInfo]:
    return _get_scaffold_admin_service().scaffold_list_projects()


# ---------------------------------------------------------------------------
# T6.4.7 — Public hostname management
# ---------------------------------------------------------------------------




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


def update_service_public_hostname(
    service_id: str,
    payload: UpdatePublicHostnameRequest,
    response: Response,
    _admin: str = Depends(require_admin),
) -> UpdatePublicHostnameResponse | None:
    del _admin
    return _get_scaffold_admin_service().update_service_public_hostname(
        service_id=service_id,
        payload=payload,
        response=response,
    )


def _build_scaffold_admin_service() -> ScaffoldAdminService:
    return _compose_scaffold_admin_service(
        workloads_repo_slug=_workloads_repo_slug,
        workloads_base_branch=_workloads_base_branch,
        build_config_edit_branch_name=_build_config_edit_branch_name,
        build_config_edit_pr_body=_build_config_edit_pr_body,
        build_secret_edit_branch_name=_build_secret_edit_branch_name,
        build_secret_edit_pr_body=_build_secret_edit_pr_body,
        generate_scaffold_files_and_updates=_generate_scaffold_files_and_updates,
        read_current_public_host_from_services_yaml=_read_current_public_host_from_services_yaml,
        read_current_host_from_patch_ingress=_read_current_host_from_patch_ingress,
        update_services_yaml_public_host=_update_services_yaml_public_host,
        update_patch_ingress_host=_update_patch_ingress_host,
        workloads_catalog_path=_WORKLOADS_CATALOG_PATH,
    )

def _get_scaffold_admin_service() -> ScaffoldAdminService:
    return get_backend_service_builders(app).build_scaffold_admin_service()


configure_backend_services()


# Route registration now lives in app.api.routes.* so this module can keep
# the existing endpoint implementations while shedding route concentration.
import sys

from app.api.app import register_api_routes

register_api_routes(app, sys.modules[__name__])
