#!/usr/bin/env python3
from __future__ import annotations

import json

import psycopg

from app.db import get_psycopg_database_url
from app.service_registry_sync import sync_service_registry_from_cluster


def main() -> None:
    with psycopg.connect(get_psycopg_database_url()) as conn:
        summary = sync_service_registry_from_cluster(conn)
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

