from app.catalog_reconciliation import build_catalog_join


def test_build_catalog_join_matches_primary_and_one_to_many() -> None:
    result = build_catalog_join(
        project_rows=[
            {
                "project_id": "homelab-api",
                "project_name": "Homelab API",
                "env": "dev",
                "namespace": "homelab-api",
                "app_label": "homelab-api",
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
            },
            {
                "service_id": "homelab-api-admin",
                "service_name": "homelab-api-admin",
                "env": "dev",
                "namespace": "homelab-api",
                "app_label": "homelab-api",
                "argo_app_name": "homelab-api-dev",
            },
        ],
    )

    assert len(result["rows"]) == 1
    row = result["rows"][0]
    assert row["projectId"] == "homelab-api"
    assert row["joinSource"] == "primary_key"
    assert row["primaryServiceId"] == "homelab-api"
    assert row["serviceCount"] == 2
    assert row["serviceIds"] == ["homelab-api", "homelab-api-admin"]
    assert result["diagnostics"]["oneToManyCount"] == 1
    assert result["diagnostics"]["projectOnlyCount"] == 0


def test_build_catalog_join_reports_project_only_and_service_only() -> None:
    result = build_catalog_join(
        project_rows=[
            {
                "project_id": "portal-project",
                "project_name": "Portal Project",
                "env": "dev",
                "namespace": "portal",
                "app_label": "portal-project",
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
            }
        ],
    )

    assert result["rows"][0]["joinSource"] == "unmatched"
    assert result["rows"][0]["primaryServiceId"] is None
    assert result["diagnostics"]["projectOnlyCount"] == 1
    assert result["diagnostics"]["serviceOnlyCount"] == 1
    assert result["diagnostics"]["projectOnlyKeys"] == ["portal-project|Portal Project|dev"]
    assert result["diagnostics"]["serviceOnlyKeys"] == ["homelab-api|homelab-api|dev"]
