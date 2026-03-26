from datetime import datetime
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.service_identity import is_canonical_service_id


class DeploymentRecordResponse(BaseModel):
    id: str
    service_id: str = Field(alias="serviceId")
    env: str
    action: str
    version: str | None = None
    status: str | None = None
    requested_at: str | None = Field(default=None, alias="requestedAt")
    requested_by: str | None = Field(default=None, alias="requestedBy")
    deployed_at: str | None = Field(default=None, alias="deployedAt")
    commit_sha: str | None = Field(default=None, alias="commitSha")
    image_ref: str | None = Field(default=None, alias="imageRef")
    previous_image_ref: str | None = Field(default=None, alias="previousImageRef")
    git_ref: str | None = Field(default=None, alias="gitRef")
    git_pr_url: str | None = Field(default=None, alias="gitPrUrl")
    git_pr_number: int | None = Field(default=None, alias="gitPrNumber")
    compare_url: str | None = Field(default=None, alias="compareUrl")
    merge_sha: str | None = Field(default=None, alias="mergeSha")
    argo_app: str | None = Field(default=None, alias="argoApp")
    sync_status: str | None = Field(default=None, alias="syncStatus")
    health_status: str | None = Field(default=None, alias="healthStatus")
    deploy_reason: str | None = Field(default=None, alias="deployReason")
    started_at: str | None = Field(default=None, alias="startedAt")
    finished_at: str | None = Field(default=None, alias="finishedAt")
    deploy_window_start: str | None = Field(default=None, alias="deployWindowStart")
    deploy_window_end: str | None = Field(default=None, alias="deployWindowEnd")
    failure_reason: str | None = Field(default=None, alias="failureReason")
    result: str | None = None
    result_reason: str | None = Field(default=None, alias="resultReason")
    error_rate_pct: dict[str, float] | None = Field(default=None, alias="errorRatePct")
    p95_latency_ms: dict[str, float] | None = Field(default=None, alias="p95LatencyMs")
    availability_pct: dict[str, float] | None = Field(default=None, alias="availabilityPct")
    metrics_source: Literal["live_query", "stored_snapshot", "none"] | None = Field(
        default=None, alias="metricsSource"
    )
    metadata: dict[str, Any] | None = None

    model_config = ConfigDict(populate_by_name=True)


class ServiceDeploymentsResponse(BaseModel):
    deployments: list[DeploymentRecordResponse]


class ServiceDeploymentInfoResponse(BaseModel):
    deployment_id: str | None = Field(default=None, alias="deploymentId")
    service_id: str = Field(alias="serviceId")
    env: str | None = None
    action: str | None = None
    deployed_image: str | None = Field(default=None, alias="deployedImage")
    previous_image: str | None = Field(default=None, alias="previousImage")
    image_digest: str | None = Field(default=None, alias="imageDigest")
    git_commit: str | None = Field(default=None, alias="gitCommit")
    deployed_timestamp: str | None = Field(default=None, alias="deployedTimestamp")
    git_pr_url: str | None = Field(default=None, alias="gitPrUrl")
    git_pr_number: int | None = Field(default=None, alias="gitPrNumber")
    compare_url: str | None = Field(default=None, alias="compareUrl")
    deploy_reason: str | None = Field(default=None, alias="deployReason")
    result: str | None = None
    result_reason: str | None = Field(default=None, alias="resultReason")
    commit_url: str | None = Field(default=None, alias="commitUrl")
    image_url: str | None = Field(default=None, alias="imageUrl")
    argo_app: str | None = Field(default=None, alias="argoApp")
    sync_status: str | None = Field(default=None, alias="syncStatus")
    health_status: str | None = Field(default=None, alias="healthStatus")

    model_config = ConfigDict(populate_by_name=True)


class DeploymentReconcileResponse(BaseModel):
    pull_requests_scanned: int = Field(alias="pullRequestsScanned")
    records_upserted: int = Field(alias="recordsUpserted")
    status_counts: dict[str, int] = Field(alias="statusCounts")
    generated_at: str = Field(alias="generatedAt")

    model_config = ConfigDict(populate_by_name=True)


