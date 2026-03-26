"""Deployment lifecycle and rollout routes."""

from types import ModuleType

from fastapi import FastAPI, status

from app.api.endpoints import deployments as deployment_endpoints
from app.api.routes._utils import wrap_sync_endpoint


def register_routes(app: FastAPI, main_module: ModuleType) -> None:
    deployment_endpoints.init(app)

    app.add_api_route(
        "/deployments/reconcile",
        endpoint=wrap_sync_endpoint(deployment_endpoints.reconcile_deployments),
        methods=["POST"],
        response_model=main_module.DeploymentReconcileResponse,
        tags=["metadata"],
    )

    app.add_api_route(
        "/deployments/{deployment_id}",
        endpoint=wrap_sync_endpoint(deployment_endpoints.get_deployment),
        methods=["GET"],
        response_model=main_module.DeploymentRecordResponse,
        tags=["metadata"],
    )

    app.add_api_route(
        "/deployments",
        endpoint=wrap_sync_endpoint(deployment_endpoints.create_deployment_record),
        methods=["POST"],
        response_model=main_module.DeploymentRecordResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["metadata"],
    )

    app.add_api_route(
        "/deployments/{deployment_id}/cancel",
        endpoint=wrap_sync_endpoint(deployment_endpoints.cancel_deployment),
        methods=["POST"],
        response_model=main_module.DeploymentRecordResponse,
        tags=["metadata"],
    )

    app.add_api_route(
        "/services/{service_id}/deploy-to-dev",
        endpoint=wrap_sync_endpoint(deployment_endpoints.request_portal_deploy_to_dev),
        methods=["POST"],
        response_model=main_module.PortalDeployToDevResponse,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["metadata"],
    )

    app.add_api_route(
        "/services/{service_id}/promote-to-prod",
        endpoint=wrap_sync_endpoint(deployment_endpoints.request_portal_promote_to_prod),
        methods=["POST"],
        response_model=main_module.PortalPromoteToProdResponse,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["metadata"],
    )

    app.add_api_route(
        "/services/{service_id}/rollback-candidates",
        endpoint=wrap_sync_endpoint(deployment_endpoints.list_service_rollback_candidates),
        methods=["GET"],
        response_model=main_module.PortalServiceRollbackCandidatesResponse,
        tags=["metadata"],
    )

    app.add_api_route(
        "/services/{service_id}/rollback",
        endpoint=wrap_sync_endpoint(deployment_endpoints.request_service_rollback),
        methods=["POST"],
        response_model=main_module.PortalServiceRollbackResponse,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["metadata"],
    )

    app.add_api_route(
        "/rollbacks",
        endpoint=wrap_sync_endpoint(deployment_endpoints.request_portal_rollback),
        methods=["POST"],
        response_model=main_module.PortalRollbackResponse,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["metadata"],
    )

    app.add_api_route(
        "/services/{service_id}/deployments",
        endpoint=wrap_sync_endpoint(deployment_endpoints.get_service_deployments),
        methods=["GET"],
        response_model=main_module.ServiceDeploymentsResponse,
        tags=["metadata"],
    )

    app.add_api_route(
        "/services/{service_id}/deployment-info",
        endpoint=wrap_sync_endpoint(deployment_endpoints.get_service_deployment_info),
        methods=["GET"],
        response_model=main_module.ServiceDeploymentInfoResponse,
        tags=["metadata"],
    )
