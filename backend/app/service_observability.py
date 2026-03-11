from __future__ import annotations

from typing import Literal


ObservabilityMode = Literal["app-native", "ingress-derived", "no-http"]

OBSERVABILITY_MODES: tuple[ObservabilityMode, ...] = (
    "app-native",
    "ingress-derived",
    "no-http",
)

OBSERVABILITY_MODE_SET = set(OBSERVABILITY_MODES)


def normalize_observability_mode(value: str | None) -> ObservabilityMode | None:
    trimmed = (value or "").strip().lower()
    if not trimmed:
        return None

    normalized = trimmed.replace("_", "-")
    if normalized in OBSERVABILITY_MODE_SET:
        return normalized  # type: ignore[return-value]
    return None


def is_valid_observability_mode(value: str | None) -> bool:
    return normalize_observability_mode(value) is not None


def observability_metrics_authority(
    value: str | None,
) -> Literal["app", "ingress", "none"] | None:
    mode = normalize_observability_mode(value)
    if mode == "app-native":
        return "app"
    if mode == "ingress-derived":
        return "ingress"
    if mode == "no-http":
        return "none"
    return None
