from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, TypedDict
from uuid import uuid4

import psycopg
from psycopg.types.json import Json


ACTION_TYPES = ("deploy", "promote", "rollback", "config-change")
DEPLOYMENT_STATUSES = ("pending", "deploying", "live", "failed")
DEPLOYMENT_RESULTS = ("success", "failure", "cancelled", "skipped")


class DeploymentRecordRow(TypedDict, total=False):
    deploymentId: str
    serviceId: str
    env: str
    action: str
    status: str
    requestedBy: str | None
    requestedAt: str
    prUrl: str | None
    prNumber: int | None
    mergeSha: str | None
    targetImage: str | None
    previousImage: str | None
    argoApp: str | None
    syncStatus: str | None
    healthStatus: str | None
    startedAt: str | None
    finishedAt: str | None
    deployWindowStart: str | None
    deployWindowEnd: str | None
    deployReason: str | None
    compareUrl: str | None
    gitRef: str | None
    requestKey: str | None
    result: str | None
    resultReason: str | None
    metadata: dict[str, Any]
    createdAt: str
    updatedAt: str


def _parse_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _serialize_datetime(value: object) -> str | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _row_from_tuple(row: tuple[object, ...]) -> DeploymentRecordRow:
    metadata = row[22] if isinstance(row[22], dict) else {}
    return {
        "deploymentId": str(row[0]),
        "serviceId": str(row[1]),
        "env": str(row[2]),
        "action": str(row[3]),
        "status": str(row[4]),
        "requestedBy": row[5] if isinstance(row[5], str) else None,
        "requestedAt": _serialize_datetime(row[6]) or "",
        "prUrl": row[7] if isinstance(row[7], str) else None,
        "prNumber": int(row[8]) if isinstance(row[8], int) else None,
        "mergeSha": row[9] if isinstance(row[9], str) else None,
        "targetImage": row[10] if isinstance(row[10], str) else None,
        "previousImage": row[11] if isinstance(row[11], str) else None,
        "argoApp": row[12] if isinstance(row[12], str) else None,
        "syncStatus": row[13] if isinstance(row[13], str) else None,
        "healthStatus": row[14] if isinstance(row[14], str) else None,
        "startedAt": _serialize_datetime(row[15]),
        "finishedAt": _serialize_datetime(row[16]),
        "deployWindowStart": _serialize_datetime(row[17]),
        "deployWindowEnd": _serialize_datetime(row[18]),
        "deployReason": row[19] if isinstance(row[19], str) else None,
        "compareUrl": row[20] if isinstance(row[20], str) else None,
        "gitRef": row[21] if isinstance(row[21], str) else None,
        "metadata": metadata,
        "requestKey": row[23] if isinstance(row[23], str) else None,
        "createdAt": _serialize_datetime(row[24]) or "",
        "updatedAt": _serialize_datetime(row[25]) or "",
        "result": row[26] if isinstance(row[26], str) else None,
        "resultReason": row[27] if isinstance(row[27], str) else None,
    }


def _base_select_sql() -> str:
    return """
        SELECT
            deployment_id,
            service_id,
            env,
            action_type,
            status,
            requested_by,
            requested_at,
            pr_url,
            pr_number,
            merge_sha,
            target_image,
            previous_image,
            argo_app,
            sync_status,
            health_status,
            started_at,
            finished_at,
            deploy_window_start,
            deploy_window_end,
            deploy_reason,
            compare_url,
            git_ref,
            metadata,
            request_key,
            created_at,
            updated_at,
            result,
            result_reason
        FROM deployments
    """


def list_deployment_records_for_service(
    conn: psycopg.Connection,
    *,
    service_id: str,
    env: str | None = None,
    limit: int = 20,
) -> list[DeploymentRecordRow]:
    conditions = ["service_id = %s"]
    params: list[object] = [service_id]
    if env:
        conditions.append("env = %s")
        params.append(env)
    params.append(max(1, min(limit, 100)))

    with conn.cursor() as cur:
        cur.execute(
            f"""
            {_base_select_sql()}
            WHERE {' AND '.join(conditions)}
            ORDER BY requested_at DESC, deployment_id DESC
            LIMIT %s
            """,
            tuple(params),
        )
        rows = cur.fetchall()

    return [_row_from_tuple(row) for row in rows]


