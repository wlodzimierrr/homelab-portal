"""Scaffold, adoption, migration, and hostname endpoint handlers.

Extracted from main.py (Phase R1) to reduce file size without changing
behaviour.  The handlers are thin wrappers that delegate to
ScaffoldAdminService; the private helpers are callback implementations
injected into that service at composition time.
"""

import os
import re as _re

from fastapi import Depends, FastAPI, Response

from app.api.deps import require_admin
from app.api.schemas.migration import (
    AdoptServiceRequest,
    AdoptServiceResponse,
    MigrationConsolidateRequest,
    MigrationConsolidateResponse,
    MigrationValidateRequest,
    MigrationValidateResponse,
)
from app.api.schemas.scaffold import (
    ScaffoldPreviewResponse,
    ScaffoldProjectInfo,
    ScaffoldServiceRequest,
    ScaffoldSubmitResponse,
)
from app.api.schemas.deployments import (
    UpdatePublicHostnameRequest,
    UpdatePublicHostnameResponse,
)
from app.scaffold_service import (
    ScaffoldAddServiceInput,
    ScaffoldBundleInput,
    ScaffoldError,
    ScaffoldServiceInput,
    build_appproject_addition,
    build_catalog_add_service_entry,
    build_catalog_bundle_entries,
    build_catalog_entry_addition,
    generate_gitops_add_service_files,
    generate_gitops_bundle_files,
    generate_gitops_new_files,
    update_kustomization_resources,
    update_overlay_kustomization_patches,
    validate_add_service,
    validate_service_name,
)
from app.services.composition import get_backend_service_builders
from app.services.scaffold_admin_service import ScaffoldAdminService

# ---------------------------------------------------------------------------
# Module-level app reference (set once by init())
# ---------------------------------------------------------------------------

_app: FastAPI | None = None


def init(app: FastAPI) -> None:
    """Store the FastAPI instance so handlers can resolve services lazily."""
    global _app  # noqa: PLW0603
    _app = app


def _get_scaffold_admin_service() -> ScaffoldAdminService:
    assert _app is not None, "scaffold endpoints not initialised — call init(app) first"
    return get_backend_service_builders(_app).build_scaffold_admin_service()


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WORKLOADS_KUSTOMIZATION_PATH = "environments/dev/workloads/kustomization.yaml"
WORKLOADS_PROD_KUSTOMIZATION_PATH = "environments/prod/workloads/kustomization.yaml"
WORKLOADS_APPPROJECT_PATH = "bootstrap/project-homelab.yaml"
WORKLOADS_CATALOG_PATH = "services.yaml"


# ---------------------------------------------------------------------------
# Scaffold input builders
# ---------------------------------------------------------------------------


def _kustomization_path_for(inp: ScaffoldServiceInput) -> str:
    return WORKLOADS_PROD_KUSTOMIZATION_PATH if inp.template in ("postgres", "mysql") else WORKLOADS_KUSTOMIZATION_PATH


def _build_scaffold_input(payload: ScaffoldServiceRequest) -> ScaffoldServiceInput:
    from app.runtime_config import (
        workloads_gitops_repo_url as _workloads_gitops_repo_url,
        workloads_repo_slug as _workloads_repo_slug,
    )

    repo_slug = _workloads_repo_slug()
    name = payload.name.strip().lower()
    namespace = payload.namespace.strip() or name
    dev_host = payload.dev_host.strip() or f"{name}.dev.homelab.local"
    prod_host = payload.prod_host.strip() or f"{name}.homelab.local"
    base_domain = os.getenv("PUBLIC_BASE_DOMAIN", "homelab.local").strip() or "homelab.local"
    public_host = payload.public_host.strip() or f"{name}.{base_domain}"
    image_repo = payload.image_repo.strip()
    if payload.template == "wordpress" and not image_repo:
        image_repo = "wordpress:latest"
    return ScaffoldServiceInput(
        name=name,
        description=payload.description.strip(),
        image_repo=image_repo,
        repo_url=payload.repo_url.strip(),
        owner_email=payload.owner_email.strip(),
        owner=payload.owner.strip(),
        template=payload.template,
        namespace=namespace,
        dev_host=dev_host,
        prod_host=prod_host,
        public_host=public_host,
        workloads_repo_url=_workloads_gitops_repo_url(repo_slug),
        db_username=payload.db_username.strip() or "appuser",
        db_password=payload.db_password.strip() or "changeme",
        db_name=payload.db_name.strip() or "appdb",
    )


