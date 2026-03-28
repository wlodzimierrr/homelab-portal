import app.main as app_main
from app.github_workflows import (
    GitHubWorkflowDispatchError,
    GitHubWorkflowDispatchResult,
)

def test_service_details_include_release_metadata(client, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.main._load_service_rows",
        lambda **_kwargs: [
            {
                "service_id": "homelab-api",
                "service_name": "homelab-api",
                "env": "dev",
                "namespace": "homelab-api",
                "app_label": "homelab-api",
                "argo_app_name": "homelab-api-dev",
                "source": "cluster_services",
                "source_ref": "kubernetes_api",
                "last_synced_at": "2026-03-06T00:00:00+00:00",
            }
        ],
    )
    monkeypatch.setattr(
        "app.main._load_release_rows_for_service",
        lambda *_args, **_kwargs: [
            {
                "serviceId": "homelab-api",
                "env": "dev",
                "commitSha": "abc123",
                "imageRef": "ghcr.io/example/homelab-api:v1.2.3",
                "deployedAt": "2026-03-06T12:00:00Z",
                "argo": {
                    "appName": "homelab-api-dev",
                    "syncStatus": "synced",
                    "healthStatus": "healthy",
                    "revision": "abc123",
                },
                "drift": {
                    "isDrifted": False,
                    "expectedRevision": "abc123",
                    "liveRevision": "abc123",
                },
            }
        ],
    )
    monkeypatch.setattr("app.main._get_active_deployment_lock", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.main._load_project_catalog_rows", lambda **_kwargs: [])
    monkeypatch.setattr("app.main._load_service_catalog_rows", lambda **_kwargs: [])

    response = client.get(
        "/services/homelab-api?env=dev",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "homelab-api"
    assert body["version"] == "v1.2.3"
    assert body["health"] == "healthy"
    assert body["sync"] == "synced"


def test_service_details_fall_back_to_live_runtime_metadata(client, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.main._load_service_rows",
        lambda **_kwargs: [
            {
                "service_id": "homelab-api",
                "service_name": "homelab-api",
                "env": "dev",
                "namespace": "homelab-api",
                "app_label": "homelab-api",
                "argo_app_name": "homelab-api-dev",
                "source": "cluster_services",
                "source_ref": "kubernetes_api",
                "last_synced_at": "2026-03-07T00:00:00+00:00",
            }
        ],
    )
    monkeypatch.setattr(
        "app.main._load_release_rows_for_service",
        lambda *_args, **_kwargs: [
            {
                "serviceId": "homelab-api",
                "env": "dev",
                "commitSha": None,
                "imageRef": None,
                "deployedAt": None,
                "argo": {
                    "appName": "homelab-api-dev",
                    "syncStatus": "unknown",
                    "healthStatus": "unknown",
                    "revision": None,
                },
                "drift": {
                    "isDrifted": False,
                    "expectedRevision": None,
                    "liveRevision": None,
                },
            }
        ],
    )
    monkeypatch.setattr(
        "app.main._load_live_service_runtime_rows",
        lambda _row: [
            {
                "serviceId": "homelab-api",
                "env": "dev",
                "commitSha": None,
                "imageRef": "ghcr.io/example/homelab-api:v2.0.0",
                "deployedAt": "2026-03-07T10:00:00Z",
                "argo": {
                    "appName": "homelab-api-dev",
                    "syncStatus": "synced",
                    "healthStatus": "healthy",
                    "revision": "def456",
                },
                "drift": {
                    "isDrifted": False,
                    "expectedRevision": None,
                    "liveRevision": "def456",
                },
            }
        ],
    )
    monkeypatch.setattr("app.main._get_active_deployment_lock", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.main._load_project_catalog_rows", lambda **_kwargs: [])
    monkeypatch.setattr("app.main._load_service_catalog_rows", lambda **_kwargs: [])

    response = client.get(
        "/services/homelab-api?env=dev",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["version"] == "v2.0.0"
    assert body["health"] == "healthy"
    assert body["sync"] == "synced"


def test_service_deployments_endpoint_returns_deployment_records(client, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.main._load_service_rows",
        lambda **_kwargs: [
            {
                "service_id": "homelab-api",
                "service_name": "homelab-api",
                "env": "dev",
                "namespace": "homelab-api",
                "app_label": "homelab-api",
                "argo_app_name": "homelab-api-dev",
                "source": "cluster_services",
                "source_ref": "kubernetes_api",
                "last_synced_at": "2026-03-07T00:00:00+00:00",
            }
        ],
    )
    monkeypatch.setattr(
        "app.main._list_deployment_records_for_service",
        lambda *_args, **_kwargs: [
            {
                "deploymentId": "dep-123",
                "serviceId": "homelab-api",
                "env": "dev",
                "action": "deploy",
                "status": "live",
                "requestedAt": "2026-03-06T11:55:00Z",
                "finishedAt": "2026-03-06T12:00:00Z",
                "mergeSha": "abc123",
                "targetImage": "ghcr.io/example/homelab-api:v1.2.3",
                "prUrl": "https://github.com/example/homelab-workloads/pull/12",
                "prNumber": 12,
                "compareUrl": "https://github.com/example/homelab-portal/compare/old...new",
                "gitRef": "automation/dev-image-bump-abc123",
                "metadata": {"source": "workflow"},
            }
        ],
    )
    monkeypatch.setattr(
        "app.main._load_deployment_metric_snapshots",
        lambda *_args, **_kwargs: ({}, "none"),
    )

    response = client.get(
        "/services/homelab-api/deployments?env=dev",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["deployments"]) == 1
    assert body["deployments"][0]["id"] == "dep-123"
    assert body["deployments"][0]["serviceId"] == "homelab-api"
    assert body["deployments"][0]["env"] == "dev"
    assert body["deployments"][0]["action"] == "deploy"
    assert body["deployments"][0]["version"] == "v1.2.3"
    assert body["deployments"][0]["status"] == "live"
    assert body["deployments"][0]["requestedAt"] == "2026-03-06T11:55:00Z"
    assert body["deployments"][0]["deployedAt"] == "2026-03-06T12:00:00Z"
    assert (
        body["deployments"][0]["gitPrUrl"]
        == "https://github.com/example/homelab-workloads/pull/12"
    )
    assert (
        body["deployments"][0]["compareUrl"]
        == "https://github.com/example/homelab-portal/compare/old...new"
    )


def test_service_deployments_endpoint_returns_empty_list_without_records(client, monkeypatch,) -> None:
    monkeypatch.setattr(
        "app.main._load_service_rows",
        lambda **_kwargs: [
            {
                "service_id": "homelab-api",
                "service_name": "homelab-api",
                "env": "dev",
                "namespace": "homelab-api",
                "app_label": "homelab-api",
                "argo_app_name": "homelab-api-dev",
                "source": "cluster_services",
                "source_ref": "kubernetes_api",
                "last_synced_at": "2026-03-07T00:00:00+00:00",
            }
        ],
    )
    monkeypatch.setattr(
        "app.main._list_deployment_records_for_service", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(
        "app.main._load_deployment_metric_snapshots",
        lambda *_args, **_kwargs: ({}, "none"),
    )

    response = client.get(
        "/services/homelab-api/deployments?env=dev",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["deployments"] == []


def test_service_deployments_endpoint_includes_observability_snapshots(client, monkeypatch,) -> None:
    monkeypatch.setattr(
        "app.main._load_service_rows",
        lambda **_kwargs: [
            {
                "service_id": "homelab-api",
                "service_name": "homelab-api",
                "env": "dev",
                "namespace": "homelab-api",
                "app_label": "homelab-api",
                "argo_app_name": "homelab-api-dev",
                "source": "cluster_services",
                "source_ref": "kubernetes_api",
                "last_synced_at": "2026-03-07T00:00:00+00:00",
            }
        ],
    )
    monkeypatch.setattr(
        "app.main._list_deployment_records_for_service",
        lambda *_args, **_kwargs: [
            {
                "deploymentId": "dep-123",
                "serviceId": "homelab-api",
                "env": "dev",
                "action": "deploy",
                "status": "live",
                "requestedAt": "2026-03-06T11:55:00Z",
                "finishedAt": "2026-03-06T12:00:00Z",
                "mergeSha": "abc123",
                "targetImage": "ghcr.io/example/homelab-api:v1.2.3",
            }
        ],
    )
    monkeypatch.setattr(
        "app.main._load_deployment_metric_snapshots",
        lambda *_args, **_kwargs: (
            {
                "errorRatePct": {"before": 0.1, "after": 0.3, "delta": 0.2},
                "p95LatencyMs": {"before": 110.0, "after": 140.0, "delta": 30.0},
                "availabilityPct": {"before": 99.9, "after": 99.4, "delta": -0.5},
            },
            "live_query",
        ),
    )

    response = client.get(
        "/services/homelab-api/deployments?env=dev",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["deployments"][0]["errorRatePct"] == {
        "before": 0.1,
        "after": 0.3,
        "delta": 0.2,
    }
    assert body["deployments"][0]["p95LatencyMs"] == {
        "before": 110.0,
        "after": 140.0,
        "delta": 30.0,
    }
    assert body["deployments"][0]["availabilityPct"] == {
        "before": 99.9,
        "after": 99.4,
        "delta": -0.5,
    }


def test_load_deployment_metric_snapshots_uses_record_window(client, monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake_load_window(_service_row, *, window_start, window_end):
        captured["window_start"] = window_start.isoformat()
        captured["window_end"] = window_end.isoformat()
        return {
            "errorRatePct": {"before": 0.1, "after": 0.3, "delta": 0.2},
        }

    monkeypatch.setattr("app.main._load_metric_snapshots_for_window", _fake_load_window)

    snapshots, source = app_main._load_deployment_metric_snapshots(
        {
            "service_id": "homelab-api",
            "env": "dev",
            "namespace": "homelab-api",
            "app_label": "homelab-api",
        },
        {
            "deployWindowStart": "2026-03-10T16:35:09+00:00",
            "deployWindowEnd": "2026-03-10T16:37:20+00:00",
            "finishedAt": "2026-03-10T16:37:20+00:00",
        },
    )

    assert captured == {
        "window_start": "2026-03-10T16:35:09+00:00",
        "window_end": "2026-03-10T16:37:20+00:00",
    }
    assert source == "live_query"
    assert snapshots["errorRatePct"]["delta"] == 0.2


def test_service_deployment_observability_returns_window_scoped_sections(client, monkeypatch,) -> None:
    monkeypatch.setattr(
        "app.main._get_deployment_record_by_id",
        lambda _deployment_id: {
            "deploymentId": "dep-123",
            "serviceId": "homelab-api",
            "env": "dev",
            "action": "deploy",
            "status": "live",
            "deployWindowStart": "2026-03-10T16:35:09+00:00",
            "deployWindowEnd": "2026-03-10T16:37:20+00:00",
            "compareUrl": "https://github.com/example/homelab-portal/compare/a...b",
            "prUrl": "https://github.com/example/homelab-workloads/pull/46",
            "prNumber": 46,
            "deployReason": "Ship observability fix",
        },
    )
    monkeypatch.setattr(
        "app.main._load_service_rows",
        lambda **_kwargs: [
            {
                "service_id": "homelab-api",
                "service_name": "homelab-api",
                "env": "dev",
                "namespace": "homelab-api",
                "app_label": "homelab-api",
                "argo_app_name": "homelab-api-dev",
                "source": "cluster_services",
                "source_ref": "kubernetes_api",
                "last_synced_at": "2026-03-11T00:00:00+00:00",
            }
        ],
    )
    monkeypatch.setattr(
        "app.main._load_metric_snapshots_for_window",
        lambda *_args, **_kwargs: {
            "errorRatePct": {"before": 0.1, "after": 0.2, "delta": 0.1},
            "p95LatencyMs": {"before": 120.0, "after": 160.0, "delta": 40.0},
            "availabilityPct": {"before": 99.9, "after": 99.7, "delta": -0.2},
        },
    )
    monkeypatch.setattr(
        "app.main._build_deployment_timeline_response",
        lambda **_kwargs: {
            "queryStatus": "ok",
            "queryMessage": None,
            "serviceId": "homelab-api",
            "windowStart": "2026-03-10T16:35:09+00:00",
            "windowEnd": "2026-03-10T16:37:20+00:00",
            "generatedAt": "2026-03-11T12:00:00+00:00",
            "providerStatus": {
                "provider": "prometheus",
                "baseUrl": "http://prometheus.local",
                "status": "healthy",
                "reachable": True,
                "checkedAt": "2026-03-11T12:00:00+00:00",
            },
            "segments": [
                {
                    "start": "2026-03-10T16:35:09+00:00",
                    "end": "2026-03-10T16:37:20+00:00",
                    "status": "healthy",
                }
            ],
        },
    )
    monkeypatch.setattr(
        "app.main._build_deployment_logs_response",
        lambda **_kwargs: {
            "queryStatus": "ok",
            "queryMessage": None,
            "serviceId": "homelab-api",
            "preset": "errors",
            "generatedAt": "2026-03-11T12:00:00+00:00",
            "windowStart": "2026-03-10T16:35:09+00:00",
            "windowEnd": "2026-03-10T16:37:20+00:00",
            "limit": 50,
            "returned": 1,
            "moreAvailable": False,
            "lines": [
                {
                    "timestamp": "2026-03-10T16:36:00+00:00",
                    "message": "line-1",
                    "labels": {"app": "homelab-api"},
                }
            ],
            "providerStatus": {
                "provider": "loki",
                "baseUrl": "http://loki.local",
                "status": "healthy",
                "reachable": True,
                "checkedAt": "2026-03-11T12:00:00+00:00",
            },
        },
    )

    response = client.get(
        "/services/homelab-api/observability/window?deploymentId=dep-123&logsPreset=errors&logsLimit=50",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["context"]["deploymentId"] == "dep-123"
    assert body["context"]["windowSource"] == "deployment_record"
    assert body["metrics"]["queryStatus"] == "ok"
    assert body["metrics"]["errorRatePct"]["delta"] == 0.1
    assert body["healthTimeline"]["queryStatus"] == "ok"
    assert body["logsQuickView"]["queryStatus"] == "ok"
    assert body["logsQuickView"]["returned"] == 1


def test_service_deployment_observability_reports_missing_deploy_window(client, monkeypatch,) -> None:
    monkeypatch.setattr(
        "app.main._get_deployment_record_by_id",
        lambda _deployment_id: {
            "deploymentId": "dep-missing",
            "serviceId": "homelab-api",
            "env": "dev",
            "action": "deploy",
            "status": "pending",
            "requestedAt": None,
            "startedAt": None,
            "deployWindowStart": None,
            "deployWindowEnd": None,
        },
    )
    monkeypatch.setattr(
        "app.main._load_service_rows",
        lambda **_kwargs: [
            {
                "service_id": "homelab-api",
                "service_name": "homelab-api",
                "env": "dev",
                "namespace": "homelab-api",
                "app_label": "homelab-api",
                "argo_app_name": "homelab-api-dev",
                "source": "cluster_services",
                "source_ref": "kubernetes_api",
                "last_synced_at": "2026-03-11T00:00:00+00:00",
            }
        ],
    )

    response = client.get(
        "/services/homelab-api/observability/window?deploymentId=dep-missing",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["context"]["evidenceStatus"] == "missing"
    assert body["metrics"]["queryStatus"] == "no_deployment_window"
    assert body["healthTimeline"]["queryStatus"] == "no_deployment_window"
    assert body["logsQuickView"]["queryStatus"] == "no_deployment_window"


def test_service_deployment_observability_supports_explicit_window(client, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.main._load_service_rows",
        lambda **_kwargs: [
            {
                "service_id": "homelab-api",
                "service_name": "homelab-api",
                "env": "dev",
                "namespace": "homelab-api",
                "app_label": "homelab-api",
                "argo_app_name": "homelab-api-dev",
                "source": "cluster_services",
                "source_ref": "kubernetes_api",
                "last_synced_at": "2026-03-11T00:00:00+00:00",
            }
        ],
    )
    monkeypatch.setattr(
        "app.main._load_metric_snapshots_for_window",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "app.main._build_deployment_timeline_response",
        lambda **_kwargs: {
            "queryStatus": "no_data",
            "queryMessage": "No health data.",
            "serviceId": "homelab-api",
            "windowStart": "2026-03-11T10:00:00+00:00",
            "windowEnd": "2026-03-11T10:10:00+00:00",
            "generatedAt": "2026-03-11T12:00:00+00:00",
            "providerStatus": None,
            "segments": [],
        },
    )
    monkeypatch.setattr(
        "app.main._build_deployment_logs_response",
        lambda **_kwargs: {
            "queryStatus": "no_data",
            "queryMessage": "No logs retained.",
            "serviceId": "homelab-api",
            "preset": "errors",
            "generatedAt": "2026-03-11T12:00:00+00:00",
            "windowStart": "2026-03-11T10:00:00+00:00",
            "windowEnd": "2026-03-11T10:10:00+00:00",
            "limit": 50,
            "returned": 0,
            "moreAvailable": False,
            "lines": [],
            "providerStatus": None,
        },
    )

    response = client.get(
        "/services/homelab-api/observability/window?windowStart=2026-03-11T10:00:00%2B00:00&windowEnd=2026-03-11T10:10:00%2B00:00",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["context"]["windowSource"] == "explicit_window"
    assert body["context"]["deploymentId"] is None
    assert body["metrics"]["queryStatus"] == "no_data"


def test_create_deployment_record_endpoint_returns_record(client, monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake_upsert(payload, *, requested_by):
        captured["service_id"] = payload.service_id
        captured["env"] = payload.env
        captured["action"] = payload.action
        captured["requested_by"] = requested_by
        return {
            "deploymentId": "dep-123",
            "serviceId": payload.service_id,
            "env": payload.env,
            "action": payload.action,
            "status": payload.status,
            "requestedAt": "2026-03-09T20:00:00Z",
            "requestedBy": requested_by,
            "prUrl": payload.pr_url,
            "prNumber": payload.pr_number,
            "mergeSha": payload.merge_sha,
            "targetImage": payload.target_image,
            "previousImage": payload.previous_image,
            "argoApp": "homelab-api-dev",
            "syncStatus": None,
            "healthStatus": None,
            "startedAt": None,
            "finishedAt": None,
            "deployWindowStart": None,
            "deployWindowEnd": None,
            "deployReason": payload.deploy_reason,
            "compareUrl": payload.compare_url,
            "gitRef": payload.git_ref,
            "metadata": payload.metadata or {},
        }

    monkeypatch.setattr("app.main._upsert_deployment_record_row", _fake_upsert)
    monkeypatch.setattr(
        "app.main._load_service_rows",
        lambda **_kwargs: [
            {
                "service_id": "homelab-api",
                "service_name": "homelab-api",
                "env": "dev",
                "namespace": "homelab-api",
                "app_label": "homelab-api",
                "argo_app_name": "homelab-api-dev",
                "source": "cluster_services",
                "source_ref": "kubernetes_api",
                "last_synced_at": "2026-03-07T00:00:00+00:00",
            }
        ],
    )
    monkeypatch.setattr(
        "app.main._load_deployment_metric_snapshots",
        lambda *_args, **_kwargs: ({}, "none"),
    )

    response = client.post(
        "/deployments",
        headers={"Authorization": "Bearer dev-static-token"},
        json={
            "serviceId": "homelab-api",
            "env": "dev",
            "action": "deploy",
            "status": "pending",
            "requestedAt": "2026-03-09T20:00:00Z",
            "gitPrUrl": "https://github.com/example/homelab-workloads/pull/12",
            "gitPrNumber": 12,
            "mergeSha": "abc123",
            "imageRef": "ghcr.io/example/homelab-api:sha-abc123",
            "previousImageRef": "ghcr.io/example/homelab-api:sha-old",
            "gitRef": "automation/dev-image-bump-abc123",
            "compareUrl": "https://github.com/example/homelab-portal/compare/old...new",
            "deployReason": "Ship fix",
            "metadata": {"source": "workflow"},
        },
    )

    assert response.status_code == 201
    assert captured == {
        "service_id": "homelab-api",
        "env": "dev",
        "action": "deploy",
        "requested_by": "dev-static-token",
    }
    body = response.json()
    assert body["id"] == "dep-123"
    assert body["serviceId"] == "homelab-api"
    assert body["action"] == "deploy"
    assert body["status"] == "pending"
    assert body["gitPrUrl"] == "https://github.com/example/homelab-workloads/pull/12"
    assert body["imageRef"] == "ghcr.io/example/homelab-api:sha-abc123"


def test_request_portal_deploy_to_dev_endpoint_opens_pr_and_creates_record(client, monkeypatch,) -> None:
    captured: dict[str, object] = {}
    current_sha = "a" * 40
    next_sha = "b" * 40
    current_image = f"ghcr.io/wlodzimierrr/homelab-api:sha-{current_sha}"
    next_image = f"ghcr.io/wlodzimierrr/homelab-api:sha-{next_sha}"
    files = {
        "apps/homelab-api/envs/dev/patch-deployment.yaml": f"image: {current_image}\n",
        "apps/homelab-api/envs/dev/patch-migration-job.yaml": f"image: {current_image}\n",
        "apps/homelab-api/envs/dev/patch-catalog-sync-cronjob.yaml": f"image: {current_image}\n",
    }

    class _FakeGitProvider:
        def create_branch(self, repo, from_branch, new_branch):
            captured["create_branch"] = (repo, from_branch, new_branch)
            return {
                "branch": new_branch,
                "ref": f"refs/heads/{new_branch}",
                "sha": "head",
                "url": None,
            }

        def read_file(self, repo, branch, file_path):
            assert repo == "wlodzimierrr/homelab-workloads"
            assert branch == "main"
            return files[file_path]

        def commit_to_branch(self, repo, branch, files_dict, message):
            captured["commit"] = {
                "repo": repo,
                "branch": branch,
                "files": files_dict,
                "message": message,
            }
            return {
                "branch": branch,
                "commit_sha": "commit-sha",
                "tree_sha": "tree-sha",
                "files": sorted(files_dict),
            }

        def open_pr(self, repo, from_branch, to_branch, title, description):
            captured["pr"] = {
                "repo": repo,
                "from_branch": from_branch,
                "to_branch": to_branch,
                "title": title,
                "description": description,
            }
            return {
                "id": 60,
                "number": 61,
                "url": "https://github.com/example/homelab-workloads/pull/61",
                "state": "open",
            }

        def close_pr(self, repo, pr_id):
            captured["closed_pr"] = (repo, pr_id)
            return {
                "id": pr_id,
                "number": pr_id,
                "url": f"https://github.com/example/homelab-workloads/pull/{pr_id}",
                "state": "closed",
            }

    def _fake_upsert(payload, *, requested_by):
        captured["record"] = {
            "service_id": payload.service_id,
            "env": payload.env,
            "action": payload.action,
            "status": payload.status,
            "request_key": payload.request_key,
            "target_image": payload.target_image,
            "previous_image": payload.previous_image,
            "compare_url": payload.compare_url,
            "deploy_reason": payload.deploy_reason,
            "metadata": payload.metadata,
            "requested_by": requested_by,
        }
        return {
            "deploymentId": "dep-456",
            "serviceId": payload.service_id,
            "env": payload.env,
            "action": payload.action,
            "status": payload.status,
            "requestedAt": "2026-03-12T12:00:00Z",
            "requestedBy": requested_by,
            "prUrl": "https://github.com/example/homelab-workloads/pull/61",
            "prNumber": 61,
            "mergeSha": None,
            "targetImage": payload.target_image,
            "previousImage": payload.previous_image,
            "argoApp": "homelab-api-dev",
            "syncStatus": None,
            "healthStatus": None,
            "startedAt": None,
            "finishedAt": None,
            "deployWindowStart": None,
            "deployWindowEnd": None,
            "deployReason": payload.deploy_reason,
            "compareUrl": payload.compare_url,
            "gitRef": payload.git_ref,
            "metadata": payload.metadata or {},
        }

    monkeypatch.setattr(
        "app.main.build_default_git_provider", lambda: _FakeGitProvider()
    )
    monkeypatch.setattr(
        "app.main._resolve_latest_portal_image_candidate",
        lambda _service_id: {
            "tag": f"sha-{next_sha}",
            "imageRef": next_image,
            "sourceCommitSha": next_sha,
            "workflowRunId": 101,
            "workflowRunUrl": "https://github.com/wlodzimierrr/homelab-portal/actions/runs/101",
        },
    )
    monkeypatch.setattr(
        "app.main._get_active_deployment_lock", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr("app.main._upsert_deployment_record_row", _fake_upsert)

    response = client.post(
        "/services/homelab-api/deploy-to-dev",
        headers={"Authorization": "Bearer dev-static-token"},
        json={"deployReason": "Ship the latest portal backend fix to dev."},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "accepted"
    assert body["action"] == "deploy"
    assert body["serviceId"] == "homelab-api"
    assert body["deploymentId"] == "dep-456"
    assert body["gitPrNumber"] == 61
    assert body["gitPrUrl"] == "https://github.com/example/homelab-workloads/pull/61"
    assert body["previousTag"] == f"sha-{current_sha}"
    assert body["newTag"] == f"sha-{next_sha}"
    assert body["previousImageRef"] == current_image
    assert body["newImageRef"] == next_image
    assert (
        body["compareUrl"]
        == f"https://github.com/wlodzimierrr/homelab-portal/compare/{current_sha}...{next_sha}"
    )
    assert captured["create_branch"][0] == "wlodzimierrr/homelab-workloads"
    assert captured["pr"]["title"] == f"Deploy homelab-api: sha-{next_sha} to dev"
    assert (
        "- Reason: Ship the latest portal backend fix to dev."
        in captured["pr"]["description"]
    )
    assert f"- Target image: `{next_image}`" in captured["pr"]["description"]
    committed_files = captured["commit"]["files"]
    assert set(committed_files) == set(files)
    assert all(next_image in content for content in committed_files.values())
    assert captured["record"]["request_key"] == "gitops-pr:61:homelab-api:dev:deploy"
    assert captured["record"]["requested_by"] == "dev-static-token"
    assert captured["record"]["metadata"]["previousTag"] == f"sha-{current_sha}"
    assert captured["record"]["metadata"]["newTag"] == f"sha-{next_sha}"


def test_request_portal_deploy_to_dev_endpoint_returns_noop_when_latest_tag_is_already_deployed(client, monkeypatch,) -> None:
    current_sha = "a" * 40
    current_image = f"ghcr.io/wlodzimierrr/homelab-web:sha-{current_sha}"
    called: dict[str, bool] = {}

    class _FakeGitProvider:
        def create_branch(self, *_args, **_kwargs):
            called["create_branch"] = True
            raise AssertionError("create_branch should not be called for noop")

        def read_file(self, _repo, _branch, _file_path):
            return f"image: {current_image}\n"

        def commit_to_branch(self, *_args, **_kwargs):
            raise AssertionError("commit_to_branch should not be called for noop")

        def open_pr(self, *_args, **_kwargs):
            raise AssertionError("open_pr should not be called for noop")

        def close_pr(self, *_args, **_kwargs):
            raise AssertionError("close_pr should not be called for noop")

    monkeypatch.setattr(
        "app.main.build_default_git_provider", lambda: _FakeGitProvider()
    )
    monkeypatch.setattr(
        "app.main._resolve_latest_portal_image_candidate",
        lambda _service_id: {
            "tag": f"sha-{current_sha}",
            "imageRef": current_image,
            "sourceCommitSha": current_sha,
            "workflowRunId": 102,
            "workflowRunUrl": "https://github.com/wlodzimierrr/homelab-portal/actions/runs/102",
        },
    )

    response = client.post(
        "/services/homelab-web/deploy-to-dev",
        headers={"Authorization": "Bearer dev-static-token"},
        json={"deployReason": "No-op deploy request."},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "noop"
    assert body["gitPrUrl"] is None
    assert body["deploymentId"] is None
    assert body["previousImageRef"] == current_image
    assert body["newImageRef"] == current_image
    assert "already points at the latest deployable image tag" in body["message"]
    assert "create_branch" not in called


def test_request_portal_deploy_to_dev_endpoint_validates_reason(client) -> None:
    response = client.post(
        "/services/homelab-api/deploy-to-dev",
        headers={"Authorization": "Bearer dev-static-token"},
        json={"deployReason": "bad"},
    )

    assert response.status_code == 422


def test_request_portal_deploy_to_dev_closes_pr_when_lock_conflict_happens_after_pr_creation(client, monkeypatch,) -> None:
    current_sha = "a" * 40
    next_sha = "b" * 40
    current_image = f"ghcr.io/wlodzimierrr/homelab-api:sha-{current_sha}"
    next_image = f"ghcr.io/wlodzimierrr/homelab-api:sha-{next_sha}"
    captured: dict[str, object] = {}

    class _FakeGitProvider:
        def create_branch(self, repo, from_branch, new_branch):
            return {
                "branch": new_branch,
                "ref": f"refs/heads/{new_branch}",
                "sha": "head",
                "url": None,
            }

        def read_file(self, _repo, _branch, _file_path):
            return f"image: {current_image}\n"

        def commit_to_branch(self, *_args, **_kwargs):
            return {
                "branch": "branch",
                "commit_sha": "commit-sha",
                "tree_sha": "tree-sha",
                "files": [],
            }

        def open_pr(self, *_args, **_kwargs):
            return {
                "id": 60,
                "number": 62,
                "url": "https://github.com/example/homelab-workloads/pull/62",
                "state": "open",
            }

        def close_pr(self, repo, pr_id):
            captured["closed_pr"] = (repo, pr_id)
            return {
                "id": pr_id,
                "number": pr_id,
                "url": f"https://github.com/example/homelab-workloads/pull/{pr_id}",
                "state": "closed",
            }

    monkeypatch.setattr(
        "app.main.build_default_git_provider", lambda: _FakeGitProvider()
    )
    monkeypatch.setattr(
        "app.main._resolve_latest_portal_image_candidate",
        lambda _service_id: {
            "tag": f"sha-{next_sha}",
            "imageRef": next_image,
            "sourceCommitSha": next_sha,
            "workflowRunId": 103,
            "workflowRunUrl": "https://github.com/wlodzimierrr/homelab-portal/actions/runs/103",
        },
    )
    monkeypatch.setattr(
        "app.main._get_active_deployment_lock", lambda *_args, **_kwargs: None
    )

    active_lock = {
        "serviceId": "homelab-api",
        "env": "dev",
        "deploymentId": "dep-lock",
        "requestKey": "existing",
        "action": "deploy",
        "status": "pending",
        "argoApp": "homelab-api-dev",
        "requestedBy": "alice",
        "requestedAt": "2026-03-12T12:00:00+00:00",
        "gitPrUrl": "https://github.com/example/homelab-workloads/pull/99",
        "gitPrNumber": 99,
        "gitRef": "automation/dev-image-bump-lock",
        "deployReason": "Existing mutation",
        "lockedAt": "2026-03-12T12:00:00+00:00",
        "expiresAt": "2026-03-12T12:30:00+00:00",
        "metadata": {},
    }

    def _raise_lock_conflict(_payload, *, requested_by):
        raise app_main.DeploymentLockConflictError(active_lock)

    monkeypatch.setattr("app.main._upsert_deployment_record_row", _raise_lock_conflict)

    response = client.post(
        "/services/homelab-api/deploy-to-dev",
        headers={"Authorization": "Bearer dev-static-token"},
        json={"deployReason": "Try conflicting deploy."},
    )

    assert response.status_code == 409
    assert captured["closed_pr"] == ("wlodzimierrr/homelab-workloads", 62)
    assert response.json()["detail"]["activeLock"]["deploymentId"] == "dep-lock"


def test_request_portal_promote_to_prod_opens_pr_and_creates_record(client, monkeypatch,) -> None:
    current_sha = "a" * 40
    promoted_sha = "b" * 40
    current_image = f"ghcr.io/wlodzimierrr/homelab-api:sha-{current_sha}"
    promoted_image = f"ghcr.io/wlodzimierrr/homelab-api:sha-{promoted_sha}"
    source_file = "apps/homelab-api/envs/dev/patch-deployment.yaml"
    files = {
        source_file: f"image: {promoted_image}\n",
        "apps/homelab-api/envs/prod/patch-deployment.yaml": f"image: {current_image}\n",
        "apps/homelab-api/envs/prod/patch-migration-job.yaml": f"image: {current_image}\n",
        "apps/homelab-api/envs/prod/patch-catalog-sync-cronjob.yaml": f"image: {current_image}\n",
    }
    captured: dict[str, object] = {}

    class _FakeGitProvider:
        def create_branch(self, repo, from_branch, new_branch):
            captured["create_branch"] = (repo, from_branch, new_branch)
            return {
                "branch": new_branch,
                "ref": f"refs/heads/{new_branch}",
                "sha": "head-sha",
                "url": None,
            }

        def read_file(self, _repo, _branch, file_path):
            return files[file_path]

        def commit_to_branch(self, repo, branch, updated_files, message):
            captured["commit"] = {
                "repo": repo,
                "branch": branch,
                "files": dict(updated_files),
                "message": message,
            }
            return {
                "branch": branch,
                "commit_sha": "commit-sha",
                "tree_sha": "tree-sha",
                "files": sorted(updated_files),
            }

        def open_pr(self, repo, from_branch, to_branch, title, description):
            captured["pr"] = {
                "repo": repo,
                "from_branch": from_branch,
                "to_branch": to_branch,
                "title": title,
                "description": description,
            }
            return {
                "id": 71,
                "number": 72,
                "url": "https://github.com/example/homelab-workloads/pull/72",
                "state": "open",
            }

        def close_pr(self, *_args, **_kwargs):
            raise AssertionError("close_pr should not be called")

    def _fake_upsert(payload, *, requested_by):
        captured["record"] = {
            "service_id": payload.service_id,
            "env": payload.env,
            "action": payload.action,
            "status": payload.status,
            "request_key": payload.request_key,
            "target_image": payload.target_image,
            "previous_image": payload.previous_image,
            "compare_url": payload.compare_url,
            "deploy_reason": payload.deploy_reason,
            "metadata": payload.metadata,
            "requested_by": requested_by,
        }
        return {
            "deploymentId": "dep-789",
            "serviceId": payload.service_id,
            "env": payload.env,
            "action": payload.action,
            "status": payload.status,
            "requestedAt": "2026-03-12T13:00:00Z",
            "requestedBy": requested_by,
            "prUrl": "https://github.com/example/homelab-workloads/pull/72",
            "prNumber": 72,
            "mergeSha": None,
            "targetImage": payload.target_image,
            "previousImage": payload.previous_image,
            "argoApp": "homelab-api-prod",
            "syncStatus": None,
            "healthStatus": None,
            "startedAt": None,
            "finishedAt": None,
            "deployWindowStart": None,
            "deployWindowEnd": None,
            "deployReason": payload.deploy_reason,
            "compareUrl": payload.compare_url,
            "gitRef": payload.git_ref,
            "metadata": payload.metadata or {},
        }

    monkeypatch.setattr(
        "app.main.build_default_git_provider", lambda: _FakeGitProvider()
    )
    monkeypatch.setattr(
        "app.main._ensure_ghcr_tag_exists", lambda _repo, _tag, **_kwargs: None
    )
    monkeypatch.setattr(
        "app.main._get_active_deployment_lock", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr("app.main._upsert_deployment_record_row", _fake_upsert)

    response = client.post(
        "/services/homelab-api/promote-to-prod",
        headers={"Authorization": "Bearer dev-static-token"},
        json={"deployReason": "Promote the verified dev release to prod."},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "accepted"
    assert body["action"] == "promote"
    assert body["serviceId"] == "homelab-api"
    assert body["deploymentId"] == "dep-789"
    assert body["gitPrNumber"] == 72
    assert body["gitPrUrl"] == "https://github.com/example/homelab-workloads/pull/72"
    assert body["previousTag"] == f"sha-{current_sha}"
    assert body["newTag"] == f"sha-{promoted_sha}"
    assert body["previousImageRef"] == current_image
    assert body["newImageRef"] == promoted_image
    assert (
        body["compareUrl"]
        == f"https://github.com/wlodzimierrr/homelab-portal/compare/{current_sha}...{promoted_sha}"
    )
    assert body["sourceCommitSha"] == promoted_sha
    assert captured["pr"]["title"] == f"Promote homelab-api: sha-{promoted_sha} to prod"
    assert (
        "- Reason: Promote the verified dev release to prod."
        in captured["pr"]["description"]
    )
    assert "- Source environment: `dev`" in captured["pr"]["description"]
    committed_files = captured["commit"]["files"]
    assert set(committed_files) == {
        "apps/homelab-api/envs/prod/patch-deployment.yaml",
        "apps/homelab-api/envs/prod/patch-migration-job.yaml",
        "apps/homelab-api/envs/prod/patch-catalog-sync-cronjob.yaml",
    }
    assert all(promoted_image in content for content in committed_files.values())
    assert captured["record"]["request_key"] == "gitops-pr:72:homelab-api:prod:promote"
    assert captured["record"]["requested_by"] == "dev-static-token"
    assert captured["record"]["metadata"]["sourceEnvironment"] == "dev"
    assert captured["record"]["metadata"]["newTag"] == f"sha-{promoted_sha}"


def test_request_portal_promote_to_prod_returns_noop_when_prod_already_matches_dev(client, monkeypatch,) -> None:
    promoted_sha = "c" * 40
    promoted_image = f"ghcr.io/wlodzimierrr/homelab-web:sha-{promoted_sha}"
    called: dict[str, bool] = {}

    class _FakeGitProvider:
        def create_branch(self, *_args, **_kwargs):
            called["create_branch"] = True
            raise AssertionError("create_branch should not be called for noop")

        def read_file(self, _repo, _branch, _file_path):
            return f"image: {promoted_image}\n"

        def commit_to_branch(self, *_args, **_kwargs):
            raise AssertionError("commit_to_branch should not be called for noop")

        def open_pr(self, *_args, **_kwargs):
            raise AssertionError("open_pr should not be called for noop")

        def close_pr(self, *_args, **_kwargs):
            raise AssertionError("close_pr should not be called for noop")

    monkeypatch.setattr(
        "app.main.build_default_git_provider", lambda: _FakeGitProvider()
    )
    monkeypatch.setattr(
        "app.main._ensure_ghcr_tag_exists", lambda _repo, _tag, **_kwargs: None
    )

    response = client.post(
        "/services/homelab-web/promote-to-prod",
        headers={"Authorization": "Bearer dev-static-token"},
        json={"deployReason": "No-op promote request."},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "noop"
    assert body["gitPrUrl"] is None
    assert body["deploymentId"] is None
    assert body["previousImageRef"] == promoted_image
    assert body["newImageRef"] == promoted_image
    assert "already matches the current dev image tag" in body["message"]
    assert "create_branch" not in called


def test_request_portal_promote_to_prod_validates_reason(client) -> None:
    response = client.post(
        "/services/homelab-api/promote-to-prod",
        headers={"Authorization": "Bearer dev-static-token"},
        json={"deployReason": "bad"},
    )

    assert response.status_code == 422


def test_request_portal_promote_to_prod_closes_pr_when_lock_conflict_happens_after_pr_creation(client, monkeypatch,) -> None:
    current_sha = "d" * 40
    promoted_sha = "e" * 40
    current_image = f"ghcr.io/wlodzimierrr/homelab-web:sha-{current_sha}"
    promoted_image = f"ghcr.io/wlodzimierrr/homelab-web:sha-{promoted_sha}"
    captured: dict[str, object] = {}

    class _FakeGitProvider:
        def create_branch(self, repo, from_branch, new_branch):
            return {
                "branch": new_branch,
                "ref": f"refs/heads/{new_branch}",
                "sha": "head",
                "url": None,
            }

        def read_file(self, _repo, _branch, file_path):
            if file_path == "apps/homelab-web/envs/dev/patch-deployment.yaml":
                return f"image: {promoted_image}\n"
            return f"image: {current_image}\n"

        def commit_to_branch(self, *_args, **_kwargs):
            return {
                "branch": "branch",
                "commit_sha": "commit-sha",
                "tree_sha": "tree-sha",
                "files": [],
            }

        def open_pr(self, *_args, **_kwargs):
            return {
                "id": 81,
                "number": 82,
                "url": "https://github.com/example/homelab-workloads/pull/82",
                "state": "open",
            }

        def close_pr(self, repo, pr_id):
            captured["closed_pr"] = (repo, pr_id)
            return {
                "id": pr_id,
                "number": pr_id,
                "url": f"https://github.com/example/homelab-workloads/pull/{pr_id}",
                "state": "closed",
            }

    monkeypatch.setattr(
        "app.main.build_default_git_provider", lambda: _FakeGitProvider()
    )
    monkeypatch.setattr(
        "app.main._ensure_ghcr_tag_exists", lambda _repo, _tag, **_kwargs: None
    )
    monkeypatch.setattr(
        "app.main._get_active_deployment_lock", lambda *_args, **_kwargs: None
    )

    active_lock = {
        "serviceId": "homelab-web",
        "env": "prod",
        "deploymentId": "dep-lock",
        "requestKey": "existing",
        "action": "promote",
        "status": "pending",
        "argoApp": "homelab-web-prod",
        "requestedBy": "alice",
        "requestedAt": "2026-03-12T12:00:00+00:00",
        "gitPrUrl": "https://github.com/example/homelab-workloads/pull/99",
        "gitPrNumber": 99,
        "gitRef": "automation/prod-promote-lock",
        "deployReason": "Existing mutation",
        "lockedAt": "2026-03-12T12:00:00+00:00",
        "expiresAt": "2026-03-12T12:30:00+00:00",
        "metadata": {},
    }

    def _raise_lock_conflict(_payload, *, requested_by):
        raise app_main.DeploymentLockConflictError(active_lock)

    monkeypatch.setattr("app.main._upsert_deployment_record_row", _raise_lock_conflict)

    response = client.post(
        "/services/homelab-web/promote-to-prod",
        headers={"Authorization": "Bearer dev-static-token"},
        json={"deployReason": "Try conflicting promote."},
    )

    assert response.status_code == 409
    assert captured["closed_pr"] == ("wlodzimierrr/homelab-workloads", 82)
    assert response.json()["detail"]["activeLock"]["deploymentId"] == "dep-lock"


def test_list_service_rollback_candidates_returns_current_tag_and_candidates(client, monkeypatch,) -> None:
    current_sha = "1" * 40
    current_image = f"ghcr.io/wlodzimierrr/homelab-web:sha-{current_sha}"

    class _FakeGitProvider:
        def read_file(self, _repo, _branch, file_path):
            assert file_path == "apps/homelab-web/envs/dev/patch-deployment.yaml"
            return f"image: {current_image}\n"

    monkeypatch.setattr(
        "app.main.build_default_git_provider", lambda: _FakeGitProvider()
    )
    monkeypatch.setattr(
        "app.main._list_service_rollback_candidates",
        lambda **_kwargs: [
            {
                "tag": "sha-2222222222222222222222222222222222222222",
                "imageRef": "ghcr.io/wlodzimierrr/homelab-web:sha-2222222222222222222222222222222222222222",
                "compareUrl": "https://github.com/example/compare/one...two",
                "sourceCommitSha": "2" * 40,
                "publishedAt": "2026-03-12T14:00:00Z",
            }
        ],
    )

    response = client.get(
        "/services/homelab-web/rollback-candidates?targetEnvironment=dev",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["serviceId"] == "homelab-web"
    assert body["targetEnvironment"] == "dev"
    assert body["currentTag"] == f"sha-{current_sha}"
    assert body["currentImageRef"] == current_image
    assert len(body["candidates"]) == 1
    assert (
        body["candidates"][0]["tag"] == "sha-2222222222222222222222222222222222222222"
    )


def test_request_service_rollback_opens_pr_and_creates_record(client, monkeypatch) -> None:
    current_sha = "a" * 40
    rollback_sha = "b" * 40
    current_image = f"ghcr.io/wlodzimierrr/homelab-web:sha-{current_sha}"
    rollback_image = f"ghcr.io/wlodzimierrr/homelab-web:sha-{rollback_sha}"
    file_path = "apps/homelab-web/envs/dev/patch-deployment.yaml"
    captured: dict[str, object] = {}

    class _FakeGitProvider:
        def create_branch(self, repo, from_branch, new_branch):
            captured["create_branch"] = (repo, from_branch, new_branch)
            return {
                "branch": new_branch,
                "ref": f"refs/heads/{new_branch}",
                "sha": "head-sha",
                "url": None,
            }

        def read_file(self, _repo, _branch, requested_file_path):
            assert requested_file_path == file_path
            return f"image: {current_image}\n"

        def commit_to_branch(self, repo, branch, updated_files, message):
            captured["commit"] = {
                "repo": repo,
                "branch": branch,
                "files": dict(updated_files),
                "message": message,
            }
            return {
                "branch": branch,
                "commit_sha": "commit-sha",
                "tree_sha": "tree-sha",
                "files": sorted(updated_files),
            }

        def open_pr(self, repo, from_branch, to_branch, title, description):
            captured["pr"] = {
                "repo": repo,
                "from_branch": from_branch,
                "to_branch": to_branch,
                "title": title,
                "description": description,
            }
            return {
                "id": 83,
                "number": 84,
                "url": "https://github.com/example/homelab-workloads/pull/84",
                "state": "open",
            }

        def close_pr(self, *_args, **_kwargs):
            raise AssertionError("close_pr should not be called")

    def _fake_upsert(payload, *, requested_by):
        captured["record"] = {
            "service_id": payload.service_id,
            "env": payload.env,
            "action": payload.action,
            "status": payload.status,
            "request_key": payload.request_key,
            "target_image": payload.target_image,
            "previous_image": payload.previous_image,
            "compare_url": payload.compare_url,
            "deploy_reason": payload.deploy_reason,
            "metadata": payload.metadata,
            "requested_by": requested_by,
        }
        return {
            "deploymentId": "dep-rollback-1",
            "serviceId": payload.service_id,
            "env": payload.env,
            "action": payload.action,
            "status": payload.status,
            "requestedAt": "2026-03-12T14:00:00Z",
            "requestedBy": requested_by,
            "prUrl": "https://github.com/example/homelab-workloads/pull/84",
            "prNumber": 84,
            "mergeSha": None,
            "targetImage": payload.target_image,
            "previousImage": payload.previous_image,
            "argoApp": "homelab-web-dev",
            "syncStatus": None,
            "healthStatus": None,
            "startedAt": None,
            "finishedAt": None,
            "deployWindowStart": None,
            "deployWindowEnd": None,
            "deployReason": payload.deploy_reason,
            "compareUrl": payload.compare_url,
            "gitRef": payload.git_ref,
            "metadata": payload.metadata or {},
        }

    monkeypatch.setattr(
        "app.main.build_default_git_provider", lambda: _FakeGitProvider()
    )
    monkeypatch.setattr(
        "app.main._ensure_ghcr_tag_exists", lambda _repo, _tag, **_kwargs: None
    )
    monkeypatch.setattr(
        "app.main._get_active_deployment_lock", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr("app.main._upsert_deployment_record_row", _fake_upsert)

    response = client.post(
        "/services/homelab-web/rollback",
        headers={"Authorization": "Bearer dev-static-token"},
        json={
            "targetEnvironment": "dev",
            "rollbackTag": f"sha-{rollback_sha}",
            "deployReason": "Rollback homelab-web dev to the previous known-good image.",
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "accepted"
    assert body["action"] == "rollback"
    assert body["serviceId"] == "homelab-web"
    assert body["targetEnvironment"] == "dev"
    assert body["deploymentId"] == "dep-rollback-1"
    assert body["gitPrNumber"] == 84
    assert body["gitPrUrl"] == "https://github.com/example/homelab-workloads/pull/84"
    assert body["previousTag"] == f"sha-{current_sha}"
    assert body["newTag"] == f"sha-{rollback_sha}"
    assert body["previousImageRef"] == current_image
    assert body["newImageRef"] == rollback_image
    assert (
        body["compareUrl"]
        == f"https://github.com/wlodzimierrr/homelab-portal/compare/{current_sha}...{rollback_sha}"
    )
    assert body["sourceCommitSha"] == rollback_sha
    assert captured["pr"]["title"] == f"Rollback homelab-web: sha-{rollback_sha} in dev"
    assert (
        "- Reason: Rollback homelab-web dev to the previous known-good image."
        in captured["pr"]["description"]
    )
    assert "- Target environment: `dev`" in captured["pr"]["description"]
    committed_files = captured["commit"]["files"]
    assert set(committed_files) == {file_path}
    assert committed_files[file_path] == f"image: {rollback_image}\n"
    assert captured["record"]["request_key"] == "gitops-pr:84:homelab-web:dev:rollback"
    assert captured["record"]["requested_by"] == "dev-static-token"
    assert captured["record"]["metadata"]["targetEnvironment"] == "dev"
    assert captured["record"]["metadata"]["newTag"] == f"sha-{rollback_sha}"


def test_request_service_rollback_returns_noop_when_target_tag_is_already_deployed(client, monkeypatch,) -> None:
    rollback_sha = "c" * 40
    rollback_image = f"ghcr.io/wlodzimierrr/homelab-api:sha-{rollback_sha}"
    called: dict[str, bool] = {}

    class _FakeGitProvider:
        def create_branch(self, *_args, **_kwargs):
            called["create_branch"] = True
            raise AssertionError("create_branch should not be called for noop")

        def read_file(self, _repo, _branch, _file_path):
            return f"image: {rollback_image}\n"

        def commit_to_branch(self, *_args, **_kwargs):
            raise AssertionError("commit_to_branch should not be called for noop")

        def open_pr(self, *_args, **_kwargs):
            raise AssertionError("open_pr should not be called for noop")

        def close_pr(self, *_args, **_kwargs):
            raise AssertionError("close_pr should not be called for noop")

    monkeypatch.setattr(
        "app.main.build_default_git_provider", lambda: _FakeGitProvider()
    )
    monkeypatch.setattr(
        "app.main._ensure_ghcr_tag_exists", lambda _repo, _tag, **_kwargs: None
    )

    response = client.post(
        "/services/homelab-api/rollback",
        headers={"Authorization": "Bearer dev-static-token"},
        json={
            "targetEnvironment": "dev",
            "rollbackTag": f"sha-{rollback_sha}",
            "deployReason": "No-op rollback request.",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "noop"
    assert body["gitPrUrl"] is None
    assert body["deploymentId"] is None
    assert body["previousImageRef"] == rollback_image
    assert body["newImageRef"] == rollback_image
    assert "already matches the requested rollback tag" in body["message"]
    assert "create_branch" not in called


def test_request_service_rollback_validates_tag_and_reason(client) -> None:
    response = client.post(
        "/services/homelab-api/rollback",
        headers={"Authorization": "Bearer dev-static-token"},
        json={
            "targetEnvironment": "dev",
            "rollbackTag": "latest",
            "deployReason": "bad",
        },
    )

    assert response.status_code == 422


def test_request_service_rollback_closes_pr_when_lock_conflict_happens_after_pr_creation(client, monkeypatch,) -> None:
    current_sha = "d" * 40
    rollback_sha = "e" * 40
    current_image = f"ghcr.io/wlodzimierrr/homelab-api:sha-{current_sha}"
    captured: dict[str, object] = {}

    class _FakeGitProvider:
        def create_branch(self, repo, from_branch, new_branch):
            return {
                "branch": new_branch,
                "ref": f"refs/heads/{new_branch}",
                "sha": "head",
                "url": None,
            }

        def read_file(self, _repo, _branch, _file_path):
            return f"image: {current_image}\n"

        def commit_to_branch(self, *_args, **_kwargs):
            return {
                "branch": "branch",
                "commit_sha": "commit-sha",
                "tree_sha": "tree-sha",
                "files": [],
            }

        def open_pr(self, *_args, **_kwargs):
            return {
                "id": 91,
                "number": 92,
                "url": "https://github.com/example/homelab-workloads/pull/92",
                "state": "open",
            }

        def close_pr(self, repo, pr_id):
            captured["closed_pr"] = (repo, pr_id)
            return {
                "id": pr_id,
                "number": pr_id,
                "url": f"https://github.com/example/homelab-workloads/pull/{pr_id}",
                "state": "closed",
            }

    monkeypatch.setattr(
        "app.main.build_default_git_provider", lambda: _FakeGitProvider()
    )
    monkeypatch.setattr(
        "app.main._ensure_ghcr_tag_exists", lambda _repo, _tag, **_kwargs: None
    )
    monkeypatch.setattr(
        "app.main._get_active_deployment_lock", lambda *_args, **_kwargs: None
    )

    active_lock = {
        "serviceId": "homelab-api",
        "env": "dev",
        "deploymentId": "dep-lock",
        "requestKey": "existing",
        "action": "rollback",
        "status": "pending",
        "argoApp": "homelab-api-dev",
        "requestedBy": "alice",
        "requestedAt": "2026-03-12T12:00:00+00:00",
        "gitPrUrl": "https://github.com/example/homelab-workloads/pull/99",
        "gitPrNumber": 99,
        "gitRef": "automation/dev-rollback-lock",
        "deployReason": "Existing rollback",
        "lockedAt": "2026-03-12T12:00:00+00:00",
        "expiresAt": "2026-03-12T12:30:00+00:00",
        "metadata": {},
    }

    def _raise_lock_conflict(_payload, *, requested_by):
        raise app_main.DeploymentLockConflictError(active_lock)

    monkeypatch.setattr("app.main._upsert_deployment_record_row", _raise_lock_conflict)

    response = client.post(
        "/services/homelab-api/rollback",
        headers={"Authorization": "Bearer dev-static-token"},
        json={
            "targetEnvironment": "dev",
            "rollbackTag": f"sha-{rollback_sha}",
            "deployReason": "Try conflicting rollback.",
        },
    )

    assert response.status_code == 409
    assert captured["closed_pr"] == ("wlodzimierrr/homelab-workloads", 92)
    assert response.json()["detail"]["activeLock"]["deploymentId"] == "dep-lock"


def test_request_portal_rollback_endpoint_dispatches_workflow(client, monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake_dispatch(**kwargs):
        captured.update(kwargs)
        return GitHubWorkflowDispatchResult(
            repository="wlodzimierrr/homelab-portal",
            workflow_file="gated-promotion.yml",
            workflow_ref="main",
            workflow_url="https://github.com/wlodzimierrr/homelab-portal/actions/workflows/gated-promotion.yml",
        )

    monkeypatch.setattr("app.main.dispatch_portal_rollback_workflow", _fake_dispatch)

    response = client.post(
        "/rollbacks",
        headers={"Authorization": "Bearer dev-static-token"},
        json={
            "targetEnvironment": "prod",
            "rollbackApiTag": "sha-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "rollbackWebTag": "sha-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "reason": "Restore known-good portal release after login regression.",
        },
    )

    assert response.status_code == 202
    assert captured == {
        "rollback_api_tag": "sha-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "rollback_web_tag": "sha-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "operator_reason": "Restore known-good portal release after login regression.",
        "target_environment": "prod",
    }
    body = response.json()
    assert body["status"] == "accepted"
    assert body["action"] == "rollback"
    assert body["targetEnvironment"] == "prod"
    assert body["rollbackApiTag"] == "sha-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert body["rollbackWebTag"] == "sha-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    assert body["requestedBy"] == "dev-static-token"
    assert body["workflowFile"] == "gated-promotion.yml"


def test_request_portal_rollback_endpoint_validates_tags_and_reason(client) -> None:
    response = client.post(
        "/rollbacks",
        headers={"Authorization": "Bearer dev-static-token"},
        json={
            "targetEnvironment": "prod",
            "rollbackApiTag": "latest",
            "rollbackWebTag": "sha-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "reason": "bad",
        },
    )

    assert response.status_code == 422


def test_request_portal_rollback_endpoint_maps_dispatch_errors(client, monkeypatch) -> None:
    def _fake_dispatch(**_kwargs):
        raise GitHubWorkflowDispatchError("upstream unavailable", status_code=503)

    monkeypatch.setattr("app.main.dispatch_portal_rollback_workflow", _fake_dispatch)

    response = client.post(
        "/rollbacks",
        headers={"Authorization": "Bearer dev-static-token"},
        json={
            "targetEnvironment": "prod",
            "rollbackApiTag": "sha-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "rollbackWebTag": "sha-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "reason": "Restore known-good portal release after login regression.",
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "upstream unavailable"


def test_get_deployment_endpoint_returns_record(client, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.main._get_deployment_record_by_id",
        lambda _deployment_id: {
            "deploymentId": "dep-123",
            "serviceId": "homelab-api",
            "env": "dev",
            "action": "deploy",
            "status": "live",
            "requestedAt": "2026-03-09T20:00:00Z",
            "requestedBy": "dev-static-token",
            "prUrl": "https://github.com/example/homelab-workloads/pull/12",
            "prNumber": 12,
            "mergeSha": "abc123",
            "targetImage": "ghcr.io/example/homelab-api:v1.2.3",
            "previousImage": "ghcr.io/example/homelab-api:v1.2.2",
            "argoApp": "homelab-api-dev",
            "syncStatus": "synced",
            "healthStatus": "healthy",
            "startedAt": "2026-03-09T20:01:00Z",
            "finishedAt": "2026-03-09T20:03:00Z",
            "deployWindowStart": "2026-03-09T20:01:00Z",
            "deployWindowEnd": "2026-03-09T20:08:00Z",
            "deployReason": "Ship fix",
            "compareUrl": "https://github.com/example/homelab-portal/compare/old...new",
            "gitRef": "automation/dev-image-bump-abc123",
            "metadata": {"source": "workflow"},
        },
    )
    monkeypatch.setattr(
        "app.main._load_service_rows",
        lambda **_kwargs: [
            {
                "service_id": "homelab-api",
                "service_name": "homelab-api",
                "env": "dev",
                "namespace": "homelab-api",
                "app_label": "homelab-api",
                "argo_app_name": "homelab-api-dev",
                "source": "cluster_services",
                "source_ref": "kubernetes_api",
                "last_synced_at": "2026-03-07T00:00:00+00:00",
            }
        ],
    )
    monkeypatch.setattr(
        "app.main._load_deployment_metric_snapshots",
        lambda *_args, **_kwargs: ({}, "none"),
    )

    response = client.get(
        "/deployments/dep-123",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "dep-123"
    assert body["serviceId"] == "homelab-api"
    assert body["status"] == "live"
    assert body["deployedAt"] == "2026-03-09T20:03:00Z"
    assert body["healthStatus"] == "healthy"


def test_releases_endpoint_returns_traceability_rows(client, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.main._load_project_rows",
        lambda: [{"service_id": "homelab-api", "env": "dev"}],
    )
    monkeypatch.setattr("app.main._load_service_rows", lambda **_kwargs: [])
    monkeypatch.setattr("app.main._load_live_service_runtime_rows", lambda _row: [])
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


def test_releases_endpoint_supports_service_filter(client, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.main._load_project_rows",
        lambda: [
            {"service_id": "homelab-api", "env": "dev"},
            {"service_id": "homelab-web", "env": "dev"},
        ],
    )
    monkeypatch.setattr("app.main._load_service_rows", lambda **_kwargs: [])
    monkeypatch.setattr("app.main._load_live_service_runtime_rows", lambda _row: [])
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


def test_releases_endpoint_falls_back_to_live_runtime_metadata(client, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.main._load_project_rows",
        lambda: [
            {"service_id": "homelab-api", "service_name": "homelab-api", "env": "dev"}
        ],
    )
    monkeypatch.setattr(
        "app.main._load_service_rows",
        lambda **_kwargs: [
            {
                "service_id": "homelab-api",
                "service_name": "homelab-api",
                "env": "dev",
                "namespace": "homelab-api",
                "app_label": "homelab-api",
                "argo_app_name": "homelab-api-dev",
                "source": "cluster_services",
                "source_ref": "kubernetes_api",
                "last_synced_at": None,
            }
        ],
    )
    monkeypatch.setattr("app.main.load_ci_metadata_rows", lambda: [])
    monkeypatch.setattr("app.main.load_argo_metadata_rows", lambda: [])
    monkeypatch.setattr(
        "app.main._load_live_service_runtime_rows",
        lambda _row: [
            {
                "serviceId": "homelab-api",
                "env": "dev",
                "commitSha": None,
                "imageRef": "ghcr.io/wlodzimierrr/homelab-api:sha-live123",
                "deployedAt": "2026-03-09T10:00:00Z",
                "argo": {
                    "appName": "homelab-api-dev",
                    "syncStatus": "synced",
                    "healthStatus": "healthy",
                    "revision": "abcdef1234567890",
                },
                "drift": {
                    "isDrifted": False,
                    "expectedRevision": None,
                    "liveRevision": "abcdef1234567890",
                },
            }
        ],
    )

    response = client.get(
        "/releases?env=dev&limit=50",
        headers={"Authorization": "Bearer dev-static-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    row = body[0]
    assert row["commitSha"] == "abcdef1234567890"
    assert row["imageRef"] == "ghcr.io/wlodzimierrr/homelab-api:sha-live123"
    assert row["deployedAt"] == "2026-03-09T10:00:00Z"
    assert row["argo"]["appName"] == "homelab-api-dev"
    assert row["argo"]["syncStatus"] == "synced"
    assert row["argo"]["healthStatus"] == "healthy"
    assert row["argo"]["revision"] == "abcdef1234567890"


def test_release_dashboard_compat_endpoint_available(client, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.main._load_project_rows",
        lambda: [{"service_id": "homelab-api", "env": "dev"}],
    )
    monkeypatch.setattr("app.main._load_service_rows", lambda **_kwargs: [])
    monkeypatch.setattr("app.main._load_live_service_runtime_rows", lambda _row: [])
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
