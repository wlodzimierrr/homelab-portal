"""Observability-oriented application service helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
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
    get_prometheus_base_url,
)
from app.release_traceability import (
    build_release_traceability_rows,
)
from app.service_observability import normalize_observability_mode
from app.observability_config import load_observability_config
from app.api.schemas.observability import (
    ActiveAlertResponse,
    ActiveAlertsResponse,
    DeploymentObservabilityResponse,
    LogsQuickViewResponse,
    MonitoringIncidentCompatResponse,
    MonitoringIncidentsCompatEnvelope,
    MonitoringProviderStatusResponse,
    MonitoringProvidersDiagnosticsResponse,
    QuickViewLogLineResponse,
    ServiceHealthTimelineSegmentResponse,
    ServiceMetricsSummaryResponse,
    ServiceMetricsTrendsResponse,
)
from app.api.schemas.deployments import (
    ReleaseArgoStateResponse,
    ReleaseDashboardCompatResponse,
    ReleaseDashboardCompatRow,
    ReleaseDriftStateResponse,
    ReleaseTraceabilityResponse,
)


@dataclass(frozen=True)
class ObservabilityServiceDeps:
    resolve_deployment_observability_context: Any
    build_no_window_metrics_response: Any
    build_no_window_timeline_response: Any
    build_no_window_logs_response: Any
    build_deployment_metrics_response: Any
    build_deployment_timeline_response: Any
    build_deployment_logs_response: Any
    extract_provider_failure: Any
    build_provider_error_metrics_response: Any
    build_provider_error_timeline_response: Any
    build_provider_error_logs_response: Any
    validate_selected_range: Any
    resolve_service_monitoring_context: Any
    resolve_service_monitoring_metadata: Any
    build_service_metrics_queries: Any
    build_metrics_observability_diagnostics: Any
    build_metric_trend_series: Any
    validate_step_for_range: Any
    build_health_timeline_queries: Any
    query_prometheus_scalar: Any
    query_prometheus_range: Any
    query_loki_range: Any
    query_alertmanager_active_alerts: Any
    select_timeline_step_seconds: Any
    effective_limit: Any
    enrich_release_rows_with_live_runtime: Any
    load_project_rows: Any
    metrics_summary_cache: Any
    timeline_cache: Any
    logs_quickview_cache: Any
    logger: Any
    probe_monitoring_provider: Any
    normalize_active_alerts: Any
    load_ci_metadata_rows: Any
    load_argo_metadata_rows: Any


class ObservabilityService:
    def __init__(self, deps: ObservabilityServiceDeps) -> None:
        self.deps = deps

    def get_service_deployment_observability(
        self,
        *,
        service_id: str,
        deployment_id: str | None,
        window_start: str | None,
        window_end: str | None,
        logs_preset: str,
        logs_limit: int,
        user: str,
    ) -> DeploymentObservabilityResponse:
        context, service_row, resolved_start, resolved_end = (
            self.deps.resolve_deployment_observability_context(
                service_id=service_id,
                deployment_id=deployment_id,
                window_start_value=window_start,
                window_end_value=window_end,
            )
        )
        if context.evidence_status != "resolved" or resolved_start is None or resolved_end is None:
            message = context.evidence_message or "No deployment window was available for this deployment record."
            return DeploymentObservabilityResponse(
                serviceId=service_id,
                context=context,
                metrics=self.deps.build_no_window_metrics_response(
                    message=message,
                    window_start=resolved_start,
                    window_end=resolved_end,
                ),
                healthTimeline=self.deps.build_no_window_timeline_response(
                    service_id=service_id,
                    message=message,
                    window_start=resolved_start,
                    window_end=resolved_end,
                ),
                logsQuickView=self.deps.build_no_window_logs_response(
                    service_id=service_id,
                    preset=logs_preset,
                    limit=logs_limit,
                    message=message,
                    window_start=resolved_start,
                    window_end=resolved_end,
                ),
            )

        try:
            metrics = self.deps.build_deployment_metrics_response(
                service_id=service_id,
                service_row=service_row,
                window_start=resolved_start,
                window_end=resolved_end,
            )
        except HTTPException as exc:
            if exc.status_code != status.HTTP_502_BAD_GATEWAY:
                raise
            message, provider_status = self.deps.extract_provider_failure(exc)
            metrics = self.deps.build_provider_error_metrics_response(
                message=message,
                provider_status=provider_status,
                window_start=resolved_start,
                window_end=resolved_end,
            )

        try:
            timeline = self.deps.build_deployment_timeline_response(
                service_id=service_id,
                service_row=service_row,
                window_start=resolved_start,
                window_end=resolved_end,
            )
        except HTTPException as exc:
            if exc.status_code != status.HTTP_502_BAD_GATEWAY:
                raise
            message, provider_status = self.deps.extract_provider_failure(exc)
            timeline = self.deps.build_provider_error_timeline_response(
                service_id=service_id,
                message=message,
                provider_status=provider_status,
                window_start=resolved_start,
                window_end=resolved_end,
            )

        try:
            logs = self.deps.build_deployment_logs_response(
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
            message, provider_status = self.deps.extract_provider_failure(exc)
            logs = self.deps.build_provider_error_logs_response(
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

    def get_monitoring_provider_diagnostics(self) -> MonitoringProvidersDiagnosticsResponse:
        generated_at = datetime.now(tz=timezone.utc).isoformat()
        providers = [
            self.deps.probe_monitoring_provider("prometheus", correlation_id=str(uuid4())),
            self.deps.probe_monitoring_provider("loki", correlation_id=str(uuid4())),
            self.deps.probe_monitoring_provider("alertmanager", correlation_id=str(uuid4())),
        ]
        overall_status = "healthy" if all(item["status"] == "healthy" for item in providers) else "degraded"
        return MonitoringProvidersDiagnosticsResponse(
            generatedAt=generated_at,
            overallStatus=overall_status,
            providers=[MonitoringProviderStatusResponse(**item) for item in providers],
        )

    def get_service_metrics_summary(
        self,
        *,
        service_id: str,
        selected_range: str,
    ) -> ServiceMetricsSummaryResponse:
        config = load_observability_config()
        safe_range = self.deps.validate_selected_range(
            selected_range=selected_range,
            allowed_ranges=config.metrics_allowed_ranges,
            field_name="range",
        )
        namespace, app_label, observability_mode = self.deps.resolve_service_monitoring_context(service_id)

        def _load_summary() -> ServiceMetricsSummaryResponse:
            now = datetime.now(tz=timezone.utc)
            correlation_id = str(uuid4())
            durations = {
                "1h": timedelta(hours=1),
                "24h": timedelta(hours=24),
                "7d": timedelta(days=7),
            }
            window_start = now - durations[safe_range]
            queries = self.deps.build_service_metrics_queries(
                service_id=service_id,
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
                    value = self.deps.query_prometheus_scalar(
                        query,
                        field_name,
                        correlation_id=correlation_id,
                    )
                    if value is not None:
                        break
                values[field_name] = value
                no_data[field_name] = value is None

            observability_diagnostics = self.deps.build_metrics_observability_diagnostics(
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

        return self.deps.metrics_summary_cache.get_or_set(
            key=("metrics-summary", service_id, safe_range),
            ttl_seconds=config.metrics_cache_ttl_seconds,
            loader=_load_summary,
        )

    def get_service_metrics_trends(
        self,
        *,
        service_id: str,
        selected_range: str,
    ) -> ServiceMetricsTrendsResponse:
        config = load_observability_config()
        safe_range = self.deps.validate_selected_range(
            selected_range=selected_range,
            allowed_ranges=config.metrics_allowed_ranges,
            field_name="range",
        )
        namespace, app_label, observability_mode = self.deps.resolve_service_monitoring_context(service_id)

        def _load_trends() -> ServiceMetricsTrendsResponse:
            now = datetime.now(tz=timezone.utc)
            durations = {
                "1h": timedelta(hours=1),
                "24h": timedelta(hours=24),
                "7d": timedelta(days=7),
            }
            window_start = now - durations[safe_range]
            step_seconds = self.deps.select_timeline_step_seconds(now - window_start, config)
            correlation_id = str(uuid4())
            queries = self.deps.build_service_metrics_queries(
                service_id=service_id,
                namespace=namespace,
                app_label=app_label,
                selected_range=safe_range,
                config=config,
            )

            p95_latency = self.deps.build_metric_trend_series(
                field_name="p95LatencyMs",
                query_candidates=queries["p95LatencyMs"],
                start=window_start,
                end=now,
                step_seconds=step_seconds,
                correlation_id=correlation_id,
            )
            error_rate = self.deps.build_metric_trend_series(
                field_name="errorRatePct",
                query_candidates=queries["errorRatePct"],
                start=window_start,
                end=now,
                step_seconds=step_seconds,
                correlation_id=correlation_id,
            )
            observability_diagnostics = self.deps.build_metrics_observability_diagnostics(
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

        return self.deps.metrics_summary_cache.get_or_set(
            key=("metrics-trends", service_id, safe_range),
            ttl_seconds=config.metrics_cache_ttl_seconds,
            loader=_load_trends,
        )

    def get_service_health_timeline(
        self,
        *,
        service_id: str,
        selected_range: str,
        step: str,
    ) -> list[ServiceHealthTimelineSegmentResponse]:
        config = load_observability_config()
        safe_range = self.deps.validate_selected_range(
            selected_range=selected_range,
            allowed_ranges=config.timeline_allowed_ranges,
            field_name="range",
        )
        step_seconds = self.deps.validate_step_for_range(range_value=safe_range, step_value=step)

        def _load_timeline() -> list[ServiceHealthTimelineSegmentResponse]:
            end = now_utc()
            window = parse_range(safe_range)
            start = end - window
            correlation_id = str(uuid4())

            namespace, app_label, observability_mode = self.deps.resolve_service_monitoring_context(service_id)
            queries = self.deps.build_health_timeline_queries(
                namespace=namespace,
                app_label=app_label,
                config=config,
            )

            availability_points = self.deps.query_prometheus_range(
                queries["availability"][0],
                "availability",
                start=start,
                end=end,
                step_seconds=step_seconds,
                correlation_id=correlation_id,
            )
            error_points: dict[int, float] = {}
            for index, query in enumerate(queries["errorRatePct"]):
                error_points = self.deps.query_prometheus_range(
                    query,
                    f"errorRatePct_{index}",
                    start=start,
                    end=end,
                    step_seconds=step_seconds,
                    correlation_id=correlation_id,
                )
                if error_points:
                    break
            readiness_points = self.deps.query_prometheus_range(
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

        return self.deps.timeline_cache.get_or_set(
            key=("health-timeline", service_id, safe_range, step_seconds),
            ttl_seconds=config.timeline_cache_ttl_seconds,
            loader=_load_timeline,
        )

    def get_active_alerts(
        self,
        *,
        env: str | None,
        service_id: str | None,
        limit: int,
    ) -> ActiveAlertsResponse:
        config = load_observability_config()
        safe_limit = self.deps.effective_limit(limit, config.alerts_max_rows)

        correlation_id = str(uuid4())
        now = datetime.now(tz=timezone.utc).isoformat()

        try:
            raw_alerts, provider_status = self.deps.query_alertmanager_active_alerts(
                correlation_id=correlation_id,
            )
            normalized = self.deps.normalize_active_alerts(raw_alerts)
        except HTTPException as exc:
            if exc.status_code == status.HTTP_502_BAD_GATEWAY and isinstance(exc.detail, dict):
                self.deps.logger.warning("alerts_active_degraded detail=%s", exc.detail)
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

    def get_monitoring_incidents_compat(
        self,
        *,
        env: str | None,
        service_id: str | None,
        limit: int,
    ) -> MonitoringIncidentsCompatEnvelope:
        active_alerts = self.get_active_alerts(
            env=env,
            service_id=service_id,
            limit=limit,
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

    def get_release_traceability(
        self,
        *,
        env: str | None,
        service_id: str | None,
        limit: int,
    ) -> list[ReleaseTraceabilityResponse]:
        rows = build_release_traceability_rows(
            project_rows=self.deps.load_project_rows(),
            ci_rows=self.deps.load_ci_metadata_rows(),
            argo_rows=self.deps.load_argo_metadata_rows(),
            env_filter=env,
            service_id_filter=service_id,
            limit=limit,
        )
        rows = self.deps.enrich_release_rows_with_live_runtime(rows, env=env)
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

    def get_release_dashboard_compat(
        self,
        *,
        env: str | None,
        service_id: str | None,
        limit: int,
    ) -> ReleaseDashboardCompatResponse:
        rows = self.get_release_traceability(
            env=env,
            service_id=service_id,
            limit=limit,
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

    def get_service_logs_quickview(
        self,
        *,
        service_id: str,
        preset: str,
        selected_range: str,
        limit: int,
        cursor: str | None,
        namespace: str | None,
        app_label: str | None,
        user: str,
    ) -> LogsQuickViewResponse:
        config = load_observability_config()
        safe_range = self.deps.validate_selected_range(
            selected_range=selected_range,
            allowed_ranges=config.logs_allowed_ranges,
            field_name="range",
        )
        safe_limit = self.deps.effective_limit(limit, config.logs_max_lines)
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

        resolved_namespace, resolved_app_label = self.deps.resolve_service_monitoring_metadata(service_id)
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
        lines = self.deps.logs_quickview_cache.get_or_set(
            key=cache_key,
            ttl_seconds=config.logs_cache_ttl_seconds,
            loader=lambda: self.deps.query_loki_range(
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
