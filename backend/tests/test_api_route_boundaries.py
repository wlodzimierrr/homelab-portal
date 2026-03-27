from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from fastapi import Response
from fastapi.routing import APIRoute

import app.main as app_main
from app.api.endpoints import admin as admin_endpoints
from app.api.endpoints import catalog as catalog_endpoints
from app.api.endpoints import deployments as deployment_endpoints
from app.api.endpoints import observability as observability_endpoints
from app.api.endpoints import scaffold as scaffold_endpoints
from app.api.endpoints import system as system_endpoints
from app.api.schemas.catalog import Project, ProjectsResponse
from app.api.schemas.deployments import (
    DeploymentRecordResponse,
    ServiceConfigEntry,
    ServiceConfigResponse,
    UpdatePublicHostnameRequest,
    UpdatePublicHostnameResponse,
)
from app.api.schemas.observability import (
    MonitoringProviderStatusResponse,
    MonitoringProvidersDiagnosticsResponse,
)
from app.api.schemas.scaffold import (
    ScaffoldPreviewFile,
    ScaffoldPreviewResponse,
    ScaffoldServiceRequest,
)
from app.services.composition import BackendServiceBuilders, get_backend_service_builders


def _find_route(path: str, method: str) -> APIRoute:
    return next(
        route
        for route in app_main.app.routes
        if isinstance(route, APIRoute) and route.path == path and method in route.methods
    )


@contextmanager
def _override_backend_service_builders(
    *,
    build_catalog_service=None,
    build_deployment_service=None,
    build_observability_service=None,
    build_scaffold_admin_service=None,
) -> Iterator[BackendServiceBuilders]:
    original = get_backend_service_builders(app_main.app)
    app_main.configure_backend_services(
        app_main.app,
        build_catalog_service=build_catalog_service or original.build_catalog_service,
        build_deployment_service=build_deployment_service or original.build_deployment_service,
        build_observability_service=(
            build_observability_service or original.build_observability_service
        ),
        build_scaffold_admin_service=(
            build_scaffold_admin_service or original.build_scaffold_admin_service
        ),
    )
    try:
        yield original
    finally:
        app_main.configure_backend_services(
            app_main.app,
            build_catalog_service=original.build_catalog_service,
            build_deployment_service=original.build_deployment_service,
            build_observability_service=original.build_observability_service,
            build_scaffold_admin_service=original.build_scaffold_admin_service,
        )


def test_split_route_modules_register_expected_main_handlers() -> None:
    expected_routes = [
        ("/health", "GET", system_endpoints.health, app_main.HealthResponse, ["system"]),
        ("/auth/login", "POST", admin_endpoints.login, app_main.LoginResponse, ["auth"]),
        (
            "/projects",
            "GET",
            catalog_endpoints.list_projects,
            app_main.ProjectsResponse,
            ["metadata"],
        ),
        (
            "/deployments/{deployment_id}",
            "GET",
            deployment_endpoints.get_deployment,
            app_main.DeploymentRecordResponse,
            ["metadata"],
        ),
        (
            "/services/{service_id}/metrics/summary",
            "GET",
            observability_endpoints.get_service_metrics_summary,
            app_main.ServiceMetricsSummaryResponse,
            ["monitoring"],
        ),
        (
            "/scaffold/preview",
            "POST",
            scaffold_endpoints.scaffold_preview,
            app_main.ScaffoldPreviewResponse,
            ["scaffold"],
        ),
        (
            "/services/{service_id}/public-hostname",
            "PUT",
            scaffold_endpoints.update_service_public_hostname,
            app_main.UpdatePublicHostnameResponse,
            ["scaffold"],
        ),
    ]

    for path, method, handler, response_model, tags in expected_routes:
        route = _find_route(path, method)

        assert getattr(route.endpoint, "__wrapped__", None) is handler
        assert route.response_model is response_model
        assert route.tags == tags


