"""Deployment-oriented application service helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from typing import Any

from fastapi import HTTPException, Response, status

from app.deployment_locks import DeploymentLockConflictError, release_deployment_lock
from app.deployment_records import upsert_deployment_record
from app.github_workflows import GitHubWorkflowDispatchError, dispatch_portal_rollback_workflow
from app.lib import (
    GitServiceAuthError,
    GitServiceConfigurationError,
    GitServiceConflictError,
    GitServiceError,
    build_default_git_provider,
)
from app.api.schemas.deployments import (
    CreateDeploymentRecordRequest,
    DeploymentRecordResponse,
    PortalDeployToDevRequest,
    PortalDeployToDevResponse,
    PortalPromoteToProdRequest,
    PortalPromoteToProdResponse,
    PortalRollbackRequest,
    PortalRollbackResponse,
    PortalServiceRollbackCandidatesResponse,
    PortalServiceRollbackRequest,
    PortalServiceRollbackResponse,
    ServiceDeploymentInfoResponse,
    ServiceDeploymentsResponse,
)


@dataclass(frozen=True)
class DeploymentServiceDeps:
    get_deployment_record_by_id: Any
    maybe_reconcile_recent_deployments: Any
    list_deployment_records_for_service: Any
    load_service_rows: Any
    select_preferred_service_row: Any
    build_deployment_record_response: Any
    upsert_deployment_record_row: Any
    build_deployment_lock_response: Any
    get_active_deployment_lock: Any
    with_connection: Any
    deployment_history_cache: Any
    deployment_reconcile_cache: Any
    logger: Any
    dev_deploy_target: Any
    workloads_repo_slug: Any
    workloads_base_branch: Any
    resolve_latest_portal_image_candidate: Any
    load_dev_overlay_update_plan: Any
    extract_version_from_image_ref: Any
    build_compare_url_for_portal_tags: Any
    build_dev_deploy_branch_name: Any
    build_dev_deploy_pr_body: Any
    promote_to_prod_target: Any
    load_promote_to_prod_update_plan: Any
    ensure_ghcr_tag_exists: Any
    extract_sha_from_tag: Any
    build_prod_promote_branch_name: Any
    build_promote_to_prod_pr_body: Any
    rollback_target: Any
    extract_image_ref_from_overlay: Any
    list_service_rollback_candidates: Any
    load_service_rollback_update_plan: Any
    build_service_rollback_branch_name: Any
    build_service_rollback_pr_body: Any
    select_latest_deployment_info_record: Any
    extract_image_digest: Any
    deployment_record_timestamp: Any
    build_commit_url: Any
    build_package_url_from_image_ref: Any
    deploy_to_dev_error_type: type[Exception]
    promote_to_prod_error_type: type[Exception]
    service_rollback_error_type: type[Exception]


class DeploymentService:
    def __init__(self, deps: DeploymentServiceDeps) -> None:
        self.deps = deps

    def _active_lock_conflict_detail(self, message: str, active_lock: Any) -> dict[str, object]:
        return {
            "message": message,
            "activeLock": self.deps.build_deployment_lock_response(active_lock).model_dump(
                by_alias=True
            ),
        }

    def _raise_git_service_http_exception(self, exc: Exception) -> None:
        if isinstance(exc, GitServiceConfigurationError):
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        if isinstance(exc, GitServiceAuthError):
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
        if isinstance(exc, GitServiceConflictError):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        if isinstance(exc, GitServiceError):
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    def _close_pr_quietly(
        self,
        *,
        git_provider: Any,
        repo_slug: str,
        pr_number: int,
        log_prefix: str,
        service_id: str,
    ) -> None:
        try:
            git_provider.close_pr(repo_slug, pr_number)
        except Exception as close_exc:  # pragma: no cover - cleanup fallback only
            self.deps.logger.warning(
                "%s service_id=%s pr_number=%s error=%s",
                log_prefix,
                service_id,
                pr_number,
                close_exc,
            )

    def get_deployment(self, deployment_id: str) -> DeploymentRecordResponse:
        record = self.deps.get_deployment_record_by_id(deployment_id)
        if record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Deployment record not found",
            )

        service_id = str(record.get("serviceId") or "")
        env = record.get("env") if isinstance(record.get("env"), str) else None
        if service_id:
            self.deps.maybe_reconcile_recent_deployments(service_id=service_id, env=env)
            refreshed = self.deps.get_deployment_record_by_id(deployment_id)
            if refreshed is not None:
                record = refreshed

        service_rows = self.deps.load_service_rows(service_id=service_id or None, env=env)
        selected = self.deps.select_preferred_service_row(service_id, service_rows, env) if service_id else None
        return self.deps.build_deployment_record_response(record, selected)

    def create_deployment_record(
        self,
        payload: CreateDeploymentRecordRequest,
        *,
        admin_user: str,
    ) -> DeploymentRecordResponse:
        try:
            record = self.deps.upsert_deployment_record_row(payload, requested_by=admin_user)
        except DeploymentLockConflictError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=self._active_lock_conflict_detail(
                    (
                        f"Active deployment lock already exists for {payload.service_id}/{payload.env}. "
                        "Wait for the in-flight mutation to finish or clear its stale lock."
                    ),
                    exc.active_lock,
                ),
            ) from exc
        service_rows = self.deps.load_service_rows(service_id=payload.service_id, env=payload.env)
        selected = self.deps.select_preferred_service_row(payload.service_id, service_rows, payload.env)
        return self.deps.build_deployment_record_response(record, selected)

    def cancel_deployment(self, deployment_id: str, *, admin_user: str) -> DeploymentRecordResponse:
        record = self.deps.get_deployment_record_by_id(deployment_id)
        if record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Deployment record not found",
            )

        current_status = record.get("status")
        if current_status in {"live", "failed"}:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Deployment is already in terminal state '{current_status}' and cannot be cancelled.",
            )

        service_id = str(record.get("serviceId") or "")
        env = str(record.get("env") or "")
        request_key = record.get("requestKey")

        with self.deps.with_connection() as conn:
            now = datetime.now(tz=timezone.utc)
            updated = upsert_deployment_record(
                conn,
                service_id=service_id,
                env=env,
                action=str(record.get("action") or "deploy"),
                status="failed",
                result="cancelled",
                result_reason=f"Cancelled by operator ({admin_user})",
                request_key=request_key if isinstance(request_key, str) else None,
                requested_by=record.get("requestedBy") if isinstance(record.get("requestedBy"), str) else None,
                requested_at=record.get("requestedAt"),
                pr_url=record.get("prUrl") if isinstance(record.get("prUrl"), str) else None,
                pr_number=record.get("prNumber") if isinstance(record.get("prNumber"), int) else None,
                merge_sha=record.get("mergeSha") if isinstance(record.get("mergeSha"), str) else None,
                target_image=record.get("targetImage") if isinstance(record.get("targetImage"), str) else None,
                previous_image=record.get("previousImage") if isinstance(record.get("previousImage"), str) else None,
                argo_app=record.get("argoApp") if isinstance(record.get("argoApp"), str) else None,
                sync_status=record.get("syncStatus") if isinstance(record.get("syncStatus"), str) else None,
                health_status=record.get("healthStatus") if isinstance(record.get("healthStatus"), str) else None,
                started_at=record.get("startedAt"),
                finished_at=now,
                deploy_window_start=record.get("deployWindowStart"),
                deploy_window_end=now,
                deploy_reason=record.get("deployReason") if isinstance(record.get("deployReason"), str) else None,
                compare_url=record.get("compareUrl") if isinstance(record.get("compareUrl"), str) else None,
                git_ref=record.get("gitRef") if isinstance(record.get("gitRef"), str) else None,
                metadata=record.get("metadata") if isinstance(record.get("metadata"), dict) else None,
            )
            if service_id and env:
                release_deployment_lock(
                    conn,
                    service_id=service_id,
                    env=env,
                    request_key=request_key if isinstance(request_key, str) else None,
                    deployment_id=deployment_id,
                )

        self.deps.deployment_history_cache.clear()
        self.deps.deployment_reconcile_cache.clear()

        service_rows = self.deps.load_service_rows(service_id=service_id or None, env=env or None)
        selected = self.deps.select_preferred_service_row(service_id, service_rows, env) if service_id else None
        return self.deps.build_deployment_record_response(updated, selected)

    def request_portal_deploy_to_dev(
        self,
        service_id: str,
        payload: PortalDeployToDevRequest,
        *,
        response: Response,
        requested_by: str,
    ) -> PortalDeployToDevResponse:
        target = self.deps.dev_deploy_target(service_id)
        initiated_at = datetime.now(tz=timezone.utc)
        workloads_repo = self.deps.workloads_repo_slug()
        base_branch = self.deps.workloads_base_branch()

        latest_candidate = self.deps.resolve_latest_portal_image_candidate(service_id)
        new_tag = str(latest_candidate["tag"])
        new_image_ref = str(latest_candidate["imageRef"])

        try:
            git_provider = build_default_git_provider()
            _image_repo, previous_image_ref, updated_files = self.deps.load_dev_overlay_update_plan(
                git_provider,
                service_id=service_id,
                repo_slug=workloads_repo,
                branch=base_branch,
                new_image_ref=new_image_ref,
            )
        except Exception as exc:
            self._raise_git_service_http_exception(exc)
            if isinstance(exc, self.deps.deploy_to_dev_error_type):
                raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
            raise

        previous_tag = self.deps.extract_version_from_image_ref(previous_image_ref)
        compare_url = self.deps.build_compare_url_for_portal_tags(previous_tag, new_tag)
        if previous_image_ref == new_image_ref:
            response.status_code = status.HTTP_200_OK
            return PortalDeployToDevResponse(
                status="noop",
                action="deploy",
                serviceId=service_id,
                targetEnvironment="dev",
                requestedBy=requested_by,
                repository=workloads_repo,
                baseBranch=base_branch,
                branchName=None,
                deploymentId=None,
                gitPrUrl=None,
                gitPrNumber=None,
                previousTag=previous_tag,
                newTag=new_tag,
                previousImageRef=previous_image_ref,
                newImageRef=new_image_ref,
                compareUrl=compare_url,
                sourceCommitSha=latest_candidate.get("sourceCommitSha"),
                sourceWorkflowRunUrl=latest_candidate.get("workflowRunUrl"),
                message="Dev overlay already points at the latest deployable image tag.",
                initiatedAt=initiated_at.isoformat(),
            )

        active_lock = self.deps.get_active_deployment_lock(service_id, "dev")
        if active_lock is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=self._active_lock_conflict_detail(
                    (
                        f"Active deployment lock already exists for {service_id}/dev. "
                        "Wait for the in-flight mutation to finish or clear its stale lock."
                    ),
                    active_lock,
                ),
            )

        branch_name = self.deps.build_dev_deploy_branch_name(service_id, new_tag, initiated_at)
        pr_title = f"Deploy {service_id}: {new_tag} to dev"
        pr_body = self.deps.build_dev_deploy_pr_body(
            service_id=service_id,
            requested_by=requested_by,
            deploy_reason=payload.deploy_reason,
            previous_tag=previous_tag,
            new_tag=new_tag,
            new_image_ref=new_image_ref,
            compare_url=compare_url,
            source_commit_sha=latest_candidate.get("sourceCommitSha") if isinstance(latest_candidate.get("sourceCommitSha"), str) else None,
            workflow_run_url=latest_candidate.get("workflowRunUrl") if isinstance(latest_candidate.get("workflowRunUrl"), str) else None,
        )

        try:
            git_provider.create_branch(workloads_repo, base_branch, branch_name)
            git_provider.commit_to_branch(workloads_repo, branch_name, updated_files, pr_title)
            pr = git_provider.open_pr(workloads_repo, branch_name, base_branch, pr_title, pr_body)
        except Exception as exc:
            self._raise_git_service_http_exception(exc)
            raise

        request_key = f"gitops-pr:{pr['number']}:{service_id}:dev:deploy"
        record_payload = CreateDeploymentRecordRequest(
            serviceId=service_id,
            env="dev",
            action="deploy",
            status="pending",
            requestedAt=initiated_at,
            requestedBy=requested_by,
            gitPrUrl=pr["url"],
            gitPrNumber=pr["number"],
            imageRef=new_image_ref,
            previousImageRef=previous_image_ref,
            argoApp=str(target["argo_app"]),
            gitRef=branch_name,
            deployReason=payload.deploy_reason,
            compareUrl=compare_url,
            requestKey=request_key,
            metadata={
                "source": "portal-deploy-to-dev",
                "sourceCommitSha": latest_candidate.get("sourceCommitSha"),
                "previousTag": previous_tag,
                "newTag": new_tag,
                "workflowRunId": latest_candidate.get("workflowRunId"),
                "workflowRunUrl": latest_candidate.get("workflowRunUrl"),
                "patchFiles": sorted(updated_files),
            },
        )

        try:
            record = self.deps.upsert_deployment_record_row(record_payload, requested_by=requested_by)
        except DeploymentLockConflictError as exc:
            self._close_pr_quietly(
                git_provider=git_provider,
                repo_slug=workloads_repo,
                pr_number=pr["number"],
                log_prefix="deploy_to_dev_failed_to_close_pr",
                service_id=service_id,
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=self._active_lock_conflict_detail(
                    (
                        f"Active deployment lock already exists for {service_id}/dev. "
                        "Wait for the in-flight mutation to finish or clear its stale lock."
                    ),
                    exc.active_lock,
                ),
            ) from exc
        except Exception:
            self._close_pr_quietly(
                git_provider=git_provider,
                repo_slug=workloads_repo,
                pr_number=pr["number"],
                log_prefix="deploy_to_dev_failed_to_close_pr",
                service_id=service_id,
            )
            raise

        return PortalDeployToDevResponse(
            status="accepted",
            action="deploy",
            serviceId=service_id,
            targetEnvironment="dev",
            requestedBy=requested_by,
            repository=workloads_repo,
            baseBranch=base_branch,
            branchName=branch_name,
            deploymentId=record.get("deploymentId") if isinstance(record.get("deploymentId"), str) else None,
            gitPrUrl=pr["url"],
            gitPrNumber=pr["number"],
            previousTag=previous_tag,
            newTag=new_tag,
            previousImageRef=previous_image_ref,
            newImageRef=new_image_ref,
            compareUrl=compare_url,
            sourceCommitSha=latest_candidate.get("sourceCommitSha"),
            sourceWorkflowRunUrl=latest_candidate.get("workflowRunUrl"),
            message=None,
            initiatedAt=initiated_at.isoformat(),
        )

    def request_portal_promote_to_prod(
        self,
        service_id: str,
        payload: PortalPromoteToProdRequest,
        *,
        response: Response,
        requested_by: str,
    ) -> PortalPromoteToProdResponse:
        target = self.deps.promote_to_prod_target(service_id)
        initiated_at = datetime.now(tz=timezone.utc)
        workloads_repo = self.deps.workloads_repo_slug()
        base_branch = self.deps.workloads_base_branch()

        try:
            git_provider = build_default_git_provider()
            image_repo, previous_image_ref, new_image_ref, new_tag, updated_files = self.deps.load_promote_to_prod_update_plan(
                git_provider,
                service_id=service_id,
                repo_slug=workloads_repo,
                branch=base_branch,
            )
            self.deps.ensure_ghcr_tag_exists(image_repo, new_tag)
        except Exception as exc:
            self._raise_git_service_http_exception(exc)
            if isinstance(exc, self.deps.promote_to_prod_error_type):
                raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
            raise

        previous_tag = self.deps.extract_version_from_image_ref(previous_image_ref)
        compare_url = self.deps.build_compare_url_for_portal_tags(previous_tag, new_tag)
        source_commit_sha = self.deps.extract_sha_from_tag(new_tag)

        if previous_image_ref == new_image_ref:
            response.status_code = status.HTTP_200_OK
            return PortalPromoteToProdResponse(
                status="noop",
                action="promote",
                serviceId=service_id,
                targetEnvironment="prod",
                requestedBy=requested_by,
                repository=workloads_repo,
                baseBranch=base_branch,
                branchName=None,
                deploymentId=None,
                gitPrUrl=None,
                gitPrNumber=None,
                previousTag=previous_tag,
                newTag=new_tag,
                previousImageRef=previous_image_ref,
                newImageRef=new_image_ref,
                compareUrl=compare_url,
                sourceCommitSha=source_commit_sha,
                message="Prod overlay already matches the current dev image tag.",
                initiatedAt=initiated_at.isoformat(),
            )

        active_lock = self.deps.get_active_deployment_lock(service_id, "prod")
        if active_lock is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=self._active_lock_conflict_detail(
                    (
                        f"Active deployment lock already exists for {service_id}/prod. "
                        "Wait for the in-flight mutation to finish or clear its stale lock."
                    ),
                    active_lock,
                ),
            )

        branch_name = self.deps.build_prod_promote_branch_name(service_id, new_tag, initiated_at)
        pr_title = f"Promote {service_id}: {new_tag} to prod"
        pr_body = self.deps.build_promote_to_prod_pr_body(
            service_id=service_id,
            requested_by=requested_by,
            deploy_reason=payload.deploy_reason,
            previous_tag=previous_tag,
            new_tag=new_tag,
            new_image_ref=new_image_ref,
            compare_url=compare_url,
        )

        try:
            git_provider.create_branch(workloads_repo, base_branch, branch_name)
            git_provider.commit_to_branch(workloads_repo, branch_name, updated_files, pr_title)
            pr = git_provider.open_pr(workloads_repo, branch_name, base_branch, pr_title, pr_body)
        except Exception as exc:
            self._raise_git_service_http_exception(exc)
            raise

        request_key = f"gitops-pr:{pr['number']}:{service_id}:prod:promote"
        record_payload = CreateDeploymentRecordRequest(
            serviceId=service_id,
            env="prod",
            action="promote",
            status="pending",
            requestedAt=initiated_at,
            requestedBy=requested_by,
            gitPrUrl=pr["url"],
            gitPrNumber=pr["number"],
            imageRef=new_image_ref,
            previousImageRef=previous_image_ref,
            argoApp=str(target["argo_app"]),
            gitRef=branch_name,
            deployReason=payload.deploy_reason,
            compareUrl=compare_url,
            requestKey=request_key,
            metadata={
                "source": "portal-promote-to-prod",
                "previousTag": previous_tag,
                "newTag": new_tag,
                "patchFiles": sorted(updated_files),
                "sourceEnvironment": "dev",
                "targetEnvironment": "prod",
            },
        )

        try:
            record = self.deps.upsert_deployment_record_row(record_payload, requested_by=requested_by)
        except DeploymentLockConflictError as exc:
            self._close_pr_quietly(
                git_provider=git_provider,
                repo_slug=workloads_repo,
                pr_number=pr["number"],
                log_prefix="promote_to_prod_failed_to_close_pr",
                service_id=service_id,
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=self._active_lock_conflict_detail(
                    (
                        f"Active deployment lock already exists for {service_id}/prod. "
                        "Wait for the in-flight mutation to finish or clear its stale lock."
                    ),
                    exc.active_lock,
                ),
            ) from exc
        except Exception:
            self._close_pr_quietly(
                git_provider=git_provider,
                repo_slug=workloads_repo,
                pr_number=pr["number"],
                log_prefix="promote_to_prod_failed_to_close_pr",
                service_id=service_id,
            )
            raise

        return PortalPromoteToProdResponse(
            status="accepted",
            action="promote",
            serviceId=service_id,
            targetEnvironment="prod",
            requestedBy=requested_by,
            repository=workloads_repo,
            baseBranch=base_branch,
            branchName=branch_name,
            deploymentId=record.get("deploymentId") if isinstance(record.get("deploymentId"), str) else None,
            gitPrUrl=pr["url"],
            gitPrNumber=pr["number"],
            previousTag=previous_tag,
            newTag=new_tag,
            previousImageRef=previous_image_ref,
            newImageRef=new_image_ref,
            compareUrl=compare_url,
            sourceCommitSha=source_commit_sha,
            message=None,
            initiatedAt=initiated_at.isoformat(),
        )

    def list_service_rollback_candidates(
        self,
        service_id: str,
        *,
        target_environment: str,
    ) -> PortalServiceRollbackCandidatesResponse:
        initiated_at = datetime.now(tz=timezone.utc)
        workloads_repo = self.deps.workloads_repo_slug()
        base_branch = self.deps.workloads_base_branch()

        try:
            git_provider = build_default_git_provider()
            target = self.deps.rollback_target(service_id, target_environment)
            image_repo = str(target["image_repo"])
            patch_files = [str(path) for path in target["patch_files"]]
            previous_image_ref: str | None = None
            for file_path in patch_files:
                current_content = git_provider.read_file(workloads_repo, base_branch, file_path)
                file_image_ref = self.deps.extract_image_ref_from_overlay(
                    current_content,
                    image_repo=image_repo,
                    file_path=file_path,
                )
                if previous_image_ref is None:
                    previous_image_ref = file_image_ref
                elif previous_image_ref != file_image_ref:
                    raise self.deps.service_rollback_error_type(
                        f"GitOps {target_environment} overlay for {service_id} is inconsistent across image patch files.",
                        status_code=status.HTTP_409_CONFLICT,
                    )
            current_tag = self.deps.extract_version_from_image_ref(previous_image_ref)
            candidates = self.deps.list_service_rollback_candidates(
                image_repo=image_repo,
                current_tag=current_tag,
            )
        except Exception as exc:
            self._raise_git_service_http_exception(exc)
            if isinstance(
                exc,
                (
                    self.deps.deploy_to_dev_error_type,
                    self.deps.promote_to_prod_error_type,
                    self.deps.service_rollback_error_type,
                ),
            ):
                raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
            raise

        return PortalServiceRollbackCandidatesResponse(
            serviceId=service_id,
            targetEnvironment=target_environment,
            currentTag=current_tag,
            currentImageRef=previous_image_ref,
            candidates=candidates,
            generatedAt=initiated_at.isoformat(),
        )

    def request_service_rollback(
        self,
        service_id: str,
        payload: PortalServiceRollbackRequest,
        *,
        response: Response,
        requested_by: str,
    ) -> PortalServiceRollbackResponse:
        initiated_at = datetime.now(tz=timezone.utc)
        workloads_repo = self.deps.workloads_repo_slug()
        base_branch = self.deps.workloads_base_branch()

        try:
            git_provider = build_default_git_provider()
            target = self.deps.rollback_target(service_id, payload.target_environment)
            image_repo, previous_image_ref, new_image_ref, updated_files = self.deps.load_service_rollback_update_plan(
                git_provider,
                service_id=service_id,
                repo_slug=workloads_repo,
                branch=base_branch,
                target_environment=payload.target_environment,
                rollback_tag=payload.rollback_tag,
            )
            self.deps.ensure_ghcr_tag_exists(
                image_repo,
                payload.rollback_tag,
                purpose="Rollback image tag",
            )
        except Exception as exc:
            self._raise_git_service_http_exception(exc)
            if isinstance(
                exc,
                (self.deps.promote_to_prod_error_type, self.deps.service_rollback_error_type),
            ):
                raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
            raise

        previous_tag = self.deps.extract_version_from_image_ref(previous_image_ref)
        compare_url = self.deps.build_compare_url_for_portal_tags(previous_tag, payload.rollback_tag)
        source_commit_sha = self.deps.extract_sha_from_tag(payload.rollback_tag)

        if previous_image_ref == new_image_ref:
            response.status_code = status.HTTP_200_OK
            return PortalServiceRollbackResponse(
                status="noop",
                action="rollback",
                serviceId=service_id,
                targetEnvironment=payload.target_environment,
                requestedBy=requested_by,
                repository=workloads_repo,
                baseBranch=base_branch,
                branchName=None,
                deploymentId=None,
                gitPrUrl=None,
                gitPrNumber=None,
                previousTag=previous_tag,
                newTag=payload.rollback_tag,
                previousImageRef=previous_image_ref,
                newImageRef=new_image_ref,
                compareUrl=compare_url,
                sourceCommitSha=source_commit_sha,
                message=f"{payload.target_environment.title()} overlay already matches the requested rollback tag.",
                initiatedAt=initiated_at.isoformat(),
            )

        active_lock = self.deps.get_active_deployment_lock(service_id, payload.target_environment)
        if active_lock is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=self._active_lock_conflict_detail(
                    (
                        f"Active deployment lock already exists for {service_id}/{payload.target_environment}. "
                        "Wait for the in-flight mutation to finish or clear its stale lock."
                    ),
                    active_lock,
                ),
            )

        branch_name = self.deps.build_service_rollback_branch_name(
            service_id,
            payload.target_environment,
            payload.rollback_tag,
            initiated_at,
        )
        pr_title = f"Rollback {service_id}: {payload.rollback_tag} in {payload.target_environment}"
        pr_body = self.deps.build_service_rollback_pr_body(
            service_id=service_id,
            target_environment=payload.target_environment,
            requested_by=requested_by,
            deploy_reason=payload.deploy_reason,
            previous_tag=previous_tag,
            rollback_tag=payload.rollback_tag,
            rollback_image_ref=new_image_ref,
            compare_url=compare_url,
        )

        try:
            git_provider.create_branch(workloads_repo, base_branch, branch_name)
            git_provider.commit_to_branch(workloads_repo, branch_name, updated_files, pr_title)
            pr = git_provider.open_pr(workloads_repo, branch_name, base_branch, pr_title, pr_body)
        except Exception as exc:
            self._raise_git_service_http_exception(exc)
            raise

        request_key = f"gitops-pr:{pr['number']}:{service_id}:{payload.target_environment}:rollback"
        record_payload = CreateDeploymentRecordRequest(
            serviceId=service_id,
            env=payload.target_environment,
            action="rollback",
            status="pending",
            requestedAt=initiated_at,
            requestedBy=requested_by,
            gitPrUrl=pr["url"],
            gitPrNumber=pr["number"],
            imageRef=new_image_ref,
            previousImageRef=previous_image_ref,
            argoApp=str(target["argo_app"]),
            gitRef=branch_name,
            deployReason=payload.deploy_reason,
            compareUrl=compare_url,
            requestKey=request_key,
            metadata={
                "source": "portal-service-rollback",
                "previousTag": previous_tag,
                "newTag": payload.rollback_tag,
                "patchFiles": sorted(updated_files),
                "targetEnvironment": payload.target_environment,
            },
        )

        try:
            record = self.deps.upsert_deployment_record_row(record_payload, requested_by=requested_by)
        except DeploymentLockConflictError as exc:
            self._close_pr_quietly(
                git_provider=git_provider,
                repo_slug=workloads_repo,
                pr_number=pr["number"],
                log_prefix="service_rollback_failed_to_close_pr",
                service_id=service_id,
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=self._active_lock_conflict_detail(
                    (
                        f"Active deployment lock already exists for {service_id}/{payload.target_environment}. "
                        "Wait for the in-flight mutation to finish or clear its stale lock."
                    ),
                    exc.active_lock,
                ),
            ) from exc
        except Exception:
            self._close_pr_quietly(
                git_provider=git_provider,
                repo_slug=workloads_repo,
                pr_number=pr["number"],
                log_prefix="service_rollback_failed_to_close_pr",
                service_id=service_id,
            )
            raise

        return PortalServiceRollbackResponse(
            status="accepted",
            action="rollback",
            serviceId=service_id,
            targetEnvironment=payload.target_environment,
            requestedBy=requested_by,
            repository=workloads_repo,
            baseBranch=base_branch,
            branchName=branch_name,
            deploymentId=record.get("deploymentId") if isinstance(record.get("deploymentId"), str) else None,
            gitPrUrl=pr["url"],
            gitPrNumber=pr["number"],
            previousTag=previous_tag,
            newTag=payload.rollback_tag,
            previousImageRef=previous_image_ref,
            newImageRef=new_image_ref,
            compareUrl=compare_url,
            sourceCommitSha=source_commit_sha,
            message=None,
            initiatedAt=initiated_at.isoformat(),
        )

    def request_portal_rollback(
        self,
        payload: PortalRollbackRequest,
        *,
        admin_user: str,
    ) -> PortalRollbackResponse:
        try:
            result = dispatch_portal_rollback_workflow(
                rollback_api_tag=payload.rollback_api_tag,
                rollback_web_tag=payload.rollback_web_tag,
                operator_reason=payload.reason,
                target_environment=payload.target_environment,
            )
        except GitHubWorkflowDispatchError as exc:
            status_code = (
                status.HTTP_503_SERVICE_UNAVAILABLE
                if exc.status_code is None or exc.status_code >= 500
                else status.HTTP_502_BAD_GATEWAY
            )
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc

        return PortalRollbackResponse(
            status="accepted",
            action="rollback",
            targetEnvironment=payload.target_environment,
            rollbackApiTag=payload.rollback_api_tag,
            rollbackWebTag=payload.rollback_web_tag,
            reason=payload.reason,
            requestedBy=admin_user,
            repository=result.repository,
            workflowFile=result.workflow_file,
            workflowRef=result.workflow_ref,
            workflowUrl=result.workflow_url,
            initiatedAt=datetime.now(tz=timezone.utc).isoformat(),
        )

    def get_service_deployments(
        self,
        service_id: str,
        *,
        env: str | None,
        limit: int,
    ) -> ServiceDeploymentsResponse:
        selected_env = env or os.getenv("PORTAL_ENV", "dev")
        self.deps.maybe_reconcile_recent_deployments(service_id=service_id, env=selected_env)
        service_rows = self.deps.load_service_rows(service_id=service_id, env=env)
        selected = self.deps.select_preferred_service_row(service_id, service_rows, selected_env)
        rows = self.deps.list_deployment_records_for_service(service_id, env=env, limit=limit)
        deployments = [self.deps.build_deployment_record_response(row, selected) for row in rows]
        return ServiceDeploymentsResponse(deployments=deployments)

    def get_service_deployment_info(
        self,
        service_id: str,
        *,
        env: str | None,
    ) -> ServiceDeploymentInfoResponse:
        selected_env = env or os.getenv("PORTAL_ENV", "dev")
        self.deps.maybe_reconcile_recent_deployments(service_id=service_id, env=selected_env)
        records = self.deps.list_deployment_records_for_service(service_id, env=env, limit=50)
        record = self.deps.select_latest_deployment_info_record(records)
        if record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Deployment info not found",
            )

        resolved_env = record.get("env") if isinstance(record.get("env"), str) else selected_env
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else None
        deployed_image = record.get("targetImage") if isinstance(record.get("targetImage"), str) else None
        previous_image = record.get("previousImage") if isinstance(record.get("previousImage"), str) else None
        commit_sha = record.get("mergeSha") if isinstance(record.get("mergeSha"), str) else None
        if commit_sha is None and isinstance(metadata, dict):
            source_commit_sha = metadata.get("sourceCommitSha")
            if isinstance(source_commit_sha, str) and source_commit_sha.strip():
                commit_sha = source_commit_sha.strip()
        result_reason = None
        if isinstance(metadata, dict):
            failure_reason = metadata.get("failureReason")
            if isinstance(failure_reason, str) and failure_reason.strip():
                result_reason = failure_reason.strip()
        if result_reason is None:
            deploy_reason = record.get("deployReason")
            if isinstance(deploy_reason, str) and deploy_reason.strip():
                result_reason = (
                    deploy_reason.strip()
                    if str(record.get("status") or "").strip().lower() == "failed"
                    else None
                )

        return ServiceDeploymentInfoResponse(
            deploymentId=record.get("deploymentId") if isinstance(record.get("deploymentId"), str) else None,
            serviceId=service_id,
            env=resolved_env,
            action=record.get("action") if isinstance(record.get("action"), str) else None,
            deployedImage=deployed_image,
            previousImage=previous_image,
            imageDigest=self.deps.extract_image_digest(record),
            gitCommit=commit_sha,
            deployedTimestamp=self.deps.deployment_record_timestamp(record),
            gitPrUrl=record.get("prUrl") if isinstance(record.get("prUrl"), str) else None,
            gitPrNumber=record.get("prNumber") if isinstance(record.get("prNumber"), int) else None,
            compareUrl=record.get("compareUrl") if isinstance(record.get("compareUrl"), str) else None,
            deployReason=record.get("deployReason") if isinstance(record.get("deployReason"), str) else None,
            result=record.get("status") if isinstance(record.get("status"), str) else None,
            resultReason=result_reason,
            commitUrl=self.deps.build_commit_url(commit_sha),
            imageUrl=self.deps.build_package_url_from_image_ref(deployed_image),
            argoApp=record.get("argoApp") if isinstance(record.get("argoApp"), str) else None,
            syncStatus=record.get("syncStatus") if isinstance(record.get("syncStatus"), str) else None,
            healthStatus=record.get("healthStatus") if isinstance(record.get("healthStatus"), str) else None,
        )
