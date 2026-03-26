"""Scaffold and admin-mutation application service helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, Response, status

from app.config_editing import (
    ALLOWED_CONFIG_VALUES,
    ConfigEditingError,
    compute_config_checksum_from_manifest,
    enforce_config_edit_rate_limit,
    get_config_edit_target,
    normalize_config_value,
    parse_config_map_data,
    resolve_config_edit_target,
    update_config_map_manifest_document,
    update_deployment_patch_checksum,
)
from app.lib import (
    GitServiceAuthError,
    GitServiceConfigurationError,
    GitServiceConflictError,
    GitServiceError,
    build_default_git_provider,
)
from app.scaffold_service import ScaffoldError
from app.secret_editing import (
    SecretEditingError,
    decrypt_secret_manifest,
    encrypt_secret_manifest,
    enforce_secret_edit_rate_limit,
    resolve_secret_edit_target,
    update_secret_manifest_document,
)
from app.api.schemas.deployments import (
    PortalSetConfigRequest,
    PortalSetConfigResponse,
    PortalSetSecretRequest,
    PortalSetSecretResponse,
    ServiceConfigEntry,
    ServiceConfigResponse,
    UpdatePublicHostnameRequest,
    UpdatePublicHostnameResponse,
)
from app.api.schemas.scaffold import (
    ScaffoldPreviewFile,
    ScaffoldPreviewResponse,
    ScaffoldProjectInfo,
    ScaffoldServiceRequest,
    ScaffoldSubmitResponse,
)


@dataclass(frozen=True)
class ScaffoldAdminServiceDeps:
    workloads_repo_slug: Any
    workloads_base_branch: Any
    build_config_edit_branch_name: Any
    build_config_edit_pr_body: Any
    build_secret_edit_branch_name: Any
    build_secret_edit_pr_body: Any
    generate_scaffold_files_and_updates: Any
    read_current_public_host_from_services_yaml: Any
    read_current_host_from_patch_ingress: Any
    update_services_yaml_public_host: Any
    update_patch_ingress_host: Any
    workloads_catalog_path: str


class ScaffoldAdminService:
    def __init__(self, deps: ScaffoldAdminServiceDeps) -> None:
        self.deps = deps

    @staticmethod
    def _raise_git_service_http_exception(exc: Exception, *, not_found_is_404: bool = False) -> None:
        if isinstance(exc, GitServiceConfigurationError):
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        if isinstance(exc, GitServiceAuthError):
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
        if isinstance(exc, GitServiceConflictError):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        if isinstance(exc, GitServiceError):
            status_code = status.HTTP_404_NOT_FOUND if not_found_is_404 else status.HTTP_502_BAD_GATEWAY
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    def get_service_config(
        self,
        *,
        service_id: str,
        env: str,
    ) -> ServiceConfigResponse:
        workloads_repo = self.deps.workloads_repo_slug()
        base_branch = self.deps.workloads_base_branch()
        try:
            target = get_config_edit_target(service_id, env)
            git_provider = build_default_git_provider()
            config_contents = git_provider.read_file(workloads_repo, base_branch, target.file_path)
            data = parse_config_map_data(config_contents)
        except ConfigEditingError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        except Exception as exc:
            self._raise_git_service_http_exception(exc)
            raise

        entries = [
            ServiceConfigEntry(
                key=key,
                value=data.get(key, ""),
                allowedValues=list(ALLOWED_CONFIG_VALUES.get(key, ())),
            )
            for key in target.allowed_keys
        ]
        return ServiceConfigResponse(serviceId=service_id, env=env, entries=entries)

    def request_portal_set_config(
        self,
        *,
        service_id: str,
        payload: PortalSetConfigRequest,
        admin_user: str,
    ) -> PortalSetConfigResponse:
        initiated_at = datetime.now(tz=timezone.utc)
        workloads_repo = self.deps.workloads_repo_slug()
        base_branch = self.deps.workloads_base_branch()

        try:
            enforce_config_edit_rate_limit(
                identity_key=f"config-edit:{admin_user}",
                now=initiated_at,
            )
            target = resolve_config_edit_target(service_id, payload.env, payload.config_key)
            normalized_value = normalize_config_value(payload.config_key, payload.config_value)
            git_provider = build_default_git_provider()
            config_contents = git_provider.read_file(workloads_repo, base_branch, target.file_path)
            updated_contents, previous_value = update_config_map_manifest_document(
                config_contents,
                target=target,
                config_key=payload.config_key,
                config_value=normalized_value,
            )
            patch_contents = git_provider.read_file(
                workloads_repo, base_branch, target.deployment_patch_file_path
            )
            checksum = compute_config_checksum_from_manifest(updated_contents)
            updated_patch_contents = update_deployment_patch_checksum(patch_contents, checksum)
        except ConfigEditingError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        except Exception as exc:
            self._raise_git_service_http_exception(exc)
            raise

        if previous_value == normalized_value:
            return PortalSetConfigResponse(
                status="noop",
                serviceId=service_id,
                env=payload.env,
                configKey=payload.config_key,
                previousValue=previous_value,
                configValue=normalized_value,
                requestedBy=admin_user,
                repository=workloads_repo,
                baseBranch=base_branch,
                branchName=None,
                gitPrUrl=None,
                gitPrNumber=None,
                configFilePath=target.file_path,
                message="ConfigMap already contains the requested value.",
                initiatedAt=initiated_at.isoformat(),
            )

        branch_name = self.deps.build_config_edit_branch_name(
            service_id, payload.env, payload.config_key, initiated_at
        )
        pr_title = f"Config: {service_id} {payload.env} {payload.config_key} updated"
        pr_body = self.deps.build_config_edit_pr_body(
            service_id=service_id,
            env=payload.env,
            config_key=payload.config_key,
            config_value=normalized_value,
            previous_value=previous_value,
            requested_by=admin_user,
            config_file_path=target.file_path,
        )

        try:
            git_provider.create_branch(workloads_repo, base_branch, branch_name)
            git_provider.commit_to_branch(
                workloads_repo,
                branch_name,
                {
                    target.file_path: updated_contents,
                    target.deployment_patch_file_path: updated_patch_contents,
                },
                pr_title,
            )
            pr = git_provider.open_pr(
                workloads_repo,
                branch_name,
                base_branch,
                pr_title,
                pr_body,
            )
        except Exception as exc:
            self._raise_git_service_http_exception(exc)
            raise

        return PortalSetConfigResponse(
            status="accepted",
            serviceId=service_id,
            env=payload.env,
            configKey=payload.config_key,
            previousValue=previous_value,
            configValue=normalized_value,
            requestedBy=admin_user,
            repository=workloads_repo,
            baseBranch=base_branch,
            branchName=branch_name,
            gitPrUrl=pr["url"],
            gitPrNumber=pr["number"],
            configFilePath=target.file_path,
            message="Config update pull request created.",
            initiatedAt=initiated_at.isoformat(),
        )

    def request_portal_set_secret(
        self,
        *,
        service_id: str,
        payload: PortalSetSecretRequest,
        admin_user: str,
    ) -> PortalSetSecretResponse:
        initiated_at = datetime.now(tz=timezone.utc)
        workloads_repo = self.deps.workloads_repo_slug()
        base_branch = self.deps.workloads_base_branch()

        try:
            enforce_secret_edit_rate_limit(
                identity_key=f"secret-edit:{admin_user}",
                now=initiated_at,
            )
            target = resolve_secret_edit_target(service_id, payload.env, payload.secret_key)
            git_provider = build_default_git_provider()
            encrypted_contents = git_provider.read_file(workloads_repo, base_branch, target.file_path)
            sops_config_contents = git_provider.read_file(workloads_repo, base_branch, ".sops.yaml")
            decrypted_manifest = decrypt_secret_manifest(encrypted_contents)
            updated_manifest = update_secret_manifest_document(
                decrypted_manifest,
                target=target,
                secret_key=payload.secret_key,
                secret_value=payload.secret_value,
            )
            encrypted_manifest = encrypt_secret_manifest(
                updated_manifest,
                target_file_path=target.file_path,
                sops_config_contents=sops_config_contents,
            )
        except SecretEditingError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        except Exception as exc:
            self._raise_git_service_http_exception(exc)
            raise

        branch_name = self.deps.build_secret_edit_branch_name(
            service_id, payload.env, payload.secret_key, initiated_at
        )
        pr_title = f"Secret: {service_id} {payload.env} {payload.secret_key} updated"
        pr_body = self.deps.build_secret_edit_pr_body(
            service_id=service_id,
            env=payload.env,
            secret_key=payload.secret_key,
            requested_by=admin_user,
            secret_file_path=target.file_path,
        )

        try:
            git_provider.create_branch(workloads_repo, base_branch, branch_name)
            git_provider.commit_to_branch(
                workloads_repo,
                branch_name,
                {target.file_path: encrypted_manifest},
                pr_title,
            )
            pr = git_provider.open_pr(
                workloads_repo,
                branch_name,
                base_branch,
                pr_title,
                pr_body,
            )
        except Exception as exc:
            self._raise_git_service_http_exception(exc)
            raise

        return PortalSetSecretResponse(
            status="accepted",
            serviceId=service_id,
            env=payload.env,
            secretKey=payload.secret_key,
            requestedBy=admin_user,
            repository=workloads_repo,
            baseBranch=base_branch,
            branchName=branch_name,
            gitPrUrl=pr["url"],
            gitPrNumber=pr["number"],
            secretFilePath=target.file_path,
            message="Encrypted secret update pull request created.",
            initiatedAt=initiated_at.isoformat(),
        )

    def scaffold_preview(self, *, payload: ScaffoldServiceRequest) -> ScaffoldPreviewResponse:
        workloads_repo = self.deps.workloads_repo_slug()
        base_branch = self.deps.workloads_base_branch()

        try:
            git_provider = build_default_git_provider()
            new_files, modified_files = self.deps.generate_scaffold_files_and_updates(
                payload, workloads_repo, base_branch, git_provider
            )
        except ScaffoldError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        except Exception as exc:
            self._raise_git_service_http_exception(exc)
            raise

        preview_files: list[ScaffoldPreviewFile] = [
            ScaffoldPreviewFile(path=path, content=content, changeType="create")
            for path, content in sorted(new_files.items())
        ]
        preview_files += [
            ScaffoldPreviewFile(path=path, content=content, changeType="modify")
            for path, content in sorted(modified_files.items())
        ]
        return ScaffoldPreviewResponse(files=preview_files)

    def scaffold_submit(self, *, payload: ScaffoldServiceRequest) -> ScaffoldSubmitResponse:
        initiated_at = datetime.now(tz=timezone.utc)
        workloads_repo = self.deps.workloads_repo_slug()
        base_branch = self.deps.workloads_base_branch()
        name = payload.name.strip().lower()

        try:
            git_provider = build_default_git_provider()
            new_files, modified_files = self.deps.generate_scaffold_files_and_updates(
                payload, workloads_repo, base_branch, git_provider
            )
        except ScaffoldError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        except Exception as exc:
            self._raise_git_service_http_exception(exc)
            raise

        all_files: dict[str, str] = {**new_files, **modified_files}

        timestamp = initiated_at.strftime("%Y%m%d-%H%M%S")
        namespace = payload.namespace.strip() or name
        is_add_to_project = payload.mode == "add-to-project"
        is_bundle = not is_add_to_project and payload.topology != "single-service"
        if is_add_to_project:
            service_id = f"{payload.project_id.strip().lower()}-{payload.service_name.strip().lower()}"
            kind_label = f"service to {payload.project_id.strip().lower()}"
            template_label = payload.template
        elif is_bundle:
            kind_label = f"{payload.topology} project"
            template_label = (
                f"{payload.frontend_template} + {payload.backend_template}"
                + (
                    f" + {payload.db_template}"
                    if payload.db_template and payload.topology == "frontend-backend-db"
                    else ""
                )
            )
        else:
            service_id = name
            kind_label = "service"
            template_label = payload.template
        branch_name = f"scaffold/{name}-{timestamp}"
        pr_title = f"feat(scaffold): add {service_id if is_add_to_project else name} {kind_label}"
        project_name = payload.project_id.strip().lower() if is_add_to_project else name
        pr_body = (
            f"## Scaffold: {service_id if is_add_to_project else name}\n\n"
            f"**Description:** {payload.description}\n"
            f"**Mode:** {payload.mode}\n"
            f"**Template:** {template_label}\n"
            f"**Namespace:** {namespace}\n"
            f"**Repository:** {payload.repo_url}\n\n"
            f"Generated by the homelab portal scaffold wizard.\n\n"
            f"### Checklist\n"
            f"- [ ] Review generated manifests\n"
            f"- [ ] Create image pull secret in `{namespace}` if using GHCR private images\n"
            f"- [ ] Update `runbook_url` in `services.yaml` once a runbook exists\n"
            f"- [ ] Verify kustomize renders without errors: "
            f"`./scripts/render-kustomize.sh apps/{project_name}/envs/dev`\n"
        )

        try:
            git_provider.create_branch(workloads_repo, base_branch, branch_name)
            git_provider.commit_to_branch(
                workloads_repo,
                branch_name,
                all_files,
                (
                    f"feat(scaffold): add {service_id if is_add_to_project else name} "
                    f"{kind_label} manifests and catalog entry"
                ),
            )
            pr = git_provider.open_pr(workloads_repo, branch_name, base_branch, pr_title, pr_body)
        except Exception as exc:
            self._raise_git_service_http_exception(exc)
            raise

        return ScaffoldSubmitResponse(
            prUrl=pr["url"],
            prNumber=pr["number"],
            branchName=branch_name,
            filesCommitted=sorted(all_files),
            initiatedAt=initiated_at.isoformat(),
        )

    def scaffold_list_projects(self) -> list[ScaffoldProjectInfo]:
        import yaml as _yaml

        workloads_repo = self.deps.workloads_repo_slug()
        base_branch = self.deps.workloads_base_branch()

        try:
            git_provider = build_default_git_provider()
            services_yaml_raw = git_provider.read_file(
                workloads_repo, base_branch, self.deps.workloads_catalog_path
            )
        except Exception as exc:
            self._raise_git_service_http_exception(exc)
            raise

        try:
            data = _yaml.safe_load(services_yaml_raw)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to parse services.yaml: {exc}",
            ) from exc

        if not isinstance(data, dict) or "services" not in data:
            return []

        projects: dict[str, dict[str, object]] = {}
        for svc in data["services"]:
            if not isinstance(svc, dict):
                continue
            service_id = svc.get("service_id", "")
            project_id = svc.get("project_id", service_id)
            if project_id not in projects:
                ns = project_id
                envs = svc.get("envs", [])
                if envs and isinstance(envs[0], dict):
                    ns = envs[0].get("namespace", project_id)
                projects[project_id] = {"namespace": ns, "service_ids": []}
            projects[project_id]["service_ids"].append(service_id)  # type: ignore[union-attr]

        return [
            ScaffoldProjectInfo(
                projectId=pid,
                namespace=str(info["namespace"]),
                serviceIds=info["service_ids"],  # type: ignore[arg-type]
            )
            for pid, info in sorted(projects.items())
        ]

    def update_service_public_hostname(
        self,
        *,
        service_id: str,
        payload: UpdatePublicHostnameRequest,
        response: Response,
    ) -> UpdatePublicHostnameResponse | None:
        new_host = payload.public_host.strip()
        if not new_host:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="publicHost must be non-empty",
            )

        workloads_repo = self.deps.workloads_repo_slug()
        base_branch = self.deps.workloads_base_branch()
        patch_ingress_path = f"apps/{service_id}/envs/prod/patch-ingress.yaml"

        try:
            git_provider = build_default_git_provider()
            services_yaml_raw = git_provider.read_file(
                workloads_repo, base_branch, self.deps.workloads_catalog_path
            )
            patch_ingress_raw = git_provider.read_file(workloads_repo, base_branch, patch_ingress_path)
        except Exception as exc:
            self._raise_git_service_http_exception(exc, not_found_is_404=True)
            raise

        current_catalog_host = self.deps.read_current_public_host_from_services_yaml(
            services_yaml_raw, service_id
        )
        current_ingress_host = self.deps.read_current_host_from_patch_ingress(patch_ingress_raw)
        if current_catalog_host == new_host and current_ingress_host == new_host:
            response.status_code = status.HTTP_204_NO_CONTENT
            return None

        updated_services_yaml = self.deps.update_services_yaml_public_host(
            services_yaml_raw, service_id, new_host
        )
        updated_patch_ingress = self.deps.update_patch_ingress_host(patch_ingress_raw, new_host)

        initiated_at = datetime.now(tz=timezone.utc)
        timestamp = initiated_at.strftime("%Y%m%d-%H%M%S")
        branch_name = f"hostname/{service_id}-{timestamp}"
        pr_title = f"feat(hostname): update {service_id} public hostname"
        pr_body = (
            f"## Public hostname update: {service_id}\n\n"
            f"**New hostname:** `{new_host}`\n\n"
            f"### Files changed\n"
            f"- `{self.deps.workloads_catalog_path}` — updated `envs[prod].public_host`\n"
            f"- `{patch_ingress_path}` — updated Ingress host field\n\n"
            f"> **Note:** DNS record creation is out of scope. Point `{new_host}` at the "
            f"cluster ingress IP after merging.\n"
        )

        all_files = {
            self.deps.workloads_catalog_path: updated_services_yaml,
            patch_ingress_path: updated_patch_ingress,
        }

        try:
            git_provider.create_branch(workloads_repo, base_branch, branch_name)
            git_provider.commit_to_branch(
                workloads_repo,
                branch_name,
                all_files,
                f"feat(hostname): update {service_id} public hostname to {new_host}",
            )
            pr = git_provider.open_pr(workloads_repo, branch_name, base_branch, pr_title, pr_body)
        except Exception as exc:
            self._raise_git_service_http_exception(exc)
            raise

        return UpdatePublicHostnameResponse(
            prUrl=pr["url"],
            prNumber=pr["number"],
            branchName=branch_name,
        )