def _build_scaffold_bundle_input(payload: ScaffoldServiceRequest) -> ScaffoldBundleInput:
    from app.runtime_config import (
        workloads_gitops_repo_url as _workloads_gitops_repo_url,
        workloads_repo_slug as _workloads_repo_slug,
    )

    repo_slug = _workloads_repo_slug()
    name = payload.name.strip().lower()
    namespace = payload.namespace.strip() or name
    dev_host = payload.dev_host.strip() or f"{name}.dev.homelab.local"
    prod_host = payload.prod_host.strip() or f"{name}.homelab.local"
    base_domain = os.getenv("PUBLIC_BASE_DOMAIN", "homelab.local").strip() or "homelab.local"
    public_host = payload.public_host.strip() or f"{name}.{base_domain}"

    if not payload.frontend_template:
        raise ScaffoldError("frontendTemplate is required for bundle topologies.", status_code=422)
    if not payload.backend_template:
        raise ScaffoldError("backendTemplate is required for bundle topologies.", status_code=422)
    if not payload.frontend_image_repo.strip():
        raise ScaffoldError("frontendImageRepo is required for bundle topologies.", status_code=422)
    if not payload.backend_image_repo.strip():
        raise ScaffoldError("backendImageRepo is required for bundle topologies.", status_code=422)
    if payload.topology == "frontend-backend-db" and not payload.db_template:
        raise ScaffoldError("dbTemplate is required for frontend-backend-db topology.", status_code=422)

    return ScaffoldBundleInput(
        name=name,
        description=payload.description.strip(),
        owner_email=payload.owner_email.strip(),
        owner=payload.owner.strip(),
        namespace=namespace,
        dev_host=dev_host,
        prod_host=prod_host,
        public_host=public_host,
        workloads_repo_url=_workloads_gitops_repo_url(repo_slug),
        repo_url=payload.repo_url.strip(),
        topology=payload.topology,
        frontend_template=payload.frontend_template,
        frontend_image_repo=payload.frontend_image_repo.strip(),
        backend_template=payload.backend_template,
        backend_image_repo=payload.backend_image_repo.strip(),
        db_template=payload.db_template,
        db_username=payload.db_username.strip() or "appuser",
        db_password=payload.db_password.strip() or "changeme",
        db_name=payload.db_name.strip() or "appdb",
    )


def _build_scaffold_add_service_input(payload: ScaffoldServiceRequest) -> ScaffoldAddServiceInput:
    from app.runtime_config import (
        workloads_gitops_repo_url as _workloads_gitops_repo_url,
        workloads_repo_slug as _workloads_repo_slug,
    )

    repo_slug = _workloads_repo_slug()
    project_id = payload.project_id.strip().lower()
    service_name = payload.service_name.strip().lower()
    if not project_id:
        raise ScaffoldError("projectId is required for add-to-project mode.", status_code=422)
    if not service_name:
        raise ScaffoldError("serviceName is required for add-to-project mode.", status_code=422)

    namespace = payload.namespace.strip() or project_id
    dev_host = payload.dev_host.strip() or f"{project_id}.dev.homelab.local"
    prod_host = payload.prod_host.strip() or f"{project_id}.homelab.local"
    base_domain = os.getenv("PUBLIC_BASE_DOMAIN", "homelab.local").strip() or "homelab.local"
    public_host = payload.public_host.strip() or f"{project_id}.{base_domain}"
    return ScaffoldAddServiceInput(
        project_id=project_id,
        service_name=service_name,
        description=payload.description.strip(),
        owner_email=payload.owner_email.strip(),
        owner=payload.owner.strip(),
        namespace=namespace,
        template=payload.template,
        image_repo=payload.image_repo.strip(),
        repo_url=payload.repo_url.strip(),
        dev_host=dev_host,
        prod_host=prod_host,
        public_host=public_host,
        workloads_repo_url=_workloads_gitops_repo_url(repo_slug),
        db_username=payload.db_username.strip() or "appuser",
        db_password=payload.db_password.strip() or "changeme",
        db_name=payload.db_name.strip() or "appdb",
    )


# ---------------------------------------------------------------------------
# Scaffold file generation (shared by preview + submit)
# ---------------------------------------------------------------------------


