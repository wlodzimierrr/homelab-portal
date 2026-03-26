"""Observability and monitoring endpoint handlers.

Extracted from main.py (Phase R3) to reduce file size without changing
behaviour.  The handlers are thin wrappers that delegate to
ObservabilityService.
"""

from fastapi import Depends, FastAPI, Query

from app.api.deps import get_current_user
from app.api.schemas.deployments import (
    ReleaseDashboardCompatResponse,
    ReleaseTraceabilityResponse,
)
from app.api.schemas.observability import (
    ActiveAlertsResponse,
    DeploymentObservabilityResponse,
    LogsQuickViewResponse,
    MonitoringIncidentsCompatEnvelope,
    MonitoringProvidersDiagnosticsResponse,
    ServiceHealthTimelineSegmentResponse,
    ServiceMetricsSummaryResponse,
    ServiceMetricsTrendsResponse,
)
from app.services.composition import get_backend_service_builders
from app.services.observability_service import ObservabilityService

# ---------------------------------------------------------------------------
# Module-level app reference (set once by init())
# ---------------------------------------------------------------------------

_app: FastAPI | None = None


def init(app: FastAPI) -> None:
    """Store the FastAPI instance so handlers can resolve services lazily."""
    global _app  # noqa: PLW0603
    _app = app


def _get_observability_service() -> ObservabilityService:
    assert _app is not None, "observability endpoints not initialised — call init(app) first"
    return get_backend_service_builders(_app).build_observability_service()


# ---------------------------------------------------------------------------
# Endpoint handlers
# ---------------------------------------------------------------------------


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


def get_monitoring_provider_diagnostics(
    _: tuple[str, set[str]] = Depends(get_current_user),
) -> MonitoringProvidersDiagnosticsResponse:
    return _get_observability_service().get_monitoring_provider_diagnostics()


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
