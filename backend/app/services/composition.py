"""Override-friendly composition helpers for backend application services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from fastapi import FastAPI

from app.services.catalog_service import CatalogService
from app.services.deployment_service import DeploymentService
from app.services.observability_service import ObservabilityService
from app.services.scaffold_admin_service import ScaffoldAdminService


@dataclass(frozen=True)
class BackendServiceBuilders:
    """Factories for the app-level services mounted onto a FastAPI instance."""

    build_catalog_service: Callable[[], CatalogService]
    build_deployment_service: Callable[[], DeploymentService]
    build_observability_service: Callable[[], ObservabilityService]
    build_scaffold_admin_service: Callable[[], ScaffoldAdminService]


def configure_backend_service_builders(
    target_app: FastAPI,
    builders: BackendServiceBuilders,
) -> BackendServiceBuilders:
    """Attach the current service builder set to the FastAPI app state."""

    target_app.state.backend_service_builders = builders
    return builders


def get_backend_service_builders(target_app: FastAPI) -> BackendServiceBuilders:
    """Load the configured builders so routes can resolve services lazily."""

    builders = getattr(target_app.state, "backend_service_builders", None)
    if builders is None:
        raise RuntimeError("Backend service builders have not been configured")
    return builders