def generate_scaffold_files_and_updates(
    payload: ScaffoldServiceRequest,
    workloads_repo: str,
    base_branch: str,
    git_provider: object,
) -> tuple[dict[str, str], dict[str, str]]:
    """Generate files and catalog updates for scaffold preview/submit.

    Returns (new_files, modified_files) where new_files are created and
    modified_files are existing files with updated content.
    """
    is_add_to_project = payload.mode == "add-to-project"
    is_bundle = not is_add_to_project and payload.topology != "single-service"

    if is_add_to_project:
        inp = _build_scaffold_add_service_input(payload)

        base_kust_path = f"apps/{inp.project_id}/base/kustomization.yaml"
        base_kustomization_raw = git_provider.read_file(workloads_repo, base_branch, base_kust_path)  # type: ignore[union-attr]
        services_yaml_raw = git_provider.read_file(workloads_repo, base_branch, WORKLOADS_CATALOG_PATH)  # type: ignore[union-attr]

        validate_add_service(inp, base_kustomization_raw, services_yaml_raw)

        new_files, new_resources = generate_gitops_add_service_files(inp)
        modified_files: dict[str, str] = {}

        updated_base_kust = base_kustomization_raw
        for resource in new_resources:
            updated_base_kust = update_kustomization_resources(updated_base_kust, resource)
        modified_files[base_kust_path] = updated_base_kust

        is_db = inp.template in ("postgres", "mysql")
        if not is_db:
            patch_filename = f"patch-{inp.service_name}-deployment.yaml"
            for env_name in ("dev", "prod"):
                overlay_kust_path = f"apps/{inp.project_id}/envs/{env_name}/kustomization.yaml"
                overlay_kust_raw = git_provider.read_file(workloads_repo, base_branch, overlay_kust_path)  # type: ignore[union-attr]
                modified_files[overlay_kust_path] = update_overlay_kustomization_patches(
                    overlay_kust_raw, patch_filename,
                )

        modified_files[WORKLOADS_CATALOG_PATH] = build_catalog_add_service_entry(
            services_yaml_raw, inp,
        )
        return new_files, modified_files

    if is_bundle:
        inp_bundle = _build_scaffold_bundle_input(payload)
        validate_service_name(inp_bundle.name)
        kustomization_path = WORKLOADS_KUSTOMIZATION_PATH
        kustomization_raw = git_provider.read_file(workloads_repo, base_branch, kustomization_path)  # type: ignore[union-attr]
        appproject_raw = git_provider.read_file(workloads_repo, base_branch, WORKLOADS_APPPROJECT_PATH)  # type: ignore[union-attr]
        services_yaml_raw = git_provider.read_file(workloads_repo, base_branch, WORKLOADS_CATALOG_PATH)  # type: ignore[union-attr]

        new_files = generate_gitops_bundle_files(inp_bundle)
        single_inp = ScaffoldServiceInput(
            name=inp_bundle.name, description=inp_bundle.description, image_repo="",
            repo_url=inp_bundle.repo_url, owner_email=inp_bundle.owner_email, owner=inp_bundle.owner,
            template="python-fastapi", namespace=inp_bundle.namespace,
            dev_host=inp_bundle.dev_host, prod_host=inp_bundle.prod_host, public_host=inp_bundle.public_host,
            workloads_repo_url=inp_bundle.workloads_repo_url,
        )
        modified_files = {
            kustomization_path: update_kustomization_resources(kustomization_raw, f"{inp_bundle.name}-app.yaml"),
            WORKLOADS_APPPROJECT_PATH: build_appproject_addition(appproject_raw, single_inp),
            WORKLOADS_CATALOG_PATH: build_catalog_bundle_entries(services_yaml_raw, inp_bundle),
        }
    else:
        inp_single = _build_scaffold_input(payload)
        validate_service_name(inp_single.name)
        kustomization_path = _kustomization_path_for(inp_single)
        kustomization_raw = git_provider.read_file(workloads_repo, base_branch, kustomization_path)  # type: ignore[union-attr]
        appproject_raw = git_provider.read_file(workloads_repo, base_branch, WORKLOADS_APPPROJECT_PATH)  # type: ignore[union-attr]
        services_yaml_raw = git_provider.read_file(workloads_repo, base_branch, WORKLOADS_CATALOG_PATH)  # type: ignore[union-attr]

        new_files = generate_gitops_new_files(inp_single)
        modified_files = {
            kustomization_path: update_kustomization_resources(kustomization_raw, f"{inp_single.name}-app.yaml"),
            WORKLOADS_APPPROJECT_PATH: build_appproject_addition(appproject_raw, inp_single),
            WORKLOADS_CATALOG_PATH: build_catalog_entry_addition(services_yaml_raw, inp_single),
        }

    return new_files, modified_files


# ---------------------------------------------------------------------------
# Hostname management helpers
# ---------------------------------------------------------------------------


