#!/usr/bin/env python3
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

import psycopg

from app.db import get_psycopg_database_url
from app.gitops_project_sync import sync_project_registry_from_gitops
from app.service_registry_sync import sync_service_registry_from_cluster

logger = logging.getLogger("homelab.backend.catalog_sync")


def _env_name() -> str | None:
    raw = os.getenv("PORTAL_ENV")
    if raw and raw.strip():
        return raw.strip()
    return None


def _configure_logging() -> None:
    if logging.getLogger().handlers:
        return
    logging.basicConfig(level=logging.INFO, format="%(message)s")


def _source_name(summary: dict) -> str:
    return str(summary.get("source") or "unknown")


def _source_failures(summary: dict) -> list[dict]:
    failures = summary.get("sourceFailures", [])
    if isinstance(failures, list):
        return failures
    return []


def _emit_source_summary(env_name: str | None, summary: dict) -> None:
    logger.info(
        "catalog_sync_source_result env=%s source=%s correlation_id=%s discovered=%s upserted=%s inserted=%s updated=%s deleted=%s failures=%s duration_ms=%s",
        env_name or "all",
        _source_name(summary),
        summary.get("correlationId"),
        summary.get("discovered"),
        summary.get("upserted"),
        summary.get("inserted"),
        summary.get("updated"),
        summary.get("deleted"),
        len(_source_failures(summary)),
        summary.get("durationMs"),
    )


def _emit_run_result(output: dict, exit_code: int) -> None:
    sources = output.get("sources", {})
    project_summary = sources.get("gitops_apps", {})
    service_summary = sources.get("cluster_services", {})
    log_method = logger.error if output.get("hasFailures") else logger.info
    log_method(
        "catalog_sync_run_result env=%s has_failures=%s exit_code=%s gitops_failures=%s cluster_failures=%s gitops_correlation_id=%s cluster_correlation_id=%s",
        output.get("env"),
        output.get("hasFailures"),
        exit_code,
        len(_source_failures(project_summary)),
        len(_source_failures(service_summary)),
        project_summary.get("correlationId"),
        service_summary.get("correlationId"),
    )


def main() -> int:
    _configure_logging()
    env_name = _env_name()
    try:
        with psycopg.connect(get_psycopg_database_url()) as conn:
            project_summary = sync_project_registry_from_gitops(conn, env_name=env_name)
            service_summary = sync_service_registry_from_cluster(conn, env_name=env_name)
    except Exception as exc:
        output = {
            "generatedAt": datetime.now(tz=timezone.utc).isoformat(),
            "env": env_name or "all",
            "sources": {},
            "hasFailures": True,
            "fatalError": str(exc),
        }
        print(json.dumps(output, indent=2, sort_keys=True))
        logger.exception(
            "catalog_sync_run_error env=%s error=%s",
            env_name or "all",
            exc,
        )
        _emit_run_result(output, 1)
        return 1

    output = {
        "generatedAt": datetime.now(tz=timezone.utc).isoformat(),
        "env": env_name or "all",
        "sources": {
            "gitops_apps": project_summary,
            "cluster_services": service_summary,
        },
        "hasFailures": bool(project_summary["sourceFailures"] or service_summary["sourceFailures"]),
    }
    _emit_source_summary(env_name, project_summary)
    _emit_source_summary(env_name, service_summary)
    print(json.dumps(output, indent=2, sort_keys=True))
    exit_code = 1 if output["hasFailures"] else 0
    _emit_run_result(output, exit_code)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
