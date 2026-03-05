from app.release_traceability import (
    build_release_join_diagnostics,
    build_release_traceability_rows,
    compute_is_drifted,
)


def test_compute_is_drifted_true_for_out_of_sync() -> None:
    assert (
        compute_is_drifted(
            sync_status="out_of_sync",
            expected_revision=None,
            live_revision=None,
            expected_image_ref=None,
            live_image_ref=None,
        )
        is True
    )


def test_compute_is_drifted_true_for_revision_mismatch() -> None:
    assert (
        compute_is_drifted(
            sync_status="synced",
            expected_revision="abc",
            live_revision="def",
            expected_image_ref=None,
            live_image_ref=None,
        )
        is True
    )


def test_compute_is_drifted_false_when_missing_comparison_values() -> None:
    assert (
        compute_is_drifted(
            sync_status="synced",
            expected_revision="abc",
            live_revision=None,
            expected_image_ref=None,
            live_image_ref=None,
        )
        is False
    )


def test_build_release_rows_supports_filters_and_unknowns() -> None:
    rows = build_release_traceability_rows(
        project_rows=[
            {"service_id": "homelab-api", "env": "dev"},
            {"service_id": "homelab-web", "env": "prod"},
        ],
        ci_rows=[
            {
                "serviceId": "homelab-api",
                "env": "dev",
                "commitSha": "abc123",
                "imageRef": "ghcr.io/x/api:v1",
                "expectedRevision": "abc123",
            }
        ],
        argo_rows=[],
        env_filter="dev",
        service_id_filter=None,
        limit=50,
    )

    assert len(rows) == 1
    assert rows[0]["serviceId"] == "homelab-api"
    assert rows[0]["argo"]["syncStatus"] == "unknown"
    assert rows[0]["drift"]["isDrifted"] is False


def test_build_release_rows_marks_drift_with_mismatch() -> None:
    rows = build_release_traceability_rows(
        project_rows=[{"service_id": "homelab-api", "env": "dev"}],
        ci_rows=[
            {
                "serviceId": "homelab-api",
                "env": "dev",
                "commitSha": "abc123",
                "expectedRevision": "abc123",
            }
        ],
        argo_rows=[
            {
                "serviceId": "homelab-api",
                "env": "dev",
                "syncStatus": "synced",
                "healthStatus": "healthy",
                "revision": "def456",
            }
        ],
        env_filter=None,
        service_id_filter=None,
        limit=50,
    )

    assert len(rows) == 1
    assert rows[0]["drift"]["isDrifted"] is True
    assert rows[0]["drift"]["expectedRevision"] == "abc123"
    assert rows[0]["drift"]["liveRevision"] == "def456"


def test_build_release_rows_maps_upstream_service_name_to_canonical_service_id() -> None:
    rows = build_release_traceability_rows(
        project_rows=[{"service_id": "portal-project", "service_name": "Portal Project", "env": "dev"}],
        ci_rows=[
            {
                "serviceId": "Portal Project",
                "env": "dev",
                "commitSha": "abc123",
                "expectedRevision": "abc123",
            }
        ],
        argo_rows=[],
        env_filter=None,
        service_id_filter=None,
        limit=50,
    )

    assert len(rows) == 1
    assert rows[0]["serviceId"] == "portal-project"
    assert rows[0]["commitSha"] == "abc123"


def test_build_release_rows_logs_unmatched_upstream_keys(caplog) -> None:
    caplog.set_level("WARNING")
    rows = build_release_traceability_rows(
        project_rows=[{"service_id": "homelab-api", "service_name": "homelab-api", "env": "dev"}],
        ci_rows=[{"serviceId": "portal-project", "serviceName": "Portal Project", "env": "dev"}],
        argo_rows=[],
        env_filter=None,
        service_id_filter=None,
        limit=50,
    )

    assert len(rows) == 1
    assert "release_join_mismatch source=ci key=portal-project|Portal Project|dev" in caplog.text


def test_build_release_join_diagnostics_reports_unmatched_keys() -> None:
    diagnostics = build_release_join_diagnostics(
        project_rows=[{"service_id": "homelab-api", "service_name": "Homelab API", "env": "dev"}],
        ci_rows=[{"serviceId": "portal-project", "serviceName": "Portal Project", "env": "dev"}],
        argo_rows=[{"serviceId": "portal-project", "serviceName": "Portal Project", "env": "dev"}],
        env_filter="dev",
        service_id_filter=None,
    )

    assert diagnostics["ciUnmatchedCount"] == 1
    assert diagnostics["argoUnmatchedCount"] == 1
    assert diagnostics["ciUnmatchedKeys"] == ["portal-project|Portal Project|dev"]
    assert diagnostics["argoUnmatchedKeys"] == ["portal-project|Portal Project|dev"]