def read_current_public_host_from_services_yaml(services_yaml: str, service_id: str) -> str | None:
    """Parse services.yaml and return the current prod public_host for the given service_id, or None."""
    import yaml as _yaml

    try:
        data = _yaml.safe_load(services_yaml)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    for entry in (data.get("services") or []):
        if not isinstance(entry, dict):
            continue
        if entry.get("service_id") != service_id:
            continue
        for env_entry in (entry.get("envs") or []):
            if not isinstance(env_entry, dict):
                continue
            if env_entry.get("name") == "prod":
                val = env_entry.get("public_host")
                return str(val).strip() if val else None
    return None


def read_current_host_from_patch_ingress(patch_ingress: str) -> str | None:
    """Return the current host value from a patch-ingress.yaml, or None."""
    match = _re.search(r"^\s*-\s*host:\s*(.+)$", patch_ingress, _re.MULTILINE)
    return match.group(1).strip() if match else None


def update_services_yaml_public_host(services_yaml: str, service_id: str, new_host: str) -> str:
    """Return services.yaml content with the prod public_host for service_id set to new_host.

    Adds the field if absent; replaces it if present.  The file's existing whitespace
    and ordering are preserved for all other entries.
    """
    from app.scaffold_service import _yaml_string as _ys

    lines = services_yaml.splitlines(keepends=True)
    in_service = False
    in_prod_env = False
    service_indent = ""
    prod_env_start = -1
    public_host_line_idx = -1

    for i, line in enumerate(lines):
        stripped = line.lstrip()
        indent = line[: len(line) - len(stripped)]

        if stripped.startswith(f"service_id: {service_id}"):
            in_service = True
            service_indent = indent
            in_prod_env = False
            prod_env_start = -1
            public_host_line_idx = -1
            continue

        if in_service:
            if stripped.startswith("- service_id:") and indent == service_indent:
                break

            if stripped.startswith("- name: prod"):
                in_prod_env = True
                prod_env_start = i
                continue

            if in_prod_env:
                if stripped.startswith("- name:") and not stripped.startswith("- name: prod"):
                    in_prod_env = False
                    continue
                if stripped.startswith("public_host:"):
                    public_host_line_idx = i

    new_host_line = f"        public_host: {_ys(new_host)}\n"

    if public_host_line_idx >= 0:
        lines[public_host_line_idx] = new_host_line
    elif prod_env_start >= 0:
        insert_after = prod_env_start
        for j in range(prod_env_start + 1, len(lines)):
            stripped_j = lines[j].lstrip()
            if stripped_j.startswith("- name:") or stripped_j.startswith("- service_id:"):
                break
            if stripped_j and not stripped_j.startswith("#"):
                insert_after = j
        lines.insert(insert_after + 1, new_host_line)
    else:
        lines.append(new_host_line)

    return "".join(lines)


def update_patch_ingress_host(patch_ingress: str, new_host: str) -> str:
    """Replace the host value in patch-ingress.yaml."""
    return _re.sub(
        r"^(\s*-\s*host:\s*)(.+)$",
        lambda m: f"{m.group(1)}{new_host}",
        patch_ingress,
        flags=_re.MULTILINE,
    )


# ---------------------------------------------------------------------------
# Endpoint handlers
# ---------------------------------------------------------------------------


def scaffold_preview(
    payload: ScaffoldServiceRequest,
) -> ScaffoldPreviewResponse:
    return _get_scaffold_admin_service().scaffold_preview(payload=payload)


def scaffold_submit(
    payload: ScaffoldServiceRequest,
) -> ScaffoldSubmitResponse:
    return _get_scaffold_admin_service().scaffold_submit(payload=payload)


def scaffold_list_projects() -> list[ScaffoldProjectInfo]:
    return _get_scaffold_admin_service().scaffold_list_projects()


def adopt_service(
    service_id: str,
    payload: AdoptServiceRequest,
    _admin: str = Depends(require_admin),
) -> AdoptServiceResponse:
    return _get_scaffold_admin_service().adopt_service(service_id=service_id, payload=payload)


def validate_migration(
    payload: MigrationValidateRequest,
    _admin: str = Depends(require_admin),
) -> MigrationValidateResponse:
    return _get_scaffold_admin_service().validate_migration(payload=payload)


def consolidate_migration(
    payload: MigrationConsolidateRequest,
    _admin: str = Depends(require_admin),
) -> MigrationConsolidateResponse:
    return _get_scaffold_admin_service().consolidate_migration(payload=payload)


def update_service_public_hostname(
    service_id: str,
    payload: UpdatePublicHostnameRequest,
    response: Response,
    _admin: str = Depends(require_admin),
) -> UpdatePublicHostnameResponse | None:
    del _admin
    return _get_scaffold_admin_service().update_service_public_hostname(
        service_id=service_id,
        payload=payload,
        response=response,
    )
