def test_create_project_rejected_for_gitops_owned_catalog(client) -> None:
    headers = {"Authorization": "Bearer dev-static-token"}

    response = client.post(
        "/projects",
        headers=headers,
        json={"id": "proj-e2e", "name": "E2E Project", "environment": "dev"},
    )
    assert response.status_code == 409
    assert "GitOps app definitions" in response.json()["detail"]


def test_create_project_forbidden_for_non_admin_forwarded_user(client, monkeypatch) -> None:
    monkeypatch.setenv("PORTAL_AUTH_MODE", "forwarded_identity")
    response = client.post(
        "/projects",
        headers={"X-Auth-Request-User": "alice"},
        json={"id": "proj-forbidden", "name": "Nope", "environment": "dev"},
    )
    assert response.status_code == 403


def test_create_project_rejected_for_admin_group_when_catalog_is_gitops_owned(client, monkeypatch) -> None:
    monkeypatch.setenv("PORTAL_AUTH_MODE", "forwarded_identity")
    response = client.post(
        "/projects",
        headers={
            "X-Auth-Request-User": "alice",
            "X-Auth-Request-Groups": "team-developers,team-admins",
        },
        json={"id": "proj-admin", "name": "Allowed", "environment": "dev"},
    )
    assert response.status_code == 409
    assert "GitOps app definitions" in response.json()["detail"]


def test_create_project_rejects_forwarded_identity_when_bearer_mode_is_active(client) -> None:
    response = client.post(
        "/projects",
        headers={"X-Auth-Request-User": "alice", "X-Auth-Request-Groups": "team-admins"},
        json={"id": "proj-forwarded-ignored", "name": "Nope", "environment": "dev"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing bearer token"


def test_service_registry_sync_requires_auth(client) -> None:
    response = client.post("/service-registry/sync")
    assert response.status_code == 401


def test_service_registry_sync_returns_summary_for_admin(client, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.catalog_service.sync_service_registry_from_cluster",
        lambda conn, env_name=None: {
            "correlationId": "cid-1",
            "source": "cluster_services",
            "env": env_name or "dev",
            "namespaces": ["homelab-api"],
            "discovered": 2,
            "upserted": 2,
            "inserted": 1,
            "updated": 1,
            "deleted": 0,
            "sourceFailures": [],
            "generatedAt": "2026-03-05T00:00:00+00:00",
            "durationMs": 12,
        },
    )

    response = client.post(
        "/service-registry/sync",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["correlationId"] == "cid-1"
    assert body["source"] == "cluster_services"
    assert body["inserted"] == 1
    assert body["updated"] == 1
    assert body["deleted"] == 0


def test_service_registry_sync_dispatches_gitops_source(client, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.catalog_service.sync_project_registry_from_gitops",
        lambda conn, env_name=None: {
            "correlationId": "cid-gitops",
            "source": "gitops_apps",
            "env": env_name or "all",
            "namespaces": ["homelab-api", "homelab-web"],
            "discovered": 2,
            "upserted": 2,
            "inserted": 2,
            "updated": 0,
            "deleted": 1,
            "sourceFailures": [],
            "generatedAt": "2026-03-05T00:00:00+00:00",
            "durationMs": 9,
        },
    )

    response = client.post(
        "/service-registry/sync?source=gitops_apps&env=dev",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["correlationId"] == "cid-gitops"
    assert body["source"] == "gitops_apps"
    assert body["env"] == "dev"
    assert body["deleted"] == 1
