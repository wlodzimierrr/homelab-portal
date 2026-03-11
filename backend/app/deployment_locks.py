from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, TypedDict

import psycopg
from psycopg.types.json import Json


ACTIVE_DEPLOYMENT_LOCK_STATUSES = ("pending", "deploying")


class DeploymentLockRow(TypedDict, total=False):
    serviceId: str
    env: str
    deploymentId: str
    requestKey: str
    action: str
    status: str
    argoApp: str | None
    requestedBy: str | None
    requestedAt: str
    prUrl: str | None
    prNumber: int | None
    gitRef: str | None
    deployReason: str | None
    metadata: dict[str, Any]
    lockedAt: str
    expiresAt: str
    createdAt: str
    updatedAt: str


class DeploymentLockConflictError(Exception):
    def __init__(self, active_lock: DeploymentLockRow):
        super().__init__("Deployment lock is already active for this service/environment")
        self.active_lock = active_lock


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


def _row_from_tuple(row: tuple[object, ...]) -> DeploymentLockRow:
    metadata = row[12] if isinstance(row[12], dict) else {}
    return {
        "serviceId": str(row[0]),
        "env": str(row[1]),
        "deploymentId": str(row[2]),
        "requestKey": str(row[3]),
        "action": str(row[4]),
        "status": str(row[5]),
        "argoApp": row[6] if isinstance(row[6], str) else None,
        "requestedBy": row[7] if isinstance(row[7], str) else None,
        "requestedAt": _serialize_datetime(row[8]) or "",
        "prUrl": row[9] if isinstance(row[9], str) else None,
        "prNumber": int(row[10]) if isinstance(row[10], int) else None,
        "gitRef": row[11] if isinstance(row[11], str) else None,
        "metadata": metadata,
        "deployReason": row[13] if isinstance(row[13], str) else None,
        "lockedAt": _serialize_datetime(row[14]) or "",
        "expiresAt": _serialize_datetime(row[15]) or "",
        "createdAt": _serialize_datetime(row[16]) or "",
        "updatedAt": _serialize_datetime(row[17]) or "",
    }


def _base_select_sql() -> str:
    return """
        SELECT
            service_id,
            env,
            deployment_id,
            request_key,
            action_type,
            status,
            argo_app,
            requested_by,
            requested_at,
            pr_url,
            pr_number,
            git_ref,
            metadata,
            deploy_reason,
            locked_at,
            expires_at,
            created_at,
            updated_at
        FROM deployment_locks
    """


def cleanup_stale_deployment_locks(
    conn: psycopg.Connection,
    *,
    now: datetime | None = None,
    service_id: str | None = None,
    env: str | None = None,
) -> int:
    current_time = now or datetime.now(tz=timezone.utc)
    conditions = ["expires_at <= %s"]
    params: list[object] = [current_time]
    if service_id:
        conditions.append("service_id = %s")
        params.append(service_id)
    if env:
        conditions.append("env = %s")
        params.append(env)

    with conn.cursor() as cur:
        cur.execute(
            f"""
            DELETE FROM deployment_locks
            WHERE {' AND '.join(conditions)}
            """,
            tuple(params),
        )
        return cur.rowcount or 0


def get_deployment_lock(
    conn: psycopg.Connection,
    *,
    service_id: str,
    env: str,
    now: datetime | None = None,
) -> DeploymentLockRow | None:
    cleanup_stale_deployment_locks(conn, now=now, service_id=service_id, env=env)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            {_base_select_sql()}
            WHERE service_id = %s AND env = %s
            """,
            (service_id, env),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return _row_from_tuple(row)


def _resolved_request_key(
    *,
    deployment_id: str,
    request_key: str | None,
) -> str:
    normalized = request_key.strip() if isinstance(request_key, str) else ""
    if normalized:
        return normalized
    return f"deployment:{deployment_id}"


def acquire_deployment_lock(
    conn: psycopg.Connection,
    *,
    service_id: str,
    env: str,
    deployment_id: str,
    action: str,
    status: str,
    request_key: str | None,
    requested_by: str | None = None,
    requested_at: object = None,
    pr_url: str | None = None,
    pr_number: int | None = None,
    git_ref: str | None = None,
    argo_app: str | None = None,
    deploy_reason: str | None = None,
    metadata: dict[str, Any] | None = None,
    stale_after_seconds: int = 30 * 60,
    now: datetime | None = None,
    enforce_conflict: bool = True,
) -> DeploymentLockRow:
    if status not in ACTIVE_DEPLOYMENT_LOCK_STATUSES:
        raise ValueError(f"Unsupported lock status: {status}")

    current_time = now or datetime.now(tz=timezone.utc)
    resolved_request_key = _resolved_request_key(
        deployment_id=deployment_id,
        request_key=request_key,
    )
    requested_at_value = _parse_datetime(requested_at) or current_time
    expires_at = current_time + timedelta(seconds=max(60, stale_after_seconds))
    lock_metadata = metadata or {}

    cleanup_stale_deployment_locks(conn, now=current_time, service_id=service_id, env=env)
    existing_lock = get_deployment_lock(conn, service_id=service_id, env=env, now=current_time)
    if existing_lock is not None and existing_lock.get("requestKey") != resolved_request_key:
        if enforce_conflict:
            raise DeploymentLockConflictError(existing_lock)
        return existing_lock

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO deployment_locks (
                service_id,
                env,
                deployment_id,
                request_key,
                action_type,
                status,
                argo_app,
                requested_by,
                requested_at,
                pr_url,
                pr_number,
                git_ref,
                metadata,
                deploy_reason,
                locked_at,
                expires_at,
                created_at,
                updated_at
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (service_id, env) DO UPDATE
            SET
                deployment_id = EXCLUDED.deployment_id,
                request_key = EXCLUDED.request_key,
                action_type = EXCLUDED.action_type,
                status = EXCLUDED.status,
                argo_app = EXCLUDED.argo_app,
                requested_by = EXCLUDED.requested_by,
                requested_at = EXCLUDED.requested_at,
                pr_url = EXCLUDED.pr_url,
                pr_number = EXCLUDED.pr_number,
                git_ref = EXCLUDED.git_ref,
                metadata = EXCLUDED.metadata,
                deploy_reason = EXCLUDED.deploy_reason,
                locked_at = EXCLUDED.locked_at,
                expires_at = EXCLUDED.expires_at,
                updated_at = EXCLUDED.updated_at
            RETURNING
                service_id,
                env,
                deployment_id,
                request_key,
                action_type,
                status,
                argo_app,
                requested_by,
                requested_at,
                pr_url,
                pr_number,
                git_ref,
                metadata,
                deploy_reason,
                locked_at,
                expires_at,
                created_at,
                updated_at
            """,
            (
                service_id,
                env,
                deployment_id,
                resolved_request_key,
                action,
                status,
                argo_app,
                requested_by,
                requested_at_value,
                pr_url,
                pr_number,
                git_ref,
                Json(lock_metadata),
                deploy_reason,
                current_time,
                expires_at,
                current_time,
                current_time,
            ),
        )
        row = cur.fetchone()

    if row is None:
        raise RuntimeError("Failed to acquire deployment lock")
    return _row_from_tuple(row)


