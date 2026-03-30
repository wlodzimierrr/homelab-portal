# --- stdlib / fastapi --------------------------------------------------------
import json
import logging
from typing import Callable
from urllib import error as urlerror
from urllib import request as urlrequest  # noqa: F401 — patched by tests at app.main.urlrequest

from fastapi import FastAPI, status

# --- app module imports ------------------------------------------------------
# Core domain modules used directly by the deployment helpers defined below.
from app.deployment_locks import DeploymentLockConflictError  # noqa: F401
from app.deployment_locks import (
    cleanup_stale_deployment_locks,
    sync_deployment_lock_for_deployment_row,
)
from app.deployment_records import upsert_deployment_record
from app.alerts_feed import normalize_active_alerts
from app.catalog_reconciliation import build_catalog_join
from app.deployment_reconciler import make_pr_comment_poster, reconcile_recent_gitops_deployments
from app.github_workflows import dispatch_portal_rollback_workflow  # noqa: F401 — patched by tests
from app.lib import build_default_git_provider  # noqa: F401 — patched by tests
from app.monitoring_providers import probe_monitoring_provider  # noqa: F401 — patched by tests
from app.release_traceability import (
    build_release_join_diagnostics,
    load_argo_metadata_rows,  # noqa: F401 — patched by tests
    load_ci_metadata_rows,  # noqa: F401 — patched by tests
)
from app.service_identity_validation import build_service_identity_diagnostics

# --- service layer -----------------------------------------------------------
# Service types are imported so configure_backend_services() can type its
# optional override parameters, and so the BackendServiceBuilders dataclass
# can be constructed below.
from app.services.deployment_service import DeploymentService
from app.services.builders import (
    build_deployment_service as _compose_deployment_service,
    build_observability_service as _compose_observability_service,
    build_catalog_service as _compose_catalog_service,
    build_scaffold_admin_service as _compose_scaffold_admin_service,
)
from app.services.catalog_service import CatalogService
from app.services.composition import (
    BackendServiceBuilders,
    configure_backend_service_builders as _configure_backend_service_builders,
)
from app.services.observability_service import ObservabilityService
from app.services.scaffold_admin_service import ScaffoldAdminService
from app.services.startup_jobs import register_deployment_reconciler_jobs