class CreateDeploymentRecordRequest(BaseModel):
    service_id: str = Field(..., alias="serviceId", min_length=1)
    env: str = Field(min_length=1)
    action: Literal["deploy", "promote", "rollback", "config-change"]
    status: Literal["pending", "deploying", "live", "failed"] = "pending"
    requested_at: datetime | None = Field(default=None, alias="requestedAt")
    requested_by: str | None = Field(default=None, alias="requestedBy")
    pr_url: str | None = Field(default=None, alias="gitPrUrl")
    pr_number: int | None = Field(default=None, alias="gitPrNumber")
    merge_sha: str | None = Field(default=None, alias="mergeSha")
    target_image: str | None = Field(default=None, alias="imageRef")
    previous_image: str | None = Field(default=None, alias="previousImageRef")
    argo_app: str | None = Field(default=None, alias="argoApp")
    sync_status: str | None = Field(default=None, alias="syncStatus")
    health_status: str | None = Field(default=None, alias="healthStatus")
    started_at: datetime | None = Field(default=None, alias="startedAt")
    finished_at: datetime | None = Field(default=None, alias="finishedAt")
    deploy_window_start: datetime | None = Field(default=None, alias="deployWindowStart")
    deploy_window_end: datetime | None = Field(default=None, alias="deployWindowEnd")
    deploy_reason: str | None = Field(default=None, alias="deployReason")
    compare_url: str | None = Field(default=None, alias="compareUrl")
    git_ref: str | None = Field(default=None, alias="gitRef")
    request_key: str | None = Field(default=None, alias="requestKey")
    metadata: dict[str, Any] | None = None

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("service_id")
    @classmethod
    def validate_canonical_service_id(cls, value: str) -> str:
        if not is_canonical_service_id(value):
            raise ValueError("serviceId must use canonical lowercase-hyphen identity")
        return value


ROLLBACK_TAG_RE = re.compile(r"^(sha-[0-9a-f]{40}|v?[0-9]+(\.[0-9]+){2}([.-][0-9A-Za-z.-]+)?)$")


class PortalRollbackRequest(BaseModel):
    target_environment: Literal["prod"] = Field(default="prod", alias="targetEnvironment")
    rollback_api_tag: str = Field(..., alias="rollbackApiTag", min_length=1)
    rollback_web_tag: str = Field(..., alias="rollbackWebTag", min_length=1)
    reason: str = Field(..., min_length=5, max_length=500)

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("rollback_api_tag", "rollback_web_tag")
    @classmethod
    def validate_rollback_tag(cls, value: str) -> str:
        normalized = value.strip()
        if not ROLLBACK_TAG_RE.fullmatch(normalized):
            raise ValueError("rollback tags must use sha-<40 hex> or semver format")
        return normalized

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 5:
            raise ValueError("reason must be at least 5 characters long")
        return normalized


class PortalRollbackResponse(BaseModel):
    status: Literal["accepted"]
    action: Literal["rollback"]
    target_environment: str = Field(alias="targetEnvironment")
    rollback_api_tag: str = Field(alias="rollbackApiTag")
    rollback_web_tag: str = Field(alias="rollbackWebTag")
    reason: str
    requested_by: str = Field(alias="requestedBy")
    repository: str
    workflow_file: str = Field(alias="workflowFile")
    workflow_ref: str = Field(alias="workflowRef")
    workflow_url: str = Field(alias="workflowUrl")
    initiated_at: str = Field(alias="initiatedAt")

    model_config = ConfigDict(populate_by_name=True)


