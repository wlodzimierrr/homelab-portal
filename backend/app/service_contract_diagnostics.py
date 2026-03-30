from __future__ import annotations

from typing import Any, Literal, TypedDict

from app.catalog_reconciliation import build_catalog_join
from app.release_traceability import (
    ArgoMetadataRow,
    CiMetadataRow,
    ProjectRow,
    ReleaseTraceabilityProbe,
    build_release_traceability_probe,
)
from app.service_identity import normalize_service_id
from app.service_identity_validation import build_service_identity_diagnostics
from app.service_observability import (
    ObservabilityAuthority,
    ObservabilityMode,
    observability_metrics_authority,
)


LikelyReason = Literal[
    "service_registry_row_missing",
    "project_catalog_row_missing",
    "project_registry_row_missing",
    "catalog_join_unlinked",
    "identity_drift",
    "ci_metadata_missing",
    "argo_metadata_missing",
    "release_join_missing_project_mapping",
    "release_metadata_missing",
]


class ServiceContractRegistryDiagnostics(TypedDict):
    serviceRowFound: bool
    serviceName: str | None
    namespace: str | None
    appLabel: str | None
    argoAppName: str | None
    sourceRef: str | None
    projectId: str | None
    projectCatalogRowFound: bool
    projectName: str | None
    projectObservabilityMode: ObservabilityMode | None
    expectedMetricsSource: ObservabilityAuthority | None
    projectRegistryRowFound: bool
    catalogLinked: bool


class ServiceContractMetadataDiagnostics(TypedDict):
    release: ReleaseTraceabilityProbe
    ciPresent: bool
    argoPresent: bool


class ServiceContractDiagnostics(TypedDict):
    serviceId: str
    canonicalServiceId: str
    env: str
    registry: ServiceContractRegistryDiagnostics
    identity: dict[str, Any]
    metadata: ServiceContractMetadataDiagnostics
    likelyReasons: list[LikelyReason]


def _matching_project_context_row(
    rows: list[dict[str, Any]],
    *,
    service_id: str,
) -> dict[str, Any] | None:
    service_key = service_id.strip()
    for row in rows:
        service_ids = row.get("serviceIds")
        if isinstance(service_ids, list) and service_key in {
            str(candidate).strip() for candidate in service_ids
        }:
            return row
    return None


def build_service_contract_diagnostics(
    *,
    service_id: str,
    env: str,
    service_rows: list[dict[str, Any]],
    project_catalog_rows: list[dict[str, Any]],
    project_registry_rows: list[dict[str, Any]],
    ci_rows: list[CiMetadataRow],
    argo_rows: list[ArgoMetadataRow],
) -> ServiceContractDiagnostics:
    canonical_service_id = normalize_service_id(service_id)
    service_row = next(
        (
            row
            for row in service_rows
            if str(row.get("service_id") or "").strip() == service_id
            and str(row.get("env") or "").strip() == env
        ),
        None,
    )
    project_catalog_row = next(
        (
            row
            for row in project_catalog_rows
            if str(row.get("project_id") or "").strip() == service_id
            and str(row.get("env") or "").strip() == env
        ),
        None,
    )
    project_registry_row = next(
        (
            row
            for row in project_registry_rows
            if str(row.get("service_id") or "").strip() == service_id
            and str(row.get("env") or "").strip() == env
        ),
        None,
    )

    catalog_join = build_catalog_join(
        project_rows=project_catalog_rows,
        service_rows=service_rows,
        env_filter=env,
        project_id_filter=None,
        service_id_filter=service_id,
    )
    linked_catalog_row = _matching_project_context_row(catalog_join["rows"], service_id=service_id)

    identity_diagnostics = build_service_identity_diagnostics(
        project_rows=project_catalog_rows,
        service_rows=service_rows,
        ci_rows=ci_rows,
        argo_rows=argo_rows,
        env_filter=env,
        service_id_filter=service_id,
    )
    identity_row = identity_diagnostics["rows"][0] if identity_diagnostics["rows"] else None

    release_probe = build_release_traceability_probe(
        project_rows=[
            ProjectRow(
                service_id=str(row.get("service_id") or "").strip(),
                service_name=str(row.get("service_name") or "").strip(),
                env=str(row.get("env") or "").strip(),
            )
            for row in project_registry_rows
        ],
        ci_rows=ci_rows,
        argo_rows=argo_rows,
        service_id=service_id,
        env=env,
    )

    observability_mode = (
        project_catalog_row.get("observability_mode")
        if isinstance(project_catalog_row, dict)
        else None
    )
    expected_metrics_source = observability_metrics_authority(
        observability_mode if isinstance(observability_mode, str) else None
    )

    likely_reasons: list[LikelyReason] = []
    if service_row is None:
        likely_reasons.append("service_registry_row_missing")
    if project_catalog_row is None:
        likely_reasons.append("project_catalog_row_missing")
    if project_registry_row is None:
        likely_reasons.append("project_registry_row_missing")
    if linked_catalog_row is None:
        likely_reasons.append("catalog_join_unlinked")
    if identity_row and identity_row.get("violations"):
        likely_reasons.append("identity_drift")
    if release_probe["ci"]["status"] == "missing":
        likely_reasons.append("ci_metadata_missing")
    if release_probe["argo"]["status"] == "missing":
        likely_reasons.append("argo_metadata_missing")
    if release_probe["joinRowPresent"] is False and project_registry_row is None:
        likely_reasons.append("release_join_missing_project_mapping")
    elif release_probe["joinHasMeaningfulMetadata"] is False:
        likely_reasons.append("release_metadata_missing")

    deduped_reasons: list[LikelyReason] = []
    for reason in likely_reasons:
        if reason not in deduped_reasons:
            deduped_reasons.append(reason)

    return {
        "serviceId": service_id,
        "canonicalServiceId": canonical_service_id,
        "env": env,
        "registry": {
            "serviceRowFound": service_row is not None,
            "serviceName": str(service_row.get("service_name") or "").strip() or None
            if service_row
            else None,
            "namespace": str(service_row.get("namespace") or "").strip() or None
            if service_row
            else None,
            "appLabel": str(service_row.get("app_label") or "").strip() or None
            if service_row
            else None,
            "argoAppName": str(service_row.get("argo_app_name") or "").strip() or None
            if service_row
            else None,
            "sourceRef": str(service_row.get("source_ref") or "").strip() or None
            if service_row
            else None,
            "projectId": str(service_row.get("project_id") or "").strip() or None
            if service_row
            else None,
            "projectCatalogRowFound": project_catalog_row is not None,
            "projectName": str(project_catalog_row.get("project_name") or "").strip() or None
            if project_catalog_row
            else None,
            "projectObservabilityMode": observability_mode
            if isinstance(observability_mode, str)
            else None,
            "expectedMetricsSource": expected_metrics_source,
            "projectRegistryRowFound": project_registry_row is not None,
            "catalogLinked": linked_catalog_row is not None,
        },
        "identity": identity_row or {
            "serviceId": service_id,
            "env": env,
            "violations": ["service_identity_row_missing"],
        },
        "metadata": {
            "release": release_probe,
            "ciPresent": release_probe["ci"]["status"] == "matched",
            "argoPresent": release_probe["argo"]["status"] == "matched",
        },
        "likelyReasons": deduped_reasons,
    }
