from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import re
import subprocess
import time
from uuid import uuid4

import psycopg
import yaml

logger = logging.getLogger("homelab.backend.gitops_project_sync")

DEFAULT_SOURCE = "gitops_apps"


def _resolve_default_workloads_repo_path(current_file: Path | None = None) -> Path:
    source_file = (current_file or Path(__file__)).resolve()
    candidates: list[Path] = []

    for parent in [source_file.parent, *source_file.parents]:
        candidate = parent / "workloads"
        if candidate not in candidates:
            candidates.append(candidate)

    for candidate in candidates:
        if candidate.exists():
            return candidate

    # Do not crash API startup when the container filesystem doesn't mirror the
    # monorepo layout; sync callers can still override this path via env var.
    return source_file.parent / "workloads"


DEFAULT_WORKLOADS_REPO_PATH = _resolve_default_workloads_repo_path()


@dataclass(frozen=True)
class ProjectRegistryRecord:
    project_id: str
    project_name: str
    namespace: str
    env: str
    app_label: str
    source: str
    source_ref: str
    last_synced_at: datetime


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _normalize_project_id(value: str) -> str:
    normalized = value.strip().lower()
    normalized = re.sub(r"[^a-z0-9._-]+", "-", normalized)
    normalized = normalized.strip("-")
    return normalized or "unknown-project"


def _workloads_repo_path() -> Path:
    configured = os.getenv("GITOPS_WORKLOADS_REPO_PATH")
    if configured:
        return Path(configured).expanduser().resolve()
    return DEFAULT_WORKLOADS_REPO_PATH.resolve()


def _run_git(repo_path: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_path), *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    value = completed.stdout.strip()
    return value or None


def _repo_origin(repo_path: Path) -> str:
    configured = os.getenv("GITOPS_WORKLOADS_REPO_URL")
    if configured:
        return configured.strip()
    return _run_git(repo_path, "config", "--get", "remote.origin.url") or repo_path.name


def _repo_revision(repo_path: Path) -> str:
    configured = os.getenv("GITOPS_WORKLOADS_REVISION")
    if configured:
        return configured.strip()
    return _run_git(repo_path, "rev-parse", "HEAD") or "working-tree"


