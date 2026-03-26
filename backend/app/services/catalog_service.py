"""Catalog-oriented application service helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from typing import Any

from fastapi import HTTPException, status

from app.catalog_reconciliation import build_catalog_join
from app.gitops_project_sync import sync_project_registry_from_gitops
from app.release_traceability import (
    build_release_join_diagnostics,
    load_argo_metadata_rows,
    load_ci_metadata_rows,
)
from app.service_identity_validation import build_service_identity_diagnostics
from app.service_registry_sync import sync_service_registry_from_cluster
from app.api.schemas.catalog import (
    CatalogJoinDiagnosticsResponse,
    CatalogJoinResponse,
    CatalogJoinRowResponse,
    Project,
    ProjectCatalogDiagnosticsResponse,
    ProjectsResponse,
    ServiceDetailResponse,
    ServiceIdentityDiagnosticsResponse,
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


class CatalogService:
    def __init__(self, deps: CatalogServiceDeps) -> None:
        self.deps = deps

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
        catalog_join = build_catalog_join(
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
        )

    def get_catalog_reconciliation(
        self,
        *,
        env: str | None,
        project_id: str | None,
        service_id: str | None,
    ) -> CatalogJoinResponse:
        now = datetime.now(tz=timezone.utc)
        result = build_catalog_join(
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
                summary = sync_service_registry_from_cluster(conn, env_name=env)
            elif source == "gitops_apps":
                summary = sync_project_registry_from_gitops(conn, env_name=env)
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
        ci_rows = load_ci_metadata_rows()
        argo_rows = load_argo_metadata_rows()
        mismatches = build_release_join_diagnostics(
            project_rows=project_rows,
            ci_rows=ci_rows,
            argo_rows=argo_rows,
            env_filter=env,
            service_id_filter=None,
        )
        catalog_join = build_catalog_join(
            project_rows=project_catalog_rows,
            service_rows=service_catalog_rows,
            env_filter=env,
            project_id_filter=None,
            service_id_filter=None,
        )
        identity_drift = build_service_identity_diagnostics(
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
