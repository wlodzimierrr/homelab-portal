"""Catalog-oriented application service helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from typing import Any

from fastapi import HTTPException, status

# These imports are intentionally kept at module scope because some API tests
# monkeypatch the catalog service module directly.
from app.gitops_project_sync import sync_project_registry_from_gitops  # noqa: F401
from app.service_registry_sync import sync_service_registry_from_cluster  # noqa: F401

from app.api.schemas.catalog import (
    CatalogJoinDiagnosticsResponse,
    CatalogJoinResponse,
    CatalogJoinRowResponse,
    Project,
    ProjectCatalogDiagnosticsResponse,
    ProjectsResponse,
    ServiceCapabilitiesResponse,
    ServiceDetailResponse,
    ServiceIdentityDiagnosticsResponse,
    ServiceProjectContextResponse,
    ServiceRegistryDiagnosticsResponse,
    ServiceRegistryFreshnessResponse,
    ServiceRegistryJoinMismatchResponse,
    ServiceRegistrySyncResponse,
    ServiceRow,
    ServicesResponse,
)


@dataclass(frozen=True)
class CatalogServiceDeps:
    load_project_rows: Any
    load_project_catalog_rows: Any
    load_service_catalog_rows: Any
    load_service_rows: Any
    maybe_reconcile_recent_deployments: Any
    select_preferred_service_row: Any
    sort_release_rows_by_deployed_at: Any
    load_release_rows_for_service: Any
    release_row_has_meaningful_metadata: Any
    load_live_service_runtime_rows: Any
    coalesce_service_status: Any
    extract_version_from_image_ref: Any
    get_active_deployment_lock: Any
    build_deployment_lock_response: Any
    with_connection: Any
    registry_stale_after_minutes: Any
    registry_warning_after_minutes: Any
    build_catalog_join: Any
    dev_deploy_target: Any
    promote_to_prod_target: Any
    rollback_target: Any
    config_edit_targets: Any
    sync_project_registry_from_gitops: Any
    sync_service_registry_from_cluster: Any
    load_ci_metadata_rows: Any
    load_argo_metadata_rows: Any
    build_release_join_diagnostics: Any
    build_service_identity_diagnostics: Any


@dataclass(frozen=True)
class DecommissionCapabilityDecision:
    mode: str
    reason: str | None = None


class CatalogService:
    def __init__(self, deps: CatalogServiceDeps) -> None:
        self.deps = deps

    @staticmethod
    def _supports_optional_target(loader: Any, *args: object) -> bool:
        try:
            loader(*args)
        except HTTPException:
            return False
        return True

    @staticmethod
    def _matching_project_context_row(
        rows: list[dict[str, Any]],
        *,
        service_id: str,
    ) -> dict[str, Any] | None:
        service_key = service_id.strip()
        for row in rows:
            service_ids = row.get("serviceIds")
            if isinstance(service_ids, list) and service_key in {
                str(candidate).strip() for candidate in service_ids
            }:
                return row
        return None

    def _build_service_project_context(
        self,
        *,
        service_id: str,
        selected_row: dict[str, Any],
    ) -> ServiceProjectContextResponse:
        selected_env = str(selected_row["env"])
        selected_namespace = str(selected_row["namespace"])
        result = self.deps.build_catalog_join(
            project_rows=self.deps.load_project_catalog_rows(env=selected_env),
            service_rows=self.deps.load_service_catalog_rows(env=selected_env, service_id=service_id),
            env_filter=selected_env,
            project_id_filter=None,
            service_id_filter=service_id,
        )
        matching_row = self._matching_project_context_row(result.get("rows", []), service_id=service_id)
        if not matching_row:
            return ServiceProjectContextResponse(
                projectId=None,
                projectName=None,
                namespace=selected_namespace,
                siblingServiceIds=[],
                isLinked=False,
            )

        sibling_ids = [
            str(candidate).strip()
            for candidate in matching_row.get("serviceIds", [])
            if str(candidate).strip() and str(candidate).strip() != service_id
        ]
        project_id = str(matching_row.get("projectId") or "").strip() or None
        project_name = str(matching_row.get("projectName") or "").strip() or None

        return ServiceProjectContextResponse(
            projectId=project_id,
            projectName=project_name,
            namespace=str(matching_row.get("namespace") or selected_namespace),
            siblingServiceIds=sibling_ids,
            isLinked=True,
        )

    def _build_service_capabilities(
        self,
        *,
        service_id: str,
        project_context: ServiceProjectContextResponse | None,
        selected_row: dict[str, Any],
    ) -> ServiceCapabilitiesResponse:
        can_deploy_to_dev = self._supports_optional_target(self.deps.dev_deploy_target, service_id)
        can_promote_to_prod = self._supports_optional_target(self.deps.promote_to_prod_target, service_id)

        rollback_envs = [
            env_name
            for env_name in ("dev", "prod")
            if self._supports_optional_target(self.deps.rollback_target, service_id, env_name)
        ]

        config_envs = sorted(
            {
                str(target.env)
                for target in self.deps.config_edit_targets
                if str(getattr(target, "service_id", "")).strip() == service_id
            }
        )

        project_id = project_context.project_id if project_context else None
        is_self_owned = project_id is None or project_id == service_id
        can_edit_public_hostname = bool(
            is_self_owned
            and self.deps.load_project_catalog_rows(env="prod", project_id=service_id)
        )

        # This stays conservative for now: adoption is only surfaced for standalone
        # or self-owned services, while project-linked services should use later
        # migration flows instead of the phase-1 adopt action.
        can_adopt = is_self_owned
        decommission = self._determine_decommission_capability(
            service_id=service_id,
            project_context=project_context,
            selected_row=selected_row,
        )
        can_delete = decommission.mode != "unsupported"

        return ServiceCapabilitiesResponse(
            canDeployToDev=can_deploy_to_dev,
            canPromoteToProd=can_promote_to_prod,
            canRollback=bool(rollback_envs),
            rollbackEnvs=rollback_envs,
            canEditConfig=bool(config_envs),
            configEnvs=config_envs,
            canEditPublicHostname=can_edit_public_hostname,
            canAdopt=can_adopt,
            canDelete=can_delete,
            decommissionMode=decommission.mode,
            decommissionReason=decommission.reason,
        )

    @staticmethod
    def _determine_decommission_capability(
        *,
        service_id: str,
        project_context: ServiceProjectContextResponse | None,
        selected_row: dict[str, Any],
    ) -> DecommissionCapabilityDecision:
        project_id = project_context.project_id if project_context else None
        is_self_owned = project_id is None or project_id == service_id
        if is_self_owned:
            return DecommissionCapabilityDecision(mode="standalone")

        normalized_project_id = str(project_id or "").strip()
        if not normalized_project_id or not service_id.startswith(f"{normalized_project_id}-"):
            return DecommissionCapabilityDecision(
                mode="unsupported",
                reason="Legacy or manually managed shared services cannot be decommissioned from Service Settings yet.",
            )

        service_suffix = service_id.removeprefix(f"{normalized_project_id}-").strip()
        if service_suffix in {"frontend", "backend", "db"}:
            return DecommissionCapabilityDecision(
                mode="unsupported",
                reason="Bundle core components cannot be removed individually from Service Settings yet.",
            )

        if str(selected_row.get("namespace") or "").strip() != str(project_context.namespace or "").strip():
            return DecommissionCapabilityDecision(
                mode="unsupported",
                reason="Shared-service ownership is ambiguous, so decommission is blocked for safety.",
            )

        return DecommissionCapabilityDecision(
            mode="project-component",
            reason="This will remove only this service from the shared project while preserving the project and sibling services.",
        )

    @staticmethod
    def _project_catalog_index(
        rows: list[dict[str, str | None]],
    ) -> dict[tuple[str, str], dict[str, str | None]]:
        return {
            (
                str(row.get("project_id") or "").strip(),
                str(row.get("env") or "").strip(),
            ): row
            for row in rows
        }

    def _load_registry_freshness(
        self,
        *,
        table_name: str,
        source: str | None = None,
        env: str | None = None,
    ) -> tuple[int, object]:
        conditions: list[str] = []
        params: list[object] = []
        if source:
            conditions.append("source = %s")
            params.append(source)
        if env:
            conditions.append("env = %s")
            params.append(env)
        where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""

        with self.deps.with_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT COUNT(*), MAX(last_synced_at)
                    FROM {table_name}
                    {where_clause}
                    """,
                    tuple(params),
                )
                count_row = cur.fetchone()

        return int(count_row[0] or 0), count_row[1]

    def _build_freshness_response(
        self,
        *,
        row_count: int,
        last_synced_at: object,
    ) -> ServiceRegistryFreshnessResponse:
        stale_after_minutes = self.deps.registry_stale_after_minutes()
        warning_after_minutes = self.deps.registry_warning_after_minutes(stale_after_minutes)
        now = datetime.now(tz=timezone.utc)

        is_empty = row_count == 0
        if is_empty:
            is_warning = False
            is_stale = False
            state = "empty"
        else:
            age = None if last_synced_at is None else now - last_synced_at
            if last_synced_at is None:
                is_warning = True
                is_stale = True
            else:
                is_warning = age > timedelta(minutes=warning_after_minutes)
                is_stale = age > timedelta(minutes=stale_after_minutes)
            if is_stale:
                state = "stale"
            elif is_warning:
                state = "warning"
            else:
                state = "fresh"

        return ServiceRegistryFreshnessResponse(
            rowCount=row_count,
            lastSyncedAt=last_synced_at.isoformat() if last_synced_at else None,
            warningAfterMinutes=warning_after_minutes,
            staleAfterMinutes=stale_after_minutes,
            isEmpty=is_empty,
            isWarning=is_warning,
            isStale=is_stale,
            state=state,
        )

    def list_projects(self, *, env: str | None) -> ProjectsResponse:
        rows = self.deps.load_project_rows(env=env)
        return ProjectsResponse(
            projects=[
                Project(
                    id=row["service_id"],
                    name=row["service_name"],
                    environment=row["env"],
                    owner=row["owner"] if isinstance(row.get("owner"), str) else None,
                    repoUrl=row["repo_url"] if isinstance(row.get("repo_url"), str) else None,
                    runbookUrl=row["runbook_url"] if isinstance(row.get("runbook_url"), str) else None,
                    observabilityMode=(
                        row["observability_mode"] if isinstance(row.get("observability_mode"), str) else None
                    ),
                )
                for row in rows
            ]
        )

    def get_project_catalog_diagnostics(
        self,
        *,
        env: str | None,
    ) -> ProjectCatalogDiagnosticsResponse:
        row_count, last_synced_at = self._load_registry_freshness(
            table_name="project_registry",
            source="gitops_apps",
            env=env,
        )
        now = datetime.now(tz=timezone.utc)
        catalog_join = self.deps.build_catalog_join(
            project_rows=self.deps.load_project_catalog_rows(env=env),
            service_rows=self.deps.load_service_catalog_rows(env=env),
            env_filter=env,
            project_id_filter=None,
            service_id_filter=None,
        )

        return ProjectCatalogDiagnosticsResponse(
            generatedAt=now.isoformat(),
            env=env,
            freshness=self._build_freshness_response(
                row_count=row_count,
                last_synced_at=last_synced_at,
            ),
            catalogJoin=CatalogJoinDiagnosticsResponse(**catalog_join["diagnostics"]),
        )

    def list_services(
        self,
        *,
        env: str | None,
        namespace: str | None,
    ) -> ServicesResponse:
        rows = self.deps.load_service_rows(env=env, namespace=namespace)
        project_index = self._project_catalog_index(self.deps.load_project_catalog_rows(env=env))
        return ServicesResponse(
            services=[
                ServiceRow(
                    serviceId=str(row["service_id"]),
                    serviceName=str(row["service_name"]),
                    env=str(row["env"]),
                    namespace=str(row["namespace"]),
                    appLabel=str(row["app_label"]),
                    argoAppName=row["argo_app_name"] if isinstance(row["argo_app_name"], str) else None,
                    source=str(row["source"]),
                    sourceRef=row["source_ref"] if isinstance(row["source_ref"], str) else None,
                    lastSyncedAt=row["last_synced_at"] if isinstance(row["last_synced_at"], str) else None,
                    observabilityMode=(
                        project_index.get((str(row["service_id"]), str(row["env"])), {}).get(
                            "observability_mode"
                        )
                    ),
                )
                for row in rows
            ]
        )

    def get_service(
        self,
        *,
        service_id: str,
        env: str | None,
    ) -> ServiceDetailResponse:
        preferred_env = env or os.getenv("PORTAL_ENV", "dev")
        self.deps.maybe_reconcile_recent_deployments(service_id=service_id, env=preferred_env)
        rows = self.deps.load_service_rows(service_id=service_id, env=env)
        if not rows:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Service not found",
            )

        selected = self.deps.select_preferred_service_row(service_id, rows, preferred_env) or rows[0]
        release_rows = self.deps.sort_release_rows_by_deployed_at(
            self.deps.load_release_rows_for_service(service_id, env)
        )
        release = next((row for row in release_rows if self.deps.release_row_has_meaningful_metadata(row)), {})
        live_rows = self.deps.sort_release_rows_by_deployed_at(
            self.deps.load_live_service_runtime_rows(selected)
        )
        live_release = next((row for row in live_rows if self.deps.release_row_has_meaningful_metadata(row)), {})
        argo = release.get("argo") if isinstance(release.get("argo"), dict) else {}
        live_argo = live_release.get("argo") if isinstance(live_release.get("argo"), dict) else {}
        image_ref = (
            release.get("imageRef")
            if isinstance(release.get("imageRef"), str) and release.get("imageRef")
            else live_release.get("imageRef")
            if isinstance(live_release.get("imageRef"), str)
            else None
        )
        active_lock = self.deps.get_active_deployment_lock(
            str(selected["service_id"]),
            str(selected["env"]),
        )
        project_rows = self.deps.load_project_catalog_rows(
            env=str(selected["env"]),
            project_id=str(selected["service_id"]),
        )
        observability_mode = project_rows[0].get("observability_mode") if project_rows else None
        catalog_public_host = project_rows[0].get("public_host") if project_rows else None
        project_context = self._build_service_project_context(
            service_id=str(selected["service_id"]),
            selected_row=selected,
        )
        capabilities = self._build_service_capabilities(
            service_id=str(selected["service_id"]),
            project_context=project_context,
            selected_row=selected,
        )

        return ServiceDetailResponse(
            id=str(selected["service_id"]),
            name=str(selected["service_name"]),
            namespace=str(selected["namespace"]),
            env=str(selected["env"]),
            appLabel=str(selected["app_label"]),
            argoAppName=selected["argo_app_name"] if isinstance(selected["argo_app_name"], str) else None,
            version=self.deps.extract_version_from_image_ref(image_ref if isinstance(image_ref, str) else None),
            health=self.deps.coalesce_service_status(argo.get("healthStatus"), live_argo.get("healthStatus")),
            sync=self.deps.coalesce_service_status(argo.get("syncStatus"), live_argo.get("syncStatus")),
            source=str(selected["source"]),
            sourceRef=selected["source_ref"] if isinstance(selected["source_ref"], str) else None,
            lastSyncedAt=selected["last_synced_at"] if isinstance(selected["last_synced_at"], str) else None,
            observabilityMode=observability_mode if isinstance(observability_mode, str) else None,
            publicHost=catalog_public_host if isinstance(catalog_public_host, str) else None,
            deploymentLock=self.deps.build_deployment_lock_response(active_lock),
            projectContext=project_context,
            capabilities=capabilities,
        )

    def get_catalog_reconciliation(
        self,
        *,
        env: str | None,
        project_id: str | None,
        service_id: str | None,
    ) -> CatalogJoinResponse:
        now = datetime.now(tz=timezone.utc)
        result = self.deps.build_catalog_join(
            project_rows=self.deps.load_project_catalog_rows(env=env, project_id=project_id),
            service_rows=self.deps.load_service_catalog_rows(env=env, service_id=service_id),
            env_filter=env,
            project_id_filter=project_id,
            service_id_filter=service_id,
        )
        return CatalogJoinResponse(
            generatedAt=now.isoformat(),
            env=env,
            rows=[CatalogJoinRowResponse(**row) for row in result["rows"]],
            diagnostics=CatalogJoinDiagnosticsResponse(**result["diagnostics"]),
        )

    def sync_service_registry(
        self,
        *,
        source: str,
        env: str | None,
    ) -> ServiceRegistrySyncResponse:
        with self.deps.with_connection() as conn:
            if source == "cluster_services":
                summary = self.deps.sync_service_registry_from_cluster(conn, env_name=env)
            elif source == "gitops_apps":
                summary = self.deps.sync_project_registry_from_gitops(conn, env_name=env)
            else:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="source must be one of: cluster_services,gitops_apps",
                )
        return ServiceRegistrySyncResponse(**summary)

    def get_service_registry_diagnostics(
        self,
        *,
        env: str | None,
    ) -> ServiceRegistryDiagnosticsResponse:
        row_count, last_synced_at = self._load_registry_freshness(
            table_name="service_registry",
            env=env,
        )
        now = datetime.now(tz=timezone.utc)
        project_rows = self.deps.load_project_rows()
        project_catalog_rows = self.deps.load_project_catalog_rows(env=env)
        service_catalog_rows = self.deps.load_service_catalog_rows(env=env)
        ci_rows = self.deps.load_ci_metadata_rows()
        argo_rows = self.deps.load_argo_metadata_rows()
        mismatches = self.deps.build_release_join_diagnostics(
            project_rows=project_rows,
            ci_rows=ci_rows,
            argo_rows=argo_rows,
            env_filter=env,
            service_id_filter=None,
        )
        catalog_join = self.deps.build_catalog_join(
            project_rows=project_catalog_rows,
            service_rows=service_catalog_rows,
            env_filter=env,
            project_id_filter=None,
            service_id_filter=None,
        )
        identity_drift = self.deps.build_service_identity_diagnostics(
            project_rows=project_catalog_rows,
            service_rows=service_catalog_rows,
            ci_rows=ci_rows,
            argo_rows=argo_rows,
            env_filter=env,
            service_id_filter=None,
        )

        return ServiceRegistryDiagnosticsResponse(
            generatedAt=now.isoformat(),
            env=env,
            freshness=self._build_freshness_response(
                row_count=row_count,
                last_synced_at=last_synced_at,
            ),
            joinMismatch=ServiceRegistryJoinMismatchResponse(**mismatches),
            catalogJoin=CatalogJoinDiagnosticsResponse(**catalog_join["diagnostics"]),
            identityDrift=ServiceIdentityDiagnosticsResponse(**identity_drift),
        )
