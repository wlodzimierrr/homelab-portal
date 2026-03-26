from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AdoptServiceRequest(BaseModel):
    project_id: str = Field(alias="projectId", min_length=1)

    model_config = ConfigDict(populate_by_name=True)


class AdoptServiceResponse(BaseModel):
    status: str
    service_id: str = Field(alias="serviceId")
    project_id: str = Field(alias="projectId")
    pr_url: str | None = Field(default=None, alias="prUrl")
    pr_number: int | None = Field(default=None, alias="prNumber")
    message: str

    model_config = ConfigDict(populate_by_name=True)


class MigrationValidateRequest(BaseModel):
    service_id: str = Field(alias="serviceId")
    target_project_id: str = Field(alias="targetProjectId")
    target_namespace: str = Field(alias="targetNamespace")

    model_config = ConfigDict(populate_by_name=True)


class MigrationConflictResponse(BaseModel):
    kind: str
    severity: Literal["error", "warning"]
    message: str
    details: dict[str, Any] | None = None


class MigrationValidateResponse(BaseModel):
    is_safe: bool = Field(alias="isSafe")
    service_id: str = Field(alias="serviceId")
    current_namespace: str = Field(alias="currentNamespace")
    target_namespace: str = Field(alias="targetNamespace")
    conflicts: list[MigrationConflictResponse]

    model_config = ConfigDict(populate_by_name=True)


class MigrationConsolidateRequest(BaseModel):
    service_id: str = Field(alias="serviceId")
    target_project_id: str = Field(alias="targetProjectId")
    target_namespace: str = Field(alias="targetNamespace")
    acknowledge_conflicts: bool = Field(alias="acknowledgeConflicts", default=False)

    model_config = ConfigDict(populate_by_name=True)


class MigrationConsolidateResponse(BaseModel):
    status: str
    pr_url: str = Field(alias="prUrl")
    pr_number: int = Field(alias="prNumber")
    branch_name: str = Field(alias="branchName")
    files_changed: list[str] = Field(alias="filesChanged")

    model_config = ConfigDict(populate_by_name=True)
