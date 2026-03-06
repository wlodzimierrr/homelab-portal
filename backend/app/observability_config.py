from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import os
import re


TEMPLATE_TOKEN_REGEX = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
PROMQL_REGEX_META = re.compile(r'([\\.^$|?*+()[\]{}])')


@dataclass(frozen=True)
class ObservabilityConfig:
    metrics_allowed_ranges: tuple[str, ...]
    timeline_allowed_ranges: tuple[str, ...]
    logs_allowed_ranges: tuple[str, ...]
    timeline_step_min: timedelta
    timeline_step_max: timedelta
    timeline_max_points: int
    logs_max_lines: int
    alerts_max_rows: int
    metrics_cache_ttl_seconds: int
    timeline_cache_ttl_seconds: int
    logs_cache_ttl_seconds: int
    alerts_cache_ttl_seconds: int
    metrics_query_uptime_template: str
    metrics_query_p95_latency_template: str
    metrics_query_p95_latency_fallback_template: str
    metrics_query_error_rate_template: str
    metrics_query_error_rate_fallback_template: str
    metrics_query_restart_count_template: str
    timeline_query_availability_template: str
    timeline_query_error_rate_template: str
    timeline_query_readiness_template: str


def _read_int(name: str, fallback: int, minimum: int = 0, maximum: int | None = None) -> int:
    raw = os.getenv(name)
    if raw is None:
        return fallback
    try:
        value = int(raw)
    except ValueError:
        return fallback
    if value < minimum:
        return fallback
    if maximum is not None and value > maximum:
        return maximum
    return value


def _read_csv(name: str, fallback: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name)
    if raw is None:
        return fallback
    values = tuple(item.strip() for item in raw.split(",") if item.strip())
    return values or fallback


def parse_duration_token(value: str) -> timedelta:
    if not value:
        raise ValueError("Duration token is required")
    if value.endswith("m"):
        return timedelta(minutes=int(value[:-1]))
    if value.endswith("h"):
        return timedelta(hours=int(value[:-1]))
    if value.endswith("d"):
        return timedelta(days=int(value[:-1]))
    raise ValueError(f"Unsupported duration token: {value}")


def render_query_template(template: str, values: dict[str, str], context: str) -> str:
    missing: set[str] = set()

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        value = values.get(key)
        if value is None or value == "":
            missing.add(key)
            return ""
        return value

    rendered = TEMPLATE_TOKEN_REGEX.sub(_replace, template)
    if missing:
        missing_sorted = ",".join(sorted(missing))
        raise ValueError(f"Missing query template variables ({context}): {missing_sorted}")
    return rendered


def escape_promql_regex_literal(value: str) -> str:
    return PROMQL_REGEX_META.sub(r"\\\1", value)


