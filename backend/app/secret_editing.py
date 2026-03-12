from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import base64
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from threading import Lock
from typing import Literal

import yaml


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


def _sops_command() -> str:
    return os.getenv("SOPS_BIN", "sops")


def ensure_secret_edit_runtime_ready() -> None:
    age_key_file = os.getenv("SOPS_AGE_KEY_FILE")
    if not age_key_file:
        raise SecretEditingError(
            "SOPS_AGE_KEY_FILE is not configured in the backend runtime.",
            status_code=503,
        )
    key_path = Path(age_key_file)
    if not key_path.exists():
        raise SecretEditingError(
            f"SOPS age key file {age_key_file!r} does not exist in the backend runtime.",
            status_code=503,
        )
    try:
        subprocess.run(
            [_sops_command(), "--version"],
            check=True,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
    except FileNotFoundError as exc:
        raise SecretEditingError(
            "sops is not installed in the backend runtime.",
            status_code=503,
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise SecretEditingError(
            f"sops is not usable in the backend runtime: {exc.stderr.strip() or exc.stdout.strip() or exc}",
            status_code=503,
        ) from exc


def decrypt_secret_manifest(encrypted_contents: str) -> dict:
    ensure_secret_edit_runtime_ready()
    with TemporaryDirectory(prefix="portal-secret-edit-") as tmp_dir:
        encrypted_path = Path(tmp_dir) / "secret.enc.yaml"
        encrypted_path.write_text(encrypted_contents, encoding="utf-8")
        try:
            completed = subprocess.run(
                [_sops_command(), "--decrypt", str(encrypted_path)],
                check=True,
                capture_output=True,
                text=True,
                env=os.environ.copy(),
            )
        except subprocess.CalledProcessError as exc:
            raise SecretEditingError(
                f"Failed to decrypt secret manifest with sops: {exc.stderr.strip() or exc.stdout.strip() or exc}",
                status_code=502,
            ) from exc
        payload = yaml.safe_load(completed.stdout) or {}
        if not isinstance(payload, dict):
            raise SecretEditingError(
                "Decrypted secret manifest did not produce a YAML mapping.",
                status_code=502,
            )
        return payload


def update_secret_manifest_document(
    payload: dict,
    *,
    target: SecretEditTarget,
    secret_key: str,
    secret_value: str,
) -> dict:
    updated = dict(payload)
    field_name = target.encoding_mode
    current = updated.get(field_name)
    if current is None:
        current = {}
    if not isinstance(current, dict):
        raise SecretEditingError(
            f"Secret manifest field {field_name!r} is not a mapping.",
            status_code=502,
        )
    current_mapping = dict(current)

    if target.encoding_mode == "data":
        rendered_value = base64.b64encode(secret_value.encode("utf-8")).decode("ascii")
    else:
        rendered_value = secret_value
    current_mapping[secret_key] = rendered_value
    updated[field_name] = current_mapping
    return updated


def encrypt_secret_manifest(payload: dict) -> str:
    ensure_secret_edit_runtime_ready()
    with TemporaryDirectory(prefix="portal-secret-edit-") as tmp_dir:
        plain_path = Path(tmp_dir) / "secret.yaml"
        plain_path.write_text(
            yaml.safe_dump(payload, sort_keys=False),
            encoding="utf-8",
        )
        try:
            completed = subprocess.run(
                [_sops_command(), "--encrypt", str(plain_path)],
                check=True,
                capture_output=True,
                text=True,
                env=os.environ.copy(),
            )
        except subprocess.CalledProcessError as exc:
            raise SecretEditingError(
                f"Failed to encrypt secret manifest with sops: {exc.stderr.strip() or exc.stdout.strip() or exc}",
                status_code=502,
            ) from exc
        return completed.stdout
