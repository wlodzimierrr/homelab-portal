#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

import psycopg

from app.db import get_psycopg_database_url
from app.gitops_project_sync import sync_project_registry_from_gitops
from app.service_registry_sync import sync_service_registry_from_cluster


def _env_name() -> str | None:
    raw = os.getenv("PORTAL_ENV")
    if raw and raw.strip():
        return raw.strip()
    return None


def main() -> int:
    env_name = _env_name()
    with psycopg.connect(get_psycopg_database_url()) as conn:
        project_summary = sync_project_registry_from_gitops(conn, env_name=env_name)
        service_summary = sync_service_registry_from_cluster(conn, env_name=env_name)

    output = {
        "generatedAt": datetime.now(tz=timezone.utc).isoformat(),
        "env": env_name or "all",
        "sources": {
            "gitops_apps": project_summary,
            "cluster_services": service_summary,
        },
        "hasFailures": bool(project_summary["sourceFailures"] or service_summary["sourceFailures"]),
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 1 if output["hasFailures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
