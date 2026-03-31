from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


VerificationCheckStatus = Literal["present", "missing", "unknown", "not_applicable"]
VerificationOverallStatus = Literal[
    "live",
    "declared_not_applied",
    "partially_applied",
    "workloads_not_declared",
    "verification_unavailable",
]


class ServiceOnboardingVerificationCheck(BaseModel):
    name: Literal[
        "workloadsDeclaration",
        "argoApplication",
        "namespace",
        "deployment",
        "statefulset",
        "service",
    ]
    status: VerificationCheckStatus
    detail: str


class ServiceOnboardingVerification(BaseModel):
    service_id: str = Field(alias="serviceId")
    namespace: str
    argo_application: str = Field(alias="argoApplication")
    workload_kind: Literal["deployment", "statefulset"] = Field(alias="workloadKind")
    workload_name: str = Field(alias="workloadName")
    service_name: str = Field(alias="serviceName")
    overall_status: VerificationOverallStatus = Field(alias="overallStatus")
    summary: str
    checks: list[ServiceOnboardingVerificationCheck]

    model_config = ConfigDict(populate_by_name=True)