# --- schema re-exports -------------------------------------------------------
# Route modules and tests resolve response model classes through `app.main.*`.
# These imports keep that working without duplicating schema definitions.
from app.api.schemas.auth import LoginResponse  # noqa: F401
from app.api.bootstrap import (
    clear_observability_caches,
    create_api_app,
    create_http_metrics_state,
    create_observability_caches,
    install_http_metrics_middleware,
)
from app.api.schemas.catalog import (  # noqa: F401
    CatalogJoinResponse,
    DeploymentLockResponse,
    Project,
    ProjectCatalogDiagnosticsResponse,
    ProjectsResponse,
    ServiceCapabilitiesResponse,
    ServiceDetailResponse,
    ServiceProjectContextResponse,
    ServiceRegistryDiagnosticsResponse,
    ServiceRegistrySyncResponse,
    ServicesResponse,
)
from app.api.schemas.deployments import (  # noqa: F401
    CreateDeploymentRecordRequest,
    DeploymentReconcileResponse,
    DeploymentRecordResponse,
    PortalDeployToDevResponse,
    PortalPromoteToProdResponse,
    PortalRollbackResponse,
    PortalServiceRollbackCandidatesResponse,
    PortalServiceRollbackResponse,
    PortalSetConfigResponse,
    PortalSetSecretResponse,
    ROLLBACK_TAG_RE,
    ReleaseDashboardCompatResponse,
    ReleaseTraceabilityResponse,
    ServiceConfigResponse,
    ServiceDeploymentInfoResponse,
    ServiceDeploymentsResponse,
    UpdatePublicHostnameResponse,
)
from app.api.schemas.observability import (  # noqa: F401
    ActiveAlertsResponse,
    DeploymentObservabilityResponse,
    DeploymentObservabilityContextResponse,
    DeploymentObservabilityLogsResponse,
    DeploymentObservabilityMetricSnapshotResponse,
    DeploymentObservabilityMetricsResponse,
    DeploymentObservabilityTimelineResponse,
    HealthResponse,
    LogsQuickViewResponse,
    MonitoringIncidentsCompatEnvelope,
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
from app.api.schemas.migration import (  # noqa: F401
    AdoptServiceResponse,
    MigrationConsolidateResponse,
    MigrationValidateResponse,
)
from app.api.schemas.scaffold import ScaffoldPreviewResponse, ScaffoldProjectInfo, ScaffoldSubmitResponse  # noqa: F401

# --- runtime config ----------------------------------------------------------
# Thin callables that read env vars at call time rather than at import time,
# so tests can override them without reloading the module.
from app.admin.config_policy import CONFIG_EDIT_TARGETS as _CONFIG_EDIT_TARGETS
from app.runtime_config import (
    deployment_lock_stale_timeout_seconds as _deployment_lock_stale_timeout_seconds,
    deployment_reconciler_enabled as _deployment_reconciler_enabled,
    deployment_reconciler_gitops_repo_slug as _deployment_reconciler_gitops_repo_slug,
    deployment_reconciler_interval_seconds as _deployment_reconciler_interval_seconds,
    deployment_reconciler_pr_comments_enabled as _deployment_reconciler_pr_comments_enabled,
    deployment_reconciler_read_ttl_seconds as _deployment_reconciler_read_ttl_seconds,
    deployment_reconciler_readthrough_enabled as _deployment_reconciler_readthrough_enabled,
    dev_deploy_target as _dev_deploy_target,
    ghcr_token as _ghcr_token,  # noqa: F401 — patched by tests at app.main._ghcr_token
    github_api_base_url as _github_api_base_url,  # noqa: F401 — patched by tests at app.main._github_api_base_url
    github_api_token_for_path as _github_api_token_for_path,  # noqa: F401 — patched by tests
    promote_to_prod_target as _promote_to_prod_target,
    rollback_target as _rollback_target,
    workloads_base_branch as _workloads_base_branch,
    workloads_repo_slug as _workloads_repo_slug,
)

# --- helper re-exports -------------------------------------------------------
# All helpers live in app/helpers/. They are re-imported here so that:
#   (a) tests can monkeypatch app.main.<name> and affect the service builders,
#   (b) any existing code that resolves helpers through app.main.* keeps working.
from app.helpers.deployment_helpers import (
    PortalDeployToDevError,
    PortalPromoteToProdError,
    PortalServiceRollbackError,
    _with_connection,
    _list_deployment_records_for_service,
    _get_deployment_record_by_id,
    _get_active_deployment_lock,
    _load_project_rows,
    _load_service_rows,
    _extract_version_from_image_ref,
    _github_api_json,
    _build_service_image_ref,
    _build_prod_service_image_ref,
    _extract_sha_from_tag,
    _build_compare_url_for_portal_tags,
    _build_commit_url,
    _parse_ghcr_image_repo,
    _build_package_url_from_image_ref,
    _extract_image_digest,
    _deployment_record_timestamp,
    _select_latest_deployment_info_record,
    _github_package_version_paths,
    _package_version_has_tag,
    _safe_branch_fragment,
    _build_dev_deploy_branch_name,
    _build_prod_promote_branch_name,
    _build_service_rollback_branch_name,
    _extract_image_ref_from_overlay,
    _replace_image_ref_in_overlay,
    _resolve_latest_portal_image_candidate,
    _load_dev_overlay_update_plan,
    _load_promote_to_prod_update_plan,
    _load_service_rollback_update_plan,
    _list_service_rollback_candidates,
    _build_dev_deploy_pr_body,
    _build_promote_to_prod_pr_body,
    _build_service_rollback_pr_body,
    _build_secret_edit_branch_name,
    _build_config_edit_branch_name,
    _build_config_edit_pr_body,
    _build_secret_edit_pr_body,
    _select_preferred_service_row,
    _normalize_live_sync_status,
    _normalize_live_health_status,
    _release_row_has_meaningful_metadata,
    _coalesce_service_status,
    _list_live_deployments_for_service,
    _load_live_argo_status_for_service,
    _extract_live_deployment_image_ref,
    _extract_live_deployment_health,
    _extract_live_deployment_timestamp,
    _load_live_service_runtime_rows,
    _load_release_rows_for_service,
    _sort_release_rows_by_deployed_at,
    _coalesce_release_string,
    _enrich_release_row_with_live_runtime,
    _enrich_release_rows_with_live_runtime,
    _registry_stale_after_minutes,
    _registry_warning_after_minutes,
    _deployment_history_cache_ttl_seconds,
    _deployment_comparison_window_token,
    _parse_iso_datetime,
    _build_metric_snapshot,
    _format_duration_token,
    _resolve_window_end,
    _expand_observability_query_window,
    _resolve_record_window,
    _query_prometheus_comparison_snapshot,
    _load_metric_snapshots_for_window,
    _load_deployment_metric_snapshots,
    _persist_observability_snapshot_safe,
    _deployment_record_sort_timestamp,
    _build_deployment_record_response,
    _build_deployment_lock_response,
    _query_prometheus_range,
    _build_service_metrics_queries,
)
import app.helpers.deployment_helpers as _deployment_helpers_module
import app.helpers.catalog_helpers as _catalog_helpers_module
from app.helpers.catalog_helpers import _load_service_catalog_rows
from app.helpers.observability_helpers import (
    _load_project_catalog_rows,
    _resolve_service_monitoring_context,
    _resolve_service_monitoring_metadata,
    _build_service_metrics_probe_queries,
    _query_prometheus_series_present,
    _build_metrics_observability_diagnostics,
    _query_prometheus_scalar,
    _query_loki_range,
    _query_alertmanager_active_alerts,
    _validate_selected_range,
    _effective_limit,
    _serialize_metric_trend_points,
    _build_metric_trend_series,
    _build_health_timeline_queries,
    _validate_step_for_range,
    _serialize_metric_snapshot,
    _select_timeline_step_seconds,
    _resolve_deployment_observability_context,
    _build_no_window_metrics_response,
    _build_no_window_timeline_response,
    _build_no_window_logs_response,
    _extract_provider_failure,
    _build_provider_error_metrics_response,
    _build_provider_error_timeline_response,
    _build_provider_error_logs_response,
    _build_deployment_metrics_response,
    _build_deployment_timeline_response,
    _build_deployment_logs_response,
)
import app.helpers.observability_helpers as _observability_helpers_module

# FastAPI entrypoint for the portal backend.
#
# After the R1–R9 refactor this module is intentionally thin:
#   - Imports and re-exports every name that route modules or tests resolve via
#     `app.main.*` (schemas, helper functions, error types).
#   - Creates the shared TTLCache singletons and injects them into the helper
#     modules that need them (see "cache injection" block below).
#   - Defines the three deployment helpers that must live here because they
#     close over the live cache objects: _upsert_deployment_record_row,
#     _reconcile_recent_deployment_activity, _maybe_reconcile_recent_deployments.
#   - Calls configure_backend_services() to wire up the service layer, then
#     _register_api_routes() to mount all API routers.
#
# Business logic lives in app/helpers/ and app/services/. Endpoint handlers live
# in app/api/endpoints/. This file should not grow new logic.


def _proxy_main_callable(name: str) -> Callable[..., object]:
    """Route helper-module callbacks through app.main so test monkeypatches stick."""

    def _call(*args: object, **kwargs: object) -> object:
        return globals()[name](*args, **kwargs)

    return _call


# --- monkeypatch seam restoration -------------------------------------------
# The refactor moved a number of helper functions into dedicated modules, but the
# test suite intentionally monkeypatches app.main.* as the stable seam. Rebind the
# helper-module globals that call each other internally so they resolve through
# app.main at runtime instead of closing over their original module globals.
_catalog_helpers_module._with_connection = _proxy_main_callable("_with_connection")
_deployment_helpers_module._load_project_rows = _proxy_main_callable("_load_project_rows")
_deployment_helpers_module._load_service_rows = _proxy_main_callable("_load_service_rows")
_deployment_helpers_module._select_preferred_service_row = _proxy_main_callable("_select_preferred_service_row")
_deployment_helpers_module._load_live_service_runtime_rows = _proxy_main_callable(
    "_load_live_service_runtime_rows"
)
_deployment_helpers_module._load_metric_snapshots_for_window = _proxy_main_callable(
    "_load_metric_snapshots_for_window"
)
_deployment_helpers_module._load_deployment_metric_snapshots = _proxy_main_callable(
    "_load_deployment_metric_snapshots"
)
_observability_helpers_module._load_service_rows = _proxy_main_callable("_load_service_rows")
_observability_helpers_module._select_preferred_service_row = _proxy_main_callable(
    "_select_preferred_service_row"
)
_observability_helpers_module._get_deployment_record_by_id = _proxy_main_callable(
    "_get_deployment_record_by_id"
)
_observability_helpers_module._resolve_record_window = _proxy_main_callable("_resolve_record_window")
_observability_helpers_module._expand_observability_query_window = _proxy_main_callable(
    "_expand_observability_query_window"
)
_observability_helpers_module._load_metric_snapshots_for_window = _proxy_main_callable(
    "_load_metric_snapshots_for_window"
)
_observability_helpers_module._load_project_catalog_rows = _proxy_main_callable(
    "_load_project_catalog_rows"
)

# --- app init ----------------------------------------------------------------
app = create_api_app()
logger = logging.getLogger("homelab.backend.monitoring")

# --- cache creation ----------------------------------------------------------
# TTLCache singletons are created once at import time. They are exposed as
# module-level names so tests can call clear_observability_caches_for_tests()
# to reset state between test runs.
_observability_caches = create_observability_caches()
metrics_summary_cache = _observability_caches.metrics_summary_cache
timeline_cache = _observability_caches.timeline_cache
logs_quickview_cache = _observability_caches.logs_quickview_cache
alerts_cache = _observability_caches.alerts_cache
deployment_history_cache = _observability_caches.deployment_history_cache
deployment_reconcile_cache = _observability_caches.deployment_reconcile_cache

# --- cache injection ---------------------------------------------------------
# Helper modules declare module-level None sentinels and we assign the real
# singletons here after creation. This avoids circular imports (helpers don't
# import main) while ensuring every module shares the same TTLCache instance —
# so clearing works correctly in tests and in production.
_deployment_helpers_module._deployment_history_cache = deployment_history_cache
_observability_helpers_module._timeline_cache = timeline_cache
_observability_helpers_module._logs_quickview_cache = logs_quickview_cache

# --- HTTP metrics middleware --------------------------------------------------
_http_metrics_state = create_http_metrics_state()
install_http_metrics_middleware(app, _http_metrics_state)


# --- test seam ---------------------------------------------------------------
def clear_observability_caches_for_tests() -> None:
    clear_observability_caches(_observability_caches)


# --- deployment helpers (defined here, not in deployment_helpers.py) ---------
# These three functions must live in app.main because they close over the live
# deployment_history_cache and deployment_reconcile_cache objects created above.
# Moving them to a helper module would break cache clearing (the helper module
# would hold a stale reference to the original None sentinel).

def _ensure_ghcr_tag_exists(
    image_repo: str,
    tag: str,
    *,
    purpose: str = "Requested image tag",
    timeout_seconds: float = 10.0,
) -> None:
    """Check that *tag* exists in GitHub Packages for *image_repo*.

    Defined here (rather than only in helpers) so that tests can patch
    ``app.main.urlrequest``, ``app.main._ghcr_token``, and
    ``app.main._github_api_base_url`` to intercept HTTP calls.
    """
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


# --- background reconciler registration --------------------------------------
# Registers FastAPI startup/shutdown hooks that run the deployment reconciler
# on a background thread. The thread lifecycle is managed by startup_jobs.py.
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


# --- service builders --------------------------------------------------------
# These four functions must live in app.main so that every dependency they
# pass resolves from app.main's namespace at call time. Tests monkeypatch
# names on app.main (e.g. app.main._load_service_rows) and these builders
# are called fresh per request — so the patched names are picked up correctly.

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
        build_default_git_provider=build_default_git_provider,
        dispatch_portal_rollback_workflow=dispatch_portal_rollback_workflow,
        deploy_to_dev_error_type=PortalDeployToDevError,
        promote_to_prod_error_type=PortalPromoteToProdError,
        service_rollback_error_type=PortalServiceRollbackError,
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
        probe_monitoring_provider=probe_monitoring_provider,
        normalize_active_alerts=normalize_active_alerts,
        load_ci_metadata_rows=load_ci_metadata_rows,
        load_argo_metadata_rows=load_argo_metadata_rows,
    )


