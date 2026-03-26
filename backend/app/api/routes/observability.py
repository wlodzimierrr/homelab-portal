"""Observability and monitoring routes."""

from types import ModuleType

from fastapi import FastAPI

from app.api.routes._utils import wrap_sync_endpoint


def register_routes(app: FastAPI, main_module: ModuleType) -> None:
    app.add_api_route(
        "/services/{service_id}/observability/window",
        endpoint=wrap_sync_endpoint(main_module.get_service_deployment_observability),
        methods=["GET"],
        response_model=main_module.DeploymentObservabilityResponse,
        tags=["monitoring"],
    )

    app.add_api_route(
        "/monitoring/providers/diagnostics",
        endpoint=wrap_sync_endpoint(main_module.get_monitoring_provider_diagnostics),
        methods=["GET"],
        response_model=main_module.MonitoringProvidersDiagnosticsResponse,
        tags=["monitoring"],
    )

    app.add_api_route(
        "/services/{service_id}/metrics/summary",
        endpoint=wrap_sync_endpoint(main_module.get_service_metrics_summary),
        methods=["GET"],
        response_model=main_module.ServiceMetricsSummaryResponse,
        tags=["monitoring"],
    )

    app.add_api_route(
        "/services/{service_id}/metrics/trends",
        endpoint=wrap_sync_endpoint(main_module.get_service_metrics_trends),
        methods=["GET"],
        response_model=main_module.ServiceMetricsTrendsResponse,
        tags=["monitoring"],
    )

    app.add_api_route(
        "/services/{service_id}/metrics-summary",
        endpoint=wrap_sync_endpoint(main_module.get_service_metrics_summary_legacy),
        methods=["GET"],
        response_model=main_module.ServiceMetricsSummaryResponse,
        tags=["monitoring"],
    )

    app.add_api_route(
        "/services/{service_id}/health/timeline",
        endpoint=wrap_sync_endpoint(main_module.get_service_health_timeline),
        methods=["GET"],
        response_model=list[main_module.ServiceHealthTimelineSegmentResponse],
        tags=["monitoring"],
    )

    app.add_api_route(
        "/alerts/active",
        endpoint=wrap_sync_endpoint(main_module.get_active_alerts),
        methods=["GET"],
        response_model=main_module.ActiveAlertsResponse,
        tags=["monitoring"],
    )

    app.add_api_route(
        "/monitoring/incidents",
        endpoint=wrap_sync_endpoint(main_module.get_monitoring_incidents_compat),
        methods=["GET"],
        response_model=main_module.MonitoringIncidentsCompatEnvelope,
        tags=["monitoring"],
    )

    app.add_api_route(
        "/releases",
        endpoint=wrap_sync_endpoint(main_module.get_release_traceability),
        methods=["GET"],
        response_model=list[main_module.ReleaseTraceabilityResponse],
        tags=["monitoring"],
    )

    app.add_api_route(
        "/release-dashboard",
        endpoint=wrap_sync_endpoint(main_module.get_release_dashboard_compat),
        methods=["GET"],
        response_model=main_module.ReleaseDashboardCompatResponse,
        tags=["monitoring"],
    )

    app.add_api_route(
        "/services/{service_id}/logs/quickview",
        endpoint=wrap_sync_endpoint(main_module.get_service_logs_quickview),
        methods=["GET"],
        response_model=main_module.LogsQuickViewResponse,
        tags=["monitoring"],
    )
