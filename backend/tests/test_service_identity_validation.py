from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.main import CreateDeploymentRecordRequest
from app.service_identity_validation import build_service_identity_diagnostics


def test_build_service_identity_diagnostics_reports_no_drift_for_canonical_rows() -> None:
    diagnostics = build_service_identity_diagnostics(
        project_rows=[
            {
                "project_id": "homelab-api",
                "project_name": "homelab-api",
                "env": "dev",
                "namespace": "homelab-api",
                "app_label": "homelab-api",
                "source_ref": "repo@sha:apps/homelab-api/envs/dev",
                "observability_mode": "app-native",
            }
        ],
        service_rows=[
            {
                "service_id": "homelab-api",
                "service_name": "homelab-api",
                "env": "dev",
                "namespace": "homelab-api",
                "app_label": "homelab-api",
                "argo_app_name": "homelab-api-dev",
                "source": "cluster_services",
                "source_ref": "kubernetes_api",
                "last_synced_at": "2026-03-11T00:00:00Z",
            }
        ],
        ci_rows=[
            {
                "serviceId": "homelab-api",
                "serviceName": "homelab-api",
                "env": "dev",
                "expectedRevision": "abc123",
                "expectedImageRef": "ghcr.io/wlodzimierrr/homelab-api:sha-abc123",
            }
        ],
        argo_rows=[
            {
                "serviceId": "homelab-api",
                "serviceName": "homelab-api",
                "env": "dev",
                "appName": "homelab-api-dev",
                "expectedRevision": "abc123",
                "revision": "abc123",
                "liveRevision": "abc123",
                "imageRef": "ghcr.io/wlodzimierrr/homelab-api:sha-abc123",
                "syncStatus": "Synced",
                "healthStatus": "Healthy",
            }
        ],
        env_filter="dev",
        service_id_filter=None,
    )

    assert diagnostics["driftCount"] == 0
    assert diagnostics["okCount"] == 1
    assert diagnostics["rows"][0]["violations"] == []
    assert diagnostics["rows"][0]["observabilityMode"] == "app-native"


def test_build_service_identity_diagnostics_reports_multiple_drift_vectors() -> None:
    diagnostics = build_service_identity_diagnostics(
        project_rows=[
            {
                "project_id": "homelab-api",
                "project_name": "homelab-api",
                "env": "dev",
                "namespace": "homelab-api",
                "app_label": "homelab-api",
                "source_ref": "repo@sha:apps/homelab-api/envs/dev",
                "observability_mode": "ingress-derived",
            }
        ],
        service_rows=[
            {
                "service_id": "homelab-api",
                "service_name": "homelab-api",
                "env": "dev",
                "namespace": "default",
                "app_label": "portal-api",
                "argo_app_name": "portal-api-dev",
                "source": "cluster_services",
                "source_ref": "kubernetes_api",
                "last_synced_at": "2026-03-11T00:00:00Z",
            }
        ],
        ci_rows=[],
        argo_rows=[],
        env_filter="dev",
        service_id_filter=None,
    )

    assert diagnostics["driftCount"] == 1
    assert diagnostics["driftKeys"] == ["homelab-api|dev"]
    assert "namespace_mismatch" in diagnostics["rows"][0]["violations"]
    assert "gitops_app_label_mismatch" in diagnostics["rows"][0]["violations"]
    assert "argo_app_mismatch" in diagnostics["rows"][0]["violations"]
    assert diagnostics["rows"][0]["observabilityMode"] == "ingress-derived"


def test_build_service_identity_diagnostics_ignores_unknown_release_app_and_support_service() -> None:
    diagnostics = build_service_identity_diagnostics(
        project_rows=[
            {
                "project_id": "homelab-web",
                "project_name": "homelab-web",
                "env": "dev",
                "namespace": "homelab-web",
                "app_label": "homelab-web",
                "source_ref": "repo@sha:apps/homelab-web/envs/dev",
                "observability_mode": "ingress-derived",
            },
            {
                "project_id": "oauth2-proxy",
                "project_name": "OAuth2 Proxy",
                "env": "dev",
                "namespace": "homelab-web",
                "app_label": "oauth2-proxy",
                "source_ref": "repo@sha:apps/homelab-web/envs/dev/oauth2-proxy.yaml",
                "observability_mode": "no-http",
            }
        ],
        service_rows=[
            {
                "service_id": "homelab-web",
                "service_name": "homelab-web",
                "env": "dev",
                "namespace": "homelab-web",
                "app_label": "homelab-web",
                "argo_app_name": "homelab-web-dev",
                "source": "cluster_services",
                "source_ref": "kubernetes_api",
                "last_synced_at": "2026-03-11T00:00:00Z",
            },
            {
                "service_id": "oauth2-proxy",
                "service_name": "oauth2-proxy",
                "env": "dev",
                "namespace": "homelab-web",
                "app_label": "oauth2-proxy",
                "argo_app_name": "homelab-web-dev",
                "source": "cluster_services",
                "source_ref": "kubernetes_api",
                "last_synced_at": "2026-03-11T00:00:00Z",
            },
        ],
        ci_rows=[],
        argo_rows=[
            {
                "serviceId": "homelab-web",
                "serviceName": "homelab-web",
                "env": "dev",
                "appName": "unknown",
                "revision": "abc123",
                "liveRevision": "abc123",
                "syncStatus": "Synced",
                "healthStatus": "Healthy",
            }
        ],
        env_filter="dev",
        service_id_filter=None,
    )

    assert diagnostics["driftCount"] == 0
    assert diagnostics["okCount"] == 2
    assert diagnostics["rows"][0]["violations"] == []
    assert diagnostics["rows"][1]["violations"] == []
    assert diagnostics["rows"][1]["observabilityMode"] == "no-http"


def test_build_service_identity_diagnostics_reports_missing_observability_mode() -> None:
    diagnostics = build_service_identity_diagnostics(
        project_rows=[
            {
                "project_id": "homelab-api",
                "project_name": "homelab-api",
                "env": "dev",
                "namespace": "homelab-api",
                "app_label": "homelab-api",
                "source_ref": "repo@sha:apps/homelab-api/envs/dev",
            }
        ],
        service_rows=[
            {
                "service_id": "homelab-api",
                "service_name": "homelab-api",
                "env": "dev",
                "namespace": "homelab-api",
                "app_label": "homelab-api",
                "argo_app_name": "homelab-api-dev",
                "source": "cluster_services",
                "source_ref": "kubernetes_api",
                "last_synced_at": "2026-03-11T00:00:00Z",
            }
        ],
        ci_rows=[],
        argo_rows=[],
        env_filter="dev",
        service_id_filter=None,
    )

    assert diagnostics["driftCount"] == 1
    assert diagnostics["rows"][0]["observabilityMode"] is None
    assert "observability_mode_missing" in diagnostics["rows"][0]["violations"]


def test_create_deployment_record_request_rejects_noncanonical_service_id() -> None:
    with pytest.raises(ValidationError, match="canonical lowercase-hyphen identity"):
        CreateDeploymentRecordRequest(
            serviceId="Homelab API",
            env="dev",
            action="deploy",
            status="pending",
        )
