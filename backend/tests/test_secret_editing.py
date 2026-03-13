from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import subprocess

import pytest

from app.secret_editing import (
    SecretEditingError,
    SECRET_EDIT_TARGETS,
    clear_secret_edit_rate_limit_state_for_tests,
    decrypt_secret_manifest,
    encrypt_secret_manifest,
    enforce_secret_edit_rate_limit,
    resolve_secret_edit_target,
    update_secret_manifest_document,
)


def test_resolve_secret_edit_target_returns_matching_target() -> None:
    target = resolve_secret_edit_target("homelab-api", "dev", "POSTGRES_PASSWORD")

    assert target in SECRET_EDIT_TARGETS
    assert target.file_path == "apps/homelab-api/envs/dev/postgres-secret.enc.yaml"


def test_resolve_secret_edit_target_rejects_unknown_key() -> None:
    with pytest.raises(SecretEditingError) as exc:
        resolve_secret_edit_target("homelab-api", "dev", "NOT_ALLOWED")

    assert exc.value.status_code == 422


def test_update_secret_manifest_document_updates_string_data() -> None:
    target = resolve_secret_edit_target("homelab-api", "dev", "POSTGRES_PASSWORD")
    payload = {
        "apiVersion": "v1",
        "kind": "Secret",
        "stringData": {"POSTGRES_PASSWORD": "old"},
    }

    updated = update_secret_manifest_document(
        payload,
        target=target,
        secret_key="POSTGRES_PASSWORD",
        secret_value="new-secret",
    )

    assert updated["stringData"]["POSTGRES_PASSWORD"] == "new-secret"


def test_update_secret_manifest_document_updates_data_with_base64() -> None:
    target = resolve_secret_edit_target("oauth2-proxy", "dev", "OAUTH2_PROXY_CLIENT_SECRET")
    payload = {
        "apiVersion": "v1",
        "kind": "Secret",
        "data": {"OAUTH2_PROXY_CLIENT_SECRET": "b2xk"},
    }

    updated = update_secret_manifest_document(
        payload,
        target=target,
        secret_key="OAUTH2_PROXY_CLIENT_SECRET",
        secret_value="new-secret",
    )

    assert updated["data"]["OAUTH2_PROXY_CLIENT_SECRET"] == base64.b64encode(b"new-secret").decode("ascii")


def test_enforce_secret_edit_rate_limit_rejects_second_request_inside_window() -> None:
    clear_secret_edit_rate_limit_state_for_tests()
    now = datetime.now(tz=timezone.utc)

    enforce_secret_edit_rate_limit(identity_key="operator", now=now)

    with pytest.raises(SecretEditingError) as exc:
        enforce_secret_edit_rate_limit(identity_key="operator", now=now + timedelta(seconds=10))

    assert exc.value.status_code == 429


def test_enforce_secret_edit_rate_limit_allows_request_after_window() -> None:
    clear_secret_edit_rate_limit_state_for_tests()
    now = datetime.now(tz=timezone.utc)

    enforce_secret_edit_rate_limit(identity_key="operator", now=now)
    enforce_secret_edit_rate_limit(identity_key="operator", now=now + timedelta(seconds=31))


def test_decrypt_secret_manifest_requires_runtime_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SOPS_AGE_KEY_FILE", raising=False)

    with pytest.raises(SecretEditingError) as exc:
        decrypt_secret_manifest("irrelevant")

    assert exc.value.status_code == 503


def test_encrypt_secret_manifest_requires_runtime_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SOPS_AGE_KEY_FILE", raising=False)

    with pytest.raises(SecretEditingError) as exc:
        encrypt_secret_manifest(
            {"kind": "Secret"},
            target_file_path="apps/homelab-web/envs/dev/oauth2-proxy-secret.enc.yaml",
        )

    assert exc.value.status_code == 503


def test_decrypt_secret_manifest_wraps_sops_failure(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    key_path = tmp_path / "keys.txt"
    key_path.write_text("age-secret-key", encoding="utf-8")
    monkeypatch.setenv("SOPS_AGE_KEY_FILE", str(key_path))

    def _run(args, check, capture_output, text, env):  # type: ignore[no-untyped-def]
        if args[1] == "--version":
            return subprocess.CompletedProcess(args, 0, stdout="sops 3.9.4", stderr="")
        raise subprocess.CalledProcessError(1, args, stderr="decrypt failed")

    monkeypatch.setattr(subprocess, "run", _run)

    with pytest.raises(SecretEditingError) as exc:
        decrypt_secret_manifest("encrypted")

    assert exc.value.status_code == 502
    assert "Failed to decrypt secret manifest with sops" in str(exc.value)


def test_encrypt_secret_manifest_wraps_sops_failure(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    key_path = tmp_path / "keys.txt"
    key_path.write_text("age-secret-key", encoding="utf-8")
    monkeypatch.setenv("SOPS_AGE_KEY_FILE", str(key_path))
    workloads_root = tmp_path / "workloads"
    workloads_root.mkdir()
    (workloads_root / ".sops.yaml").write_text("creation_rules:\n  - path_regex: .*\\.enc\\.yaml$\n", encoding="utf-8")
    monkeypatch.setenv("GITOPS_WORKLOADS_REPO_PATH", str(workloads_root))

    def _run(args, check, capture_output, text, env, cwd=None):  # type: ignore[no-untyped-def]
        if args[1] == "--version":
            return subprocess.CompletedProcess(args, 0, stdout="sops 3.9.4", stderr="")
        raise subprocess.CalledProcessError(1, args, stderr="encrypt failed")

    monkeypatch.setattr(subprocess, "run", _run)

    with pytest.raises(SecretEditingError) as exc:
        encrypt_secret_manifest(
            {"kind": "Secret"},
            target_file_path="apps/homelab-web/envs/dev/oauth2-proxy-secret.enc.yaml",
        )

    assert exc.value.status_code == 502
    assert "Failed to encrypt secret manifest with sops" in str(exc.value)


def test_encrypt_secret_manifest_passes_sops_config_and_filename_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    key_path = tmp_path / "keys.txt"
    key_path.write_text("age-secret-key", encoding="utf-8")
    monkeypatch.setenv("SOPS_AGE_KEY_FILE", str(key_path))
    workloads_root = tmp_path / "workloads"
    workloads_root.mkdir()
    (workloads_root / ".sops.yaml").write_text("creation_rules:\n  - path_regex: .*\\.enc\\.yaml$\n", encoding="utf-8")
    monkeypatch.setenv("GITOPS_WORKLOADS_REPO_PATH", str(workloads_root))

    captured: dict[str, object] = {}

    def _run(args, check, capture_output, text, env, cwd=None):  # type: ignore[no-untyped-def]
        if args[1] == "--version":
            return subprocess.CompletedProcess(args, 0, stdout="sops 3.9.4", stderr="")
        captured["args"] = args
        captured["cwd"] = cwd
        return subprocess.CompletedProcess(args, 0, stdout="encrypted", stderr="")

    monkeypatch.setattr(subprocess, "run", _run)

    result = encrypt_secret_manifest(
        {"kind": "Secret"},
        target_file_path="apps/homelab-web/envs/dev/oauth2-proxy-secret.enc.yaml",
    )

    assert result == "encrypted"
    args = captured["args"]
    assert isinstance(args, list)
    assert "--config" in args
    assert str(workloads_root / ".sops.yaml") in args
    assert "--filename-override" in args
    assert "apps/homelab-web/envs/dev/oauth2-proxy-secret.enc.yaml" in args
    assert "--encrypt" in args
    assert captured["cwd"] == str(workloads_root)
