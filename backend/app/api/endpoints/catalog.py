"""Catalog, project, and service-registry endpoint handlers.

Extracted from main.py (Phase R4) to reduce file size without changing
behaviour. The handlers are thin wrappers that delegate to CatalogService,
except for create_project which preserves the existing conflict response.
"""

from fastapi import Depends, FastAPI, HTTPException, Query, status

from app.api.deps import get_current_user, require_admin
from app.api.schemas.catalog import (
    CatalogJoinResponse,
    CreateProjectRequest,
    Project,
    ProjectCatalogDiagnosticsResponse,
    ProjectsResponse,
    ServiceDetailResponse,
    ServiceRegistryDiagnosticsResponse,
    ServiceRegistrySyncResponse,
    ServicesResponse,
)
from app.services.catalog_service import CatalogService
from app.services.composition import get_backend_service_builders

_app: FastAPI | None = None


def init(app: FastAPI) -> None:
    """Store the FastAPI instance so handlers can resolve services lazily."""
    global _app  # noqa: PLW0603
    _app = app


def _get_catalog_service() -> CatalogService:
    assert _app is not None, "catalog endpoints not initialised — call init(app) first"
    return get_backend_service_builders(_app).build_catalog_service()


def list_projects(
    env: str | None = Query(default=None),
    _: tuple[str, set[str]] = Depends(get_current_user),
) -> ProjectsResponse:
    return _get_catalog_service().list_projects(env=env)


def get_project_catalog_diagnostics(
    env: str | None = Query(default=None),
    _: tuple[str, set[str]] = Depends(get_current_user),
) -> ProjectCatalogDiagnosticsResponse:
    return _get_catalog_service().get_project_catalog_diagnostics(env=env)


def create_project(
    payload: CreateProjectRequest,
    admin_user: str = Depends(require_admin),
) -> Project:
    del payload, admin_user
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            "Projects are sourced from GitOps app definitions; "
            "manual project creation is not allowed."
        ),
    )


def list_services(
    env: str | None = Query(default=None),
    namespace: str | None = Query(default=None),
    _: tuple[str, set[str]] = Depends(get_current_user),
) -> ServicesResponse:
    return _get_catalog_service().list_services(env=env, namespace=namespace)


def get_service(
    service_id: str,
    env: str | None = Query(default=None),
    _: tuple[str, set[str]] = Depends(get_current_user),
) -> ServiceDetailResponse:
    return _get_catalog_service().get_service(service_id=service_id, env=env)


def get_catalog_reconciliation(
    env: str | None = Query(default=None),
    project_id: str | None = Query(default=None, alias="projectId"),
    service_id: str | None = Query(default=None, alias="serviceId"),
    _: tuple[str, set[str]] = Depends(get_current_user),
) -> CatalogJoinResponse:
    return _get_catalog_service().get_catalog_reconciliation(
        env=env,
        project_id=project_id,
        service_id=service_id,
    )


def sync_service_registry(
    source: str = Query(default="cluster_services"),
    env: str | None = Query(default=None),
    _: str = Depends(require_admin),
) -> ServiceRegistrySyncResponse:
    return _get_catalog_service().sync_service_registry(source=source, env=env)


def get_service_registry_diagnostics(
    env: str | None = Query(default=None),
    _: tuple[str, set[str]] = Depends(get_current_user),
) -> ServiceRegistryDiagnosticsResponse:
    return _get_catalog_service().get_service_registry_diagnostics(env=env)
