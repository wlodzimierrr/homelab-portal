from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.api.deps.auth import get_auth_mode, get_current_user, require_admin


def _bearer(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def test_get_current_user_uses_bearer_token_mode_by_default() -> None:
    user, groups = get_current_user(
        credentials=_bearer("dev-static-token"),
        x_auth_user="alice",
        x_auth_groups="team-admins",
    )

    assert user == "dev-static-token"
    assert groups == set()


def test_get_current_user_uses_forwarded_identity_mode(monkeypatch) -> None:
    monkeypatch.setenv("PORTAL_AUTH_MODE", "forwarded_identity")

    user, groups = get_current_user(
        credentials=_bearer("dev-static-token"),
        x_auth_user="alice",
        x_auth_groups="team-developers,team-admins",
    )

    assert user == "alice"
    assert groups == {"team-developers", "team-admins"}


def test_get_current_user_rejects_bearer_only_in_forwarded_identity_mode(monkeypatch) -> None:
    monkeypatch.setenv("PORTAL_AUTH_MODE", "forwarded_identity")

    try:
        get_current_user(
            credentials=_bearer("dev-static-token"),
            x_auth_user=None,
            x_auth_groups=None,
        )
    except HTTPException as exc:
        assert exc.status_code == 401
        assert exc.detail == "Missing forwarded identity headers"
    else:
        raise AssertionError("Expected HTTPException for missing forwarded identity headers")


def test_get_current_user_rejects_forwarded_only_in_bearer_mode() -> None:
    try:
        get_current_user(
            credentials=None,
            x_auth_user="alice",
            x_auth_groups="team-admins",
        )
    except HTTPException as exc:
        assert exc.status_code == 401
        assert exc.detail == "Missing bearer token"
    else:
        raise AssertionError("Expected HTTPException for missing bearer token")


def test_get_current_user_rejects_invalid_auth_mode(monkeypatch) -> None:
    monkeypatch.setenv("PORTAL_AUTH_MODE", "mixed")

    try:
        get_current_user(
            credentials=_bearer("dev-static-token"),
            x_auth_user=None,
            x_auth_groups=None,
        )
    except HTTPException as exc:
        assert exc.status_code == 500
        assert "Invalid PORTAL_AUTH_MODE" in str(exc.detail)
    else:
        raise AssertionError("Expected HTTPException for invalid auth mode")


def test_get_auth_mode_defaults_to_bearer_token() -> None:
    assert get_auth_mode() == "bearer_token"


def test_require_admin_allows_forwarded_user_via_group(monkeypatch) -> None:
    monkeypatch.setenv("PORTAL_ADMIN_GROUPS", "team-admins")

    assert require_admin(("alice", {"team-admins"})) == "alice"
