from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib import error as urlerror

from app.api.schemas.onboarding import (
    ServiceOnboardingVerification,
    ServiceOnboardingVerificationCheck,
)
from app.service_identity import normalize_service_id
from app.service_registry_sync import DEFAULT_ARGO_NAMESPACE, _kube_get_json


WorkloadKind = Literal["deployment", "statefulset"]


@dataclass(frozen=True)
class ServiceOnboardingVerificationTarget:
    service_id: str
    namespace: str
    argo_application: str
    workload_kind: WorkloadKind = "deployment"
    workload_name: str | None = None
    service_name: str | None = None
    workloads_declared: bool = True
    declaration_source: str = "workloads_repo"


def build_service_onboarding_verification(
    target: ServiceOnboardingVerificationTarget,
) -> ServiceOnboardingVerification:
    workload_name = target.workload_name or target.service_id
    service_name = target.service_name or target.service_id
    checks = [
        _build_workloads_declaration_check(target),
        _check_argo_application(target.argo_application),
        _check_namespace(target.namespace),
    ]

    if target.workload_kind == "deployment":
        checks.append(_check_deployment(target.namespace, workload_name, target.service_id))
    else:
        checks.append(
            ServiceOnboardingVerificationCheck(
                name="deployment",
                status="not_applicable",
                detail="This service type does not create a Deployment.",
            )
        )
        checks.append(_check_statefulset(target.namespace, workload_name, target.service_id))

    checks.append(_check_service(target.namespace, service_name, target.service_id))

    overall_status, summary = _summarize_checks(checks)
    return ServiceOnboardingVerification(
        serviceId=target.service_id,
        namespace=target.namespace,
        argoApplication=target.argo_application,
        workloadKind=target.workload_kind,
        workloadName=workload_name,
        serviceName=service_name,
        overallStatus=overall_status,
        summary=summary,
        checks=checks,
    )


def build_service_onboarding_verifications(
    targets: list[ServiceOnboardingVerificationTarget],
) -> list[ServiceOnboardingVerification]:
    return [build_service_onboarding_verification(target) for target in targets]


def _build_workloads_declaration_check(
    target: ServiceOnboardingVerificationTarget,
) -> ServiceOnboardingVerificationCheck:
    if target.workloads_declared:
        return ServiceOnboardingVerificationCheck(
            name="workloadsDeclaration",
            status="present",
            detail=f"Service is declared via {target.declaration_source}.",
        )
    return ServiceOnboardingVerificationCheck(
        name="workloadsDeclaration",
        status="missing",
        detail="Service is not declared in workloads yet.",
    )


def _check_argo_application(app_name: str) -> ServiceOnboardingVerificationCheck:
    status, detail = _fetch_named_resource(
        f"/apis/argoproj.io/v1alpha1/namespaces/{DEFAULT_ARGO_NAMESPACE}/applications/{app_name}",
        present_detail=f"Argo Application {app_name} exists in {DEFAULT_ARGO_NAMESPACE}.",
        missing_detail=f"Argo Application {app_name} was not found in {DEFAULT_ARGO_NAMESPACE}.",
        unknown_prefix=f"Argo Application {app_name} could not be verified",
    )
    return ServiceOnboardingVerificationCheck(name="argoApplication", status=status, detail=detail)


def _check_namespace(namespace: str) -> ServiceOnboardingVerificationCheck:
    status, detail = _fetch_named_resource(
        f"/api/v1/namespaces/{namespace}",
        present_detail=f"Namespace {namespace} exists in the cluster.",
        missing_detail=f"Namespace {namespace} was not found in the cluster.",
        unknown_prefix=f"Namespace {namespace} could not be verified",
    )
    return ServiceOnboardingVerificationCheck(name="namespace", status=status, detail=detail)


def _check_deployment(
    namespace: str,
    workload_name: str,
    service_id: str,
) -> ServiceOnboardingVerificationCheck:
    status, detail = _check_named_or_matching_resource(
        resource_name="deployment",
        direct_path=f"/apis/apps/v1/namespaces/{namespace}/deployments/{workload_name}",
        list_path=f"/apis/apps/v1/namespaces/{namespace}/deployments",
        expected_name=workload_name,
        service_id=service_id,
        present_detail=f"Deployment {workload_name} exists in namespace {namespace}.",
        missing_detail=f"Deployment {workload_name} was not found in namespace {namespace}.",
        unknown_prefix=f"Deployment {workload_name} could not be verified",
    )
    return ServiceOnboardingVerificationCheck(name="deployment", status=status, detail=detail)


def _check_statefulset(
    namespace: str,
    workload_name: str,
    service_id: str,
) -> ServiceOnboardingVerificationCheck:
    status, detail = _check_named_or_matching_resource(
        resource_name="statefulset",
        direct_path=f"/apis/apps/v1/namespaces/{namespace}/statefulsets/{workload_name}",
        list_path=f"/apis/apps/v1/namespaces/{namespace}/statefulsets",
        expected_name=workload_name,
        service_id=service_id,
        present_detail=f"StatefulSet {workload_name} exists in namespace {namespace}.",
        missing_detail=f"StatefulSet {workload_name} was not found in namespace {namespace}.",
        unknown_prefix=f"StatefulSet {workload_name} could not be verified",
    )
    return ServiceOnboardingVerificationCheck(name="statefulset", status=status, detail=detail)


