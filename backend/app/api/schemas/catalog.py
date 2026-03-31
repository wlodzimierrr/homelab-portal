from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Project(BaseModel):
    id: str
    name: str
    environment: str
    owner: str | None = None
    repo_url: str | None = Field(default=None, alias="repoUrl")
    runbook_url: str | None = Field(default=None, alias="runbookUrl")
    observability_mode: str | None = Field(default=None, alias="observabilityMode")

    model_config = ConfigDict(populate_by_name=True)


class ProjectsResponse(BaseModel):
    projects: list[Project]


class CreateProjectRequest(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    environment: str = Field(min_length=1)


class ServiceRow(BaseModel):
    service_id: str = Field(alias="serviceId")
    service_name: str = Field(alias="serviceName")
    env: str
    namespace: str
    app_label: str = Field(alias="appLabel")
    argo_app_name: str | None = Field(default=None, alias="argoAppName")
    source: str
    source_ref: str | None = Field(default=None, alias="sourceRef")
    last_synced_at: str | None = Field(default=None, alias="lastSyncedAt")
    observability_mode: str | None = Field(default=None, alias="observabilityMode")

    model_config = ConfigDict(populate_by_name=True)


class ServicesResponse(BaseModel):
    services: list[ServiceRow]


class DeploymentLockResponse(BaseModel):
    service_id: str = Field(..., alias="serviceId")
    env: str
    deployment_id: str = Field(..., alias="deploymentId")
    request_key: str = Field(..., alias="requestKey")
    action: str
    status: str
    argo_app: str | None = Field(default=None, alias="argoApp")
    requested_by: str | None = Field(default=None, alias="requestedBy")
    requested_at: str | None = Field(default=None, alias="requestedAt")
    git_pr_url: str | None = Field(default=None, alias="gitPrUrl")
    git_pr_number: int | None = Field(default=None, alias="gitPrNumber")
    git_ref: str | None = Field(default=None, alias="gitRef")
    deploy_reason: str | None = Field(default=None, alias="deployReason")
    locked_at: str | None = Field(default=None, alias="lockedAt")
    expires_at: str | None = Field(default=None, alias="expiresAt")
    metadata: dict[str, Any] | None = None

    model_config = ConfigDict(populate_by_name=True)


class ServiceProjectContextResponse(BaseModel):
    project_id: str | None = Field(default=None, alias="projectId")
    project_name: str | None = Field(default=None, alias="projectName")
    namespace: str
    sibling_service_ids: list[str] = Field(default_factory=list, alias="siblingServiceIds")
    is_linked: bool = Field(alias="isLinked")

    model_config = ConfigDict(populate_by_name=True)


class ServiceCapabilitiesResponse(BaseModel):
    can_deploy_to_dev: bool = Field(alias="canDeployToDev")
    can_promote_to_prod: bool = Field(alias="canPromoteToProd")
    can_rollback: bool = Field(alias="canRollback")
    rollback_envs: list[str] = Field(default_factory=list, alias="rollbackEnvs")
    can_edit_config: bool = Field(alias="canEditConfig")
    config_envs: list[str] = Field(default_factory=list, alias="configEnvs")
    can_edit_public_hostname: bool = Field(alias="canEditPublicHostname")
    can_adopt: bool = Field(alias="canAdopt")
    can_delete: bool = Field(alias="canDelete")
    decommission_mode: str = Field(alias="decommissionMode")
    decommission_reason: str | None = Field(default=None, alias="decommissionReason")

    model_config = ConfigDict(populate_by_name=True)


class ServiceDetailResponse(BaseModel):
    id: str
    name: str
    namespace: str
    env: str
    app_label: str = Field(alias="appLabel")
    argo_app_name: str | None = Field(default=None, alias="argoAppName")
    version: str | None = None
    health: str | None = None
    sync: str | None = None
    source: str
    source_ref: str | None = Field(default=None, alias="sourceRef")
    last_synced_at: str | None = Field(default=None, alias="lastSyncedAt")
    observability_mode: str | None = Field(default=None, alias="observabilityMode")
    public_host: str | None = Field(default=None, alias="publicHost")
    deployment_lock: "DeploymentLockResponse | None" = Field(default=None, alias="deploymentLock")
    project_context: ServiceProjectContextResponse | None = Field(default=None, alias="projectContext")
    capabilities: ServiceCapabilitiesResponse | None = None

    model_config = ConfigDict(populate_by_name=True)


class ServiceRegistrySyncFailure(BaseModel):
    source: str
    scope: str
    error: str


class ServiceRegistrySyncResponse(BaseModel):
    correlation_id: str = Field(alias="correlationId")
    source: str
    env: str
    namespaces: list[str]
    discovered: int
    upserted: int
    inserted: int
    updated: int
    deleted: int = 0
    source_failures: list[ServiceRegistrySyncFailure] = Field(alias="sourceFailures")
    generated_at: str = Field(alias="generatedAt")
    duration_ms: int = Field(alias="durationMs")

    model_config = ConfigDict(populate_by_name=True)


class ServiceRegistryFreshnessResponse(BaseModel):
    row_count: int = Field(alias="rowCount")
    last_synced_at: str | None = Field(alias="lastSyncedAt")
    warning_after_minutes: int = Field(alias="warningAfterMinutes")
    stale_after_minutes: int = Field(alias="staleAfterMinutes")
    is_empty: bool = Field(alias="isEmpty")
    is_warning: bool = Field(alias="isWarning")
    is_stale: bool = Field(alias="isStale")
    state: str

    model_config = ConfigDict(populate_by_name=True)


class ServiceRegistryJoinMismatchResponse(BaseModel):
    ci_unmatched_count: int = Field(alias="ciUnmatchedCount")
    argo_unmatched_count: int = Field(alias="argoUnmatchedCount")
    ci_unmatched_keys: list[str] = Field(alias="ciUnmatchedKeys")
    argo_unmatched_keys: list[str] = Field(alias="argoUnmatchedKeys")

    model_config = ConfigDict(populate_by_name=True)


class CatalogJoinServiceRefResponse(BaseModel):
    service_id: str = Field(alias="serviceId")
    service_name: str = Field(alias="serviceName")
    namespace: str
    app_label: str = Field(alias="appLabel")
    argo_app_name: str | None = Field(default=None, alias="argoAppName")

    model_config = ConfigDict(populate_by_name=True)


class CatalogJoinRowResponse(BaseModel):
    project_id: str = Field(alias="projectId")
    project_name: str = Field(alias="projectName")
    env: str
    namespace: str
    app_label: str = Field(alias="appLabel")
    join_source: str = Field(alias="joinSource")
    primary_service_id: str | None = Field(default=None, alias="primaryServiceId")
    service_count: int = Field(alias="serviceCount")
    service_ids: list[str] = Field(alias="serviceIds")
    services: list[CatalogJoinServiceRefResponse]

    model_config = ConfigDict(populate_by_name=True)


class CatalogJoinDiagnosticsResponse(BaseModel):
    project_only_count: int = Field(alias="projectOnlyCount")
    service_only_count: int = Field(alias="serviceOnlyCount")
    one_to_many_count: int = Field(alias="oneToManyCount")
    ambiguous_join_count: int = Field(alias="ambiguousJoinCount")
    project_only_keys: list[str] = Field(alias="projectOnlyKeys")
    service_only_keys: list[str] = Field(alias="serviceOnlyKeys")
    one_to_many_keys: list[str] = Field(alias="oneToManyKeys")
    ambiguous_join_keys: list[str] = Field(alias="ambiguousJoinKeys")

    model_config = ConfigDict(populate_by_name=True)


class ProjectCatalogDiagnosticsResponse(BaseModel):
    generated_at: str = Field(alias="generatedAt")
    env: str | None = None
    freshness: ServiceRegistryFreshnessResponse
    catalog_join: CatalogJoinDiagnosticsResponse = Field(alias="catalogJoin")

    model_config = ConfigDict(populate_by_name=True)


class CatalogJoinResponse(BaseModel):
    generated_at: str = Field(alias="generatedAt")
    env: str | None = None
    rows: list[CatalogJoinRowResponse]
    diagnostics: CatalogJoinDiagnosticsResponse

    model_config = ConfigDict(populate_by_name=True)


class ServiceIdentityMonitoringSelectorResponse(BaseModel):
    namespace: str
    app_label: str = Field(alias="appLabel")

    model_config = ConfigDict(populate_by_name=True)


class ServiceIdentityDriftRowResponse(BaseModel):
    service_id: str = Field(alias="serviceId")
    env: str
    project_id: str | None = Field(default=None, alias="projectId")
    catalog_linked: bool = Field(alias="catalogLinked")
    namespace: str
    expected_namespace: str | None = Field(default=None, alias="expectedNamespace")
    app_label: str = Field(alias="appLabel")
    expected_app_label: str | None = Field(default=None, alias="expectedAppLabel")
    argo_app_name: str | None = Field(default=None, alias="argoAppName")
    expected_argo_app_name: str | None = Field(default=None, alias="expectedArgoAppName")
    release_argo_app_name: str | None = Field(default=None, alias="releaseArgoAppName")
    observability_mode: str | None = Field(default=None, alias="observabilityMode")
    gitops_path: str | None = Field(default=None, alias="gitopsPath")
    expected_gitops_path: str | None = Field(default=None, alias="expectedGitopsPath")
    monitoring_selector: ServiceIdentityMonitoringSelectorResponse = Field(alias="monitoringSelector")
    violations: list[str]

    model_config = ConfigDict(populate_by_name=True)


class ServiceIdentityDiagnosticsResponse(BaseModel):
    drift_count: int = Field(alias="driftCount")
    ok_count: int = Field(alias="okCount")
    drift_keys: list[str] = Field(alias="driftKeys")
    rows: list[ServiceIdentityDriftRowResponse]

    model_config = ConfigDict(populate_by_name=True)


class ServiceRegistryDiagnosticsResponse(BaseModel):
    generated_at: str = Field(alias="generatedAt")
    env: str | None = None
    freshness: ServiceRegistryFreshnessResponse
    join_mismatch: ServiceRegistryJoinMismatchResponse = Field(alias="joinMismatch")
    catalog_join: CatalogJoinDiagnosticsResponse = Field(alias="catalogJoin")
    identity_drift: ServiceIdentityDiagnosticsResponse = Field(alias="identityDrift")

    model_config = ConfigDict(populate_by_name=True)


ServiceDetailResponse.model_rebuild()