def get_deployment_record(
    conn: psycopg.Connection,
    deployment_id: str,
) -> DeploymentRecordRow | None:
    with conn.cursor() as cur:
        cur.execute(
            f"""
            {_base_select_sql()}
            WHERE deployment_id = %s
            """,
            (deployment_id,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return _row_from_tuple(row)


def get_deployment_record_by_request_key(
    conn: psycopg.Connection,
    request_key: str,
) -> DeploymentRecordRow | None:
    with conn.cursor() as cur:
        cur.execute(
            f"""
            {_base_select_sql()}
            WHERE request_key = %s
            """,
            (request_key,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return _row_from_tuple(row)


def get_latest_deployment_record_for_service(
    conn: psycopg.Connection,
    *,
    service_id: str,
    env: str,
    exclude_request_key: str | None = None,
) -> DeploymentRecordRow | None:
    conditions = ["service_id = %s", "env = %s"]
    params: list[object] = [service_id, env]
    if exclude_request_key:
        conditions.append("(request_key IS NULL OR request_key <> %s)")
        params.append(exclude_request_key)

    with conn.cursor() as cur:
        cur.execute(
            f"""
            {_base_select_sql()}
            WHERE {' AND '.join(conditions)}
            ORDER BY requested_at DESC, deployment_id DESC
            LIMIT 1
            """,
            tuple(params),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return _row_from_tuple(row)


def upsert_deployment_record(
    conn: psycopg.Connection,
    *,
    service_id: str,
    env: str,
    action: str,
    status: str,
    requested_by: str | None = None,
    requested_at: object = None,
    pr_url: str | None = None,
    pr_number: int | None = None,
    merge_sha: str | None = None,
    target_image: str | None = None,
    previous_image: str | None = None,
    argo_app: str | None = None,
    sync_status: str | None = None,
    health_status: str | None = None,
    started_at: object = None,
    finished_at: object = None,
    deploy_window_start: object = None,
    deploy_window_end: object = None,
    deploy_reason: str | None = None,
    compare_url: str | None = None,
    git_ref: str | None = None,
    request_key: str | None = None,
    metadata: dict[str, Any] | None = None,
    result: str | None = None,
    result_reason: str | None = None,
) -> DeploymentRecordRow:
    deployment_id = str(uuid4())
    now = datetime.now(tz=timezone.utc)
    requested_at_value = _parse_datetime(requested_at) or now
    started_at_value = _parse_datetime(started_at)
    finished_at_value = _parse_datetime(finished_at)
    deploy_window_start_value = _parse_datetime(deploy_window_start)
    deploy_window_end_value = _parse_datetime(deploy_window_end)
    metadata_value = metadata or {}

    insert_params = (
        deployment_id,
        service_id,
        env,
        action,
        status,
        requested_by,
        requested_at_value,
        pr_url,
        pr_number,
        merge_sha,
        target_image,
        previous_image,
        argo_app,
        sync_status,
        health_status,
        started_at_value,
        finished_at_value,
        deploy_window_start_value,
        deploy_window_end_value,
        deploy_reason,
        compare_url,
        git_ref,
        Json(metadata_value),
        request_key,
        result,
        result_reason,
    )

    with conn.cursor() as cur:
        if request_key:
            cur.execute(
                """
                INSERT INTO deployments (
                    deployment_id,
                    service_id,
                    env,
                    action_type,
                    status,
                    requested_by,
                    requested_at,
                    pr_url,
                    pr_number,
                    merge_sha,
                    target_image,
                    previous_image,
                    argo_app,
                    sync_status,
                    health_status,
                    started_at,
                    finished_at,
                    deploy_window_start,
                    deploy_window_end,
                    deploy_reason,
                    compare_url,
                    git_ref,
                    metadata,
                    request_key,
                    result,
                    result_reason
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (request_key) DO UPDATE SET
                    service_id = EXCLUDED.service_id,
                    env = EXCLUDED.env,
                    action_type = EXCLUDED.action_type,
                    status = EXCLUDED.status,
                    requested_by = COALESCE(EXCLUDED.requested_by, deployments.requested_by),
                    requested_at = COALESCE(EXCLUDED.requested_at, deployments.requested_at),
                    pr_url = COALESCE(EXCLUDED.pr_url, deployments.pr_url),
                    pr_number = COALESCE(EXCLUDED.pr_number, deployments.pr_number),
                    merge_sha = COALESCE(EXCLUDED.merge_sha, deployments.merge_sha),
                    target_image = COALESCE(EXCLUDED.target_image, deployments.target_image),
                    previous_image = COALESCE(EXCLUDED.previous_image, deployments.previous_image),
                    argo_app = COALESCE(EXCLUDED.argo_app, deployments.argo_app),
                    sync_status = COALESCE(EXCLUDED.sync_status, deployments.sync_status),
                    health_status = COALESCE(EXCLUDED.health_status, deployments.health_status),
                    started_at = COALESCE(EXCLUDED.started_at, deployments.started_at),
                    finished_at = COALESCE(EXCLUDED.finished_at, deployments.finished_at),
                    deploy_window_start = COALESCE(EXCLUDED.deploy_window_start, deployments.deploy_window_start),
                    deploy_window_end = COALESCE(EXCLUDED.deploy_window_end, deployments.deploy_window_end),
                    deploy_reason = COALESCE(EXCLUDED.deploy_reason, deployments.deploy_reason),
                    compare_url = COALESCE(EXCLUDED.compare_url, deployments.compare_url),
                    git_ref = COALESCE(EXCLUDED.git_ref, deployments.git_ref),
                    metadata = CASE
                        WHEN EXCLUDED.metadata = '{}'::jsonb THEN deployments.metadata
                        ELSE EXCLUDED.metadata
                    END,
                    result = COALESCE(EXCLUDED.result, deployments.result),
                    result_reason = COALESCE(EXCLUDED.result_reason, deployments.result_reason),
                    updated_at = NOW()
                RETURNING
                    deployment_id,
                    service_id,
                    env,
                    action_type,
                    status,
                    requested_by,
                    requested_at,
                    pr_url,
                    pr_number,
                    merge_sha,
                    target_image,
                    previous_image,
                    argo_app,
                    sync_status,
                    health_status,
                    started_at,
                    finished_at,
                    deploy_window_start,
                    deploy_window_end,
                    deploy_reason,
                    compare_url,
                    git_ref,
                    metadata,
                    request_key,
                    created_at,
                    updated_at,
                    result,
                    result_reason
                """,
                insert_params,
            )
        else:
            cur.execute(
                """
                INSERT INTO deployments (
                    deployment_id,
                    service_id,
                    env,
                    action_type,
                    status,
                    requested_by,
                    requested_at,
                    pr_url,
                    pr_number,
                    merge_sha,
                    target_image,
                    previous_image,
                    argo_app,
                    sync_status,
                    health_status,
                    started_at,
                    finished_at,
                    deploy_window_start,
                    deploy_window_end,
                    deploy_reason,
                    compare_url,
                    git_ref,
                    metadata,
                    request_key,
                    result,
                    result_reason
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                RETURNING
                    deployment_id,
                    service_id,
                    env,
                    action_type,
                    status,
                    requested_by,
                    requested_at,
                    pr_url,
                    pr_number,
                    merge_sha,
                    target_image,
                    previous_image,
                    argo_app,
                    sync_status,
                    health_status,
                    started_at,
                    finished_at,
                    deploy_window_start,
                    deploy_window_end,
                    deploy_reason,
                    compare_url,
                    git_ref,
                    metadata,
                    request_key,
                    created_at,
                    updated_at,
                    result,
                    result_reason
                """,
                insert_params,
            )

        row = cur.fetchone()

    if row is None:
        raise RuntimeError("deployment record insert returned no row")
    return _row_from_tuple(row)


def store_observability_snapshot(
    conn: psycopg.Connection,
    deployment_id: str,
    snapshot: dict[str, Any],
) -> None:
    """Merge observabilitySnapshot into deployment metadata.

    Uses jsonb_set so existing metadata keys are preserved.
    Only writes when the key is absent — idempotent on subsequent calls.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE deployments
            SET metadata = jsonb_set(
                    metadata,
                    '{observabilitySnapshot}',
                    %s::jsonb
                ),
                updated_at = NOW()
            WHERE deployment_id = %s
              AND (metadata->>'observabilitySnapshot') IS NULL
            """,
            (Json(snapshot), deployment_id),
        )
        conn.commit()
