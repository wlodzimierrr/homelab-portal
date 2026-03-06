from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import socket
import time
from urllib import error as urlerror
from urllib import request as urlrequest

from fastapi import HTTPException, status

from app.alerts_feed import get_alertmanager_base_url


def get_prometheus_base_url() -> str:
    return os.getenv(
        "PROMETHEUS_BASE_URL",
        "http://prometheus-operated.monitoring.svc.cluster.local:9090",
    ).rstrip("/")


def get_loki_base_url() -> str:
    return os.getenv(
        "LOKI_BASE_URL",
        "http://loki.monitoring.svc.cluster.local:3100",
    ).rstrip("/")


def get_monitoring_timeout_seconds() -> float:
    raw = os.getenv("PROMETHEUS_TIMEOUT_SECONDS", "8")
    try:
        value = float(raw)
    except ValueError:
        return 8.0
    return value if value > 0 else 8.0


def _provider_base_url(provider: str) -> str:
    if provider == "prometheus":
        return get_prometheus_base_url()
    if provider == "loki":
        return get_loki_base_url()
    if provider == "alertmanager":
        return get_alertmanager_base_url()
    raise ValueError(f"Unsupported monitoring provider: {provider}")


def _provider_probe_path(provider: str) -> str:
    if provider == "prometheus":
        return "/-/healthy"
    if provider == "loki":
        return "/ready"
    if provider == "alertmanager":
        return "/-/ready"
    raise ValueError(f"Unsupported monitoring provider: {provider}")


def build_provider_status(
    *,
    provider: str,
    base_url: str,
    status_value: str,
    reachable: bool,
    checked_at: str,
    correlation_id: str | None,
    latency_ms: int | None = None,
    http_status: int | None = None,
    error: str | None = None,
    probe_path: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "provider": provider,
        "baseUrl": base_url,
        "status": status_value,
        "reachable": reachable,
        "checkedAt": checked_at,
    }
    if correlation_id:
        payload["correlationId"] = correlation_id
    if latency_ms is not None:
        payload["latencyMs"] = latency_ms
    if http_status is not None:
        payload["httpStatus"] = http_status
    if error:
        payload["error"] = error
    if probe_path:
        payload["probePath"] = probe_path
    return payload


def build_provider_error_detail(
    *,
    message: str,
    provider_status: dict[str, object],
) -> dict[str, object]:
    detail = {
        "message": message,
        "providerStatus": provider_status,
    }
    correlation_id = provider_status.get("correlationId")
    if isinstance(correlation_id, str) and correlation_id:
        detail["correlationId"] = correlation_id
    return detail


def raise_provider_http_error(
    *,
    provider: str,
    base_url: str,
    correlation_id: str,
    http_status: int,
    error: str,
    checked_at: str,
    latency_ms: int | None = None,
    message: str = "Monitoring provider query failed.",
) -> None:
    provider_status = build_provider_status(
        provider=provider,
        base_url=base_url,
        status_value="auth_error" if http_status in (401, 403) else "http_error",
        reachable=True,
        checked_at=checked_at,
        correlation_id=correlation_id,
        latency_ms=latency_ms,
        http_status=http_status,
        error=error,
    )
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=build_provider_error_detail(
            message=message,
            provider_status=provider_status,
        ),
    )


def raise_provider_unreachable_error(
    *,
    provider: str,
    base_url: str,
    correlation_id: str,
    error: str,
    checked_at: str,
    latency_ms: int | None = None,
    message: str = "Monitoring provider query failed.",
) -> None:
    provider_status = build_provider_status(
        provider=provider,
        base_url=base_url,
        status_value="unreachable",
        reachable=False,
        checked_at=checked_at,
        correlation_id=correlation_id,
        latency_ms=latency_ms,
        error=error,
    )
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=build_provider_error_detail(
            message=message,
            provider_status=provider_status,
        ),
    )


def raise_provider_bad_payload_error(
    *,
    provider: str,
    base_url: str,
    correlation_id: str,
    error: str,
    checked_at: str,
    latency_ms: int | None = None,
    message: str = "Monitoring provider query failed.",
) -> None:
    provider_status = build_provider_status(
        provider=provider,
        base_url=base_url,
        status_value="bad_payload",
        reachable=True,
        checked_at=checked_at,
        correlation_id=correlation_id,
        latency_ms=latency_ms,
        error=error,
    )
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=build_provider_error_detail(
            message=message,
            provider_status=provider_status,
        ),
    )


