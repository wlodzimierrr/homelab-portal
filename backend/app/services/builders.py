"""Constructor helpers for composing application service instances."""

from __future__ import annotations

from app.services.catalog_service import CatalogService, CatalogServiceDeps
from app.services.deployment_service import DeploymentService, DeploymentServiceDeps
from app.services.observability_service import (
    ObservabilityService,
    ObservabilityServiceDeps,
)
from app.services.scaffold_admin_service import (
    ScaffoldAdminService,
    ScaffoldAdminServiceDeps,
)


def build_deployment_service(**deps) -> DeploymentService:
    return DeploymentService(DeploymentServiceDeps(**deps))


def build_observability_service(**deps) -> ObservabilityService:
    return ObservabilityService(ObservabilityServiceDeps(**deps))


def build_catalog_service(**deps) -> CatalogService:
    return CatalogService(CatalogServiceDeps(**deps))


def build_scaffold_admin_service(**deps) -> ScaffoldAdminService:
    return ScaffoldAdminService(ScaffoldAdminServiceDeps(**deps))
