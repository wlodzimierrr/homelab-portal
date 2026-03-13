from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import yaml

from app.config_editing import (
    ConfigEditingError,
    clear_config_edit_rate_limit_state_for_tests,
    enforce_config_edit_rate_limit,
    normalize_config_value,
    resolve_config_edit_target,
    update_config_map_manifest_document,
)


def test_resolve_config_edit_target_returns_matching_target() -> None:
    target = resolve_config_edit_target("homelab-api", "dev", "LOG_LEVEL")

    assert target.service_id == "homelab-api"
    assert target.env == "dev"
    assert target.file_path == "apps/homelab-api/envs/dev/runtime-config.yaml"


def test_resolve_config_edit_target_rejects_unknown_key() -> None:
    with pytest.raises(ConfigEditingError) as exc:
        resolve_config_edit_target("homelab-api", "dev", "DATABASE_URL")

    assert exc.value.status_code == 422


def test_normalize_config_value_normalizes_allowed_value() -> None:
    assert normalize_config_value("LOG_LEVEL", " DEBUG ") == "debug"


def test_normalize_config_value_rejects_invalid_value() -> None:
    with pytest.raises(ConfigEditingError) as exc:
        normalize_config_value("LOG_LEVEL", "verbose")

    assert exc.value.status_code == 422


def test_enforce_config_edit_rate_limit_rejects_second_request_inside_window() -> None:
    clear_config_edit_rate_limit_state_for_tests()
    now = datetime.now(tz=timezone.utc)

    enforce_config_edit_rate_limit(identity_key="operator", now=now)

    with pytest.raises(ConfigEditingError) as exc:
        enforce_config_edit_rate_limit(identity_key="operator", now=now + timedelta(seconds=10))

    assert exc.value.status_code == 429


def test_enforce_config_edit_rate_limit_allows_request_after_window() -> None:
    clear_config_edit_rate_limit_state_for_tests()
    now = datetime.now(tz=timezone.utc)

    enforce_config_edit_rate_limit(identity_key="operator", now=now)
    enforce_config_edit_rate_limit(identity_key="operator", now=now + timedelta(seconds=31))


def test_update_config_map_manifest_document_updates_yaml_and_returns_previous_value() -> None:
    target = resolve_config_edit_target("homelab-api", "dev", "LOG_LEVEL")
    original = """\
apiVersion: v1
kind: ConfigMap
metadata:
  name: homelab-api-runtime-config
  namespace: homelab-api
data:
  LOG_LEVEL: debug
"""

    updated_contents, previous_value = update_config_map_manifest_document(
        original,
        target=target,
        config_key="LOG_LEVEL",
        config_value="warning",
    )

    document = yaml.safe_load(updated_contents)

    assert previous_value == "debug"
    assert document["data"]["LOG_LEVEL"] == "warning"

