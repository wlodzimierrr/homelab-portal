"""Startup and shutdown wiring for long-lived in-process background jobs."""

from collections.abc import Callable
import logging
from threading import Event, Thread

from fastapi import FastAPI


def register_deployment_reconciler_jobs(
    app: FastAPI,
    *,
    enabled_fn: Callable[[], bool],
    interval_seconds_fn: Callable[[], int],
    reconcile_fn: Callable[[], None],
    logger: logging.Logger,
) -> None:
    """Register the deployment reconciler loop against the FastAPI app lifecycle."""

    stop_event = Event()
    worker_thread: Thread | None = None

    @app.on_event("startup")
    def start_deployment_reconciler_loop() -> None:
        nonlocal worker_thread

        if not enabled_fn():
            return
        if worker_thread and worker_thread.is_alive():
            return

        stop_event.clear()

        def _run() -> None:
            logger.info(
                "deployment_reconciler_started interval_seconds=%s",
                interval_seconds_fn(),
            )
            while not stop_event.is_set():
                try:
                    reconcile_fn()
                except Exception as exc:  # pragma: no cover - live background loop only
                    logger.warning("deployment_reconciler_iteration_failed error=%s", exc)
                stop_event.wait(interval_seconds_fn())

        worker_thread = Thread(
            target=_run,
            name="deployment-reconciler",
            daemon=True,
        )
        worker_thread.start()

    @app.on_event("shutdown")
    def stop_deployment_reconciler_loop() -> None:
        nonlocal worker_thread

        stop_event.set()
        if worker_thread and worker_thread.is_alive():
            worker_thread.join(timeout=1.0)
        worker_thread = None