def _build_catalog_service() -> CatalogService:
    from app.services import catalog_service as catalog_service_module

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
        build_catalog_join=build_catalog_join,
        dev_deploy_target=_dev_deploy_target,
        promote_to_prod_target=_promote_to_prod_target,
        rollback_target=_rollback_target,
        config_edit_targets=_CONFIG_EDIT_TARGETS,
        sync_project_registry_from_gitops=catalog_service_module.sync_project_registry_from_gitops,
        sync_service_registry_from_cluster=catalog_service_module.sync_service_registry_from_cluster,
        load_ci_metadata_rows=load_ci_metadata_rows,
        load_argo_metadata_rows=load_argo_metadata_rows,
        build_release_join_diagnostics=build_release_join_diagnostics,
        build_service_identity_diagnostics=build_service_identity_diagnostics,
    )


def _build_scaffold_admin_service() -> ScaffoldAdminService:
    from app.api.endpoints.scaffold import (
        WORKLOADS_CATALOG_PATH,
        WORKLOADS_CATALOG_SYNC_CRONJOB_PATH,
        generate_scaffold_files_and_updates,
        read_current_host_from_patch_ingress,
        read_current_public_host_from_services_yaml,
        update_service_registry_sync_namespaces,
        update_patch_ingress_host,
        update_services_yaml_public_host,
    )

    return _compose_scaffold_admin_service(
        workloads_repo_slug=_workloads_repo_slug,
        workloads_base_branch=_workloads_base_branch,
        build_config_edit_branch_name=_build_config_edit_branch_name,
        build_config_edit_pr_body=_build_config_edit_pr_body,
        build_secret_edit_branch_name=_build_secret_edit_branch_name,
        build_secret_edit_pr_body=_build_secret_edit_pr_body,
        generate_scaffold_files_and_updates=generate_scaffold_files_and_updates,
        read_current_public_host_from_services_yaml=read_current_public_host_from_services_yaml,
        read_current_host_from_patch_ingress=read_current_host_from_patch_ingress,
        update_services_yaml_public_host=update_services_yaml_public_host,
        update_patch_ingress_host=update_patch_ingress_host,
        workloads_catalog_path=WORKLOADS_CATALOG_PATH,
        workloads_catalog_sync_cronjob_path=WORKLOADS_CATALOG_SYNC_CRONJOB_PATH,
        update_service_registry_sync_namespaces=update_service_registry_sync_namespaces,
        build_default_git_provider=build_default_git_provider,
    )


# --- service wiring ----------------------------------------------------------
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


configure_backend_services()


# --- route registration ------------------------------------------------------
def _register_api_routes() -> None:
    # Import lazily to avoid route-module circular imports while still keeping
    # registration colocated with the app entrypoint.
    import sys

    from app.api.app import register_api_routes

    register_api_routes(app, sys.modules[__name__])


_register_api_routes()
