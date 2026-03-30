#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys

from app.helpers.catalog_helpers import (
    _load_project_catalog_rows,
    _load_project_rows,
    _load_service_rows,
)
from app.release_traceability import load_argo_metadata_rows, load_ci_metadata_rows
from app.service_contract_diagnostics import build_service_contract_diagnostics


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Diagnose why one service/env is missing release or observability metadata. "
            "Reads the live registry/catalog plus RELEASE_* metadata inputs and prints JSON."
        )
    )
    parser.add_argument("--service-id", required=True, help="Canonical service id to diagnose")
    parser.add_argument(
        "--env",
        default=os.getenv("PORTAL_ENV", "dev"),
        help="Environment to diagnose (default: PORTAL_ENV or dev)",
    )
    args = parser.parse_args()

    try:
        diagnostics = build_service_contract_diagnostics(
            service_id=args.service_id.strip(),
            env=args.env.strip(),
            service_rows=_load_service_rows(),
            project_catalog_rows=_load_project_catalog_rows(),
            project_registry_rows=_load_project_rows(),
            ci_rows=load_ci_metadata_rows(),
            argo_rows=load_argo_metadata_rows(),
        )
    except Exception as exc:  # noqa: BLE001
        json.dump(
            {
                "serviceId": args.service_id.strip(),
                "env": args.env.strip(),
                "error": str(exc),
                "hint": "Check backend database connectivity and RELEASE_* metadata inputs.",
            },
            sys.stdout,
            indent=2,
            sort_keys=True,
        )
        sys.stdout.write("\n")
        return 1

    json.dump(diagnostics, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
