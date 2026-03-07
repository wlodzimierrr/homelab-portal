from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import base64
import os
from threading import Lock


APPROVED_LOG_PRESETS: dict[str, str] = {
    "errors": ' |= "error"',
    "restarts": ' |= "restart" or |= "CrashLoopBackOff"',
    "warnings": ' |= "warn" or |= "timeout"',
}


RANGE_TO_DELTA: dict[str, timedelta] = {
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "6h": timedelta(hours=6),
    "24h": timedelta(hours=24),
}


@dataclass(frozen=True)
class QuickViewTimeWindow:
    start: datetime
    end: datetime


_rate_state: dict[str, tuple[int, int]] = {}
_rate_lock = Lock()


def get_logs_default_namespace() -> str:
    return os.getenv("LOGS_DEFAULT_NAMESPACE", "default")


def get_logs_rate_limit_per_minute() -> int:
    raw = os.getenv("LOGS_QUICKVIEW_RATE_LIMIT_PER_MIN", "60")
    try:
        value = int(raw)
    except ValueError:
        return 60
    if value <= 0:
        return 60
    return value


def validate_preset(preset: str) -> str:
    if preset not in APPROVED_LOG_PRESETS:
        raise ValueError("Unsupported preset")
    return preset


def parse_time_range(range_value: str) -> timedelta:
    if range_value not in RANGE_TO_DELTA:
        raise ValueError("Unsupported range")
    return RANGE_TO_DELTA[range_value]


def build_preset_query(*, app_label: str, namespace: str, preset: str) -> str:
    suffix = APPROVED_LOG_PRESETS[preset]
    return f'{{namespace="{namespace}", app="{app_label}"}}{suffix}'


def encode_cursor_ns(ts_ns: int) -> str:
    return base64.urlsafe_b64encode(str(ts_ns).encode("utf-8")).decode("ascii")


def decode_cursor_ns(cursor: str) -> int:
    try:
        decoded = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        return int(decoded)
    except Exception as exc:  # noqa: BLE001
        raise ValueError("Invalid cursor") from exc


def build_time_window(
    *,
    now: datetime,
    range_value: str,
    cursor: str | None,
) -> QuickViewTimeWindow:
    delta = parse_time_range(range_value)
    end = now
    if cursor:
        end_ns = decode_cursor_ns(cursor)
        end = datetime.fromtimestamp(end_ns / 1_000_000_000, tz=timezone.utc)
        end = end - timedelta(microseconds=1)
    start = end - delta
    return QuickViewTimeWindow(start=start, end=end)


def enforce_logs_rate_limit(*, identity_key: str, now: datetime) -> None:
    limit = get_logs_rate_limit_per_minute()
    bucket = int(now.timestamp() // 60)
    with _rate_lock:
        current = _rate_state.get(identity_key)
        if current is None or current[0] != bucket:
            _rate_state[identity_key] = (bucket, 1)
            return
        count = current[1] + 1
        if count > limit:
            raise ValueError("Rate limit exceeded")
        _rate_state[identity_key] = (bucket, count)


def clear_rate_limit_state_for_tests() -> None:
    with _rate_lock:
        _rate_state.clear()
