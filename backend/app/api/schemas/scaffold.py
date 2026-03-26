from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ScaffoldServiceRequest(BaseModel):
    name: str
    description: str
    image_repo: str = Field(alias="imageRepo", default="")
    repo_url: str = Field(alias="repoUrl", default="")
    owner_email: str = Field(alias="ownerEmail")
    owner: str = ""
    template: Literal["python-fastapi", "python-django", "python-flask", "static-nginx", "react", "nextjs", "vue", "wordpress", "node-express", "node-nestjs", "postgres", "mysql"] = "python-fastapi"
    namespace: str = ""
    dev_host: str = Field(alias="devHost", default="")
    prod_host: str = Field(alias="prodHost", default="")
    public_host: str = Field(alias="publicHost", default="")
    db_username: str = Field(alias="dbUsername", default="")
    db_password: str = Field(alias="dbPassword", default="")
    db_name: str = Field(alias="dbName", default="")
    # Bundle topology fields
    topology: Literal["single-service", "frontend-backend", "frontend-backend-db"] = "single-service"
    frontend_template: Literal["react", "nextjs", "vue", "static-nginx"] | None = Field(alias="frontendTemplate", default=None)
    frontend_image_repo: str = Field(alias="frontendImageRepo", default="")
    backend_template: Literal["python-fastapi", "python-django", "python-flask", "node-express", "node-nestjs"] | None = Field(alias="backendTemplate", default=None)
    backend_image_repo: str = Field(alias="backendImageRepo", default="")
    db_template: Literal["postgres", "mysql"] | None = Field(alias="dbTemplate", default=None)
    # Add-to-project fields
    mode: Literal["new-project", "add-to-project"] = Field(default="new-project")
    project_id: str = Field(alias="projectId", default="")
    service_name: str = Field(alias="serviceName", default="")

    model_config = ConfigDict(populate_by_name=True)


class ScaffoldProjectInfo(BaseModel):
    project_id: str = Field(alias="projectId")
    namespace: str
    service_ids: list[str] = Field(alias="serviceIds")

    model_config = ConfigDict(populate_by_name=True)


class ScaffoldPreviewFile(BaseModel):
    path: str
    content: str
    change_type: str = Field(alias="changeType")

    model_config = ConfigDict(populate_by_name=True)


class ScaffoldPreviewResponse(BaseModel):
    files: list[ScaffoldPreviewFile]


class ScaffoldSubmitResponse(BaseModel):
    pr_url: str = Field(alias="prUrl")
    pr_number: int = Field(alias="prNumber")
    branch_name: str = Field(alias="branchName")
    files_committed: list[str] = Field(alias="filesCommitted")
    initiated_at: str = Field(alias="initiatedAt")

    model_config = ConfigDict(populate_by_name=True)
