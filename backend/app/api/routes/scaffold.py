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