def _check_service(
    namespace: str,
    service_name: str,
    service_id: str,
) -> ServiceOnboardingVerificationCheck:
    status, detail = _check_named_or_matching_resource(
        resource_name="service",
        direct_path=f"/api/v1/namespaces/{namespace}/services/{service_name}",
        list_path=f"/api/v1/namespaces/{namespace}/services",
        expected_name=service_name,
        service_id=service_id,
        present_detail=f"Service {service_name} exists in namespace {namespace}.",
        missing_detail=f"Service {service_name} was not found in namespace {namespace}.",
        unknown_prefix=f"Service {service_name} could not be verified",
    )
    return ServiceOnboardingVerificationCheck(name="service", status=status, detail=detail)


def _check_named_or_matching_resource(
    *,
    resource_name: str,
    direct_path: str,
    list_path: str,
    expected_name: str,
    service_id: str,
    present_detail: str,
    missing_detail: str,
    unknown_prefix: str,
) -> tuple[Literal["present", "missing", "unknown"], str]:
    status, detail = _fetch_named_resource(
        direct_path,
        present_detail=present_detail,
        missing_detail=missing_detail,
        unknown_prefix=unknown_prefix,
    )
    if status != "missing":
        return status, detail

    try:
        payload = _kube_get_json(list_path)
    except Exception as exc:  # pragma: no cover - exercised by helper tests
        return "unknown", f"{unknown_prefix}: {exc}"

    items = payload.get("items", [])
    if not isinstance(items, list):
        return "missing", missing_detail

    matched_name = _find_matching_resource_name(items, expected_name=expected_name, service_id=service_id)
    if matched_name:
        return "present", f"{resource_name.capitalize()} {matched_name} matches {service_id} in cluster."
    return "missing", missing_detail


def _fetch_named_resource(
    path: str,
    *,
    present_detail: str,
    missing_detail: str,
    unknown_prefix: str,
) -> tuple[Literal["present", "missing", "unknown"], str]:
    try:
        _kube_get_json(path)
    except urlerror.HTTPError as exc:
        if exc.code == 404:
            return "missing", missing_detail
        return "unknown", f"{unknown_prefix}: HTTP {exc.code}"
    except Exception as exc:  # pragma: no cover - exercised by helper tests
        return "unknown", f"{unknown_prefix}: {exc}"
    return "present", present_detail


def _find_matching_resource_name(
    items: list[object],
    *,
    expected_name: str,
    service_id: str,
) -> str | None:
    normalized_service_id = normalize_service_id(service_id)
    normalized_expected_name = normalize_service_id(expected_name)

    for item in items:
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata", {})
        if not isinstance(metadata, dict):
            continue
        name = str(metadata.get("name") or "").strip()
        labels = metadata.get("labels", {})
        if not isinstance(labels, dict):
            labels = {}

        candidate_values = {
            name,
            str(labels.get("app.kubernetes.io/name") or "").strip(),
            str(labels.get("app.kubernetes.io/instance") or "").strip(),
            str(labels.get("app") or "").strip(),
        }
        normalized_candidates = {normalize_service_id(value) for value in candidate_values if value}
        if normalized_service_id in normalized_candidates or normalized_expected_name in normalized_candidates:
            return name or expected_name
    return None


def _summarize_checks(
    checks: list[ServiceOnboardingVerificationCheck],
) -> tuple[
    Literal[
        "live",
        "declared_not_applied",
        "partially_applied",
        "workloads_not_declared",
        "verification_unavailable",
    ],
    str,
]:
    workloads_check = next(check for check in checks if check.name == "workloadsDeclaration")
    if workloads_check.status == "missing":
        return "workloads_not_declared", "Workloads declaration is missing, so live reconciliation cannot begin."

    live_checks = [check for check in checks if check.name != "workloadsDeclaration"]
    missing_names = [check.name for check in live_checks if check.status == "missing"]
    unknown_names = [check.name for check in live_checks if check.status == "unknown"]
    present_names = [check.name for check in live_checks if check.status == "present"]

    if not missing_names and not unknown_names:
        return "live", "Workloads declaration and live cluster resources are present."

    if unknown_names and not missing_names and not present_names:
        return "verification_unavailable", "Live cluster verification is unavailable right now."

    if missing_names and not present_names and not unknown_names:
        return "declared_not_applied", (
            "Workloads declaration exists, but live cluster resources are not present yet: "
            + ", ".join(missing_names)
            + "."
        )

    fragments: list[str] = []
    if missing_names:
        fragments.append("missing " + ", ".join(missing_names))
    if unknown_names:
        fragments.append("unverified " + ", ".join(unknown_names))
    return "partially_applied", "Service onboarding is incomplete: " + "; ".join(fragments) + "."
