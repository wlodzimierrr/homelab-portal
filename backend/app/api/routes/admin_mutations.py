"""Admin mutation routes that are not core deployment actions."""

from types import ModuleType

from fastapi import FastAPI, status

from app.api.endpoints import admin as admin_endpoints
from app.api.endpoints import scaffold as scaffold_endpoints
from app.api.routes._utils import wrap_sync_endpoint


def register_routes(app: FastAPI, main_module: ModuleType) -> None:
    admin_endpoints.init(app)

    app.add_api_route(
        "/services/{service_id}/config",
        endpoint=wrap_sync_endpoint(admin_endpoints.get_service_config),
        methods=["GET"],
        response_model=main_module.ServiceConfigResponse,
        tags=["metadata"],
    )

    app.add_api_route(
        "/services/{service_id}/config/set",
        endpoint=wrap_sync_endpoint(admin_endpoints.request_portal_set_config),
        methods=["POST"],
        response_model=main_module.PortalSetConfigResponse,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["metadata"],
    )

    app.add_api_route(
        "/services/{service_id}/config/set-secret",
        endpoint=wrap_sync_endpoint(admin_endpoints.request_portal_set_secret),
        methods=["POST"],
        response_model=main_module.PortalSetSecretResponse,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["metadata"],
    )

    app.add_api_route(
        "/services/{service_id}/public-hostname",
        endpoint=wrap_sync_endpoint(scaffold_endpoints.update_service_public_hostname),
        methods=["PUT"],
        response_model=main_module.UpdatePublicHostnameResponse,
        status_code=status.HTTP_202_ACCEPTED,
        responses={204: {"description": "No-op: hostname unchanged"}},
        tags=["scaffold"],
    )
