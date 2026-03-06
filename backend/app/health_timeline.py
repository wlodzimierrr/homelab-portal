from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os


TimelineStatus = str


@dataclass(frozen=True)
class TimelineThresholds:
    degraded_availability_max: float = 0.995
    down_availability_max: float = 0.6
    degraded_error_rate_min_pct: float = 1.0
    down_error_rate_min_pct: float = 5.0
    degraded_readiness_max: float = 0.98
    down_readiness_max: float = 0.6


@dataclass(frozen=True)
class TimelinePoint:
    timestamp: datetime
    status: TimelineStatus
    reason: str | None = None


@dataclass(frozen=True)
class TimelineSegment:
    start: datetime
    end: datetime
    status: TimelineStatus
    reason: str | None = None


def _read_float(name: str, fallback: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return fallback
    try:
        value = float(raw)
    except ValueError:
        return fallback
    return value


def load_timeline_thresholds() -> TimelineThresholds:
    return TimelineThresholds(
        degraded_availability_max=_read_float(
            "TIMELINE_DEGRADED_AVAILABILITY_MAX",
            0.995,
        ),
        down_availability_max=_read_float(
            "TIMELINE_DOWN_AVAILABILITY_MAX",
            0.6,
        ),
        degraded_error_rate_min_pct=_read_float(
            "TIMELINE_DEGRADED_ERROR_RATE_MIN_PCT",
            1.0,
        ),
        down_error_rate_min_pct=_read_float(
            "TIMELINE_DOWN_ERROR_RATE_MIN_PCT",
            5.0,
        ),
        degraded_readiness_max=_read_float(
            "TIMELINE_DEGRADED_READINESS_MAX",
            0.98,
        ),
        down_readiness_max=_read_float(
            "TIMELINE_DOWN_READINESS_MAX",
            0.6,
        ),
    )


def classify_timeline_status(
    *,
    availability: float | None,
    error_rate_pct: float | None,
    readiness: float | None,
    thresholds: TimelineThresholds,
) -> tuple[TimelineStatus, str | None]:
    missing: list[str] = []
    if availability is None:
        missing.append("availability")
    if readiness is None:
        missing.append("readiness")

    if missing:
        return "unknown", f"missing:{','.join(missing)}"

    if (
        availability <= thresholds.down_availability_max
        or (
            error_rate_pct is not None
            and error_rate_pct >= thresholds.down_error_rate_min_pct
        )
        or readiness <= thresholds.down_readiness_max
    ):
        if availability <= thresholds.down_availability_max:
            return "down", "low_availability"
        if (
            error_rate_pct is not None
            and error_rate_pct >= thresholds.down_error_rate_min_pct
        ):
            return "down", "high_error_rate"
        return "down", "readiness_drop"

    if (
        availability <= thresholds.degraded_availability_max
        or (
            error_rate_pct is not None
            and error_rate_pct >= thresholds.degraded_error_rate_min_pct
        )
        or readiness <= thresholds.degraded_readiness_max
    ):
        if availability <= thresholds.degraded_availability_max:
            return "degraded", "availability_regression"
        if (
            error_rate_pct is not None
            and error_rate_pct >= thresholds.degraded_error_rate_min_pct
        ):
            return "degraded", "error_rate_regression"
        return "degraded", "readiness_regression"

    if error_rate_pct is None:
        return "healthy", "missing:error_rate"

    return "healthy", None


def compact_timeline_points(
    points: list[TimelinePoint],
    *,
    window_start: datetime,
    window_end: datetime,
    step: timedelta,
) -> list[TimelineSegment]:
    if not points:
        return [
            TimelineSegment(
                start=window_start,
                end=window_end,
                status="unknown",
                reason="no_samples",
            )
        ]

    ordered = sorted(points, key=lambda item: item.timestamp)
    segments: list[TimelineSegment] = []

    current_start = ordered[0].timestamp
    current_status = ordered[0].status
    current_reason = ordered[0].reason

    for idx in range(1, len(ordered)):
        item = ordered[idx]
        prev = ordered[idx - 1]
        if item.status == current_status and item.reason == current_reason:
            continue
        segments.append(
            TimelineSegment(
                start=current_start,
                end=item.timestamp,
                status=current_status,
                reason=current_reason,
            )
        )
        current_start = item.timestamp
        current_status = item.status
        current_reason = item.reason

        # Guard against gaps larger than step by emitting unknown bridge.
        if item.timestamp - prev.timestamp > step * 2:
            segments.append(
                TimelineSegment(
                    start=prev.timestamp,
                    end=item.timestamp,
                    status="unknown",
                    reason="sampling_gap",
                )
            )

    final_end = min(window_end, ordered[-1].timestamp + step)
    segments.append(
        TimelineSegment(
            start=current_start,
            end=final_end,
            status=current_status,
            reason=current_reason,
        )
    )

    normalized: list[TimelineSegment] = []
    for segment in segments:
        start = max(segment.start, window_start)
        end = min(segment.end, window_end)
        if end <= start:
            continue
        normalized.append(
            TimelineSegment(
                start=start,
                end=end,
                status=segment.status,
                reason=segment.reason,
            )
        )

    if not normalized:
        return [
            TimelineSegment(
                start=window_start,
                end=window_end,
                status="unknown",
                reason="no_samples",
            )
        ]
    return normalized


def parse_step(step_value: str) -> timedelta:
    if step_value.endswith("m"):
        return timedelta(minutes=int(step_value[:-1]))
    if step_value.endswith("h"):
        return timedelta(hours=int(step_value[:-1]))
    raise ValueError("Unsupported step value")


def parse_range(range_value: str) -> timedelta:
    if range_value == "24h":
        return timedelta(hours=24)
    if range_value == "7d":
        return timedelta(days=7)
    raise ValueError("Unsupported range value")


def now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)
