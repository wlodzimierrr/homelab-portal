from __future__ import annotations

from app.scaffold.models import (  # noqa: F401
    BACKEND_TEMPLATES,
    DB_TEMPLATES,
    FRONTEND_TEMPLATES,
    TEMPLATES,
    ScaffoldAddServiceInput,
    ScaffoldBundleInput,
    ScaffoldError,
    ScaffoldServiceInput,
    normalize_hostname,
    validate_service_name,
)
from app.scaffold.render import yaml_string
from app.scaffold.templates.add_service import (  # noqa: F401
    build_catalog_add_service_entry,
    generate_gitops_add_service_files,
    validate_add_service,
)
from app.scaffold.templates.bundles import (  # noqa: F401
    build_catalog_bundle_entries,
    generate_gitops_bundle_files,
)
from app.scaffold.templates.standalone import (  # noqa: F401
    build_appproject_addition,
    build_catalog_entry_addition,
    generate_gitops_new_files,
)

# This module remains the stable import surface for the rest of the backend while
# scaffold template generation is split into package modules by concern.
_TEMPLATES = TEMPLATES
_FRONTEND_TEMPLATES = FRONTEND_TEMPLATES
_BACKEND_TEMPLATES = BACKEND_TEMPLATES
_DB_TEMPLATES = DB_TEMPLATES
_yaml_string = yaml_string


def update_kustomization_resources(existing: str, resource_name: str) -> str:
    """Insert resource_name into the resources: block (sorted, deduplicated)."""
    lines = existing.splitlines()
    has_resources = any(line.strip() == "resources:" for line in lines)
    if not has_resources:
        raise ScaffoldError(
            "Expected resources: block in workloads kustomization.yaml.",
            status_code=502,
        )

    resource_lines = [line.strip() for line in lines if line.strip().startswith("- ")]
    if f"- {resource_name}" in resource_lines:
        return existing

    resource_lines.append(f"- {resource_name}")
    resource_lines = sorted(dict.fromkeys(resource_lines))

    rebuilt: list[str] = []
    in_resources = False
    for line in lines:
        stripped = line.strip()
        if stripped == "resources:":
            rebuilt.append(line)
            for resource_line in resource_lines:
                rebuilt.append(f"  {resource_line}")
            in_resources = True
            continue
        if in_resources and stripped.startswith("- "):
            continue
        in_resources = False
        rebuilt.append(line)

    result = "\n".join(rebuilt)
    if not result.endswith("\n"):
        result += "\n"
    return result


def update_overlay_kustomization_patches(existing: str, patch_name: str) -> str:
    """Insert patch_name into the patches: block (sorted, deduplicated)."""
    lines = existing.splitlines()
    has_patches = any(line.strip() == "patches:" for line in lines)
    if not has_patches:
        raise ScaffoldError(
            "Expected patches: block in overlay kustomization.yaml.",
            status_code=502,
        )

    patch_lines = [line.strip() for line in lines if line.strip().startswith("- path:")]
    candidate = f"- path: {patch_name}"
    if candidate in patch_lines:
        return existing

    patch_lines.append(candidate)
    patch_lines = sorted(dict.fromkeys(patch_lines))

    rebuilt: list[str] = []
    in_patches = False
    for line in lines:
        stripped = line.strip()
        if stripped == "patches:":
            rebuilt.append(line)
            for patch_line in patch_lines:
                rebuilt.append(f"  {patch_line}")
            in_patches = True
            continue
        if in_patches and stripped.startswith("- path:"):
            continue
        in_patches = False
        rebuilt.append(line)

    result = "\n".join(rebuilt)
    if not result.endswith("\n"):
        result += "\n"
    return result
