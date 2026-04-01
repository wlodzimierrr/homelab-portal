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


def test_login_rejected_in_forwarded_identity_mode(client, monkeypatch) -> None:
    monkeypatch.setenv("PORTAL_AUTH_MODE", "forwarded_identity")

    response = client.post(
        "/auth/login",
        json={"username": "admin", "password": "changeme"},
    )

    assert response.status_code == 409
    assert "PORTAL_AUTH_MODE=forwarded_identity" in response.json()["detail"]


def test_projects_unauthorized_without_token(client) -> None:
    response = client.get("/projects")

    assert response.status_code == 401


def test_projects_authorized_with_forwarded_user(client, monkeypatch) -> None:
    monkeypatch.setenv("PORTAL_AUTH_MODE", "forwarded_identity")

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


def test_projects_reject_bearer_token_in_forwarded_identity_mode(client, monkeypatch) -> None:
    monkeypatch.setenv("PORTAL_AUTH_MODE", "forwarded_identity")

    response = client.get(
        "/projects",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing forwarded identity headers"


def test_projects_reject_forwarded_user_in_bearer_token_mode(client) -> None:
    response = client.get(
        "/projects",
        headers={"X-Auth-Request-User": "alice"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing bearer token"


def test_projects_fail_clearly_for_invalid_auth_mode(client, monkeypatch) -> None:
    monkeypatch.setenv("PORTAL_AUTH_MODE", "mixed")

    response = client.get(
        "/projects",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 500
    assert "Invalid PORTAL_AUTH_MODE" in response.json()["detail"]
