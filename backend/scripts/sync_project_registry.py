#!/usr/bin/env python3
from __future__ import annotations

import json

import psycopg

from app.db import get_psycopg_database_url
from app.gitops_project_sync import sync_project_registry_from_gitops


def main() -> None:
    with psycopg.connect(get_psycopg_database_url()) as conn:
        summary = sync_project_registry_from_gitops(conn)
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
