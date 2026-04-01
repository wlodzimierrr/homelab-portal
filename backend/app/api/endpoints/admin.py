"""Authentication and admin-mutation endpoint handlers.

Extracted from main.py (Phase R5) to reduce file size without changing
behaviour. The handlers are thin wrappers that delegate to
ScaffoldAdminService or preserve the existing development login contract.
"""

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, status

from app.api.deps import (
    get_auth_mode,
    get_current_user,
    require_admin,
)
from app.api.deps.auth import AUTH_MODE_FORWARDED_IDENTITY
from app.api.schemas.auth import LoginRequest, LoginResponse
from app.api.schemas.deployments import (
    PortalSetConfigRequest,
    PortalSetConfigResponse,
    PortalSetSecretRequest,
    PortalSetSecretResponse,
    ServiceConfigResponse,
)
from app.services.composition import get_backend_service_builders
from app.services.scaffold_admin_service import ScaffoldAdminService

_app: FastAPI | None = None


def init(app: FastAPI) -> None:
    """Store the FastAPI instance so handlers can resolve services lazily."""
    global _app  # noqa: PLW0603
    _app = app


def _get_scaffold_admin_service() -> ScaffoldAdminService:
    assert _app is not None, "admin endpoints not initialised — call init(app) first"
    return get_backend_service_builders(_app).build_scaffold_admin_service()


def login(payload: LoginRequest) -> LoginResponse:
    # This is a development-only login contract that matches the frontend auth
    # flow. Production auth is expected to be enforced by the ingress/auth proxy.
    if get_auth_mode() == AUTH_MODE_FORWARDED_IDENTITY:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Manual /auth/login is not available when "
                "PORTAL_AUTH_MODE=forwarded_identity. "
                "Use the configured SSO/auth gateway instead."
            ),
        )

    if payload.username != "admin" or payload.password != "changeme":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    expires_at = datetime.now(tz=timezone.utc) + timedelta(hours=1)
    return LoginResponse(
        access_token="dev-static-token",
        expires_at=expires_at.isoformat(),
    )


def get_service_config(
    service_id: str,
    env: Literal["dev", "prod"],
    current_user: tuple[str, set[str]] = Depends(get_current_user),
) -> ServiceConfigResponse:
    del current_user
    return _get_scaffold_admin_service().get_service_config(service_id=service_id, env=env)


def request_portal_set_config(
    service_id: str,
    payload: PortalSetConfigRequest,
    admin_user: str = Depends(require_admin),
) -> PortalSetConfigResponse:
    return _get_scaffold_admin_service().request_portal_set_config(
        service_id=service_id,
        payload=payload,
        admin_user=admin_user,
    )


def request_portal_set_secret(
    service_id: str,
    payload: PortalSetSecretRequest,
    admin_user: str = Depends(require_admin),
) -> PortalSetSecretResponse:
    return _get_scaffold_admin_service().request_portal_set_secret(
        service_id=service_id,
        payload=payload,
        admin_user=admin_user,
    )
