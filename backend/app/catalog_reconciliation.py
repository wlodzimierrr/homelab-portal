from __future__ import annotations

from typing import TypedDict


class CatalogProjectRow(TypedDict, total=False):
    project_id: str
    project_name: str
    env: str
    namespace: str
    app_label: str


class CatalogServiceRow(TypedDict, total=False):
    service_id: str
    service_name: str
    env: str
    namespace: str
    app_label: str
    argo_app_name: str | None


class CatalogJoinServiceRef(TypedDict):
    serviceId: str
    serviceName: str
    namespace: str
    appLabel: str
    argoAppName: str | None


class CatalogJoinRow(TypedDict):
    projectId: str
    projectName: str
    env: str
    namespace: str
    appLabel: str
    joinSource: str
    primaryServiceId: str | None
    serviceCount: int
    serviceIds: list[str]
    services: list[CatalogJoinServiceRef]


class CatalogJoinDiagnostics(TypedDict):
    projectOnlyCount: int
    serviceOnlyCount: int
    oneToManyCount: int
    ambiguousJoinCount: int
    projectOnlyKeys: list[str]
    serviceOnlyKeys: list[str]
    oneToManyKeys: list[str]
    ambiguousJoinKeys: list[str]


class CatalogJoinResult(TypedDict):
    rows: list[CatalogJoinRow]
    diagnostics: CatalogJoinDiagnostics


def _normalize(value: str | None) -> str:
    safe = (value or "").strip().lower()
    if not safe:
        return ""
    normalized = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in safe)
    normalized = "-".join(part for part in normalized.split("-") if part)
    return normalized


def _project_key(row: CatalogProjectRow) -> tuple[str, str]:
    return (
        str(row.get("project_id", "")).strip(),
        str(row.get("env", "")).strip(),
    )


def _service_key(row: CatalogServiceRow) -> tuple[str, str]:
    return (
        str(row.get("service_id", "")).strip(),
        str(row.get("env", "")).strip(),
    )


def _project_diagnostic_key(row: CatalogProjectRow) -> str:
    return "|".join(
        [
            str(row.get("project_id", "")).strip() or "unknown-project",
            str(row.get("project_name", "")).strip() or "unknown-project",
            str(row.get("env", "")).strip() or "unknown",
        ]
    )


def _service_diagnostic_key(row: CatalogServiceRow) -> str:
    return "|".join(
        [
            str(row.get("service_id", "")).strip() or "unknown-service",
            str(row.get("service_name", "")).strip() or "unknown-service",
            str(row.get("env", "")).strip() or "unknown",
        ]
    )


def _primary_join_key_for_project(row: CatalogProjectRow) -> tuple[str, str, str]:
    return (
        str(row.get("env", "")).strip(),
        str(row.get("namespace", "")).strip(),
        _normalize(str(row.get("app_label", "")).strip()),
    )


def _fallback_join_key_for_project(row: CatalogProjectRow) -> tuple[str, str]:
    return (
        str(row.get("env", "")).strip(),
        _normalize(str(row.get("project_id", "")).strip()),
    )


def _primary_join_key_for_service(row: CatalogServiceRow) -> tuple[str, str, str]:
    return (
        str(row.get("env", "")).strip(),
        str(row.get("namespace", "")).strip(),
        _normalize(str(row.get("app_label", "")).strip()),
    )


def _fallback_join_key_for_service(row: CatalogServiceRow) -> tuple[str, str]:
    return (
        str(row.get("env", "")).strip(),
        _normalize(str(row.get("service_id", "")).strip()),
    )


def _sort_service_rows(rows: list[CatalogServiceRow]) -> list[CatalogServiceRow]:
    return sorted(
        rows,
        key=lambda row: (
            str(row.get("service_id", "")).strip(),
            str(row.get("namespace", "")).strip(),
            str(row.get("service_name", "")).strip(),
        ),
    )


def _choose_primary_service_id(
    project_row: CatalogProjectRow,
    matched_services: list[CatalogServiceRow],
) -> str | None:
    if not matched_services:
        return None

    normalized_project_id = _normalize(str(project_row.get("project_id", "")).strip())
    for row in matched_services:
        service_id = str(row.get("service_id", "")).strip()
        if _normalize(service_id) == normalized_project_id:
            return service_id

    return str(_sort_service_rows(matched_services)[0].get("service_id", "")).strip() or None


