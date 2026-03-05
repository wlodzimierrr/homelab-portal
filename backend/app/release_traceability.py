from __future__ import annotations

import json
import os
from typing import TypedDict


class ProjectRow(TypedDict):
    service_id: str
    env: str


class CiMetadataRow(TypedDict, total=False):
    serviceId: str
    env: str
    commitSha: str
    imageRef: str
    expectedImageRef: str
    expectedRevision: str
    deployedAt: str


class ArgoMetadataRow(TypedDict, total=False):
    serviceId: str
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


UNKNOWN = "unknown"


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
    argo_index: dict[tuple[str, str], ArgoMetadataRow] = {}
    keys: set[tuple[str, str]] = set()

    for row in project_rows:
        key = (row["service_id"], row["env"])
        keys.add(key)

    for row in ci_rows:
        service_id = str(row.get("serviceId", "")).strip()
        env = str(row.get("env", "")).strip()
        if not service_id or not env:
            continue
        key = (service_id, env)
        keys.add(key)
        ci_index[key] = row

    for row in argo_rows:
        service_id = str(row.get("serviceId", "")).strip()
        env = str(row.get("env", "")).strip()
        if not service_id or not env:
            continue
        key = (service_id, env)
        keys.add(key)
        argo_index[key] = row

    ordered = sorted(keys, key=lambda item: (item[0], item[1]))
    rows: list[ReleaseTraceabilityRow] = []

    for service_id, env in ordered:
        if env_filter and env != env_filter:
            continue
        if service_id_filter and service_id != service_id_filter:
            continue

        ci = ci_index.get((service_id, env), {})
        argo = argo_index.get((service_id, env), {})

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

    return rows[:limit]