def probe_monitoring_provider(
    provider: str,
    *,
    correlation_id: str,
    timeout_seconds: float | None = None,
) -> dict[str, object]:
    base_url = _provider_base_url(provider)
    probe_path = _provider_probe_path(provider)
    checked_at = datetime.now(tz=timezone.utc).isoformat()
    timeout = timeout_seconds or get_monitoring_timeout_seconds()
    request_started = time.perf_counter()
    request = urlrequest.Request(f"{base_url}{probe_path}", method="GET")

    try:
        with urlrequest.urlopen(request, timeout=timeout):
            latency_ms = int((time.perf_counter() - request_started) * 1000)
            return build_provider_status(
                provider=provider,
                base_url=base_url,
                status_value="healthy",
                reachable=True,
                checked_at=checked_at,
                correlation_id=correlation_id,
                latency_ms=latency_ms,
                probe_path=probe_path,
            )
    except urlerror.HTTPError as exc:
        latency_ms = int((time.perf_counter() - request_started) * 1000)
        body = exc.read().decode("utf-8", errors="replace")[:400]
        return build_provider_status(
            provider=provider,
            base_url=base_url,
            status_value="auth_error" if exc.code in (401, 403) else "http_error",
            reachable=True,
            checked_at=checked_at,
            correlation_id=correlation_id,
            latency_ms=latency_ms,
            http_status=exc.code,
            error=body or str(exc),
            probe_path=probe_path,
        )
    except (urlerror.URLError, TimeoutError, socket.timeout) as exc:
        latency_ms = int((time.perf_counter() - request_started) * 1000)
        reason = getattr(exc, "reason", exc)
        return build_provider_status(
            provider=provider,
            base_url=base_url,
            status_value="unreachable",
            reachable=False,
            checked_at=checked_at,
            correlation_id=correlation_id,
            latency_ms=latency_ms,
            error=str(reason),
            probe_path=probe_path,
        )
    except Exception as exc:
        latency_ms = int((time.perf_counter() - request_started) * 1000)
        return build_provider_status(
            provider=provider,
            base_url=base_url,
            status_value="error",
            reachable=False,
            checked_at=checked_at,
            correlation_id=correlation_id,
            latency_ms=latency_ms,
            error=str(exc),
            probe_path=probe_path,
        )


def probe_monitoring_providers() -> list[dict[str, object]]:
    return [
        probe_monitoring_provider("prometheus", correlation_id="health-prometheus"),
        probe_monitoring_provider("loki", correlation_id="health-loki"),
        probe_monitoring_provider("alertmanager", correlation_id="health-alertmanager"),
    ]


def extract_http_error_body(exc: urlerror.HTTPError) -> str:
    return exc.read().decode("utf-8", errors="replace")[:400]


def load_json_from_provider(
    *,
    provider: str,
    endpoint: str,
    correlation_id: str,
    timeout_seconds: float | None = None,
    message: str = "Monitoring provider query failed.",
) -> tuple[object, dict[str, object]]:
    checked_at = datetime.now(tz=timezone.utc).isoformat()
    timeout = timeout_seconds or get_monitoring_timeout_seconds()
    request_started = time.perf_counter()

    try:
        with urlrequest.urlopen(endpoint, timeout=timeout) as response:
            latency_ms = int((time.perf_counter() - request_started) * 1000)
            payload = json.loads(response.read())
            provider_status = build_provider_status(
                provider=provider,
                base_url=_provider_base_url(provider),
                status_value="healthy",
                reachable=True,
                checked_at=checked_at,
                correlation_id=correlation_id,
                latency_ms=latency_ms,
            )
            return payload, provider_status
    except urlerror.HTTPError as exc:
        latency_ms = int((time.perf_counter() - request_started) * 1000)
        raise_provider_http_error(
            provider=provider,
            base_url=_provider_base_url(provider),
            correlation_id=correlation_id,
            http_status=exc.code,
            error=extract_http_error_body(exc),
            checked_at=checked_at,
            latency_ms=latency_ms,
            message=message,
        )
    except (urlerror.URLError, TimeoutError, socket.timeout) as exc:
        latency_ms = int((time.perf_counter() - request_started) * 1000)
        reason = getattr(exc, "reason", exc)
        raise_provider_unreachable_error(
            provider=provider,
            base_url=_provider_base_url(provider),
            correlation_id=correlation_id,
            error=str(reason),
            checked_at=checked_at,
            latency_ms=latency_ms,
            message=message,
        )
    except Exception as exc:
        latency_ms = int((time.perf_counter() - request_started) * 1000)
        raise_provider_unreachable_error(
            provider=provider,
            base_url=_provider_base_url(provider),
            correlation_id=correlation_id,
            error=str(exc),
            checked_at=checked_at,
            latency_ms=latency_ms,
            message=message,
        )
