"""Application bootstrap helpers that keep setup logic out of app.main."""

from __future__ import annotations

from dataclasses import dataclass
import os
import time

from fastapi import FastAPI
from prometheus_client import Counter, Histogram

from app.observability_cache import TTLCache


@dataclass(frozen=True)
class ObservabilityCaches:
    metrics_summary_cache: TTLCache
    timeline_cache: TTLCache
    logs_quickview_cache: TTLCache
    alerts_cache: TTLCache
    deployment_history_cache: TTLCache
    deployment_reconcile_cache: TTLCache


@dataclass(frozen=True)
class HttpMetricsState:
    metrics_namespace_label: str
    metrics_app_label: str
    http_requests_total: Counter
    http_request_duration_seconds: Histogram


def create_api_app() -> FastAPI:
    return FastAPI(title="Homelab Backend API", version="0.1.0")


def create_observability_caches() -> ObservabilityCaches:
    return ObservabilityCaches(
        metrics_summary_cache=TTLCache(),
        timeline_cache=TTLCache(),
        logs_quickview_cache=TTLCache(),
        alerts_cache=TTLCache(),
        deployment_history_cache=TTLCache(),
        deployment_reconcile_cache=TTLCache(),
    )


def clear_observability_caches(caches: ObservabilityCaches) -> None:
    caches.metrics_summary_cache.clear()
    caches.timeline_cache.clear()
    caches.logs_quickview_cache.clear()
    caches.alerts_cache.clear()
    caches.deployment_history_cache.clear()
    caches.deployment_reconcile_cache.clear()


def create_http_metrics_state() -> HttpMetricsState:
    return HttpMetricsState(
        metrics_namespace_label=os.getenv(
            "OBS_METRICS_NAMESPACE",
            os.getenv("POD_NAMESPACE", "default"),
        ),
        metrics_app_label=os.getenv("OBS_METRICS_APP_LABEL", "homelab-api"),
        http_requests_total=Counter(
            "http_requests_total",
            "Total HTTP requests handled by the homelab API.",
            labelnames=("namespace", "app", "method", "path", "status"),
        ),
        http_request_duration_seconds=Histogram(
            "http_request_duration_seconds",
            "HTTP request latency for the homelab API.",
            labelnames=("namespace", "app", "method", "path"),
        ),
    )


def install_http_metrics_middleware(app: FastAPI, state: HttpMetricsState) -> None:
    @app.middleware("http")
    async def observe_http_requests(request, call_next):
        if request.url.path == "/metrics":
            return await call_next(request)

        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            labels = {
                "namespace": state.metrics_namespace_label,
                "app": state.metrics_app_label,
                "method": request.method,
                "path": request.url.path,
            }
            state.http_request_duration_seconds.labels(**labels).observe(
                time.perf_counter() - started
            )
            state.http_requests_total.labels(
                **labels,
                status=str(status_code),
            ).inc()
