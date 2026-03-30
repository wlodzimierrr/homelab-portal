#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys

import psycopg

from app.db import get_psycopg_database_url
from app.service_registry_sync import inspect_service_registry_sync_namespace_coverage


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect whether one namespace is covered by live service registry sync "
            "using the derived namespace scope for the selected environment."
        )
    )
    parser.add_argument("--namespace", required=True, help="Namespace to inspect")
    parser.add_argument(
        "--env",
        default=os.getenv("PORTAL_ENV", "dev"),
        help="Environment to inspect (default: PORTAL_ENV or dev)",
    )
    args = parser.parse_args()

    try:
        with psycopg.connect(get_psycopg_database_url()) as conn:
            result = inspect_service_registry_sync_namespace_coverage(
                conn,
                env_name=args.env.strip(),
                namespace=args.namespace,
            )
    except Exception as exc:  # noqa: BLE001
        json.dump(
            {
                "namespace": args.namespace.strip(),
                "covered": False,
                "error": str(exc),
                "env": args.env.strip(),
            },
            sys.stdout,
            indent=2,
            sort_keys=True,
        )
        sys.stdout.write("\n")
        return 1

    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
