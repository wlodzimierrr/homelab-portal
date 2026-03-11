from __future__ import annotations

from typing import Any, TypedDict

from app.catalog_reconciliation import build_catalog_join
from app.release_traceability import (
    ArgoMetadataRow,
    CiMetadataRow,
    ProjectRow,
    build_release_traceability_rows,
)
from app.service_identity import default_argo_app_name, normalize_service_id, parse_source_ref_path


class ServiceIdentityMonitoringSelector(TypedDict):
    namespace: str
    appLabel: str


class ServiceIdentityDriftRow(TypedDict):
    serviceId: str
    env: str
    projectId: str | None
    catalogLinked: bool
    namespace: str
    expectedNamespace: str | None
    appLabel: str
    expectedAppLabel: str | None
    argoAppName: str | None
    expectedArgoAppName: str | None
    releaseArgoAppName: str | None
    gitopsPath: str | None
    expectedGitopsPath: str | None
    monitoringSelector: ServiceIdentityMonitoringSelector
    violations: list[str]


class ServiceIdentityDiagnostics(TypedDict):
    driftCount: int
    okCount: int
    driftKeys: list[str]
    rows: list[ServiceIdentityDriftRow]


def build_service_identity_diagnostics(
    *,
    project_rows: list[dict[str, Any]],
    service_rows: list[dict[str, Any]],
    ci_rows: list[CiMetadataRow],
    argo_rows: list[ArgoMetadataRow],
    env_filter: str | None = None,
    service_id_filter: str | None = None,
) -> ServiceIdentityDiagnostics:
    filtered_project_rows = [
        row
        for row in project_rows
        if (not env_filter or str(row.get("env") or "").strip() == env_filter)
        and (not service_id_filter or str(row.get("project_id") or "").strip() == service_id_filter)
    ]
    filtered_service_rows = [
        row
        for row in service_rows
        if (not env_filter or str(row.get("env") or "").strip() == env_filter)
        and (not service_id_filter or str(row.get("service_id") or "").strip() == service_id_filter)
    ]

    catalog_join = build_catalog_join(
        project_rows=filtered_project_rows,
        service_rows=filtered_service_rows,
        env_filter=env_filter,
        project_id_filter=service_id_filter,
        service_id_filter=service_id_filter,
    )
    project_by_service_key: dict[tuple[str, str], dict[str, Any]] = {}
    for project_row in filtered_project_rows:
        key = (
            str(project_row.get("project_id") or "").strip(),
            str(project_row.get("env") or "").strip(),
        )
        project_by_service_key[key] = project_row
    for row in catalog_join["rows"]:
        project_row = project_by_service_key.get((row["projectId"], row["env"]))
        if project_row is None:
            continue
        for service_ref in row["services"]:
            project_by_service_key.setdefault((service_ref["serviceId"], row["env"]), project_row)

    release_traceability_rows = build_release_traceability_rows(
        project_rows=[
            ProjectRow(
                service_id=str(row.get("project_id") or "").strip(),
                service_name=str(row.get("project_name") or "").strip(),
                env=str(row.get("env") or "").strip(),
            )
            for row in filtered_project_rows
        ],
        ci_rows=ci_rows,
        argo_rows=argo_rows,
        env_filter=env_filter,
        service_id_filter=service_id_filter,
        limit=max(len(filtered_project_rows) + len(filtered_service_rows), 20),
    )
    release_traceability_index = {
        (str(row.get("serviceId") or "").strip(), str(row.get("env") or "").strip()): row
        for row in release_traceability_rows
    }

    rows: list[ServiceIdentityDriftRow] = []
    drift_keys: list[str] = []

    for row in sorted(
        filtered_service_rows,
        key=lambda item: (
            str(item.get("service_id") or "").strip(),
            str(item.get("env") or "").strip(),
        ),
    ):
        service_id = str(row.get("service_id") or "").strip()
        env = str(row.get("env") or "").strip()
        namespace = str(row.get("namespace") or "").strip()
        app_label = str(row.get("app_label") or "").strip()
        argo_app_name = (
            str(row.get("argo_app_name") or "").strip()
            if isinstance(row.get("argo_app_name"), str)
            else None
        )
        project_row = project_by_service_key.get((service_id, env))
        expected_namespace = (
            str(project_row.get("namespace") or "").strip() if project_row is not None else None
        )
        expected_app_label = (
            str(project_row.get("app_label") or "").strip() if project_row is not None else None
        )
        expected_argo_app_name = (
            default_argo_app_name(service_id, env) if project_row is not None else None
        )
        expected_gitops_path = (
            f"apps/{str(project_row.get('project_id') or '').strip()}/envs/{env}"
            if project_row is not None
            else None
        )
        gitops_path = (
            parse_source_ref_path(project_row.get("source_ref")) if project_row is not None else None
        )
        release_row = release_traceability_index.get((service_id, env))
        release_argo_app_name = None
        if isinstance(release_row, dict):
            argo_state = release_row.get("argo")
            if isinstance(argo_state, dict) and isinstance(argo_state.get("appName"), str):
                release_argo_app_name = str(argo_state.get("appName")).strip() or None

        violations: list[str] = []
        if service_id != normalize_service_id(service_id):
            violations.append("service_id_not_canonical")
        if not namespace:
            violations.append("missing_namespace")
        if not app_label:
            violations.append("missing_app_label")
        if app_label and normalize_service_id(app_label) != service_id:
            violations.append("app_label_mismatch")
        if project_row is not None:
            if expected_namespace and namespace != expected_namespace:
                violations.append("namespace_mismatch")
            if expected_app_label and app_label != expected_app_label:
                violations.append("gitops_app_label_mismatch")
            if not argo_app_name:
                violations.append("missing_argo_app_name")
            elif expected_argo_app_name and argo_app_name != expected_argo_app_name:
                violations.append("argo_app_mismatch")
            if expected_gitops_path and gitops_path != expected_gitops_path:
                violations.append("gitops_path_mismatch")
        else:
            violations.append("catalog_link_missing")
        if release_argo_app_name and argo_app_name and release_argo_app_name != argo_app_name:
            violations.append("release_join_argo_app_mismatch")

        drift_row: ServiceIdentityDriftRow = {
            "serviceId": service_id,
            "env": env,
            "projectId": str(project_row.get("project_id") or "").strip() if project_row is not None else None,
            "catalogLinked": project_row is not None,
            "namespace": namespace,
            "expectedNamespace": expected_namespace,
            "appLabel": app_label,
            "expectedAppLabel": expected_app_label,
            "argoAppName": argo_app_name,
            "expectedArgoAppName": expected_argo_app_name,
            "releaseArgoAppName": release_argo_app_name,
            "gitopsPath": gitops_path,
            "expectedGitopsPath": expected_gitops_path,
            "monitoringSelector": {
                "namespace": namespace,
                "appLabel": app_label,
            },
            "violations": violations,
        }
        rows.append(drift_row)
        if violations:
            drift_keys.append(f"{service_id}|{env}")

    return {
        "driftCount": len(drift_keys),
        "okCount": len(rows) - len(drift_keys),
        "driftKeys": drift_keys,
        "rows": rows,
    }