class PortalDeployToDevRequest(BaseModel):
    deploy_reason: str = Field(..., alias="deployReason", min_length=5, max_length=500)

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("deploy_reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 5:
            raise ValueError("deployReason must be at least 5 characters long")
        return normalized


class PortalDeployToDevResponse(BaseModel):
    status: Literal["accepted", "noop"]
    action: Literal["deploy"]
    service_id: str = Field(alias="serviceId")
    target_environment: str = Field(alias="targetEnvironment")
    requested_by: str = Field(alias="requestedBy")
    repository: str
    base_branch: str = Field(alias="baseBranch")
    branch_name: str | None = Field(default=None, alias="branchName")
    deployment_id: str | None = Field(default=None, alias="deploymentId")
    git_pr_url: str | None = Field(default=None, alias="gitPrUrl")
    git_pr_number: int | None = Field(default=None, alias="gitPrNumber")
    previous_tag: str | None = Field(default=None, alias="previousTag")
    new_tag: str | None = Field(default=None, alias="newTag")
    previous_image_ref: str | None = Field(default=None, alias="previousImageRef")
    new_image_ref: str | None = Field(default=None, alias="newImageRef")
    compare_url: str | None = Field(default=None, alias="compareUrl")
    source_commit_sha: str | None = Field(default=None, alias="sourceCommitSha")
    source_workflow_run_url: str | None = Field(default=None, alias="sourceWorkflowRunUrl")
    message: str | None = None
    initiated_at: str = Field(alias="initiatedAt")

    model_config = ConfigDict(populate_by_name=True)


class PortalPromoteToProdRequest(BaseModel):
    deploy_reason: str = Field(..., alias="deployReason", min_length=5, max_length=500)

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("deploy_reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 5:
            raise ValueError("deployReason must be at least 5 characters long")
        return normalized


class PortalPromoteToProdResponse(BaseModel):
    status: Literal["accepted", "noop"]
    action: Literal["promote"]
    service_id: str = Field(alias="serviceId")
    target_environment: Literal["prod"] = Field(alias="targetEnvironment")
    requested_by: str = Field(alias="requestedBy")
    repository: str
    base_branch: str = Field(alias="baseBranch")
    branch_name: str | None = Field(default=None, alias="branchName")
    deployment_id: str | None = Field(default=None, alias="deploymentId")
    git_pr_url: str | None = Field(default=None, alias="gitPrUrl")
    git_pr_number: int | None = Field(default=None, alias="gitPrNumber")
    previous_tag: str | None = Field(default=None, alias="previousTag")
    new_tag: str | None = Field(default=None, alias="newTag")
    previous_image_ref: str | None = Field(default=None, alias="previousImageRef")
    new_image_ref: str | None = Field(default=None, alias="newImageRef")
    compare_url: str | None = Field(default=None, alias="compareUrl")
    source_commit_sha: str | None = Field(default=None, alias="sourceCommitSha")
    message: str | None = None
    initiated_at: str = Field(alias="initiatedAt")

    model_config = ConfigDict(populate_by_name=True)


class PortalSetSecretRequest(BaseModel):
    env: Literal["dev", "prod"]
    secret_key: str = Field(..., alias="secretKey", min_length=1, max_length=128)
    secret_value: str = Field(..., alias="secretValue", min_length=1, max_length=10_000)

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("secret_key")
    @classmethod
    def validate_secret_key(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("secretKey must not be empty")
        return normalized

    @field_validator("secret_value")
    @classmethod
    def validate_secret_value(cls, value: str) -> str:
        if value == "":
            raise ValueError("secretValue must not be empty")
        return value


class PortalSetSecretResponse(BaseModel):
    status: Literal["accepted"]
    service_id: str = Field(alias="serviceId")
    env: Literal["dev", "prod"]
    secret_key: str = Field(alias="secretKey")
    requested_by: str = Field(alias="requestedBy")
    repository: str
    base_branch: str = Field(alias="baseBranch")
    branch_name: str = Field(alias="branchName")
    git_pr_url: str = Field(alias="gitPrUrl")
    git_pr_number: int = Field(alias="gitPrNumber")
    secret_file_path: str = Field(alias="secretFilePath")
    message: str | None = None
    initiated_at: str = Field(alias="initiatedAt")

    model_config = ConfigDict(populate_by_name=True)


class PortalSetConfigRequest(BaseModel):
    env: Literal["dev", "prod"]
    config_key: str = Field(..., alias="configKey", min_length=1, max_length=128)
    config_value: str = Field(..., alias="configValue", min_length=1, max_length=10_000)

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("config_key")
    @classmethod
    def validate_config_key(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("configKey must not be empty")
        return normalized

    @field_validator("config_value")
    @classmethod
    def validate_config_value(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("configValue must not be empty")
        return normalized


class PortalSetConfigResponse(BaseModel):
    status: Literal["accepted", "noop"]
    service_id: str = Field(alias="serviceId")
    env: Literal["dev", "prod"]
    config_key: str = Field(alias="configKey")
    previous_value: str = Field(alias="previousValue")
    config_value: str = Field(alias="configValue")
    requested_by: str = Field(alias="requestedBy")
    repository: str
    base_branch: str = Field(alias="baseBranch")
    branch_name: str | None = Field(default=None, alias="branchName")
    git_pr_url: str | None = Field(default=None, alias="gitPrUrl")
    git_pr_number: int | None = Field(default=None, alias="gitPrNumber")
    config_file_path: str = Field(alias="configFilePath")
    message: str | None = None
    initiated_at: str = Field(alias="initiatedAt")

    model_config = ConfigDict(populate_by_name=True)


class ServiceConfigEntry(BaseModel):
    key: str
    value: str
    allowed_values: list[str] = Field(alias="allowedValues")

    model_config = ConfigDict(populate_by_name=True)


class ServiceConfigResponse(BaseModel):
    service_id: str = Field(alias="serviceId")
    env: Literal["dev", "prod"]
    entries: list[ServiceConfigEntry]

    model_config = ConfigDict(populate_by_name=True)


class PortalServiceRollbackCandidate(BaseModel):
    tag: str
    image_ref: str = Field(alias="imageRef")
    compare_url: str | None = Field(default=None, alias="compareUrl")
    source_commit_sha: str | None = Field(default=None, alias="sourceCommitSha")
    published_at: str | None = Field(default=None, alias="publishedAt")

    model_config = ConfigDict(populate_by_name=True)


class PortalServiceRollbackCandidatesResponse(BaseModel):
    service_id: str = Field(alias="serviceId")
    target_environment: Literal["dev", "prod"] = Field(alias="targetEnvironment")
    current_tag: str | None = Field(default=None, alias="currentTag")
    current_image_ref: str | None = Field(default=None, alias="currentImageRef")
    candidates: list[PortalServiceRollbackCandidate]
    generated_at: str = Field(alias="generatedAt")

    model_config = ConfigDict(populate_by_name=True)


class PortalServiceRollbackRequest(BaseModel):
    target_environment: Literal["dev", "prod"] = Field(default="dev", alias="targetEnvironment")
    rollback_tag: str = Field(..., alias="rollbackTag", min_length=1)
    deploy_reason: str = Field(..., alias="deployReason", min_length=5, max_length=500)

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("rollback_tag")
    @classmethod
    def validate_rollback_tag(cls, value: str) -> str:
        normalized = value.strip()
        if not ROLLBACK_TAG_RE.fullmatch(normalized):
            raise ValueError("rollbackTag must use sha-<40 hex> or semver format")
        return normalized

    @field_validator("deploy_reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 5:
            raise ValueError("deployReason must be at least 5 characters long")
        return normalized


class PortalServiceRollbackResponse(BaseModel):
    status: Literal["accepted", "noop"]
    action: Literal["rollback"]
    service_id: str = Field(alias="serviceId")
    target_environment: Literal["dev", "prod"] = Field(alias="targetEnvironment")
    requested_by: str = Field(alias="requestedBy")
    repository: str
    base_branch: str = Field(alias="baseBranch")
    branch_name: str | None = Field(default=None, alias="branchName")
    deployment_id: str | None = Field(default=None, alias="deploymentId")
    git_pr_url: str | None = Field(default=None, alias="gitPrUrl")
    git_pr_number: int | None = Field(default=None, alias="gitPrNumber")
    previous_tag: str | None = Field(default=None, alias="previousTag")
    new_tag: str | None = Field(default=None, alias="newTag")
    previous_image_ref: str | None = Field(default=None, alias="previousImageRef")
    new_image_ref: str | None = Field(default=None, alias="newImageRef")
    compare_url: str | None = Field(default=None, alias="compareUrl")
    source_commit_sha: str | None = Field(default=None, alias="sourceCommitSha")
    message: str | None = None
    initiated_at: str = Field(alias="initiatedAt")

    model_config = ConfigDict(populate_by_name=True)


class ReleaseArgoStateResponse(BaseModel):
    app_name: str = Field(alias="appName")
    sync_status: str = Field(alias="syncStatus")
    health_status: str = Field(alias="healthStatus")
    revision: str | None = None

    model_config = ConfigDict(populate_by_name=True)


class ReleaseDriftStateResponse(BaseModel):
    is_drifted: bool = Field(alias="isDrifted")
    expected_revision: str | None = Field(default=None, alias="expectedRevision")
    live_revision: str | None = Field(default=None, alias="liveRevision")

    model_config = ConfigDict(populate_by_name=True)


class ReleaseTraceabilityResponse(BaseModel):
    service_id: str = Field(alias="serviceId")
    env: str
    commit_sha: str | None = Field(default=None, alias="commitSha")
    image_ref: str | None = Field(default=None, alias="imageRef")
    deployed_at: str | None = Field(default=None, alias="deployedAt")
    argo: ReleaseArgoStateResponse
    drift: ReleaseDriftStateResponse

    model_config = ConfigDict(populate_by_name=True)


class ReleaseDashboardCompatRow(BaseModel):
    service_id: str = Field(alias="serviceId")
    service_name: str = Field(alias="serviceName")
    environment: str
    commit_sha: str | None = Field(default=None, alias="commitSha")
    image: str | None = None
    sync: str
    health: str
    drift: bool
    deployed_at: str | None = Field(default=None, alias="deployedAt")

    model_config = ConfigDict(populate_by_name=True)


class ReleaseDashboardCompatResponse(BaseModel):
    releases: list[ReleaseDashboardCompatRow]


class UpdatePublicHostnameRequest(BaseModel):
    public_host: str = Field(alias="publicHost")

    model_config = ConfigDict(populate_by_name=True)


class UpdatePublicHostnameResponse(BaseModel):
    pr_url: str = Field(alias="prUrl")
    pr_number: int = Field(alias="prNumber")
    branch_name: str = Field(alias="branchName")

    model_config = ConfigDict(populate_by_name=True)
