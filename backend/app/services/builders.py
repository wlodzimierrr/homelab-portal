"""Constructor helpers for composing application service instances."""

from __future__ import annotations

from app.alerts_feed import normalize_active_alerts
from app.catalog_reconciliation import build_catalog_join
from app.lib import build_default_git_provider
from app.monitoring_providers import probe_monitoring_provider
from app.release_traceability import (
    build_release_join_diagnostics,
    load_argo_metadata_rows,
    load_ci_metadata_rows,
)
from app.runtime_config import (
    dev_deploy_target as _dev_deploy_target,
    promote_to_prod_target as _promote_to_prod_target,
    rollback_target as _rollback_target,
    workloads_base_branch as _workloads_base_branch,
    workloads_repo_slug as _workloads_repo_slug,
)
from app.service_identity_validation import build_service_identity_diagnostics
from app.github_workflows import dispatch_portal_rollback_workflow
from app.services.catalog_service import CatalogService, CatalogServiceDeps
from app.services.deployment_service import DeploymentService, DeploymentServiceDeps
from app.services.observability_service import (
    ObservabilityService,
    ObservabilityServiceDeps,
)
from app.services.scaffold_admin_service import (
    ScaffoldAdminService,
    ScaffoldAdminServiceDeps,
)
from app.helpers.deployment_helpers import (
    PortalDeployToDevError,
    PortalPromoteToProdError,
    PortalServiceRollbackError,
    _with_connection,
    _list_deployment_records_for_service,
    _get_deployment_record_by_id,
    _get_active_deployment_lock,
    _load_service_rows,
    _load_project_rows,
    _extract_version_from_image_ref,
    _build_compare_url_for_portal_tags,
    _build_dev_deploy_branch_name,
    _build_dev_deploy_pr_body,
    _build_prod_promote_branch_name,
    _build_promote_to_prod_pr_body,
    _build_service_rollback_branch_name,
    _build_service_rollback_pr_body,
    _build_deployment_record_response,
    _build_deployment_lock_response,
    _ensure_ghcr_tag_exists,
    _extract_sha_from_tag,
    _extract_image_ref_from_overlay,
    _list_service_rollback_candidates,
    _load_dev_overlay_update_plan,
    _load_promote_to_prod_update_plan,
    _load_service_rollback_update_plan,
    _resolve_latest_portal_image_candidate,
    _select_latest_deployment_info_record,
    _extract_image_digest,
    _deployment_record_timestamp,
    _build_commit_url,
    _build_package_url_from_image_ref,
    _select_preferred_service_row,
    _build_config_edit_branch_name,
    _build_config_edit_pr_body,
    _build_secret_edit_branch_name,
    _build_secret_edit_pr_body,
    _enrich_release_rows_with_live_runtime,
    _load_live_service_runtime_rows,
    _load_release_rows_for_service,
    _sort_release_rows_by_deployed_at,
    _release_row_has_meaningful_metadata,
    _coalesce_service_status,
    _registry_stale_after_minutes,
    _registry_warning_after_minutes,
    _build_service_metrics_queries,
    _query_prometheus_range,
)
from app.helpers.observability_helpers import (
    _load_project_catalog_rows,
    _load_service_catalog_rows,
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

# Set by app.main after cache/logger creation.
_deployment_history_cache = None
_deployment_reconcile_cache = None
_metrics_summary_cache = None
_timeline_cache = None
_logs_quickview_cache = None
_logger = None
# Set by app.main after closure functions are created.
_maybe_reconcile_recent_deployments = None
_upsert_deployment_record_row = None


def _compose_deployment_service(**deps) -> DeploymentService:
    return DeploymentService(DeploymentServiceDeps(**deps))


def _compose_observability_service(**deps) -> ObservabilityService:
    return ObservabilityService(ObservabilityServiceDeps(**deps))


def _compose_catalog_service(**deps) -> CatalogService:
    return CatalogService(CatalogServiceDeps(**deps))


def _compose_scaffold_admin_service(**deps) -> ScaffoldAdminService:
    return ScaffoldAdminService(ScaffoldAdminServiceDeps(**deps))


# Keep old names as aliases so any external callers are not broken.
build_deployment_service = _compose_deployment_service
build_observability_service = _compose_observability_service
build_catalog_service = _compose_catalog_service
build_scaffold_admin_service = _compose_scaffold_admin_service


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
        deployment_history_cache=_deployment_history_cache,
        deployment_reconcile_cache=_deployment_reconcile_cache,
        logger=_logger,
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
        metrics_summary_cache=_metrics_summary_cache,
        timeline_cache=_timeline_cache,
        logs_quickview_cache=_logs_quickview_cache,
        logger=_logger,
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
        generate_scaffold_files_and_updates,
        read_current_host_from_patch_ingress,
        read_current_public_host_from_services_yaml,
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
        build_default_git_provider=build_default_git_provider,
    )
