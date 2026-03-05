#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest


LEGACY_ID_PATTERN = re.compile(r"^proj($|[-_])")
LEGACY_ID_EXACT = {"proj-dev", "proj-prod"}
LEGACY_NAME_EXACT = {"Homelab App"}


def _request_json(base_url: str, path: str, *, token: str, cookie: str) -> tuple[int, object]:
    url = f"{base_url.rstrip('/')}{path}"
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if cookie:
        headers["Cookie"] = cookie
    req = urlrequest.Request(url, headers=headers)
    try:
        with urlrequest.urlopen(req, timeout=15) as response:
            status = response.status
            raw = response.read().decode("utf-8")
    except urlerror.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"FAIL: {path} returned HTTP {exc.code}: {raw[:300]}")
    payload = json.loads(raw)
    return status, payload


def _is_legacy_project_row(row: dict) -> bool:
    project_id = str(row.get("id", "")).strip()
    project_name = str(row.get("name", "")).strip()
    return (
        project_id in LEGACY_ID_EXACT
        or bool(LEGACY_ID_PATTERN.match(project_id))
        or project_name in LEGACY_NAME_EXACT
    )


def _check_projects(payload: object) -> dict[tuple[str, str], dict]:
    if not isinstance(payload, dict) or not isinstance(payload.get("projects"), list):
        raise SystemExit("FAIL: /projects response shape is invalid")

    rows = payload["projects"]
    legacy_rows = [row for row in rows if isinstance(row, dict) and _is_legacy_project_row(row)]
    if legacy_rows:
        print("FAIL: seeded/default legacy project rows detected:")
        print(json.dumps(legacy_rows, indent=2))
        raise SystemExit(1)

    key_map: dict[tuple[str, str], dict] = {}
    duplicates: list[tuple[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        service_id = str(row.get("id", "")).strip()
        env = str(row.get("environment", "")).strip()
        key = (service_id, env)
        if key in key_map:
            duplicates.append(key)
        key_map[key] = row

    if duplicates:
        raise SystemExit(f"FAIL: duplicate /projects rows by (id,environment): {duplicates}")

    print(f"PASS: /projects canonical rows={len(rows)} (no seeded/default legacy rows)")
    return key_map


def _check_releases(
    payload: object,
    *,
    projects_index: dict[tuple[str, str], dict],
) -> list[str]:
    if not isinstance(payload, list):
        raise SystemExit("FAIL: /releases response shape is invalid")

    missing_keys: list[str] = []
    service_ids: list[str] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        service_id = str(row.get("serviceId", "")).strip()
        env = str(row.get("env", "")).strip()
        if service_id:
            service_ids.append(service_id)
        if (service_id, env) not in projects_index:
            missing_keys.append(f"{service_id}|{env}")

    if missing_keys:
        raise SystemExit(
            "FAIL: /releases contains service/env rows missing from /projects: "
            + ", ".join(sorted(set(missing_keys)))
        )
    print(f"PASS: /releases join integrity ok rows={len(payload)}")
    return sorted(set(service_ids))


def _check_service_routes(base_url: str, service_ids: list[str], *, token: str, cookie: str, max_checks: int) -> None:
    for service_id in service_ids[:max_checks]:
        safe = urlparse.quote(service_id, safe="")
        path = f"/services/{safe}/metrics/summary?range=1h"
        status, payload = _request_json(base_url, path, token=token, cookie=cookie)
        if status not in (200,):
            raise SystemExit(f"FAIL: service route check failed for {service_id} status={status}")
        if not isinstance(payload, dict) or payload.get("serviceId") != service_id:
            raise SystemExit(f"FAIL: service route payload mismatch for {service_id}")
    print(f"PASS: service-route consistency checks ok count={min(len(service_ids), max_checks)}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Smoke checks for /projects cutover to canonical live-source and release/service join integrity."
        )
    )
    parser.add_argument("--api-base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--auth-token", default="")
    parser.add_argument("--auth-cookie", default="")
    parser.add_argument("--env", default="dev")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--service-check-limit", type=int, default=3)
    args = parser.parse_args()

    status, projects_payload = _request_json(
        args.api_base_url,
        "/projects",
        token=args.auth_token,
        cookie=args.auth_cookie,
    )
    if status != 200:
        raise SystemExit(f"FAIL: /projects status={status}")
    projects_index = _check_projects(projects_payload)

    status, releases_payload = _request_json(
        args.api_base_url,
        f"/releases?env={urlparse.quote(args.env)}&limit={args.limit}",
        token=args.auth_token,
        cookie=args.auth_cookie,
    )
    if status != 200:
        raise SystemExit(f"FAIL: /releases status={status}")
    service_ids = _check_releases(releases_payload, projects_index=projects_index)

    _check_service_routes(
        args.api_base_url,
        service_ids,
        token=args.auth_token,
        cookie=args.auth_cookie,
        max_checks=max(1, args.service_check_limit),
    )
    print("PASS: project-source cutover smoke checks passed")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)

