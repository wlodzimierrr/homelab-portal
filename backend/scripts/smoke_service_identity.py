#!/usr/bin/env python3
"""Smoke test: validate canonical service identity for live portal services.

Fetches the /services list, then for each service verifies that the
app_label returned by the API matches the service_id exactly. Any mismatch
indicates identity drift (e.g. the running workload has a stale or incorrect
app.kubernetes.io/name label).

Exit 0 when all checks pass, exit 1 if any drift is detected or if fewer
than --min-services live services are found.

Usage:
    python smoke_service_identity.py \
        --base-url http://127.0.0.1:8000 \
        --token dev-static-token \
        [--env dev] \
        [--min-services 2]
"""
from __future__ import annotations

import argparse
import json
import sys
from urllib import error as urlerror
from urllib import request as urlrequest


def api_get(url: str, token: str) -> object:
    req = urlrequest.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    with urlrequest.urlopen(req, timeout=10) as response:
        return json.loads(response.read())


def check_service_identity(
    base: str,
    token: str,
    env: str | None,
    min_services: int,
) -> int:
    url = f"{base}/services"
    if env:
        url += f"?env={env}"

    try:
        data = api_get(url, token)
    except urlerror.HTTPError as exc:
        print(json.dumps({"error": f"GET /services returned {exc.code}"}))
        return 1
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": str(exc)}))
        return 1

    services: list[dict] = data.get("services", [])  # type: ignore[union-attr]
    if len(services) < min_services:
        print(
            json.dumps(
                {
                    "result": "FAIL",
                    "reason": f"expected at least {min_services} services, found {len(services)}",
                }
            )
        )
        return 1

    drift: list[dict] = []
    ok: list[str] = []

    for svc in services:
        service_id: str = svc.get("serviceId", "")
        app_label: str = svc.get("appLabel", "")
        svc_env: str = svc.get("env", "")

        if app_label == service_id:
            ok.append(f"{service_id}/{svc_env}")
            print(
                json.dumps(
                    {
                        "check": "identity",
                        "serviceId": service_id,
                        "env": svc_env,
                        "appLabel": app_label,
                        "result": "OK",
                    }
                )
            )
        else:
            drift.append(
                {
                    "serviceId": service_id,
                    "env": svc_env,
                    "appLabel": app_label,
                    "expected": service_id,
                }
            )
            print(
                json.dumps(
                    {
                        "check": "identity",
                        "serviceId": service_id,
                        "env": svc_env,
                        "appLabel": app_label,
                        "expected": service_id,
                        "result": "DRIFT",
                    }
                ),
                file=sys.stderr,
            )

    summary = {
        "summary": {
            "total": len(services),
            "ok": len(ok),
            "drift": len(drift),
            "result": "PASS" if not drift else "FAIL",
        }
    }
    print(json.dumps(summary))

    if drift:
        print(json.dumps({"driftDetails": drift}), file=sys.stderr)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Smoke test for canonical service identity via portal API"
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Backend base URL")
    parser.add_argument("--token", default="dev-static-token", help="Bearer token")
    parser.add_argument("--env", default=None, help="Filter by environment (dev/prod)")
    parser.add_argument(
        "--min-services",
        type=int,
        default=2,
        help="Minimum number of services expected (default: 2)",
    )
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    return check_service_identity(base, args.token, args.env, args.min_services)


if __name__ == "__main__":
    raise SystemExit(main())
