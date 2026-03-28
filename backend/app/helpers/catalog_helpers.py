"""Catalog/registry DB loader helpers shared across deployment and observability helpers."""

from __future__ import annotations

import psycopg

from app.db import get_psycopg_database_url
from app.service_observability import normalize_observability_mode


def _with_connection() -> psycopg.Connection:
    return psycopg.connect(get_psycopg_database_url())


def _load_project_rows(env: str | None = None) -> list[dict[str, str | None]]:
    with _with_connection() as conn:
        with conn.cursor() as cur:
            if env:
                cur.execute(
                    """
                    SELECT project_id, project_name, env, owner, repo_url, runbook_url, observability_mode
                    FROM project_registry
                    WHERE source = %s
                      AND env = %s
                    ORDER BY project_id ASC, env ASC
                    """,
                    ("gitops_apps", env),
                )
            else:
                cur.execute(
                    """
                    SELECT project_id, project_name, env, owner, repo_url, runbook_url, observability_mode
                    FROM project_registry
                    WHERE source = %s
                    ORDER BY project_id ASC, env ASC
                    """,
                    ("gitops_apps",),
                )
            rows = cur.fetchall()

    return [
        {
            "service_id": row[0],
            "service_name": row[1],
            "env": row[2],
            "owner": row[3],
            "repo_url": row[4],
            "runbook_url": row[5],
            "observability_mode": row[6] if len(row) > 6 else None,
        }
        for row in rows
    ]


def _load_service_rows(
    *,
    env: str | None = None,
    namespace: str | None = None,
    service_id: str | None = None,
) -> list[dict[str, str | None]]:
    conditions = ["source = %s"]
    params: list[str] = ["cluster_services"]
    if env:
        conditions.append("env = %s")
        params.append(env)
    if namespace:
        conditions.append("namespace = %s")
        params.append(namespace)
    if service_id:
        conditions.append("service_id = %s")
        params.append(service_id)

    where_clause = " AND ".join(conditions)
    with _with_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT service_id, service_name, env, namespace, app_label, argo_app_name, source, source_ref, last_synced_at, project_id
                FROM service_registry
                WHERE {where_clause}
                ORDER BY service_id ASC, env ASC
                """,
                tuple(params),
            )
            rows = cur.fetchall()

    return [
        {
            "service_id": row[0],
            "service_name": row[1],
            "env": row[2],
            "namespace": row[3],
            "app_label": row[4],
            "argo_app_name": row[5],
            "source": row[6],
            "source_ref": row[7],
            "last_synced_at": row[8].isoformat() if row[8] else None,
            "project_id": row[9],
        }
        for row in rows
    ]


def _load_project_catalog_rows(
    *,
    env: str | None = None,
    project_id: str | None = None,
) -> list[dict[str, str]]:
    conditions = ["source = %s"]
    params: list[str] = ["gitops_apps"]
    if env:
        conditions.append("env = %s")
        params.append(env)
    if project_id:
        conditions.append("project_id = %s")
        params.append(project_id)

    where_clause = " AND ".join(conditions)
    with _with_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT project_id, project_name, env, namespace, app_label, source_ref, observability_mode, public_host
                FROM project_registry
                WHERE {where_clause}
                ORDER BY project_id ASC, env ASC
                """,
                tuple(params),
            )
            rows = cur.fetchall()

    return [
        {
            "project_id": row[0],
            "project_name": row[1],
            "env": row[2],
            "namespace": row[3],
            "app_label": row[4],
            "source_ref": row[5] if len(row) > 5 else None,
            "observability_mode": normalize_observability_mode(row[6] if len(row) > 6 else None),
            "public_host": row[7] if len(row) > 7 else None,
        }
        for row in rows
    ]


def _load_service_catalog_rows(
    *,
    env: str | None = None,
    service_id: str | None = None,
) -> list[dict[str, str | None]]:
    return _load_service_rows(env=env, service_id=service_id)
