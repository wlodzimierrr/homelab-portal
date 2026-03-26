"""Helpers shared by split route modules."""

from collections.abc import Callable
from functools import partial, wraps
from typing import Any

from starlette.concurrency import run_in_threadpool


def wrap_sync_endpoint(func: Callable[..., Any]) -> Callable[..., Any]:
    """Expose legacy sync handlers as async endpoints without changing signatures."""

    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        return await run_in_threadpool(partial(func, *args, **kwargs))

    return wrapper
