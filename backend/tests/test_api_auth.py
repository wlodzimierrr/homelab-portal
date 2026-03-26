import json
from io import BytesIO
from urllib import parse as urlparse
from urllib.error import HTTPError

import app.main as app_main
from app.github_workflows import (
    GitHubWorkflowDispatchError,
    GitHubWorkflowDispatchResult,
)
from app.logs_quickview import clear_rate_limit_state_for_tests



def test_login_success(client) -> None:
    response = client.post(
        "/auth/login",
        json={"username": "admin", "password": "changeme"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"] == "dev-static-token"
    assert body["token_type"] == "bearer"
    assert body["expires_at"]


def test_login_invalid_credentials(client) -> None:
    response = client.post(
        "/auth/login",
        json={"username": "admin", "password": "wrong"},
    )

    assert response.status_code == 401


def test_projects_unauthorized_without_token(client) -> None:
    response = client.get("/projects")

    assert response.status_code == 401


def test_projects_authorized_with_forwarded_user(client, monkeypatch) -> None:
    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, *_args, **_kwargs):
            return None

        def fetchall(self):
            return []

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def cursor(self):
            return _Cursor()

    monkeypatch.setattr("app.main._with_connection", lambda: _Conn())

    response = client.get(
        "/projects",
        headers={"X-Auth-Request-User": "alice"},
    )

    assert response.status_code == 200
