"""Scaffold workflow routes."""

from types import ModuleType

from fastapi import FastAPI, status

from app.api.endpoints import scaffold as scaffold_endpoints
from app.api.routes._utils import wrap_sync_endpoint


def register_routes(app: FastAPI, main_module: ModuleType) -> None:
    # Initialise the endpoints module with the app reference so handlers
    # can resolve the ScaffoldAdminService lazily via the composition system.
    scaffold_endpoints.init(app)

    app.add_api_route(
        "/scaffold/preview",
        endpoint=wrap_sync_endpoint(scaffold_endpoints.scaffold_preview),
        response_model=main_module.ScaffoldPreviewResponse,
        methods=["POST"],
        tags=["scaffold"],
    )

    app.add_api_route(
        "/scaffold/submit",
        endpoint=wrap_sync_endpoint(scaffold_endpoints.scaffold_submit),
        response_model=main_module.ScaffoldSubmitResponse,
        methods=["POST"],
        status_code=status.HTTP_202_ACCEPTED,
        tags=["scaffold"],
    )

    app.add_api_route(
        "/scaffold/projects",
        endpoint=wrap_sync_endpoint(scaffold_endpoints.scaffold_list_projects),
        response_model=list[main_module.ScaffoldProjectInfo],
        methods=["GET"],
        tags=["scaffold"],
    )

    app.add_api_route(
        "/services/{service_id}/decommission",
        endpoint=wrap_sync_endpoint(scaffold_endpoints.decommission_service),
        response_model=main_module.ServiceDecommissionResponse,
        methods=["POST"],
        status_code=status.HTTP_202_ACCEPTED,
        tags=["migration"],
    )

    # T5.3.4 — Service adoption and migration
    app.add_api_route(
        "/services/{service_id}/adopt",
        endpoint=wrap_sync_endpoint(scaffold_endpoints.adopt_service),
        response_model=main_module.AdoptServiceResponse,
        methods=["POST"],
        tags=["migration"],
    )

    app.add_api_route(
        "/migration/validate",
        endpoint=wrap_sync_endpoint(scaffold_endpoints.validate_migration),
        response_model=main_module.MigrationValidateResponse,
        methods=["POST"],
        tags=["migration"],
    )

    app.add_api_route(
        "/migration/consolidate",
        endpoint=wrap_sync_endpoint(scaffold_endpoints.consolidate_migration),
        response_model=main_module.MigrationConsolidateResponse,
        methods=["POST"],
        status_code=status.HTTP_202_ACCEPTED,
        tags=["migration"],
    )
