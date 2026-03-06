#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import re
import sys
from typing import Any
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
        with urlrequest.urlopen(req, timeout=20) as response:
            status = response.status
            raw = response.read().decode("utf-8")
    except urlerror.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"FAIL: {path} returned HTTP {exc.code}: {raw[:300]}")
    payload = json.loads(raw)
    return status, payload


def _is_legacy_project_row(row: dict[str, Any]) -> bool:
    project_id = str(row.get("id", "")).strip()
    project_name = str(row.get("name", "")).strip()
    return (
        project_id in LEGACY_ID_EXACT
        or bool(LEGACY_ID_PATTERN.match(project_id))
        or project_name in LEGACY_NAME_EXACT
    )


def _validate_projects(payload: object) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("projects"), list):
        raise SystemExit("FAIL: /projects response shape is invalid")

    rows = [row for row in payload["projects"] if isinstance(row, dict)]
    legacy_rows = [row for row in rows if _is_legacy_project_row(row)]
    if legacy_rows:
        raise SystemExit(
            "FAIL: seeded/default legacy project rows detected: "
            + json.dumps(legacy_rows, sort_keys=True)
        )

    index: dict[tuple[str, str], dict[str, Any]] = {}
    duplicates: list[str] = []
    for row in rows:
        key = (str(row.get("id", "")).strip(), str(row.get("environment", "")).strip())
        if key in index:
            duplicates.append(f"{key[0]}|{key[1]}")
        index[key] = row
    if duplicates:
        raise SystemExit(
            "FAIL: duplicate /projects rows by (id,environment): " + ", ".join(sorted(duplicates))
        )

    return index, {"count": len(rows), "legacyRows": 0}


def _validate_freshness(payload: object, *, endpoint_name: str) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("freshness"), dict):
        raise SystemExit(f"FAIL: {endpoint_name} freshness payload is invalid")

    freshness = payload["freshness"]
    state = str(freshness.get("state", "")).strip()
    row_count = int(freshness.get("rowCount") or 0)
    if state == "stale":
        raise SystemExit(f"FAIL: {endpoint_name} is stale")
    if state == "empty":
        raise SystemExit(f"FAIL: {endpoint_name} is empty")

    warning = state == "warning"
    return {
        "state": state,
        "rowCount": row_count,
        "warning": warning,
        "lastSyncedAt": freshness.get("lastSyncedAt"),
    }


def _validate_services(payload: object) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("services"), list):
        raise SystemExit("FAIL: /services response shape is invalid")

    rows = [row for row in payload["services"] if isinstance(row, dict)]
    if not rows:
        raise SystemExit("FAIL: /services returned zero rows")

    index: dict[tuple[str, str], dict[str, Any]] = {}
    duplicates: list[str] = []
    for row in rows:
        service_id = str(row.get("serviceId", "")).strip()
        env = str(row.get("env", "")).strip()
        key = (service_id, env)
        if key in index:
            duplicates.append(f"{service_id}|{env}")
        index[key] = row
    if duplicates:
        raise SystemExit("FAIL: duplicate /services rows by (serviceId,env): " + ", ".join(sorted(duplicates)))

    return index, {"count": len(rows)}


def _validate_releases(
    payload: object,
    *,
    project_index: dict[tuple[str, str], dict[str, Any]],
    service_index: dict[tuple[str, str], dict[str, Any]],
) -> tuple[list[str], dict[str, Any]]:
    if not isinstance(payload, list):
        raise SystemExit("FAIL: /releases response shape is invalid")

    service_ids: list[str] = []
    missing_projects: list[str] = []
    missing_services: list[str] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        service_id = str(row.get("serviceId", "")).strip()
        env = str(row.get("env", "")).strip()
        if service_id:
            service_ids.append(service_id)
        if (service_id, env) not in project_index:
            missing_projects.append(f"{service_id}|{env}")
        if (service_id, env) not in service_index:
            missing_services.append(f"{service_id}|{env}")

    if missing_projects:
        raise SystemExit(
            "FAIL: /releases contains service/env rows missing from /projects: "
            + ", ".join(sorted(set(missing_projects)))
        )
    if missing_services:
        raise SystemExit(
            "FAIL: /releases contains service/env rows missing from /services: "
            + ", ".join(sorted(set(missing_services)))
        )

    return sorted(set(service_ids)), {"count": len(payload)}


def _select_service_id(
    explicit_service_id: str,
    *,
    release_service_ids: list[str],
    service_index: dict[tuple[str, str], dict[str, Any]],
    env: str,
) -> str:
    if explicit_service_id.strip():
        return explicit_service_id.strip()

    for service_id in release_service_ids:
        if (service_id, env) in service_index:
            return service_id

    for service_id, row_env in service_index:
        if row_env == env:
            return service_id

    raise SystemExit("FAIL: could not select a serviceId for monitoring checks")


