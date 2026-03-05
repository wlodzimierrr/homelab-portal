from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from typing import Any


@dataclass(frozen=True)
class ActiveAlert:
    id: str
    severity: str
    title: str
    description: str | None
    starts_at: str
    labels: dict[str, str]
    service_id: str | None
    env: str | None


def get_alertmanager_base_url() -> str:
    return os.getenv(
        "ALERTMANAGER_BASE_URL",
        "http://alertmanager-operated.monitoring.svc.cluster.local:9093",
    ).rstrip("/")


def _csv_set(value: str) -> set[str]:
    return {item.strip().lower() for item in value.split(",") if item.strip()}


def critical_severity_values() -> set[str]:
    return _csv_set(
        os.getenv("ALERT_SEVERITY_CRITICAL_VALUES", "critical,error,page")
    )


def warning_severity_values() -> set[str]:
    return _csv_set(
        os.getenv("ALERT_SEVERITY_WARNING_VALUES", "warning,warn,info")
    )


def map_alert_severity(raw: str | None) -> str | None:
    if not raw:
        return None

    normalized = raw.strip().lower()
    if normalized in critical_severity_values():
        return "critical"
    if normalized in warning_severity_values():
        return "warning"
    return None


def _first_label(labels: dict[str, str], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = labels.get(key)
        if value and value.strip():
            return value.strip()
    return None


def infer_service_id(labels: dict[str, str]) -> str | None:
    return _first_label(
        labels,
        (
            "serviceId",
            "service_id",
            "service",
            "app",
            "app_kubernetes_io_name",
            "k8s_app",
            "job",
        ),
    )


def infer_env(labels: dict[str, str]) -> str | None:
    env = _first_label(labels, ("env", "environment"))
    if env:
        return env

    namespace = labels.get("namespace", "")
    lowered = namespace.lower()
    if lowered.endswith("-dev"):
        return "dev"
    if lowered.endswith("-staging"):
        return "staging"
    if lowered.endswith("-prod") or lowered.endswith("-production"):
        return "prod"
    return None


def build_alert_id(labels: dict[str, str], starts_at: str, fallback_title: str) -> str:
    fingerprint = labels.get("fingerprint")
    if fingerprint and fingerprint.strip():
        return fingerprint.strip()

    material = json.dumps(
        {
            "labels": labels,
            "startsAt": starts_at,
            "title": fallback_title,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha1(material.encode("utf-8")).hexdigest()


def _to_str_dict(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    output: dict[str, str] = {}
    for key, value in raw.items():
        output[str(key)] = str(value)
    return output


def normalize_active_alerts(payload: Any) -> list[ActiveAlert]:
    if not isinstance(payload, list):
        return []

    output: list[ActiveAlert] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            continue

        status = _to_str_dict(item.get("status"))
        if status.get("state", "").lower() == "suppressed":
            continue

        labels = _to_str_dict(item.get("labels"))
        annotations = _to_str_dict(item.get("annotations"))
        severity = map_alert_severity(labels.get("severity"))
        if severity is None:
            continue

        title = (
            annotations.get("summary")
            or annotations.get("title")
            or labels.get("alertname")
            or f"Alert {index + 1}"
        )
        description = annotations.get("description") or None
        starts_at = str(item.get("startsAt") or "")
        if not starts_at.strip():
            starts_at = str(item.get("updatedAt") or "")

        service_id = infer_service_id(labels)
        env = infer_env(labels)
        alert_id = build_alert_id(labels, starts_at, title)

        output.append(
            ActiveAlert(
                id=alert_id,
                severity=severity,
                title=title,
                description=description,
                starts_at=starts_at,
                labels=labels,
                service_id=service_id,
                env=env,
            )
        )

    output.sort(key=lambda alert: alert.starts_at, reverse=True)
    return output
