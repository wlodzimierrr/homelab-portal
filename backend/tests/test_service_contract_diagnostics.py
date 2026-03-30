from app.service_contract_diagnostics import build_service_contract_diagnostics


def test_build_service_contract_diagnostics_reports_missing_release_metadata() -> None:
    diagnostics = build_service_contract_diagnostics(
        service_id="portfolio-next",
        env="dev",
        service_rows=[
            {
                "service_id": "portfolio-next",
                "service_name": "Portfolio Next",
                "env": "dev",
                "namespace": "portfolio-next",
                "app_label": "portfolio-next",
                "argo_app_name": "portfolio-next-dev",
                "source_ref": "kubernetes_api",
                "project_id": "portfolio-next",
            }
        ],
        project_catalog_rows=[
            {
                "project_id": "portfolio-next",
                "project_name": "Portfolio Next",
                "env": "dev",
                "namespace": "portfolio-next",
                "app_label": "portfolio-next",
                "source_ref": "repo@sha:apps/portfolio-next/envs/dev",
                "observability_mode": "app-native",
            }
        ],
        project_registry_rows=[
            {
                "service_id": "portfolio-next",
                "service_name": "Portfolio Next",
                "env": "dev",
            }
        ],
        ci_rows=[],
        argo_rows=[],
    )

    assert diagnostics["serviceId"] == "portfolio-next"
    assert diagnostics["registry"]["projectObservabilityMode"] == "app-native"
    assert diagnostics["registry"]["expectedMetricsSource"] == "app"
    assert diagnostics["metadata"]["ciPresent"] is False
    assert diagnostics["metadata"]["argoPresent"] is False
    assert "ci_metadata_missing" in diagnostics["likelyReasons"]
    assert "argo_metadata_missing" in diagnostics["likelyReasons"]
    assert "release_metadata_missing" in diagnostics["likelyReasons"]


def test_build_service_contract_diagnostics_reports_missing_project_mapping() -> None:
    diagnostics = build_service_contract_diagnostics(
        service_id="portfolio-next",
        env="dev",
        service_rows=[
            {
                "service_id": "portfolio-next",
                "service_name": "Portfolio Next",
                "env": "dev",
                "namespace": "portfolio-next",
                "app_label": "portfolio-next",
                "argo_app_name": "portfolio-next-dev",
                "source_ref": "kubernetes_api",
                "project_id": None,
            }
        ],
        project_catalog_rows=[],
        project_registry_rows=[],
        ci_rows=[
            {
                "serviceId": "portfolio-next",
                "serviceName": "Portfolio Next",
                "env": "dev",
                "commitSha": "abc123",
            }
        ],
        argo_rows=[
            {
                "serviceId": "portfolio-next",
                "serviceName": "Portfolio Next",
                "env": "dev",
                "appName": "portfolio-next-dev",
                "revision": "abc123",
            }
        ],
    )

    assert diagnostics["registry"]["projectCatalogRowFound"] is False
    assert diagnostics["registry"]["projectRegistryRowFound"] is False
    assert diagnostics["registry"]["catalogLinked"] is False
    assert diagnostics["metadata"]["ciPresent"] is True
    assert diagnostics["metadata"]["argoPresent"] is True
    assert "project_catalog_row_missing" in diagnostics["likelyReasons"]
    assert "project_registry_row_missing" in diagnostics["likelyReasons"]
    assert "release_join_missing_project_mapping" in diagnostics["likelyReasons"]
