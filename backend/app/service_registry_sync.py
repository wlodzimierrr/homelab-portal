from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import os
import re
import ssl
import time
from urllib import request as urlrequest
from uuid import uuid4

import psycopg

logger = logging.getLogger("homelab.backend.service_registry_sync")

DEFAULT_SYNC_NAMESPACES = ("homelab-api", "homelab-web")
DEFAULT_ARGO_NAMESPACE = "argocd"
SERVICE_ACCOUNT_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
SERVICE_ACCOUNT_CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"


@dataclass(frozen=True)
class ServiceRegistryRecord:
    service_id: str
    service_name: str
    namespace: str
    env: str
    app_label: str
    argo_app_name: str | None
    source: str
    source_ref: str
    last_synced_at: datetime


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _normalize_service_id(value: str) -> str:
    normalized = value.strip().lower()
    normalized = re.sub(r"[^a-z0-9._-]+", "-", normalized)
    normalized = normalized.strip("-")
    return normalized or "unknown-service"


def _parse_csv_env(var_name: str, fallback: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(var_name, "")
    values = [part.strip() for part in raw.split(",") if part.strip()]
    return tuple(values) if values else fallback


def _kubernetes_api_base_url() -> str:
    host = os.getenv("KUBERNETES_SERVICE_HOST")
    port = os.getenv("KUBERNETES_SERVICE_PORT", "443")
    if host:
        return f"https://{host}:{port}"
    return os.getenv("KUBERNETES_API_URL", "").rstrip("/")


def _load_incluster_token() -> str | None:
    token = os.getenv("KUBERNETES_BEARER_TOKEN")
    if token:
        return token

    if not os.path.exists(SERVICE_ACCOUNT_TOKEN_PATH):
        return None
    with open(SERVICE_ACCOUNT_TOKEN_PATH, "r", encoding="utf-8") as token_file:
        value = token_file.read().strip()
    return value or None


def _build_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    if os.path.exists(SERVICE_ACCOUNT_CA_PATH):
        context.load_verify_locations(cafile=SERVICE_ACCOUNT_CA_PATH)
    return context


def _kube_get_json(path: str, timeout_seconds: float = 8.0) -> dict:
    base_url = _kubernetes_api_base_url()
    if not base_url:
        raise RuntimeError("Kubernetes API endpoint is not configured")

    token = _load_incluster_token()
    if not token:
        raise RuntimeError("Kubernetes API bearer token is unavailable")

    request = urlrequest.Request(
        f"{base_url}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
    )
    with urlrequest.urlopen(
        request,
        timeout=timeout_seconds,
        context=_build_ssl_context(),
    ) as response:
        return json.loads(response.read())


def _fetch_deployments_in_namespace(namespace: str) -> list[dict]:
    payload = _kube_get_json(
        f"/apis/apps/v1/namespaces/{namespace}/deployments",
    )
    items = payload.get("items", [])
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _fetch_argocd_applications(namespace: str) -> list[dict]:
    payload = _kube_get_json(
        f"/apis/argoproj.io/v1alpha1/namespaces/{namespace}/applications",
    )
    items = payload.get("items", [])
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _derive_argo_mapping(applications: list[dict]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for app in applications:
        metadata = app.get("metadata", {})
        spec = app.get("spec", {})
        if not isinstance(metadata, dict) or not isinstance(spec, dict):
            continue
        app_name = metadata.get("name")
        destination = spec.get("destination", {})
        if not isinstance(app_name, str) or not isinstance(destination, dict):
            continue
        namespace = destination.get("namespace")
        if isinstance(namespace, str) and namespace and namespace not in mapping:
            mapping[namespace] = app_name
    return mapping


def _build_records_from_deployments(
    *,
    deployments: list[dict],
    env_name: str,
    source_ref: str,
    synced_at: datetime,
    argo_by_namespace: dict[str, str],
) -> list[ServiceRegistryRecord]:
    records: list[ServiceRegistryRecord] = []
    for deployment in deployments:
        metadata = deployment.get("metadata", {})
        if not isinstance(metadata, dict):
            continue

        name = metadata.get("name")
        namespace = metadata.get("namespace")
        labels = metadata.get("labels", {})
        annotations = metadata.get("annotations", {})

        if (
            not isinstance(name, str)
            or not isinstance(namespace, str)
            or not name
            or not namespace
        ):
            continue
        if not isinstance(labels, dict):
            labels = {}
        if not isinstance(annotations, dict):
            annotations = {}

        app_label_raw = (
            labels.get("app.kubernetes.io/name")
            or labels.get("app")
            or name
        )
        app_label = str(app_label_raw)
        service_id = _normalize_service_id(app_label)

        argo_app_name: str | None = None
        annotation_app = annotations.get("argocd.argoproj.io/instance")
        if isinstance(annotation_app, str) and annotation_app:
            argo_app_name = annotation_app
        else:
            argo_app_name = argo_by_namespace.get(namespace)

        records.append(
            ServiceRegistryRecord(
                service_id=service_id,
                service_name=name,
                namespace=namespace,
                env=env_name,
                app_label=app_label,
                argo_app_name=argo_app_name,
                source="kubernetes",
                source_ref=source_ref,
                last_synced_at=synced_at,
            )
        )
    return records


def _upsert_service_registry_records(
    conn: psycopg.Connection,
    records: list[ServiceRegistryRecord],
) -> tuple[int, int]:
    if not records:
        return 0, 0

    inserted = 0
    updated = 0
    with conn.cursor() as cur:
        for row in records:
            cur.execute(
                """
                INSERT INTO service_registry (
                    service_id,
                    service_name,
                    namespace,
                    env,
                    app_label,
                    argo_app_name,
                    source,
                    source_ref,
                    last_synced_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (service_id, env) DO UPDATE
                SET service_name = EXCLUDED.service_name,
                    namespace = EXCLUDED.namespace,
                    app_label = EXCLUDED.app_label,
                    argo_app_name = EXCLUDED.argo_app_name,
                    source = EXCLUDED.source,
                    source_ref = EXCLUDED.source_ref,
                    last_synced_at = EXCLUDED.last_synced_at,
                    updated_at = CURRENT_TIMESTAMP
                RETURNING (xmax = 0) AS inserted
                """,
                (
                    row.service_id,
                    row.service_name,
                    row.namespace,
                    row.env,
                    row.app_label,
                    row.argo_app_name,
                    row.source,
                    row.source_ref,
                    row.last_synced_at,
                ),
            )
            was_inserted = bool(cur.fetchone()[0])
            if was_inserted:
                inserted += 1
            else:
                updated += 1
    return inserted, updated


def sync_service_registry_from_cluster(
    conn: psycopg.Connection,
    *,
    env_name: str | None = None,
    namespaces: tuple[str, ...] | None = None,
    argo_namespace: str | None = None,
) -> dict:
    correlation_id = str(uuid4())
    started = time.perf_counter()
    synced_at = _utc_now()

    safe_env = env_name or os.getenv("PORTAL_ENV", "dev")
    safe_namespaces = namespaces or _parse_csv_env(
        "SERVICE_REGISTRY_SYNC_NAMESPACES",
        DEFAULT_SYNC_NAMESPACES,
    )
    safe_argo_namespace = argo_namespace or os.getenv(
        "SERVICE_REGISTRY_SYNC_ARGO_NAMESPACE",
        DEFAULT_ARGO_NAMESPACE,
    )

    source_failures: list[dict[str, str]] = []
    deployments: list[dict] = []
    for namespace in safe_namespaces:
        try:
            deployments.extend(_fetch_deployments_in_namespace(namespace))
        except Exception as exc:
            logger.error(
                "service_registry_sync_source_error correlation_id=%s source=kubernetes namespace=%s error=%s",
                correlation_id,
                namespace,
                str(exc),
            )
            source_failures.append(
                {
                    "source": "kubernetes",
                    "scope": namespace,
                    "error": str(exc),
                }
            )

    argo_mapping: dict[str, str] = {}
    try:
        applications = _fetch_argocd_applications(safe_argo_namespace)
        argo_mapping = _derive_argo_mapping(applications)
    except Exception as exc:
        logger.warning(
            "service_registry_sync_source_error correlation_id=%s source=argocd namespace=%s error=%s",
            correlation_id,
            safe_argo_namespace,
            str(exc),
        )
        source_failures.append(
            {
                "source": "argocd",
                "scope": safe_argo_namespace,
                "error": str(exc),
            }
        )

    records = _build_records_from_deployments(
        deployments=deployments,
        env_name=safe_env,
        source_ref="kubernetes_api",
        synced_at=synced_at,
        argo_by_namespace=argo_mapping,
    )

    # Keep a deterministic winner when multiple deployments map to the same service/env key.
    deduped: dict[tuple[str, str], ServiceRegistryRecord] = {}
    for row in records:
        deduped[(row.service_id, row.env)] = row
    unique_records = list(deduped.values())

    inserted, updated = _upsert_service_registry_records(conn, unique_records)
    duration_ms = int((time.perf_counter() - started) * 1000)

    summary = {
        "correlationId": correlation_id,
        "env": safe_env,
        "namespaces": list(safe_namespaces),
        "discovered": len(records),
        "upserted": len(unique_records),
        "inserted": inserted,
        "updated": updated,
        "sourceFailures": source_failures,
        "generatedAt": synced_at.isoformat(),
        "durationMs": duration_ms,
    }
    logger.info(
        "service_registry_sync_summary correlation_id=%s env=%s discovered=%s upserted=%s inserted=%s updated=%s failures=%s duration_ms=%s",
        correlation_id,
        safe_env,
        len(records),
        len(unique_records),
        inserted,
        updated,
        len(source_failures),
        duration_ms,
    )
    return summary
