"""System endpoint handlers.

Extracted from main.py (Phase R6) to keep the entrypoint focused on app
bootstrap and service composition.
"""

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import Query, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.api.schemas.observability import HealthResponse, MonitoringProviderStatusResponse


def health(
    include_providers: bool = Query(default=False, alias="includeProviders"),
) -> HealthResponse:
    from app.main import probe_monitoring_provider

    # Keep the default health check lightweight for liveness/readiness probes, and
    # only fan out to Prometheus/Loki/Alertmanager when diagnostics are requested.
    if not include_providers:
        return HealthResponse(status="ok")

    providers = [
        probe_monitoring_provider("prometheus", correlation_id=str(uuid4())),
        probe_monitoring_provider("loki", correlation_id=str(uuid4())),
        probe_monitoring_provider("alertmanager", correlation_id=str(uuid4())),
    ]
    overall = "ok" if all(item["status"] == "healthy" for item in providers) else "degraded"
    return HealthResponse(
        status=overall,
        providers=[MonitoringProviderStatusResponse(**item) for item in providers],
    )


def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
