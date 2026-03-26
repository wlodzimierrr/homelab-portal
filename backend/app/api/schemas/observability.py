from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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


ServiceMetricsSummaryResponse.model_rebuild()


ServiceMetricsTrendsResponse.model_rebuild()
