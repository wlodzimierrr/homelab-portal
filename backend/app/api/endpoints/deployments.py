"""Deployment lifecycle endpoint handlers.

Extracted from main.py (Phase R2) to reduce file size without changing
behaviour.  The handlers are thin wrappers that delegate to
DeploymentService; the reconcile_deployments handler also clears the
deployment reconcile cache.
"""

from typing import Literal

from fastapi import Depends, FastAPI, Query, Response

from app.api.deps import get_current_user, require_admin
from app.api.schemas.deployments import (
    CreateDeploymentRecordRequest,
    DeploymentReconcileResponse,
    DeploymentRecordResponse,
    PortalDeployToDevRequest,
    PortalDeployToDevResponse,
    PortalPromoteToProdRequest,
    PortalPromoteToProdResponse,
    PortalRollbackRequest,
    PortalRollbackResponse,
    PortalServiceRollbackCandidatesResponse,
    PortalServiceRollbackRequest,
    PortalServiceRollbackResponse,
    ServiceDeploymentInfoResponse,
    ServiceDeploymentsResponse,
)
from app.services.composition import get_backend_service_builders
from app.services.deployment_service import DeploymentService

# ---------------------------------------------------------------------------
# Module-level app reference (set once by init())
# ---------------------------------------------------------------------------

_app: FastAPI | None = None


def init(app: FastAPI) -> None:
    """Store the FastAPI instance so handlers can resolve services lazily."""
    global _app  # noqa: PLW0603
    _app = app


def _get_deployment_service() -> DeploymentService:
    assert _app is not None, "deployment endpoints not initialised — call init(app) first"
    return get_backend_service_builders(_app).build_deployment_service()


# ---------------------------------------------------------------------------
# Endpoint handlers
# ---------------------------------------------------------------------------


def reconcile_deployments(
    service_id: str | None = Query(default=None, alias="serviceId"),
    env: str | None = Query(default=None),
    _: str = Depends(require_admin),
) -> DeploymentReconcileResponse:
    # Lazy imports to avoid circular dependency on main.py module-level state
    from app.main import _reconcile_recent_deployment_activity, deployment_reconcile_cache

    deployment_reconcile_cache.clear()
    return _reconcile_recent_deployment_activity(service_id=service_id, env=env)


def get_deployment(
    deployment_id: str,
    _: tuple[str, set[str]] = Depends(get_current_user),
) -> DeploymentRecordResponse:
    return _get_deployment_service().get_deployment(deployment_id)


def create_deployment_record(
    payload: CreateDeploymentRecordRequest,
    admin_user: str = Depends(require_admin),
) -> DeploymentRecordResponse:
    return _get_deployment_service().create_deployment_record(payload, admin_user=admin_user)


def cancel_deployment(
    deployment_id: str,
    admin_user: str = Depends(require_admin),
) -> DeploymentRecordResponse:
    """Soft-cancel a deployment that has not yet reached a terminal state.

    Sets status='failed', result='cancelled', result_reason='Cancelled by operator'
    and releases the deployment lock.  Returns 404 if the deployment is not found,
    409 if it is already in a terminal state.
    """
    return _get_deployment_service().cancel_deployment(deployment_id, admin_user=admin_user)


def request_portal_deploy_to_dev(
    service_id: str,
    payload: PortalDeployToDevRequest,
    response: Response,
    identity: tuple[str, set[str]] = Depends(get_current_user),
) -> PortalDeployToDevResponse:
    requested_by, _groups = identity
    return _get_deployment_service().request_portal_deploy_to_dev(
        service_id,
        payload,
        response=response,
        requested_by=requested_by,
    )


def request_portal_promote_to_prod(
    service_id: str,
    payload: PortalPromoteToProdRequest,
    response: Response,
    identity: tuple[str, set[str]] = Depends(get_current_user),
) -> PortalPromoteToProdResponse:
    requested_by, _groups = identity
    return _get_deployment_service().request_portal_promote_to_prod(
        service_id,
        payload,
        response=response,
        requested_by=requested_by,
    )


def list_service_rollback_candidates(
    service_id: str,
    target_environment: Literal["dev", "prod"] = Query(default="dev", alias="targetEnvironment"),
    identity: tuple[str, set[str]] = Depends(get_current_user),
) -> PortalServiceRollbackCandidatesResponse:
    del identity
    return _get_deployment_service().list_service_rollback_candidates(
        service_id,
        target_environment=target_environment,
    )


def request_service_rollback(
    service_id: str,
    payload: PortalServiceRollbackRequest,
    response: Response,
    identity: tuple[str, set[str]] = Depends(get_current_user),
) -> PortalServiceRollbackResponse:
    requested_by, _groups = identity
    return _get_deployment_service().request_service_rollback(
        service_id,
        payload,
        response=response,
        requested_by=requested_by,
    )


def request_portal_rollback(
    payload: PortalRollbackRequest,
    admin_user: str = Depends(require_admin),
) -> PortalRollbackResponse:
    return _get_deployment_service().request_portal_rollback(payload, admin_user=admin_user)


def get_service_deployments(
    service_id: str,
    env: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    _: tuple[str, set[str]] = Depends(get_current_user),
) -> ServiceDeploymentsResponse:
    return _get_deployment_service().get_service_deployments(service_id, env=env, limit=limit)


def get_service_deployment_info(
    service_id: str,
    env: str | None = Query(default=None),
    _: tuple[str, set[str]] = Depends(get_current_user),
) -> ServiceDeploymentInfoResponse:
    return _get_deployment_service().get_service_deployment_info(service_id, env=env)
