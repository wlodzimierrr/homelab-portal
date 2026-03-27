"""Catalog, project, and registry routes."""

from types import ModuleType

from fastapi import FastAPI, status

from app.api.endpoints import catalog as catalog_endpoints
from app.api.routes._utils import wrap_sync_endpoint


def register_routes(app: FastAPI, main_module: ModuleType) -> None:
    catalog_endpoints.init(app)

    app.add_api_route(
        "/projects",
        endpoint=wrap_sync_endpoint(catalog_endpoints.list_projects),
        methods=["GET"],
        response_model=main_module.ProjectsResponse,
        response_model_exclude_none=True,
        tags=["metadata"],
    )

    app.add_api_route(
        "/projects/diagnostics",
        endpoint=wrap_sync_endpoint(catalog_endpoints.get_project_catalog_diagnostics),
        methods=["GET"],
        response_model=main_module.ProjectCatalogDiagnosticsResponse,
        tags=["metadata"],
    )

    app.add_api_route(
        "/projects",
        endpoint=wrap_sync_endpoint(catalog_endpoints.create_project),
        methods=["POST"],
        response_model=main_module.Project,
        status_code=status.HTTP_201_CREATED,
        tags=["metadata"],
    )

    app.add_api_route(
        "/services",
        endpoint=wrap_sync_endpoint(catalog_endpoints.list_services),
        methods=["GET"],
        response_model=main_module.ServicesResponse,
        tags=["metadata"],
    )

    app.add_api_route(
        "/services/{service_id}",
        endpoint=wrap_sync_endpoint(catalog_endpoints.get_service),
        methods=["GET"],
        response_model=main_module.ServiceDetailResponse,
        tags=["metadata"],
    )

    app.add_api_route(
        "/catalog/reconciliation",
        endpoint=wrap_sync_endpoint(catalog_endpoints.get_catalog_reconciliation),
        methods=["GET"],
        response_model=main_module.CatalogJoinResponse,
        tags=["metadata"],
    )

    app.add_api_route(
        "/service-registry/sync",
        endpoint=wrap_sync_endpoint(catalog_endpoints.sync_service_registry),
        methods=["POST"],
        response_model=main_module.ServiceRegistrySyncResponse,
        tags=["metadata"],
    )

    app.add_api_route(
        "/service-registry/diagnostics",
        endpoint=wrap_sync_endpoint(catalog_endpoints.get_service_registry_diagnostics),
        methods=["GET"],
        response_model=main_module.ServiceRegistryDiagnosticsResponse,
        tags=["metadata"],
    )