def test_catalog_handler_uses_current_service_builder() -> None:
    class _FakeCatalogService:
        def list_projects(self, *, env: str | None) -> ProjectsResponse:
            assert env == "prod"
            return ProjectsResponse(
                projects=[
                    Project(
                        id="proj-from-builder",
                        name="Project From Builder",
                        environment="prod",
                    )
                ]
            )

    with _override_backend_service_builders(build_catalog_service=lambda: _FakeCatalogService()):
        response = catalog_endpoints.list_projects(env="prod", _=("alice", {"admin"}))

    assert response.projects[0].id == "proj-from-builder"


def test_deployment_handler_uses_current_service_builder() -> None:
    class _FakeDeploymentService:
        def get_deployment(self, deployment_id: str) -> DeploymentRecordResponse:
            assert deployment_id == "dep-builder"
            return DeploymentRecordResponse(
                id=deployment_id,
                serviceId="svc-builder",
                env="dev",
                action="deploy",
                status="pending",
                requestedAt="2026-03-26T12:00:00+00:00",
            )

    with _override_backend_service_builders(
        build_deployment_service=lambda: _FakeDeploymentService()
    ):
        response = deployment_endpoints.get_deployment("dep-builder", _=("alice", {"admin"}))

    assert response.service_id == "svc-builder"


def test_observability_handler_uses_current_service_builder() -> None:
    class _FakeObservabilityService:
        def get_monitoring_provider_diagnostics(self) -> MonitoringProvidersDiagnosticsResponse:
            return MonitoringProvidersDiagnosticsResponse(
                generatedAt="2026-03-26T12:00:00+00:00",
                overallStatus="healthy",
                providers=[
                    MonitoringProviderStatusResponse(
                        provider="prometheus",
                        baseUrl="http://prometheus.test",
                        status="healthy",
                        reachable=True,
                        checkedAt="2026-03-26T12:00:00+00:00",
                    )
                ],
            )

    with _override_backend_service_builders(
        build_observability_service=lambda: _FakeObservabilityService()
    ):
        response = observability_endpoints.get_monitoring_provider_diagnostics(_=("alice", {"admin"}))

    assert response.overall_status == "healthy"
    assert response.providers[0].provider == "prometheus"


def test_scaffold_and_admin_handlers_use_current_service_builder() -> None:
    class _FakeScaffoldAdminService:
        def scaffold_preview(
            self,
            *,
            payload: ScaffoldServiceRequest,
        ) -> ScaffoldPreviewResponse:
            return ScaffoldPreviewResponse(
                files=[
                    ScaffoldPreviewFile(
                        path=f"apps/{payload.name}/service.yaml",
                        content="kind: Service\n",
                        changeType="create",
                    )
                ]
            )

        def update_service_public_hostname(
            self,
            *,
            service_id: str,
            payload: UpdatePublicHostnameRequest,
            response: Response,
        ) -> UpdatePublicHostnameResponse:
            del response
            return UpdatePublicHostnameResponse(
                prUrl=f"https://example.test/{service_id}",
                prNumber=42,
                branchName=f"host/{payload.public_host}",
            )

        def get_service_config(self, *, service_id: str, env: str) -> ServiceConfigResponse:
            return ServiceConfigResponse(
                serviceId=service_id,
                env=env,
                entries=[
                    ServiceConfigEntry(
                        key="LOG_LEVEL",
                        value="debug",
                        allowedValues=["debug", "info"],
                    )
                ],
            )

    with _override_backend_service_builders(
        build_scaffold_admin_service=lambda: _FakeScaffoldAdminService()
    ):
        preview = scaffold_endpoints.scaffold_preview(
            ScaffoldServiceRequest(
                name="demo",
                description="Demo service",
                imageRepo="ghcr.io/example/demo",
                repoUrl="https://github.com/example/demo",
                ownerEmail="owner@example.com",
            )
        )
        config = admin_endpoints.get_service_config(
            "demo",
            env="dev",
            current_user=("alice", {"admin"}),
        )
        hostname = scaffold_endpoints.update_service_public_hostname(
            "demo",
            UpdatePublicHostnameRequest(publicHost="demo.example.test"),
            Response(),
            _admin="alice",
        )

    assert preview.files[0].path == "apps/demo/service.yaml"
    assert config.entries[0].key == "LOG_LEVEL"
    assert hostname is not None
    assert hostname.branch_name == "host/demo.example.test"
