from __future__ import annotations

import json
import logging
import os
from typing import TypedDict

logger = logging.getLogger("homelab.backend.release_traceability")


class ProjectRow(TypedDict, total=False):
    service_id: str
    service_name: str
    env: str


class CiMetadataRow(TypedDict, total=False):
    serviceId: str
    serviceName: str
    env: str
    commitSha: str
    imageRef: str
    expectedImageRef: str
    expectedRevision: str
    deployedAt: str


class ArgoMetadataRow(TypedDict, total=False):
    serviceId: str
    serviceName: str
    env: str
    appName: str
    syncStatus: str
    healthStatus: str
    revision: str
    liveRevision: str
    expectedRevision: str
    imageRef: str
    deployedAt: str


class ReleaseTraceabilityRow(TypedDict):
    serviceId: str
    env: str
    commitSha: str | None
    imageRef: str | None
    deployedAt: str | None
    argo: dict[str, str | None]
    drift: dict[str, bool | str | None]


class ReleaseJoinDiagnostics(TypedDict):
    ciUnmatchedCount: int
    argoUnmatchedCount: int
    ciUnmatchedKeys: list[str]
    argoUnmatchedKeys: list[str]


UNKNOWN = "unknown"


def _normalize_service_id(value: str) -> str:
    normalized = value.strip().lower()
    normalized = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in normalized)
    normalized = "-".join(part for part in normalized.split("-") if part)
    return normalized or "unknown-service"


def _normalize_sync(value: str | None) -> str:
    if value is None:
        return UNKNOWN
    normalized = value.strip().lower().replace("-", "_")
    if normalized in {"synced", "out_of_sync"}:
        return normalized
    return UNKNOWN


def _normalize_health(value: str | None) -> str:
    if value is None:
        return UNKNOWN
    normalized = value.strip().lower()
    if normalized in {"healthy", "degraded"}:
        return normalized
    return UNKNOWN


def _read_env_json_rows(name: str) -> list[dict]:
    raw = os.getenv(name)
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def load_ci_metadata_rows() -> list[CiMetadataRow]:
    return [row for row in _read_env_json_rows("RELEASE_CI_METADATA_JSON")]


def load_argo_metadata_rows() -> list[ArgoMetadataRow]:
    return [row for row in _read_env_json_rows("RELEASE_ARGO_METADATA_JSON")]


def compute_is_drifted(
    *,
    sync_status: str,
    expected_revision: str | None,
    live_revision: str | None,
    expected_image_ref: str | None,
    live_image_ref: str | None,
) -> bool:
    """
    Deterministic drift rule:
    1) out_of_sync always indicates drift
    2) expectedRevision != liveRevision indicates drift when both exist
    3) expectedImageRef != liveImageRef indicates drift when both exist
    """
    if sync_status == "out_of_sync":
        return True
    if expected_revision and live_revision and expected_revision != live_revision:
        return True
    if expected_image_ref and live_image_ref and expected_image_ref != live_image_ref:
        return True
    return False


