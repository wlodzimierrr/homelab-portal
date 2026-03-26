from __future__ import annotations

import hashlib
import json

import yaml

from app.admin.config_policy import ConfigEditTarget, ConfigEditingError, normalize_config_value


def parse_config_map_data(config_map_contents: str) -> dict[str, str]:
    """Parse a ConfigMap YAML and return the data section as a plain string dict."""
    try:
        document = yaml.safe_load(config_map_contents)
    except yaml.YAMLError as exc:
        raise ConfigEditingError(
            f"Failed to parse ConfigMap YAML: {exc}",
            status_code=502,
        ) from exc
    data = document.get("data") or {} if isinstance(document, dict) else {}
    return {k: str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def update_config_map_manifest_document(
    config_map_contents: str,
    *,
    target: ConfigEditTarget,
    config_key: str,
    config_value: str,
) -> tuple[str, str]:
    try:
        document = yaml.safe_load(config_map_contents)
    except yaml.YAMLError as exc:
        raise ConfigEditingError(
            f"Failed to parse ConfigMap YAML: {exc}",
            status_code=502,
        ) from exc

    if not isinstance(document, dict):
        raise ConfigEditingError(
            "ConfigMap manifest must be a YAML mapping.",
            status_code=502,
        )

    metadata = document.get("metadata")
    if not isinstance(metadata, dict) or metadata.get("name") != target.config_map_name:
        raise ConfigEditingError(
            f"Expected ConfigMap {target.config_map_name!r} in {target.file_path}.",
            status_code=502,
        )

    data = document.setdefault("data", {})
    if not isinstance(data, dict):
        raise ConfigEditingError(
            "ConfigMap data must be a YAML mapping.",
            status_code=502,
        )

    normalized_key = config_key.strip().upper()
    normalized_value = normalize_config_value(normalized_key, config_value)
    previous_value = data.get(normalized_key)
    data[normalized_key] = normalized_value

    try:
        updated_contents = yaml.safe_dump(document, sort_keys=False)
    except yaml.YAMLError as exc:
        raise ConfigEditingError(
            f"Failed to serialize ConfigMap YAML: {exc}",
            status_code=500,
        ) from exc

    return updated_contents, str(previous_value) if previous_value is not None else ""


def compute_config_checksum(data: dict) -> str:
    """Return MD5 hex digest of a configmap data dict (keys sorted for stability)."""
    serialized = json.dumps(data, sort_keys=True)
    return hashlib.md5(serialized.encode()).hexdigest()


def compute_config_checksum_from_manifest(config_map_contents: str) -> str:
    """Parse a ConfigMap manifest YAML and return the checksum of its data section."""
    try:
        document = yaml.safe_load(config_map_contents)
    except yaml.YAMLError as exc:
        raise ConfigEditingError(
            f"Failed to parse ConfigMap YAML for checksum: {exc}",
            status_code=502,
        ) from exc
    data = document.get("data") or {}
    return compute_config_checksum(data)


def update_deployment_patch_checksum(patch_contents: str, checksum: str) -> str:
    """Inject or update checksum/config annotation on the pod template in a Deployment patch YAML."""
    try:
        document = yaml.safe_load(patch_contents)
    except yaml.YAMLError as exc:
        raise ConfigEditingError(
            f"Failed to parse deployment patch YAML: {exc}",
            status_code=502,
        ) from exc
    spec = document.setdefault("spec", {})
    template = spec.setdefault("template", {})
    metadata = template.setdefault("metadata", {})
    annotations = metadata.setdefault("annotations", {})
    annotations["checksum/config"] = checksum
    try:
        return yaml.safe_dump(document, sort_keys=False)
    except yaml.YAMLError as exc:
        raise ConfigEditingError(
            f"Failed to serialize deployment patch YAML: {exc}",
            status_code=500,
        ) from exc
