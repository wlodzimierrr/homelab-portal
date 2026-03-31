from __future__ import annotations

from io import BytesIO
from urllib import error as urlerror

from app.service_onboarding_verification import (
    ServiceOnboardingVerificationTarget,
    build_service_onboarding_verification,
)


def _http_error(code: int, url: str) -> urlerror.HTTPError:
    return urlerror.HTTPError(url, code, "error", hdrs=None, fp=BytesIO())


def test_build_service_onboarding_verification_reports_live_service(monkeypatch) -> None:
    def _fake_kube_get_json(path: str):
        return {}

    monkeypatch.setattr(
        "app.service_onboarding_verification._kube_get_json",
        _fake_kube_get_json,
    )

    verification = build_service_onboarding_verification(
        ServiceOnboardingVerificationTarget(
            service_id="demo",
            namespace="demo",
            argo_application="demo-dev",
        )
    )

    assert verification.overall_status == "live"
    assert [check.status for check in verification.checks] == [
        "present",
        "present",
        "present",
        "present",
        "present",
    ]


def test_build_service_onboarding_verification_reports_declared_not_applied(monkeypatch) -> None:
    missing_paths = {
        "/apis/argoproj.io/v1alpha1/namespaces/argocd/applications/demo-dev",
        "/api/v1/namespaces/demo",
        "/apis/apps/v1/namespaces/demo/deployments/demo",
        "/api/v1/namespaces/demo/services/demo",
    }
    list_paths = {
        "/apis/apps/v1/namespaces/demo/deployments": {"items": []},
        "/api/v1/namespaces/demo/services": {"items": []},
    }

    def _fake_kube_get_json(path: str):
        if path in missing_paths:
            raise _http_error(404, path)
        if path in list_paths:
            return list_paths[path]
        raise AssertionError(f"unexpected path {path}")

    monkeypatch.setattr(
        "app.service_onboarding_verification._kube_get_json",
        _fake_kube_get_json,
    )

    verification = build_service_onboarding_verification(
        ServiceOnboardingVerificationTarget(
            service_id="demo",
            namespace="demo",
            argo_application="demo-dev",
        )
    )

    assert verification.overall_status == "declared_not_applied"
    assert verification.summary.startswith("Workloads declaration exists")
    assert [check.status for check in verification.checks] == [
        "present",
        "missing",
        "missing",
        "missing",
        "missing",
    ]


def test_build_service_onboarding_verification_reports_partially_applied(monkeypatch) -> None:
    def _fake_kube_get_json(path: str):
        if path == "/apis/argoproj.io/v1alpha1/namespaces/argocd/applications/demo-dev":
            raise _http_error(404, path)
        if path == "/apis/apps/v1/namespaces/demo/deployments/demo":
            raise _http_error(404, path)
        if path == "/apis/apps/v1/namespaces/demo/deployments":
            return {"items": []}
        return {}

    monkeypatch.setattr(
        "app.service_onboarding_verification._kube_get_json",
        _fake_kube_get_json,
    )

    verification = build_service_onboarding_verification(
        ServiceOnboardingVerificationTarget(
            service_id="demo",
            namespace="demo",
            argo_application="demo-dev",
        )
    )

    assert verification.overall_status == "partially_applied"
    assert verification.summary.startswith("Service onboarding is incomplete")
    assert [check.status for check in verification.checks] == [
        "present",
        "missing",
        "present",
        "missing",
        "present",
    ]


def test_build_service_onboarding_verification_reports_unavailable_cluster(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.service_onboarding_verification._kube_get_json",
        lambda _path: (_ for _ in ()).throw(RuntimeError("cluster unavailable")),
    )

    verification = build_service_onboarding_verification(
        ServiceOnboardingVerificationTarget(
            service_id="demo",
            namespace="demo",
            argo_application="demo-dev",
        )
    )

    assert verification.overall_status == "verification_unavailable"
    assert verification.summary == "Live cluster verification is unavailable right now."
    assert [check.status for check in verification.checks] == [
        "present",
        "unknown",
        "unknown",
        "unknown",
        "unknown",
    ]
