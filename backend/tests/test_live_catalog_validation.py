from __future__ import annotations

import pytest

from scripts.live_catalog_validation import (
    _is_legacy_project_row,
    _select_service_id,
    _validate_alerts,
    _validate_freshness,
    _validate_metrics,
    _validate_projects,
    _validate_releases,
    _validate_services,
    build_validation_report,
)


def test_validate_projects_rejects_legacy_seeded_rows() -> None:
    payload = {
        "projects": [
            {"id": "proj-dev", "name": "Homelab App", "environment": "dev"},
        ]
    }

    with pytest.raises(SystemExit, match="legacy project rows"):
        _validate_projects(payload)


def test_validate_freshness_rejects_stale_state() -> None:
    payload = {
        "freshness": {
            "rowCount": 3,
            "lastSyncedAt": "2026-03-06T00:00:00+00:00",
            "warningAfterMinutes": 20,
            "staleAfterMinutes": 30,
            "isEmpty": False,
            "isWarning": True,
            "isStale": True,
            "state": "stale",
        }
    }

    with pytest.raises(SystemExit, match="stale"):
        _validate_freshness(payload, endpoint_name="/service-registry/diagnostics")


def test_validate_releases_requires_project_and_service_matches() -> None:
    projects = {
        ("homelab-api", "dev"): {"id": "homelab-api", "environment": "dev"},
    }
    services = {
        ("homelab-api", "dev"): {"serviceId": "homelab-api", "env": "dev"},
    }
    payload = [{"serviceId": "homelab-api", "env": "dev"}]

    service_ids, summary = _validate_releases(
        payload,
        project_index=projects,
        service_index=services,
    )

    assert service_ids == ["homelab-api"]
    assert summary["count"] == 1


def test_validate_metrics_and_alerts_require_healthy_providers() -> None:
    metrics = {
        "serviceId": "homelab-api",
        "providerStatus": {"provider": "prometheus", "status": "healthy"},
        "noData": {"uptimePct": False, "p95LatencyMs": True},
    }
    alerts = {
        "alerts": [],
        "providerStatus": {"provider": "alertmanager", "status": "healthy"},
    }

    metrics_summary = _validate_metrics(metrics, service_id="homelab-api")
    alerts_summary = _validate_alerts(alerts)

    assert metrics_summary["provider"] == "prometheus"
    assert metrics_summary["noDataFields"] == 1
    assert alerts_summary["provider"] == "alertmanager"
    assert alerts_summary["count"] == 0


def test_select_service_id_prefers_release_match() -> None:
    selected = _select_service_id(
        "",
        release_service_ids=["homelab-api"],
        service_index={("homelab-api", "dev"): {"serviceId": "homelab-api", "env": "dev"}},
        env="dev",
    )

    assert selected == "homelab-api"


def test_build_validation_report_includes_warning_entries() -> None:
    report = build_validation_report(
        env="dev",
        service_id="homelab-api",
        project_result={"count": 2, "legacyRows": 0},
        project_diagnostics={"state": "warning", "rowCount": 2, "warning": True, "lastSyncedAt": "x"},
        service_result={"count": 2},
        service_diagnostics={"state": "fresh", "rowCount": 2, "warning": False, "lastSyncedAt": "x"},
        release_result={"count": 2},
        metrics_result={"provider": "prometheus", "status": "healthy", "noDataFields": 0},
        alerts_result={"provider": "alertmanager", "status": "healthy", "count": 0},
    )

    assert report["status"] == "pass"
    assert report["serviceId"] == "homelab-api"
    assert report["warnings"] == ["Project catalog freshness is in warning state."]


def test_is_legacy_project_row_matches_seeded_markers() -> None:
    assert _is_legacy_project_row({"id": "proj-dev", "name": "Whatever"}) is True
    assert _is_legacy_project_row({"id": "api", "name": "Homelab App"}) is True
    assert _is_legacy_project_row({"id": "homelab-api", "name": "Homelab API"}) is False


def test_validate_services_rejects_empty_response() -> None:
    with pytest.raises(SystemExit, match="zero rows"):
        _validate_services({"services": []})
