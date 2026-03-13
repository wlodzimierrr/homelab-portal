from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import yaml

from app.config_editing import (
    ConfigEditingError,
    clear_config_edit_rate_limit_state_for_tests,
    compute_config_checksum,
    compute_config_checksum_from_manifest,
    enforce_config_edit_rate_limit,
    normalize_config_value,
    resolve_config_edit_target,
    update_config_map_manifest_document,
    update_deployment_patch_checksum,
)


def test_resolve_config_edit_target_returns_matching_target() -> None:
    target = resolve_config_edit_target("homelab-api", "dev", "LOG_LEVEL")

    assert target.service_id == "homelab-api"
    assert target.env == "dev"
    assert target.file_path == "apps/homelab-api/envs/dev/runtime-config.yaml"
    assert target.deployment_patch_file_path == "apps/homelab-api/envs/dev/patch-deployment.yaml"


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


def test_compute_config_checksum_is_stable_for_same_data() -> None:
    data = {"LOG_LEVEL": "warning"}

    assert compute_config_checksum(data) == compute_config_checksum(data)


def test_compute_config_checksum_differs_for_different_data() -> None:
    assert compute_config_checksum({"LOG_LEVEL": "debug"}) != compute_config_checksum({"LOG_LEVEL": "warning"})


def test_compute_config_checksum_from_manifest_matches_data_checksum() -> None:
    manifest = """\
apiVersion: v1
kind: ConfigMap
metadata:
  name: homelab-api-runtime-config
  namespace: homelab-api
data:
  LOG_LEVEL: warning
"""

    assert compute_config_checksum_from_manifest(manifest) == compute_config_checksum({"LOG_LEVEL": "warning"})


def test_update_deployment_patch_checksum_adds_annotation_when_absent() -> None:
    patch = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: homelab-api
  namespace: homelab-api
spec:
  replicas: 1
  template:
    spec:
      containers:
        - name: api
          image: ghcr.io/wlodzimierrr/homelab-api:sha-abc123
"""

    updated = update_deployment_patch_checksum(patch, "deadbeef")

    document = yaml.safe_load(updated)
    assert document["spec"]["template"]["metadata"]["annotations"]["checksum/config"] == "deadbeef"


def test_update_deployment_patch_checksum_overwrites_existing_annotation() -> None:
    patch = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: homelab-api
  namespace: homelab-api
spec:
  template:
    metadata:
      annotations:
        checksum/config: oldvalue
    spec:
      containers:
        - name: api
          image: ghcr.io/wlodzimierrr/homelab-api:sha-abc123
"""

    updated = update_deployment_patch_checksum(patch, "newvalue")

    document = yaml.safe_load(updated)
    assert document["spec"]["template"]["metadata"]["annotations"]["checksum/config"] == "newvalue"

