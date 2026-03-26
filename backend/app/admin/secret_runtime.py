from __future__ import annotations

from pathlib import Path
import os
import subprocess
from tempfile import TemporaryDirectory

import yaml

from app.admin.secret_policy import SecretEditingError


def resolve_default_workloads_repo_path(current_file: Path | None = None) -> Path:
    source_file = (current_file or Path(__file__)).resolve()
    candidates: list[Path] = []

    for parent in [source_file.parent, *source_file.parents]:
        candidate = parent / "workloads"
        if candidate not in candidates:
            candidates.append(candidate)

    for candidate in candidates:
        if candidate.exists():
            return candidate

    return source_file.parent / "workloads"


DEFAULT_WORKLOADS_REPO_ROOT = resolve_default_workloads_repo_path()


def workloads_repo_root() -> Path:
    configured = os.getenv("GITOPS_WORKLOADS_REPO_PATH")
    if configured:
        return Path(configured).expanduser().resolve()
    return DEFAULT_WORKLOADS_REPO_ROOT.resolve()


def sops_command() -> str:
    return os.getenv("SOPS_BIN", "sops")


def normalize_sops_config_contents(config_contents: str) -> str:
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


def sops_config_path(*, config_contents: str | None = None, temp_dir: Path | None = None) -> Path:
    if config_contents is not None:
        if temp_dir is None:
            raise SecretEditingError(
                "Temporary directory is required when using inline SOPS config contents.",
                status_code=500,
            )
        config_path = temp_dir / ".sops.yaml"
        config_path.write_text(normalize_sops_config_contents(config_contents), encoding="utf-8")
        return config_path

    config_path = workloads_repo_root() / ".sops.yaml"
    if not config_path.exists():
        raise SecretEditingError(
            "SOPS config file is missing from the workloads repository.",
            status_code=503,
        )
    return config_path


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
            [sops_command(), "--version"],
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
                [sops_command(), "--decrypt", str(encrypted_path)],
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


def encrypt_secret_manifest(
    payload: dict,
    *,
    target_file_path: str,
    sops_config_contents: str | None = None,
) -> str:
    ensure_secret_edit_runtime_ready()
    with TemporaryDirectory(prefix="portal-secret-edit-") as tmp_dir:
        temp_dir = Path(tmp_dir)
        repo_root = workloads_repo_root() if sops_config_contents is None else temp_dir
        config_path = sops_config_path(
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
                    sops_command(),
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
                cwd=str(repo_root),
            )
        except subprocess.CalledProcessError as exc:
            raise SecretEditingError(
                f"Failed to encrypt secret manifest with sops: {exc.stderr.strip() or exc.stdout.strip() or exc}",
                status_code=502,
            ) from exc
        return completed.stdout
