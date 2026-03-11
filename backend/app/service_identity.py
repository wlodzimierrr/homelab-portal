from __future__ import annotations

import re


SERVICE_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def normalize_service_id(value: str | None) -> str:
    trimmed = (value or "").strip().lower()
    if not trimmed:
        return "unknown-service"

    normalized = re.sub(r"[^a-z0-9]+", "-", trimmed)
    normalized = normalized.strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    return normalized or "unknown-service"


def is_canonical_service_id(value: str | None) -> bool:
    trimmed = (value or "").strip()
    if not trimmed:
        return False
    return trimmed == normalize_service_id(trimmed)


def default_argo_app_name(service_id: str, env: str) -> str:
    normalized_env = (env or "").strip().lower() or "dev"
    return f"{normalize_service_id(service_id)}-{normalized_env}"


def parse_source_ref_path(source_ref: str | None) -> str | None:
    if not source_ref:
        return None
    trimmed = source_ref.strip()
    if not trimmed or ":" not in trimmed:
        return None
    path = trimmed.rsplit(":", 1)[1].strip()
    return path or None
