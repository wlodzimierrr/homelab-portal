from datetime import datetime, timedelta, timezone
import logging
import math
import os
from typing import Literal
from urllib import parse as urlparse
from uuid import uuid4

from fastapi import HTTPException, status

from app.alerts_feed import get_alertmanager_base_url
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
    raise_provider_bad_payload_error,
)
from app.observability_config import (
    build_ingress_service_pattern,
    escape_promql_regex_literal,
    load_observability_config,
    render_query_template,
)
from app.service_observability import (
    build_service_metrics_observability_diagnostics,
    normalize_observability_mode,
)
from app.helpers.deployment_helpers import (
    _load_service_rows,
    _select_preferred_service_row,
    _get_deployment_record_by_id,
    _resolve_record_window,
    _parse_iso_datetime,
    _expand_observability_query_window,
    _load_metric_snapshots_for_window,
    _query_prometheus_range,
)
from app.helpers.catalog_helpers import _load_project_catalog_rows
from app.api.schemas.observability import (
    DeploymentObservabilityContextResponse,
    DeploymentObservabilityLogsResponse,
    DeploymentObservabilityMetricSnapshotResponse,
    DeploymentObservabilityMetricsResponse,
    DeploymentObservabilityTimelineResponse,
    MonitoringProviderStatusResponse,
    QuickViewLogLineResponse,
    ServiceHealthTimelineSegmentResponse,
    ServiceMetricTrendPointResponse,
    ServiceMetricTrendSeriesResponse,
    ServiceMetricsObservabilityDiagnosticsResponse,
)

logger = logging.getLogger("homelab.backend.monitoring")

# Set by app.main after cache creation.
_timeline_cache = None
_logs_quickview_cache = None


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
    service_id: str,
) -> dict[str, str]:
    ingress_service_pattern = build_ingress_service_pattern(app_label, service_id)
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
    probe_queries = _build_service_metrics_probe_queries(
        namespace=namespace,
        app_label=app_label,
        service_id=service_id,
    )
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

    segments = _timeline_cache.get_or_set(
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
    lines = _logs_quickview_cache.get_or_set(
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
