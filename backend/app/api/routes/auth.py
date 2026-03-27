"""Authentication routes registered outside the legacy main module."""

from types import ModuleType

from fastapi import FastAPI

from app.api.endpoints import admin as admin_endpoints
from app.api.routes._utils import wrap_sync_endpoint


def register_routes(app: FastAPI, main_module: ModuleType) -> None:
    admin_endpoints.init(app)

    app.add_api_route(
        "/auth/login",
        endpoint=wrap_sync_endpoint(admin_endpoints.login),
        methods=["POST"],
        response_model=main_module.LoginResponse,
        tags=["auth"],
    )
