import json
from io import BytesIO
from urllib.error import HTTPError

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health_endpoint() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_login_success() -> None:
    response = client.post(
        "/auth/login",
        json={"username": "admin", "password": "changeme"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"] == "dev-static-token"
    assert body["token_type"] == "bearer"
    assert body["expires_at"]


def test_login_invalid_credentials() -> None:
    response = client.post(
        "/auth/login",
        json={"username": "admin", "password": "wrong"},
    )

    assert response.status_code == 401


def test_projects_unauthorized_without_token() -> None:
    response = client.get("/projects")

    assert response.status_code == 401


def test_projects_authorized_with_forwarded_user() -> None:
    response = client.get(
        "/projects",
        headers={"X-Auth-Request-User": "alice"},
    )

    assert response.status_code == 200


def test_create_and_list_projects_with_valid_token() -> None:
    headers = {"Authorization": "Bearer dev-static-token"}

    create_response = client.post(
        "/projects",
        headers=headers,
        json={"id": "proj-e2e", "name": "E2E Project", "environment": "dev"},
    )
    assert create_response.status_code == 201
    assert create_response.json()["id"] == "proj-e2e"

    list_response = client.get("/projects", headers=headers)
    assert list_response.status_code == 200

    project_ids = {project["id"] for project in list_response.json()["projects"]}
    assert "proj-e2e" in project_ids


def test_create_project_forbidden_for_non_admin_forwarded_user() -> None:
    response = client.post(
        "/projects",
        headers={"X-Auth-Request-User": "alice"},
        json={"id": "proj-forbidden", "name": "Nope", "environment": "dev"},
    )
    assert response.status_code == 403


def test_create_project_allowed_for_admin_group() -> None:
    response = client.post(
        "/projects",
        headers={
            "X-Auth-Request-User": "alice",
            "X-Auth-Request-Groups": "team-developers,team-admins",
        },
        json={"id": "proj-admin", "name": "Allowed", "environment": "dev"},
    )
    assert response.status_code == 201


class _MockPrometheusResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_service_metrics_summary_success_with_supported_range(monkeypatch) -> None:
    payloads = iter(
        [
            {"status": "success", "data": {"result": [{"value": [0, "99.95"]}]}},
            {"status": "success", "data": {"result": [{"value": [0, "320"]}]}},
            {"status": "success", "data": {"result": [{"value": [0, "0.42"]}]}},
            {"status": "success", "data": {"result": [{"value": [0, "3"]}]}},
        ]
    )

    def _mock_urlopen(*args, **kwargs):
        return _MockPrometheusResponse(next(payloads))

    monkeypatch.setattr("app.main.urlrequest.urlopen", _mock_urlopen)

    response = client.get(
        "/services/homelab-api/metrics/summary?range=24h",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["serviceId"] == "homelab-api"
    assert body["uptimePct"] == 99.95
    assert body["p95LatencyMs"] == 320.0
    assert body["errorRatePct"] == 0.42
    assert body["restartCount"] == 3.0
    assert body["windowStart"]
    assert body["windowEnd"]
    assert body["generatedAt"]
    assert body["noData"] == {
        "uptimePct": False,
        "p95LatencyMs": False,
        "errorRatePct": False,
        "restartCount": False,
    }


def test_service_metrics_summary_rejects_invalid_range() -> None:
    response = client.get(
        "/services/homelab-api/metrics/summary?range=2h",
        headers={"Authorization": "Bearer dev-static-token"},
    )
    assert response.status_code == 422


def test_service_metrics_summary_legacy_route_works(monkeypatch) -> None:
    payloads = iter(
        [
            {"status": "success", "data": {"result": [{"value": [0, "99.1"]}]}},
            {"status": "success", "data": {"result": [{"value": [0, "210"]}]}},
            {"status": "success", "data": {"result": [{"value": [0, "0.1"]}]}},
            {"status": "success", "data": {"result": [{"value": [0, "0"]}]}},
        ]
    )

    def _mock_urlopen(*args, **kwargs):
        return _MockPrometheusResponse(next(payloads))

    monkeypatch.setattr("app.main.urlrequest.urlopen", _mock_urlopen)

    response = client.get(
        "/services/homelab-api/metrics-summary?range=24h",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    assert response.json()["serviceId"] == "homelab-api"


def test_service_metrics_summary_supports_per_metric_no_data(monkeypatch) -> None:
    payloads = iter(
        [
            {"status": "success", "data": {"result": []}},
            {"status": "success", "data": {"result": [{"value": [0, "250"]}]}},
            {"status": "success", "data": {"result": []}},
            {"status": "success", "data": {"result": [{"value": [0, "1"]}]}},
        ]
    )

    def _mock_urlopen(*args, **kwargs):
        return _MockPrometheusResponse(next(payloads))

    monkeypatch.setattr("app.main.urlrequest.urlopen", _mock_urlopen)

    response = client.get(
        "/services/homelab-web/metrics/summary?range=1h",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["uptimePct"] is None
    assert body["p95LatencyMs"] == 250.0
    assert body["errorRatePct"] is None
    assert body["restartCount"] == 1.0
    assert body["noData"]["uptimePct"] is True
    assert body["noData"]["errorRatePct"] is True
    assert body["noData"]["p95LatencyMs"] is False
    assert body["noData"]["restartCount"] is False


def test_service_metrics_summary_translates_prometheus_http_errors(monkeypatch) -> None:
    def _mock_urlopen(*args, **kwargs):
        raise HTTPError(
            url="http://prometheus.local/api/v1/query",
            code=503,
            msg="Service Unavailable",
            hdrs=None,
            fp=BytesIO(b'{"status":"error","error":"provider down"}'),
        )

    monkeypatch.setattr("app.main.urlrequest.urlopen", _mock_urlopen)

    response = client.get(
        "/services/homelab-api/metrics/summary?range=7d",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "Monitoring provider query failed." in detail
    assert "correlation_id=" in detail


def test_service_health_timeline_returns_segments(monkeypatch) -> None:
    payloads = iter(
        [
            {
                "status": "success",
                "data": {
                    "result": [
                        {
                            "values": [
                                [1000, "1"],
                                [1300, "1"],
                                [1600, "0.5"],
                            ]
                        }
                    ]
                },
            },
            {
                "status": "success",
                "data": {
                    "result": [
                        {
                            "values": [
                                [1000, "0.2"],
                                [1300, "0.4"],
                                [1600, "0.4"],
                            ]
                        }
                    ]
                },
            },
            {
                "status": "success",
                "data": {
                    "result": [
                        {
                            "values": [
                                [1000, "1"],
                                [1300, "1"],
                                [1600, "0.55"],
                            ]
                        }
                    ]
                },
            },
        ]
    )

    def _mock_urlopen(*args, **kwargs):
        return _MockPrometheusResponse(next(payloads))

    monkeypatch.setattr("app.main.urlrequest.urlopen", _mock_urlopen)

    response = client.get(
        "/services/homelab-api/health/timeline?range=24h&step=5m",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert len(body) >= 1
    assert set(body[0].keys()).issuperset({"start", "end", "status"})


def test_service_health_timeline_rejects_invalid_step() -> None:
    response = client.get(
        "/services/homelab-api/health/timeline?range=24h&step=1m",
        headers={"Authorization": "Bearer dev-static-token"},
    )
    assert response.status_code == 422


def test_releases_endpoint_returns_traceability_rows(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.main._load_project_rows",
        lambda: [{"service_id": "homelab-api", "env": "dev"}],
    )
    monkeypatch.setattr(
        "app.main.load_ci_metadata_rows",
        lambda: [
            {
                "serviceId": "homelab-api",
                "env": "dev",
                "commitSha": "abc123",
                "imageRef": "ghcr.io/example/homelab-api:v1",
                "expectedRevision": "abc123",
                "deployedAt": "2026-03-05T12:00:00Z",
            }
        ],
    )
    monkeypatch.setattr(
        "app.main.load_argo_metadata_rows",
        lambda: [
            {
                "serviceId": "homelab-api",
                "env": "dev",
                "appName": "homelab-api-dev",
                "syncStatus": "synced",
                "healthStatus": "healthy",
                "revision": "abc123",
            }
        ],
    )

    response = client.get(
        "/releases?env=dev&limit=50",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert len(body) == 1
    row = body[0]
    assert row["serviceId"] == "homelab-api"
    assert row["env"] == "dev"
    assert row["argo"]["syncStatus"] == "synced"
    assert row["drift"]["isDrifted"] is False


def test_releases_endpoint_supports_service_filter(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.main._load_project_rows",
        lambda: [
            {"service_id": "homelab-api", "env": "dev"},
            {"service_id": "homelab-web", "env": "dev"},
        ],
    )
    monkeypatch.setattr("app.main.load_ci_metadata_rows", lambda: [])
    monkeypatch.setattr("app.main.load_argo_metadata_rows", lambda: [])

    response = client.get(
        "/releases?serviceId=homelab-web",
        headers={"Authorization": "Bearer dev-static-token"},
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["serviceId"] == "homelab-web"


def test_release_dashboard_compat_endpoint_available(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.main._load_project_rows",
        lambda: [{"service_id": "homelab-api", "env": "dev"}],
    )
    monkeypatch.setattr("app.main.load_ci_metadata_rows", lambda: [])
    monkeypatch.setattr("app.main.load_argo_metadata_rows", lambda: [])

    response = client.get(
        "/release-dashboard?env=dev",
        headers={"Authorization": "Bearer dev-static-token"},
    )
    assert response.status_code == 200
    body = response.json()
    assert "releases" in body
    assert len(body["releases"]) == 1
