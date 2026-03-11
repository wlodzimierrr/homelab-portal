from __future__ import annotations

from typing import Literal, TypedDict


ObservabilityMode = Literal["app-native", "ingress-derived", "no-http"]
ObservabilityAuthority = Literal["app", "ingress", "none"]
ObservabilityStatus = Literal[
    "ok",
    "unsupported",
    "no_retained_data",
    "misconfigured",
    "unknown",
]

OBSERVABILITY_MODES: tuple[ObservabilityMode, ...] = (
    "app-native",
    "ingress-derived",
    "no-http",
)

OBSERVABILITY_MODE_SET = set(OBSERVABILITY_MODES)


def normalize_observability_mode(value: str | None) -> ObservabilityMode | None:
    trimmed = (value or "").strip().lower()
    if not trimmed:
        return None

    normalized = trimmed.replace("_", "-")
    if normalized in OBSERVABILITY_MODE_SET:
        return normalized  # type: ignore[return-value]
    return None


def is_valid_observability_mode(value: str | None) -> bool:
    return normalize_observability_mode(value) is not None


def observability_metrics_authority(
    value: str | None,
) -> ObservabilityAuthority | None:
    mode = normalize_observability_mode(value)
    if mode == "app-native":
        return "app"
    if mode == "ingress-derived":
        return "ingress"
    if mode == "no-http":
        return "none"
    return None


class ServiceMetricsObservabilityDiagnostics(TypedDict):
    mode: ObservabilityMode | None
    authority: ObservabilityAuthority | None
    status: ObservabilityStatus
    reason: str
    message: str
    missingMetrics: list[str]
    sourceAvailable: bool | None
    serviceSeriesAvailable: bool | None


def _label_metrics(fields: list[str]) -> str:
    labels = []
    for field in fields:
        if field == "p95LatencyMs":
            labels.append("P95 latency")
        elif field == "errorRatePct":
            labels.append("error rate")
        elif field == "uptimePct":
            labels.append("uptime")
        elif field == "restartCount":
            labels.append("restart count")
        else:
            labels.append(field)
    if not labels:
        return "metrics"
    if len(labels) == 1:
        return labels[0]
    return ", ".join(labels[:-1]) + f" and {labels[-1]}"


def build_service_metrics_observability_diagnostics(
    *,
    mode: str | None,
    missing_metrics: list[str],
    source_available: bool | None = None,
    service_series_available: bool | None = None,
) -> ServiceMetricsObservabilityDiagnostics:
    normalized_mode = normalize_observability_mode(mode)
    authority = observability_metrics_authority(normalized_mode)
    metric_label = _label_metrics(missing_metrics)

    if normalized_mode == "no-http":
        return {
            "mode": normalized_mode,
            "authority": authority,
            "status": "unsupported",
            "reason": "no_http_mode_declared",
            "message": (
                "This service declares no-http observability mode; latency and error-rate "
                "signals are intentionally not expected."
            ),
            "missingMetrics": missing_metrics,
            "sourceAvailable": None,
            "serviceSeriesAvailable": None,
        }

    if normalized_mode is None:
        return {
            "mode": None,
            "authority": None,
            "status": "unknown",
            "reason": "observability_mode_undeclared",
            "message": (
                "This service has no declared observability mode. The portal cannot tell whether "
                "missing HTTP metrics are expected or misconfigured."
            ),
            "missingMetrics": missing_metrics,
            "sourceAvailable": source_available,
            "serviceSeriesAvailable": service_series_available,
        }

    if not missing_metrics:
        source_name = "app metrics" if authority == "app" else "ingress-derived metrics"
        return {
            "mode": normalized_mode,
            "authority": authority,
            "status": "ok",
            "reason": "metrics_available",
            "message": f"{source_name.capitalize()} are available for this service.",
            "missingMetrics": [],
            "sourceAvailable": source_available,
            "serviceSeriesAvailable": service_series_available,
        }

    if source_available is False:
        if authority == "app":
            message = (
                f"{metric_label} are missing because this service declares app-native metrics, "
                "but no matching app-metric scrape source is available. Check ServiceMonitor and "
                "the exported /metrics series."
            )
            reason = "app_metrics_source_missing"
        else:
            message = (
                f"{metric_label} are missing because this service declares ingress-derived metrics, "
                "but the ingress metrics baseline is not available in Prometheus."
            )
            reason = "ingress_metrics_source_missing"
        return {
            "mode": normalized_mode,
            "authority": authority,
            "status": "misconfigured",
            "reason": reason,
            "message": message,
            "missingMetrics": missing_metrics,
            "sourceAvailable": source_available,
            "serviceSeriesAvailable": service_series_available,
        }

    if service_series_available is False:
        if authority == "app":
            message = (
                f"{metric_label} are missing because this service declares app-native metrics, "
                "but Prometheus found no matching per-service series. Check canonical labels and "
                "ServiceMonitor selectors."
            )
            reason = "app_metrics_series_missing"
        else:
            message = (
                f"{metric_label} are missing because ingress metrics exist, but no routed series "
                "matched this service identity. Check ingress routing and service-label mapping."
            )
            reason = "ingress_metrics_series_missing"
        return {
            "mode": normalized_mode,
            "authority": authority,
            "status": "misconfigured",
            "reason": reason,
            "message": message,
            "missingMetrics": missing_metrics,
            "sourceAvailable": source_available,
            "serviceSeriesAvailable": service_series_available,
        }

    return {
        "mode": normalized_mode,
        "authority": authority,
        "status": "no_retained_data",
        "reason": "no_retained_samples",
        "message": (
            f"{metric_label} are configured for this service, but Prometheus returned no retained "
            "samples for the current time window."
        ),
        "missingMetrics": missing_metrics,
        "sourceAvailable": source_available,
        "serviceSeriesAvailable": service_series_available,
    }