def _load_yaml_documents(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        payload = list(yaml.safe_load_all(handle))
    return [item for item in payload if isinstance(item, dict)]


def _nested_get(payload: dict, *keys: str) -> object | None:
    current: object = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _load_namespace(app_root: Path) -> str | None:
    candidates = [
        app_root / "base" / "namespace.yaml",
        app_root / "base" / "deployment.yaml",
        app_root / "base" / "service.yaml",
    ]
    for candidate in candidates:
        for document in _load_yaml_documents(candidate):
            namespace = _nested_get(document, "metadata", "name")
            if candidate.name != "namespace.yaml":
                namespace = _nested_get(document, "metadata", "namespace") or namespace
            if isinstance(namespace, str) and namespace.strip():
                return namespace.strip()
    return None


def _load_app_label(app_root: Path) -> str | None:
    label_keys = (
        ("metadata", "labels", "app.kubernetes.io/name"),
        ("spec", "selector", "matchLabels", "app.kubernetes.io/name"),
        ("spec", "selector", "app.kubernetes.io/name"),
        ("metadata", "labels", "app"),
    )
    candidates = [
        app_root / "base" / "deployment.yaml",
        app_root / "base" / "service.yaml",
        app_root / "base" / "namespace.yaml",
    ]
    for candidate in candidates:
        for document in _load_yaml_documents(candidate):
            for key_path in label_keys:
                value = _nested_get(document, *key_path)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return None


def _build_source_ref(repo_path: Path, project_path: Path) -> str:
    repo_origin = _repo_origin(repo_path)
    repo_revision = _repo_revision(repo_path)
    relative_path = project_path.relative_to(repo_path).as_posix()
    return f"{repo_origin}@{repo_revision}:{relative_path}"


def discover_gitops_project_records(
    *,
    repo_path: Path | None = None,
    env_name: str | None = None,
    synced_at: datetime | None = None,
) -> tuple[list[ProjectRegistryRecord], list[dict[str, str]]]:
    safe_repo_path = (repo_path or _workloads_repo_path()).resolve()
    safe_synced_at = synced_at or _utc_now()

    if not safe_repo_path.exists():
        return [], [
            {
                "source": DEFAULT_SOURCE,
                "scope": str(safe_repo_path),
                "error": "GitOps workloads repo path does not exist",
            }
        ]

    records: list[ProjectRegistryRecord] = []
    failures: list[dict[str, str]] = []

    env_kustomizations = sorted(safe_repo_path.glob("apps/*/envs/*/kustomization.yaml"))
    for kustomization_path in env_kustomizations:
        env_dir = kustomization_path.parent
        env_value = env_dir.name
        if env_name and env_value != env_name:
            continue

        app_root = env_dir.parent.parent
        project_name = app_root.name
        namespace = _load_namespace(app_root) or project_name
        app_label = _load_app_label(app_root) or project_name
        project_id = _normalize_project_id(app_label)

        if not namespace.strip() or not app_label.strip():
            failures.append(
                {
                    "source": DEFAULT_SOURCE,
                    "scope": env_dir.relative_to(safe_repo_path).as_posix(),
                    "error": "Missing namespace or app label metadata in GitOps app definition",
                }
            )
            continue

        records.append(
            ProjectRegistryRecord(
                project_id=project_id,
                project_name=project_name,
                namespace=namespace,
                env=env_value,
                app_label=app_label,
                source=DEFAULT_SOURCE,
                source_ref=_build_source_ref(safe_repo_path, env_dir),
                last_synced_at=safe_synced_at,
            )
        )

    return records, failures


def _upsert_project_registry_records(
    conn: psycopg.Connection,
    records: list[ProjectRegistryRecord],
) -> tuple[int, int]:
    if not records:
        return 0, 0

    inserted = 0
    updated = 0
    with conn.cursor() as cur:
        for row in records:
            cur.execute(
                """
                INSERT INTO project_registry (
                    project_id,
                    project_name,
                    namespace,
                    env,
                    app_label,
                    source,
                    source_ref,
                    last_synced_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (project_id, env) DO UPDATE
                SET project_name = EXCLUDED.project_name,
                    namespace = EXCLUDED.namespace,
                    app_label = EXCLUDED.app_label,
                    source = EXCLUDED.source,
                    source_ref = EXCLUDED.source_ref,
                    last_synced_at = EXCLUDED.last_synced_at,
                    updated_at = CURRENT_TIMESTAMP
                RETURNING (xmax = 0) AS inserted
                """,
                (
                    row.project_id,
                    row.project_name,
                    row.namespace,
                    row.env,
                    row.app_label,
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


def _prune_project_registry_records(
    conn: psycopg.Connection,
    *,
    envs: set[str],
    keep_keys: set[tuple[str, str]],
) -> int:
    if not envs:
        return 0

    stale_keys: list[tuple[str, str]] = []
    with conn.cursor() as cur:
        for env in sorted(envs):
            cur.execute(
                """
                SELECT project_id, env
                FROM project_registry
                WHERE source = %s
                  AND env = %s
                """,
                (DEFAULT_SOURCE, env),
            )
            for project_id, row_env in cur.fetchall():
                key = (str(project_id), str(row_env))
                if key not in keep_keys:
                    stale_keys.append(key)

        deleted = 0
        for project_id, env in stale_keys:
            cur.execute(
                """
                DELETE FROM project_registry
                WHERE project_id = %s
                  AND env = %s
                  AND source = %s
                """,
                (project_id, env, DEFAULT_SOURCE),
            )
            deleted += cur.rowcount
    return deleted


def sync_project_registry_from_gitops(
    conn: psycopg.Connection,
    *,
    env_name: str | None = None,
    repo_path: Path | None = None,
) -> dict:
    correlation_id = str(uuid4())
    started = time.perf_counter()
    synced_at = _utc_now()
    safe_repo_path = (repo_path or _workloads_repo_path()).resolve()

    records, source_failures = discover_gitops_project_records(
        repo_path=safe_repo_path,
        env_name=env_name,
        synced_at=synced_at,
    )

    deduped: dict[tuple[str, str], ProjectRegistryRecord] = {}
    for row in records:
        deduped[(row.project_id, row.env)] = row
    unique_records = list(deduped.values())
    envs = {env_name} if env_name else {row.env for row in unique_records}
    keep_keys = set(deduped)

    inserted, updated = _upsert_project_registry_records(conn, unique_records)
    deleted = _prune_project_registry_records(conn, envs=envs, keep_keys=keep_keys)
    duration_ms = int((time.perf_counter() - started) * 1000)

    summary = {
        "correlationId": correlation_id,
        "source": DEFAULT_SOURCE,
        "env": env_name or "all",
        "namespaces": sorted({row.namespace for row in unique_records}),
        "discovered": len(records),
        "upserted": len(unique_records),
        "inserted": inserted,
        "updated": updated,
        "deleted": deleted,
        "sourceFailures": source_failures,
        "generatedAt": synced_at.isoformat(),
        "durationMs": duration_ms,
    }
    logger.info(
        "gitops_project_sync_summary correlation_id=%s env=%s discovered=%s upserted=%s inserted=%s updated=%s deleted=%s failures=%s duration_ms=%s",
        correlation_id,
        env_name or "all",
        len(records),
        len(unique_records),
        inserted,
        updated,
        deleted,
        len(source_failures),
        duration_ms,
    )
    return summary
