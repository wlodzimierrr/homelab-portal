from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Callable, TypeVar


T = TypeVar("T")


class TTLCache:
    def __init__(self) -> None:
        self._lock = Lock()
        self._items: dict[tuple, tuple[datetime, object]] = {}

    def get_or_set(
        self,
        *,
        key: tuple,
        ttl_seconds: int,
        loader: Callable[[], T],
    ) -> T:
        now = datetime.now(tz=timezone.utc)
        if ttl_seconds <= 0:
            return loader()

        with self._lock:
            current = self._items.get(key)
            if current is not None:
                expires_at, value = current
                if expires_at > now:
                    return value  # type: ignore[return-value]

        value = loader()
        expires_at = now + timedelta(seconds=ttl_seconds)
        with self._lock:
            self._items[key] = (expires_at, value)
        return value

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