def build_release_traceability_rows(
    *,
    project_rows: list[ProjectRow],
    ci_rows: list[CiMetadataRow],
    argo_rows: list[ArgoMetadataRow],
    env_filter: str | None,
    service_id_filter: str | None,
    limit: int,
) -> list[ReleaseTraceabilityRow]:
    ci_index: dict[tuple[str, str], CiMetadataRow] = {}
    ci_normalized_index: dict[tuple[str, str], CiMetadataRow] = {}
    argo_index: dict[tuple[str, str], ArgoMetadataRow] = {}
    argo_normalized_index: dict[tuple[str, str], ArgoMetadataRow] = {}
    registry_keys: set[tuple[str, str]] = set()
    registry_service_name_by_key: dict[tuple[str, str], str] = {}

    for row in ci_rows:
        service_id = str(row.get("serviceId", "")).strip()
        env = str(row.get("env", "")).strip()
        if not service_id or not env:
            continue
        key = (service_id, env)
        ci_index[key] = row
        ci_normalized_index[(_normalize_service_id(service_id), env)] = row

    for row in argo_rows:
        service_id = str(row.get("serviceId", "")).strip()
        env = str(row.get("env", "")).strip()
        if not service_id or not env:
            continue
        key = (service_id, env)
        argo_index[key] = row
        argo_normalized_index[(_normalize_service_id(service_id), env)] = row

    for row in project_rows:
        service_id = str(row.get("service_id", "")).strip()
        env = str(row.get("env", "")).strip()
        if not service_id or not env:
            continue
        key = (service_id, env)
        registry_keys.add(key)
        service_name = str(row.get("service_name", "")).strip() if row.get("service_name") else ""
        if service_name:
            registry_service_name_by_key[key] = service_name

    ordered = sorted(registry_keys, key=lambda item: (item[0], item[1]))
    rows: list[ReleaseTraceabilityRow] = []
    matched_ci_keys: set[tuple[str, str]] = set()
    matched_argo_keys: set[tuple[str, str]] = set()

    for service_id, env in ordered:
        if env_filter and env != env_filter:
            continue
        if service_id_filter and service_id != service_id_filter:
            continue

        service_name = registry_service_name_by_key.get((service_id, env))
        ci: CiMetadataRow = {}
        argo: ArgoMetadataRow = {}

        candidate_ci_keys = [
            (service_id, env),
            (service_name, env) if service_name else None,
        ]
        for candidate in candidate_ci_keys:
            if candidate and candidate in ci_index:
                ci = ci_index[candidate]
                matched_ci_keys.add(candidate)
                break
        if not ci:
            normalized_key = (_normalize_service_id(service_id), env)
            if normalized_key in ci_normalized_index:
                ci = ci_normalized_index[normalized_key]
                source_service_id = str(ci.get("serviceId", "")).strip()
                if source_service_id:
                    matched_ci_keys.add((source_service_id, env))

        candidate_argo_keys = [
            (service_id, env),
            (service_name, env) if service_name else None,
        ]
        for candidate in candidate_argo_keys:
            if candidate and candidate in argo_index:
                argo = argo_index[candidate]
                matched_argo_keys.add(candidate)
                break
        if not argo:
            normalized_key = (_normalize_service_id(service_id), env)
            if normalized_key in argo_normalized_index:
                argo = argo_normalized_index[normalized_key]
                source_service_id = str(argo.get("serviceId", "")).strip()
                if source_service_id:
                    matched_argo_keys.add((source_service_id, env))

        sync_status = _normalize_sync(argo.get("syncStatus"))  # type: ignore[arg-type]
        health_status = _normalize_health(argo.get("healthStatus"))  # type: ignore[arg-type]
        expected_revision = (
            ci.get("expectedRevision")
            if isinstance(ci.get("expectedRevision"), str)
            else argo.get("expectedRevision")
            if isinstance(argo.get("expectedRevision"), str)
            else None
        )
        live_revision = (
            argo.get("liveRevision")
            if isinstance(argo.get("liveRevision"), str)
            else argo.get("revision")
            if isinstance(argo.get("revision"), str)
            else None
        )
        expected_image_ref = (
            ci.get("expectedImageRef")
            if isinstance(ci.get("expectedImageRef"), str)
            else None
        )
        live_image_ref = (
            argo.get("imageRef")
            if isinstance(argo.get("imageRef"), str)
            else ci.get("imageRef")
            if isinstance(ci.get("imageRef"), str)
            else None
        )

        row: ReleaseTraceabilityRow = {
            "serviceId": service_id,
            "env": env,
            "commitSha": ci.get("commitSha")
            if isinstance(ci.get("commitSha"), str)
            else None,
            "imageRef": live_image_ref,
            "deployedAt": ci.get("deployedAt")
            if isinstance(ci.get("deployedAt"), str)
            else argo.get("deployedAt")
            if isinstance(argo.get("deployedAt"), str)
            else None,
            "argo": {
                "appName": argo.get("appName")
                if isinstance(argo.get("appName"), str)
                else UNKNOWN,
                "syncStatus": sync_status,
                "healthStatus": health_status,
                "revision": argo.get("revision")
                if isinstance(argo.get("revision"), str)
                else None,
            },
            "drift": {
                "isDrifted": compute_is_drifted(
                    sync_status=sync_status,
                    expected_revision=expected_revision,
                    live_revision=live_revision,
                    expected_image_ref=expected_image_ref,
                    live_image_ref=live_image_ref,
                ),
                "expectedRevision": expected_revision,
                "liveRevision": live_revision,
            },
        }
        rows.append(row)

    unmatched_ci = sorted(set(ci_index.keys()) - matched_ci_keys)
    unmatched_argo = sorted(set(argo_index.keys()) - matched_argo_keys)

    for service_id, env in unmatched_ci:
        row = ci_index[(service_id, env)]
        service_name = (
            row.get("serviceName")
            if isinstance(row.get("serviceName"), str)
            else UNKNOWN
        )
        logger.warning(
            "release_join_mismatch source=ci key=%s|%s|%s reason=missing_registry_mapping",
            service_id,
            service_name,
            env,
        )

    for service_id, env in unmatched_argo:
        row = argo_index[(service_id, env)]
        service_name = (
            row.get("serviceName")
            if isinstance(row.get("serviceName"), str)
            else UNKNOWN
        )
        logger.warning(
            "release_join_mismatch source=argo key=%s|%s|%s reason=missing_registry_mapping",
            service_id,
            service_name,
            env,
        )

    return rows[:limit]