def _validate_metrics(payload: object, *, service_id: str) -> dict[str, Any]:
    if not isinstance(payload, dict) or str(payload.get("serviceId", "")).strip() != service_id:
        raise SystemExit(f"FAIL: /services/{service_id}/metrics/summary response shape is invalid")

    provider_status = payload.get("providerStatus")
    if not isinstance(provider_status, dict) or provider_status.get("status") != "healthy":
        raise SystemExit(f"FAIL: metrics provider is not healthy for {service_id}")

    no_data = payload.get("noData")
    no_data_count = 0
    if isinstance(no_data, dict):
        no_data_count = sum(1 for value in no_data.values() if value is True)

    return {
        "provider": provider_status.get("provider"),
        "status": provider_status.get("status"),
        "noDataFields": no_data_count,
    }


def _validate_alerts(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("alerts"), list):
        raise SystemExit("FAIL: /alerts/active response shape is invalid")

    provider_status = payload.get("providerStatus")
    if not isinstance(provider_status, dict) or provider_status.get("status") != "healthy":
        raise SystemExit("FAIL: alerts provider is not healthy")

    return {
        "provider": provider_status.get("provider"),
        "status": provider_status.get("status"),
        "count": len(payload["alerts"]),
    }


def build_validation_report(
    *,
    env: str,
    service_id: str,
    project_result: dict[str, Any],
    project_diagnostics: dict[str, Any],
    service_result: dict[str, Any],
    service_diagnostics: dict[str, Any],
    release_result: dict[str, Any],
    metrics_result: dict[str, Any],
    alerts_result: dict[str, Any],
) -> dict[str, Any]:
    warnings: list[str] = []
    if project_diagnostics["warning"]:
        warnings.append("Project catalog freshness is in warning state.")
    if service_diagnostics["warning"]:
        warnings.append("Service registry freshness is in warning state.")

    return {
        "generatedAt": datetime.now(tz=timezone.utc).isoformat(),
        "env": env,
        "serviceId": service_id,
        "summary": {
            "projects": project_result,
            "projectDiagnostics": project_diagnostics,
            "services": service_result,
            "serviceDiagnostics": service_diagnostics,
            "releases": release_result,
            "metrics": metrics_result,
            "alerts": alerts_result,
        },
        "warnings": warnings,
        "status": "pass",
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "End-to-end validation for live projects/services/monitoring flow with JSON report output."
        )
    )
    parser.add_argument("--api-base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--auth-token", default="")
    parser.add_argument("--auth-cookie", default="")
    parser.add_argument("--env", default="dev")
    parser.add_argument("--service-id", default="")
    parser.add_argument("--release-limit", type=int, default=50)
    parser.add_argument("--report-file", default="")
    args = parser.parse_args()

    _, projects_payload = _request_json(
        args.api_base_url,
        f"/projects?env={urlparse.quote(args.env)}",
        token=args.auth_token,
        cookie=args.auth_cookie,
    )
    project_index, project_result = _validate_projects(projects_payload)

    _, project_diagnostics_payload = _request_json(
        args.api_base_url,
        f"/projects/diagnostics?env={urlparse.quote(args.env)}",
        token=args.auth_token,
        cookie=args.auth_cookie,
    )
    project_diagnostics = _validate_freshness(
        project_diagnostics_payload,
        endpoint_name="/projects/diagnostics",
    )

    _, services_payload = _request_json(
        args.api_base_url,
        f"/services?env={urlparse.quote(args.env)}",
        token=args.auth_token,
        cookie=args.auth_cookie,
    )
    service_index, service_result = _validate_services(services_payload)

    _, service_diagnostics_payload = _request_json(
        args.api_base_url,
        f"/service-registry/diagnostics?env={urlparse.quote(args.env)}",
        token=args.auth_token,
        cookie=args.auth_cookie,
    )
    service_diagnostics = _validate_freshness(
        service_diagnostics_payload,
        endpoint_name="/service-registry/diagnostics",
    )

    _, releases_payload = _request_json(
        args.api_base_url,
        f"/releases?env={urlparse.quote(args.env)}&limit={args.release_limit}",
        token=args.auth_token,
        cookie=args.auth_cookie,
    )
    release_service_ids, release_result = _validate_releases(
        releases_payload,
        project_index=project_index,
        service_index=service_index,
    )

    service_id = _select_service_id(
        args.service_id,
        release_service_ids=release_service_ids,
        service_index=service_index,
        env=args.env,
    )

    _, metrics_payload = _request_json(
        args.api_base_url,
        f"/services/{urlparse.quote(service_id, safe='')}/metrics/summary?range=24h",
        token=args.auth_token,
        cookie=args.auth_cookie,
    )
    metrics_result = _validate_metrics(metrics_payload, service_id=service_id)

    _, alerts_payload = _request_json(
        args.api_base_url,
        f"/alerts/active?env={urlparse.quote(args.env)}",
        token=args.auth_token,
        cookie=args.auth_cookie,
    )
    alerts_result = _validate_alerts(alerts_payload)

    report = build_validation_report(
        env=args.env,
        service_id=service_id,
        project_result=project_result,
        project_diagnostics=project_diagnostics,
        service_result=service_result,
        service_diagnostics=service_diagnostics,
        release_result=release_result,
        metrics_result=metrics_result,
        alerts_result=alerts_result,
    )

    report_json = json.dumps(report, indent=2, sort_keys=True)
    if args.report_file:
        with open(args.report_file, "w", encoding="utf-8") as handle:
            handle.write(report_json + "\n")
    print(report_json)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        sys.exit(130)
