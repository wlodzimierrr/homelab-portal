"""Generate GitOps file changes for namespace consolidation.

Produces new_files and modified_files dicts for creating a PR that moves
a standalone service into a target project's shared namespace.
"""

from __future__ import annotations

import yaml


def rewrite_namespace_in_manifest(content: str, new_namespace: str) -> str:
    """Replace metadata.namespace in a YAML manifest."""
    docs: list[str] = []
    for doc in yaml.safe_load_all(content):
        if isinstance(doc, dict) and "metadata" in doc:
            doc["metadata"]["namespace"] = new_namespace
        docs.append(yaml.dump(doc, default_flow_style=False, sort_keys=False))
    return "---\n".join(docs)


def rewrite_manifests_namespace(
    manifests: dict[str, str],
    new_namespace: str,
) -> dict[str, str]:
    """Rewrite metadata.namespace in all manifests."""
    result: dict[str, str] = {}
    for path, content in manifests.items():
        result[path] = rewrite_namespace_in_manifest(content, new_namespace)
    return result


def remap_manifest_paths(
    manifests: dict[str, str],
    source_project: str,
    target_project: str,
) -> dict[str, str]:
    """Move manifests from apps/{source}/ to apps/{target}/base/ with component prefix."""
    result: dict[str, str] = {}
    source_base = f"apps/{source_project}/base/"
    target_base = f"apps/{target_project}/base/"

    for path, content in manifests.items():
        if path.startswith(source_base):
            filename = path[len(source_base):]
            # Prefix with source project name as component if not already prefixed
            if not filename.startswith(f"{source_project}-"):
                new_filename = f"{source_project}-{filename}"
            else:
                new_filename = filename
            result[f"{target_base}{new_filename}"] = content
        else:
            # Non-base files (overlays, etc.) keep relative structure
            result[path.replace(f"apps/{source_project}/", f"apps/{target_project}/")] = content

    return result


def update_services_yaml_project_id(
    existing_yaml: str,
    service_id: str,
    project_id: str,
) -> str:
    """Add or update project_id on a service entry in services.yaml."""
    data = yaml.safe_load(existing_yaml)
    if not isinstance(data, dict) or "services" not in data:
        raise ValueError("Invalid services.yaml: missing 'services' key.")

    found = False
    for entry in data["services"]:
        if entry.get("service_id") == service_id:
            entry["project_id"] = project_id
            found = True
            break

    if not found:
        raise ValueError(f"Service '{service_id}' not found in services.yaml.")

    return yaml.dump(data, default_flow_style=False, sort_keys=False)


def update_services_yaml_namespace(
    existing_yaml: str,
    service_id: str,
    new_namespace: str,
    project_id: str | None = None,
) -> str:
    """Update namespace in env entries and optionally set project_id."""
    data = yaml.safe_load(existing_yaml)
    if not isinstance(data, dict) or "services" not in data:
        raise ValueError("Invalid services.yaml: missing 'services' key.")

    for entry in data["services"]:
        if entry.get("service_id") == service_id:
            if project_id:
                entry["project_id"] = project_id
            for env_entry in entry.get("envs", []):
                env_entry["namespace"] = new_namespace
            return yaml.dump(data, default_flow_style=False, sort_keys=False)

    raise ValueError(f"Service '{service_id}' not found in services.yaml.")


def build_kustomization_resource_additions(
    existing_kustomization: str,
    new_resources: list[str],
) -> str:
    """Append new resource entries to an existing kustomization.yaml."""
    data = yaml.safe_load(existing_kustomization)
    if not isinstance(data, dict):
        data = {}

    resources = data.get("resources", [])
    for res in new_resources:
        if res not in resources:
            resources.append(res)

    resources.sort()
    data["resources"] = resources
    return yaml.dump(data, default_flow_style=False, sort_keys=False)


def generate_consolidation_changes(
    *,
    service_id: str,
    target_project_id: str,
    target_namespace: str,
    service_base_manifests: dict[str, str],
    target_kustomization_yaml: str,
    services_yaml: str,
    source_argo_app_paths: list[str] | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    """Generate all file changes for a namespace consolidation.

    Returns (new_files, modified_files).
    """
    new_files: dict[str, str] = {}
    modified_files: dict[str, str] = {}

    # 1. Rewrite namespace in all service manifests
    rewritten = rewrite_manifests_namespace(service_base_manifests, target_namespace)

    # 2. Remap file paths into target project directory
    remapped = remap_manifest_paths(rewritten, service_id, target_project_id)

    # Filter out namespace.yaml and default-deny/dns-egress netpols (already in target)
    skip_suffixes = (
        "namespace.yaml",
        "networkpolicy-default-deny.yaml",
        "networkpolicy-allow-dns-egress.yaml",
    )
    for path, content in remapped.items():
        filename = path.rsplit("/", 1)[-1] if "/" in path else path
        if any(filename.endswith(suffix) for suffix in skip_suffixes):
            continue
        new_files[path] = content

    # 3. Update target kustomization.yaml with new resource filenames
    new_resource_names = [
        path.rsplit("/", 1)[-1] for path in new_files if "/base/" in path
    ]
    modified_files[f"apps/{target_project_id}/base/kustomization.yaml"] = (
        build_kustomization_resource_additions(
            target_kustomization_yaml, new_resource_names
        )
    )

    # 4. Update services.yaml with new namespace and project_id
    modified_files["services.yaml"] = update_services_yaml_namespace(
        services_yaml, service_id, target_namespace, project_id=target_project_id
    )

    # 5. Mark source ArgoCD Application files for deletion (empty content = delete)
    if source_argo_app_paths:
        for app_path in source_argo_app_paths:
            modified_files[app_path] = ""

    return new_files, modified_files