def release_deployment_lock(
    conn: psycopg.Connection,
    *,
    service_id: str,
    env: str,
    request_key: str | None = None,
    deployment_id: str | None = None,
    release_any: bool = False,
) -> bool:
    conditions = ["service_id = %s", "env = %s"]
    params: list[object] = [service_id, env]

    if not release_any:
        resolved_request_key = None
        if deployment_id:
            resolved_request_key = _resolved_request_key(
                deployment_id=deployment_id,
                request_key=request_key,
            )
        elif isinstance(request_key, str) and request_key.strip():
            resolved_request_key = request_key.strip()
        if resolved_request_key:
            conditions.append("request_key = %s")
            params.append(resolved_request_key)

    with conn.cursor() as cur:
        cur.execute(
            f"""
            DELETE FROM deployment_locks
            WHERE {' AND '.join(conditions)}
            """,
            tuple(params),
        )
        return (cur.rowcount or 0) > 0


def sync_deployment_lock_for_deployment_row(
    conn: psycopg.Connection,
    deployment_row: dict[str, object],
    *,
    stale_after_seconds: int = 30 * 60,
    enforce_conflict: bool = True,
    release_any_terminal: bool = False,
    now: datetime | None = None,
) -> DeploymentLockRow | None:
    service_id = deployment_row.get("serviceId")
    env = deployment_row.get("env")
    deployment_id = deployment_row.get("deploymentId")
    action = deployment_row.get("action")
    status = deployment_row.get("status")
    if not all(
        isinstance(value, str) and value
        for value in (service_id, env, deployment_id, action, status)
    ):
        return None

    if status in ACTIVE_DEPLOYMENT_LOCK_STATUSES:
        return acquire_deployment_lock(
            conn,
            service_id=service_id,
            env=env,
            deployment_id=deployment_id,
            action=action,
            status=status,
            request_key=deployment_row.get("requestKey") if isinstance(deployment_row.get("requestKey"), str) else None,
            requested_by=(
                deployment_row.get("requestedBy")
                if isinstance(deployment_row.get("requestedBy"), str)
                else None
            ),
            requested_at=deployment_row.get("requestedAt"),
            pr_url=deployment_row.get("prUrl") if isinstance(deployment_row.get("prUrl"), str) else None,
            pr_number=deployment_row.get("prNumber") if isinstance(deployment_row.get("prNumber"), int) else None,
            git_ref=deployment_row.get("gitRef") if isinstance(deployment_row.get("gitRef"), str) else None,
            argo_app=deployment_row.get("argoApp") if isinstance(deployment_row.get("argoApp"), str) else None,
            deploy_reason=(
                deployment_row.get("deployReason")
                if isinstance(deployment_row.get("deployReason"), str)
                else None
            ),
            metadata=deployment_row.get("metadata") if isinstance(deployment_row.get("metadata"), dict) else None,
            stale_after_seconds=stale_after_seconds,
            now=now,
            enforce_conflict=enforce_conflict,
        )

    release_deployment_lock(
        conn,
        service_id=service_id,
        env=env,
        request_key=deployment_row.get("requestKey") if isinstance(deployment_row.get("requestKey"), str) else None,
        deployment_id=deployment_id,
        release_any=release_any_terminal,
    )
    return None
