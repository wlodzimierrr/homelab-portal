from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from threading import Lock
from typing import Literal


@dataclass(frozen=True)
class SecretEditTarget:
    service_id: str
    env: Literal["dev", "prod"]
    file_path: str
    allowed_keys: tuple[str, ...]
    encoding_mode: Literal["stringData", "data"]


class SecretEditingError(Exception):
    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


SECRET_EDIT_TARGETS: tuple[SecretEditTarget, ...] = (
    SecretEditTarget(
        service_id="homelab-api",
        env="dev",
        file_path="apps/homelab-api/envs/dev/postgres-secret.enc.yaml",
        allowed_keys=("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"),
        encoding_mode="stringData",
    ),
    SecretEditTarget(
        service_id="homelab-api",
        env="prod",
        file_path="apps/homelab-api/envs/prod/postgres-secret.enc.yaml",
        allowed_keys=("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"),
        encoding_mode="stringData",
    ),
    SecretEditTarget(
        service_id="oauth2-proxy",
        env="dev",
        file_path="apps/homelab-web/envs/dev/oauth2-proxy-secret.enc.yaml",
        allowed_keys=(
            "OAUTH2_PROXY_CLIENT_ID",
            "OAUTH2_PROXY_CLIENT_SECRET",
            "OAUTH2_PROXY_COOKIE_SECRET",
        ),
        encoding_mode="data",
    ),
)

SECRET_EDIT_INTERVAL_SECONDS = 30
_secret_edit_state: dict[str, float] = {}
_secret_edit_lock = Lock()


def resolve_secret_edit_target(service_id: str, env: str, secret_key: str) -> SecretEditTarget:
    normalized_key = secret_key.strip()
    for target in SECRET_EDIT_TARGETS:
        if target.service_id == service_id and target.env == env:
            if normalized_key not in target.allowed_keys:
                raise SecretEditingError(
                    f"Secret key {normalized_key!r} is not editable for {service_id}/{env}.",
                    status_code=422,
                )
            return target
    raise SecretEditingError(
        f"Service {service_id!r} does not support secret editing for env {env!r}.",
        status_code=404,
    )


def enforce_secret_edit_rate_limit(*, identity_key: str, now: datetime) -> None:
    now_ts = now.timestamp()
    with _secret_edit_lock:
        last_seen = _secret_edit_state.get(identity_key)
        if last_seen is not None and now_ts - last_seen < SECRET_EDIT_INTERVAL_SECONDS:
            raise SecretEditingError(
                "Secret edits are rate limited to 1 request every 30 seconds.",
                status_code=429,
            )
        _secret_edit_state[identity_key] = now_ts


def clear_secret_edit_rate_limit_state_for_tests() -> None:
    with _secret_edit_lock:
        _secret_edit_state.clear()
