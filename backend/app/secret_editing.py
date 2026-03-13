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


def _resolve_default_workloads_repo_path(current_file: Path | None = None) -> Path:
    source_file = (current_file or Path(__file__)).resolve()
    candidates: list[Path] = []

    for parent in [source_file.parent, *source_file.parents]:
        candidate = parent / "workloads"
        if candidate not in candidates:
            candidates.append(candidate)

    for candidate in candidates:
        if candidate.exists():
            return candidate

    # Keep startup resilient when the container filesystem does not mirror the
    # monorepo layout; callers can still override this path via env var.
    return source_file.parent / "workloads"


DEFAULT_WORKLOADS_REPO_ROOT = _resolve_default_workloads_repo_path()


def _workloads_repo_root() -> Path:
    configured = os.getenv("GITOPS_WORKLOADS_REPO_PATH")
    if configured:
        return Path(configured).expanduser().resolve()
    return DEFAULT_WORKLOADS_REPO_ROOT.resolve()

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


def _sops_config_path(*, config_contents: str | None = None, temp_dir: Path | None = None) -> Path:
    if config_contents is not None:
        if temp_dir is None:
            raise SecretEditingError(
                "Temporary directory is required when using inline SOPS config contents.",
                status_code=500,
            )
        config_path = temp_dir / ".sops.yaml"
        config_path.write_text(_normalize_sops_config_contents(config_contents), encoding="utf-8")
        return config_path

    config_path = _workloads_repo_root() / ".sops.yaml"
    if not config_path.exists():
        raise SecretEditingError(
            "SOPS config file is missing from the workloads repository.",
            status_code=503,
        )
    return config_path


def _normalize_sops_config_contents(config_contents: str) -> str:
    """Normalize repo config for the runtime sops binary used in-cluster.

    Some runtime builds accept recipient lists in a narrower shape than the
    local CLI. We normalize the real repo config into a conservative format
    before writing the temporary config file.
    """

    try:
        payload = yaml.safe_load(config_contents) or {}
    except yaml.YAMLError as exc:
        raise SecretEditingError(
            f"Failed to parse SOPS config from workloads repository: {exc}",
            status_code=502,
        ) from exc

    if not isinstance(payload, dict):
        raise SecretEditingError(
            "SOPS config from workloads repository is not a YAML mapping.",
            status_code=502,
        )

    creation_rules = payload.get("creation_rules")
    if isinstance(creation_rules, list):
        normalized_rules: list[dict] = []
        for rule in creation_rules:
            if not isinstance(rule, dict):
                normalized_rules.append(rule)
                continue
            normalized_rule = dict(rule)
            age_value = normalized_rule.get("age")
            if isinstance(age_value, list):
                normalized_rule["age"] = ",".join(
                    str(item).strip() for item in age_value if str(item).strip()
                )
            normalized_rules.append(normalized_rule)
        payload["creation_rules"] = normalized_rules

    return yaml.safe_dump(payload, sort_keys=False)


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


def encrypt_secret_manifest(
    payload: dict,
    *,
    target_file_path: str,
    sops_config_contents: str | None = None,
) -> str:
    ensure_secret_edit_runtime_ready()
    with TemporaryDirectory(prefix="portal-secret-edit-") as tmp_dir:
        temp_dir = Path(tmp_dir)
        workloads_repo_root = _workloads_repo_root() if sops_config_contents is None else temp_dir
        config_path = _sops_config_path(
            config_contents=sops_config_contents,
            temp_dir=temp_dir,
        )
        plain_path = temp_dir / "secret.yaml"
        plain_path.write_text(
            yaml.safe_dump(payload, sort_keys=False),
            encoding="utf-8",
        )
        try:
            completed = subprocess.run(
                [
                    _sops_command(),
                    "--config",
                    str(config_path),
                    "--filename-override",
                    target_file_path,
                    "--encrypt",
                    str(plain_path),
                ],
                check=True,
                capture_output=True,
                text=True,
                env=os.environ.copy(),
                cwd=str(workloads_repo_root),
            )
        except subprocess.CalledProcessError as exc:
            raise SecretEditingError(
                f"Failed to encrypt secret manifest with sops: {exc.stderr.strip() or exc.stdout.strip() or exc}",
                status_code=502,
            ) from exc
        return completed.stdout