def build_catalog_join(
    *,
    project_rows: list[CatalogProjectRow],
    service_rows: list[CatalogServiceRow],
    env_filter: str | None = None,
    project_id_filter: str | None = None,
    service_id_filter: str | None = None,
) -> CatalogJoinResult:
    filtered_projects = [
        row
        for row in project_rows
        if (not env_filter or row.get("env") == env_filter)
        and (not project_id_filter or row.get("project_id") == project_id_filter)
    ]
    filtered_services = [
        row
        for row in service_rows
        if (not env_filter or row.get("env") == env_filter)
        and (not service_id_filter or row.get("service_id") == service_id_filter)
    ]

    services_by_primary: dict[tuple[str, str, str], list[CatalogServiceRow]] = {}
    services_by_fallback: dict[tuple[str, str], list[CatalogServiceRow]] = {}
    for row in filtered_services:
        services_by_primary.setdefault(_primary_join_key_for_service(row), []).append(row)
        services_by_fallback.setdefault(_fallback_join_key_for_service(row), []).append(row)

    rows: list[CatalogJoinRow] = []
    matched_service_keys: set[tuple[str, str]] = set()
    service_to_projects: dict[tuple[str, str], set[str]] = {}
    project_only_keys: list[str] = []
    one_to_many_keys: list[str] = []

    for project_row in sorted(
        filtered_projects,
        key=lambda row: (
            str(row.get("project_id", "")).strip(),
            str(row.get("env", "")).strip(),
        ),
    ):
        matched_services = _sort_service_rows(
            services_by_primary.get(_primary_join_key_for_project(project_row), [])
        )
        join_source = "primary_key"
        if not matched_services:
            matched_services = _sort_service_rows(
                services_by_fallback.get(_fallback_join_key_for_project(project_row), [])
            )
            join_source = "fallback_service_id" if matched_services else "unmatched"

        service_refs: list[CatalogJoinServiceRef] = []
        for service_row in matched_services:
            key = _service_key(service_row)
            matched_service_keys.add(key)
            service_to_projects.setdefault(key, set()).add(
                str(project_row.get("project_id", "")).strip()
            )
            service_refs.append(
                {
                    "serviceId": str(service_row.get("service_id", "")).strip(),
                    "serviceName": str(service_row.get("service_name", "")).strip(),
                    "namespace": str(service_row.get("namespace", "")).strip(),
                    "appLabel": str(service_row.get("app_label", "")).strip(),
                    "argoAppName": (
                        str(service_row.get("argo_app_name", "")).strip()
                        if isinstance(service_row.get("argo_app_name"), str)
                        and str(service_row.get("argo_app_name", "")).strip()
                        else None
                    ),
                }
            )

        if not matched_services:
            project_only_keys.append(_project_diagnostic_key(project_row))
        elif len(matched_services) > 1:
            one_to_many_keys.append(_project_diagnostic_key(project_row))

        rows.append(
            {
                "projectId": str(project_row.get("project_id", "")).strip(),
                "projectName": str(project_row.get("project_name", "")).strip(),
                "env": str(project_row.get("env", "")).strip(),
                "namespace": str(project_row.get("namespace", "")).strip(),
                "appLabel": str(project_row.get("app_label", "")).strip(),
                "joinSource": join_source,
                "primaryServiceId": _choose_primary_service_id(project_row, matched_services),
                "serviceCount": len(matched_services),
                "serviceIds": [item["serviceId"] for item in service_refs],
                "services": service_refs,
            }
        )

    service_only_keys = [
        _service_diagnostic_key(service_row)
        for service_row in filtered_services
        if _service_key(service_row) not in matched_service_keys
    ]

    ambiguous_join_keys = sorted(
        _service_diagnostic_key(service_row)
        for service_row in filtered_services
        if len(service_to_projects.get(_service_key(service_row), set())) > 1
    )

    return {
        "rows": rows,
        "diagnostics": {
            "projectOnlyCount": len(project_only_keys),
            "serviceOnlyCount": len(service_only_keys),
            "oneToManyCount": len(one_to_many_keys),
            "ambiguousJoinCount": len(ambiguous_join_keys),
            "projectOnlyKeys": sorted(project_only_keys),
            "serviceOnlyKeys": sorted(service_only_keys),
            "oneToManyKeys": sorted(one_to_many_keys),
            "ambiguousJoinKeys": ambiguous_join_keys,
        },
    }
