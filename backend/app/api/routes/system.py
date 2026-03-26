"""System/health routes registered outside the legacy main module."""

from types import ModuleType

from fastapi import FastAPI

from app.api.routes._utils import wrap_sync_endpoint


def register_routes(app: FastAPI, main_module: ModuleType) -> None:
    app.add_api_route(
        "/health",
        endpoint=wrap_sync_endpoint(main_module.health),
        methods=["GET"],
        response_model=main_module.HealthResponse,
        response_model_exclude_none=True,
        tags=["system"],
    )

    app.add_api_route(
        "/metrics",
        endpoint=wrap_sync_endpoint(main_module.metrics),
        methods=["GET"],
        include_in_schema=False,
    )
