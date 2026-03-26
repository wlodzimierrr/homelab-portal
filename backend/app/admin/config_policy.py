from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from threading import Lock
from typing import Literal


@dataclass(frozen=True)
class ConfigEditTarget:
    service_id: str
    env: Literal["dev", "prod"]
    file_path: str
    config_map_name: str
    allowed_keys: tuple[str, ...]
    deployment_patch_file_path: str


class ConfigEditingError(Exception):
    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


CONFIG_EDIT_TARGETS: tuple[ConfigEditTarget, ...] = (
    ConfigEditTarget(
        service_id="homelab-api",
        env="dev",
        file_path="apps/homelab-api/envs/dev/runtime-config.yaml",
        config_map_name="homelab-api-runtime-config",
        allowed_keys=("LOG_LEVEL",),
        deployment_patch_file_path="apps/homelab-api/envs/dev/patch-deployment.yaml",
    ),
    ConfigEditTarget(
        service_id="homelab-api",
        env="prod",
        file_path="apps/homelab-api/envs/prod/runtime-config.yaml",
        config_map_name="homelab-api-runtime-config",
        allowed_keys=("LOG_LEVEL",),
        deployment_patch_file_path="apps/homelab-api/envs/prod/patch-deployment.yaml",
    ),
)

ALLOWED_CONFIG_VALUES: dict[str, tuple[str, ...]] = {
    "LOG_LEVEL": ("debug", "info", "warning", "error", "critical"),
}

CONFIG_EDIT_INTERVAL_SECONDS = 30
_config_edit_state: dict[str, float] = {}
_config_edit_lock = Lock()


def get_config_edit_target(service_id: str, env: str) -> ConfigEditTarget:
    for target in CONFIG_EDIT_TARGETS:
        if target.service_id == service_id and target.env == env:
            return target
    raise ConfigEditingError(
        f"Service {service_id!r} does not support config editing for env {env!r}.",
        status_code=404,
    )


def resolve_config_edit_target(service_id: str, env: str, config_key: str) -> ConfigEditTarget:
    normalized_key = config_key.strip().upper()
    for target in CONFIG_EDIT_TARGETS:
        if target.service_id == service_id and target.env == env:
            if normalized_key not in target.allowed_keys:
                raise ConfigEditingError(
                    f"Config key {normalized_key!r} is not editable for {service_id}/{env}.",
                    status_code=422,
                )
            return target
    raise ConfigEditingError(
        f"Service {service_id!r} does not support config editing for env {env!r}.",
        status_code=404,
    )


def normalize_config_value(config_key: str, config_value: str) -> str:
    normalized_key = config_key.strip().upper()
    normalized_value = config_value.strip().lower()
    allowed_values = ALLOWED_CONFIG_VALUES.get(normalized_key)
    if allowed_values is None:
        raise ConfigEditingError(
            f"Config key {normalized_key!r} is not editable.",
            status_code=422,
        )
    if normalized_value not in allowed_values:
        allowed_display = ", ".join(allowed_values)
        raise ConfigEditingError(
            f"Config value for {normalized_key} must be one of: {allowed_display}.",
            status_code=422,
        )
    return normalized_value


def enforce_config_edit_rate_limit(*, identity_key: str, now: datetime) -> None:
    now_ts = now.timestamp()
    with _config_edit_lock:
        last_seen = _config_edit_state.get(identity_key)
        if last_seen is not None and now_ts - last_seen < CONFIG_EDIT_INTERVAL_SECONDS:
            raise ConfigEditingError(
                "Config edits are rate limited to 1 request every 30 seconds.",
                status_code=429,
            )
        _config_edit_state[identity_key] = now_ts


def clear_config_edit_rate_limit_state_for_tests() -> None:
    with _config_edit_lock:
        _config_edit_state.clear()
