"""Scaffold workflow routes."""

from types import ModuleType

from fastapi import FastAPI, status

from app.api.routes._utils import wrap_sync_endpoint


def register_routes(app: FastAPI, main_module: ModuleType) -> None:
    app.add_api_route(
        "/scaffold/preview",
        endpoint=wrap_sync_endpoint(main_module.scaffold_preview),
        methods=["POST"],
        response_model=main_module.ScaffoldPreviewResponse,
        tags=["scaffold"],
    )

    app.add_api_route(
        "/scaffold/submit",
        endpoint=wrap_sync_endpoint(main_module.scaffold_submit),
        methods=["POST"],
        response_model=main_module.ScaffoldSubmitResponse,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["scaffold"],
    )

    app.add_api_route(
        "/scaffold/projects",
        endpoint=wrap_sync_endpoint(main_module.scaffold_list_projects),
        methods=["GET"],
        response_model=list[main_module.ScaffoldProjectInfo],
        tags=["scaffold"],
    )

    # T5.3.4 — Service adoption and migration
    app.add_api_route(
        "/services/{service_id}/adopt",
        endpoint=wrap_sync_endpoint(main_module.adopt_service),
        methods=["POST"],
        response_model=main_module.AdoptServiceResponse,
        tags=["migration"],
    )

    app.add_api_route(
        "/migration/validate",
        endpoint=wrap_sync_endpoint(main_module.validate_migration),
        methods=["POST"],
        response_model=main_module.MigrationValidateResponse,
        tags=["migration"],
    )

    app.add_api_route(
        "/migration/consolidate",
        endpoint=wrap_sync_endpoint(main_module.consolidate_migration),
        methods=["POST"],
        response_model=main_module.MigrationConsolidateResponse,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["migration"],
    )