def load_observability_config() -> ObservabilityConfig:
    return ObservabilityConfig(
        metrics_allowed_ranges=_read_csv("OBS_METRICS_ALLOWED_RANGES", ("1h", "24h", "7d")),
        timeline_allowed_ranges=_read_csv("OBS_TIMELINE_ALLOWED_RANGES", ("24h", "7d")),
        logs_allowed_ranges=_read_csv("OBS_LOGS_ALLOWED_RANGES", ("15m", "1h", "6h", "24h")),
        timeline_step_min=parse_duration_token(os.getenv("OBS_TIMELINE_STEP_MIN", "5m")),
        timeline_step_max=parse_duration_token(os.getenv("OBS_TIMELINE_STEP_MAX", "1h")),
        timeline_max_points=_read_int("OBS_TIMELINE_MAX_POINTS", 1000, minimum=10, maximum=5000),
        logs_max_lines=_read_int("OBS_LOGS_MAX_LINES", 200, minimum=1, maximum=500),
        alerts_max_rows=_read_int("OBS_ALERTS_MAX_ROWS", 200, minimum=1, maximum=500),
        metrics_cache_ttl_seconds=_read_int("OBS_METRICS_CACHE_TTL_SECONDS", 20, minimum=0, maximum=300),
        timeline_cache_ttl_seconds=_read_int("OBS_TIMELINE_CACHE_TTL_SECONDS", 30, minimum=0, maximum=300),
        logs_cache_ttl_seconds=_read_int("OBS_LOGS_CACHE_TTL_SECONDS", 15, minimum=0, maximum=120),
        alerts_cache_ttl_seconds=_read_int("OBS_ALERTS_CACHE_TTL_SECONDS", 15, minimum=0, maximum=120),
        metrics_query_uptime_template=os.getenv(
            "OBS_QUERY_METRICS_UPTIME",
            '100 * (avg_over_time(kube_deployment_status_replicas_available{namespace="{namespace}", deployment="{deployment_name}"}[{selected_range}]) / clamp_min(avg_over_time(kube_deployment_spec_replicas{namespace="{namespace}", deployment="{deployment_name}"}[{selected_range}]), 1))',
        ),
        metrics_query_p95_latency_template=os.getenv(
            "OBS_QUERY_METRICS_P95_LATENCY",
            '1000 * histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket{namespace="{namespace}", app="{app_label}"}[5m])))',
        ),
        metrics_query_p95_latency_fallback_template=os.getenv(
            "OBS_QUERY_METRICS_P95_LATENCY_FALLBACK",
            '1000 * histogram_quantile(0.95, sum by (le) (rate(traefik_service_request_duration_seconds_bucket{service=~"{ingress_service_pattern}"}[5m])))',
        ),
        metrics_query_error_rate_template=os.getenv(
            "OBS_QUERY_METRICS_ERROR_RATE",
            '100 * ((sum(rate(http_requests_total{namespace="{namespace}", app="{app_label}", status=~"5.."}[5m])) or vector(0)) / clamp_min(sum(rate(http_requests_total{namespace="{namespace}", app="{app_label}"}[5m])), 0.000001))',
        ),
        metrics_query_error_rate_fallback_template=os.getenv(
            "OBS_QUERY_METRICS_ERROR_RATE_FALLBACK",
            '100 * ((sum(rate(traefik_service_requests_total{service=~"{ingress_service_pattern}", code=~"5.."}[5m])) or vector(0)) / clamp_min(sum(rate(traefik_service_requests_total{service=~"{ingress_service_pattern}"}[5m])), 0.000001))',
        ),
        metrics_query_restart_count_template=os.getenv(
            "OBS_QUERY_METRICS_RESTART_COUNT",
            'sum(increase(kube_pod_container_status_restarts_total{namespace="{namespace}", pod=~"{pod_pattern}.*"}[{selected_range}]))',
        ),
        timeline_query_availability_template=os.getenv(
            "OBS_QUERY_TIMELINE_AVAILABILITY",
            'avg_over_time(kube_deployment_status_replicas_available{namespace="{namespace}", deployment="{deployment_name}"}[5m]) / clamp_min(avg_over_time(kube_deployment_spec_replicas{namespace="{namespace}", deployment="{deployment_name}"}[5m]), 1)',
        ),
        timeline_query_error_rate_template=os.getenv(
            "OBS_QUERY_TIMELINE_ERROR_RATE",
            '100 * ((sum(rate(http_requests_total{namespace="{namespace}", app="{app_label}", status=~"5.."}[5m])) or vector(0)) / clamp_min(sum(rate(http_requests_total{namespace="{namespace}", app="{app_label}"}[5m])), 0.000001))',
        ),
        timeline_query_readiness_template=os.getenv(
            "OBS_QUERY_TIMELINE_READINESS",
            'avg_over_time(kube_deployment_status_replicas_available{namespace="{namespace}", deployment="{deployment_name}"}[5m]) / clamp_min(avg_over_time(kube_deployment_spec_replicas{namespace="{namespace}", deployment="{deployment_name}"}[5m]), 1)',
        ),
    )