def build_release_join_diagnostics(
    *,
    project_rows: list[ProjectRow],
    ci_rows: list[CiMetadataRow],
    argo_rows: list[ArgoMetadataRow],
    env_filter: str | None,
    service_id_filter: str | None,
) -> ReleaseJoinDiagnostics:
    ci_index: dict[tuple[str, str], CiMetadataRow] = {}
    ci_normalized_index: dict[tuple[str, str], CiMetadataRow] = {}
    argo_index: dict[tuple[str, str], ArgoMetadataRow] = {}
    argo_normalized_index: dict[tuple[str, str], ArgoMetadataRow] = {}
    registry_keys: set[tuple[str, str]] = set()
    registry_service_name_by_key: dict[tuple[str, str], str] = {}

    for row in ci_rows:
        service_id = str(row.get("serviceId", "")).strip()
        env = str(row.get("env", "")).strip()
        if not service_id or not env:
            continue
        key = (service_id, env)
        ci_index[key] = row
        ci_normalized_index[(_normalize_service_id(service_id), env)] = row

    for row in argo_rows:
        service_id = str(row.get("serviceId", "")).strip()
        env = str(row.get("env", "")).strip()
        if not service_id or not env:
            continue
        key = (service_id, env)
        argo_index[key] = row
        argo_normalized_index[(_normalize_service_id(service_id), env)] = row

    for row in project_rows:
        service_id = str(row.get("service_id", "")).strip()
        env = str(row.get("env", "")).strip()
        if not service_id or not env:
            continue
        if env_filter and env != env_filter:
            continue
        if service_id_filter and service_id != service_id_filter:
            continue
        key = (service_id, env)
        registry_keys.add(key)
        service_name = str(row.get("service_name", "")).strip() if row.get("service_name") else ""
        if service_name:
            registry_service_name_by_key[key] = service_name

    matched_ci_keys: set[tuple[str, str]] = set()
    matched_argo_keys: set[tuple[str, str]] = set()
    for service_id, env in sorted(registry_keys):
        service_name = registry_service_name_by_key.get((service_id, env))

        candidate_ci_keys = [
            (service_id, env),
            (service_name, env) if service_name else None,
        ]
        for candidate in candidate_ci_keys:
            if candidate and candidate in ci_index:
                matched_ci_keys.add(candidate)
                break
        else:
            normalized_key = (_normalize_service_id(service_id), env)
            if normalized_key in ci_normalized_index:
                row = ci_normalized_index[normalized_key]
                source_service_id = str(row.get("serviceId", "")).strip()
                if source_service_id:
                    matched_ci_keys.add((source_service_id, env))

        candidate_argo_keys = [
            (service_id, env),
            (service_name, env) if service_name else None,
        ]
        for candidate in candidate_argo_keys:
            if candidate and candidate in argo_index:
                matched_argo_keys.add(candidate)
                break
        else:
            normalized_key = (_normalize_service_id(service_id), env)
            if normalized_key in argo_normalized_index:
                row = argo_normalized_index[normalized_key]
                source_service_id = str(row.get("serviceId", "")).strip()
                if source_service_id:
                    matched_argo_keys.add((source_service_id, env))

    def _matches_filters(key: tuple[str, str]) -> bool:
        if env_filter and key[1] != env_filter:
            return False
        if service_id_filter and key[0] != service_id_filter:
            return False
        return True

    unmatched_ci = sorted(
        key for key in set(ci_index.keys()) - matched_ci_keys if _matches_filters(key)
    )
    unmatched_argo = sorted(
        key
        for key in set(argo_index.keys()) - matched_argo_keys
        if _matches_filters(key)
    )

    ci_keys = []
    for service_id, env in unmatched_ci:
        row = ci_index[(service_id, env)]
        service_name = (
            row.get("serviceName")
            if isinstance(row.get("serviceName"), str)
            else UNKNOWN
        )
        ci_keys.append(f"{service_id}|{service_name}|{env}")

    argo_keys = []
    for service_id, env in unmatched_argo:
        row = argo_index[(service_id, env)]
        service_name = (
            row.get("serviceName")
            if isinstance(row.get("serviceName"), str)
            else UNKNOWN
        )
        argo_keys.append(f"{service_id}|{service_name}|{env}")

    return {
        "ciUnmatchedCount": len(ci_keys),
        "argoUnmatchedCount": len(argo_keys),
        "ciUnmatchedKeys": ci_keys,
        "argoUnmatchedKeys": argo_keys,
    }
