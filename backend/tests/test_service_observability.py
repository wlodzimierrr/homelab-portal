from __future__ import annotations

from app.service_observability import build_service_metrics_observability_diagnostics


def test_app_native_missing_source_is_misconfigured() -> None:
    diagnostics = build_service_metrics_observability_diagnostics(
        mode="app-native",
        missing_metrics=["p95LatencyMs", "errorRatePct"],
        source_available=False,
        service_series_available=False,
    )

    assert diagnostics["status"] == "misconfigured"
    assert diagnostics["reason"] == "app_metrics_source_missing"
    assert diagnostics["authority"] == "app"


def test_ingress_derived_missing_service_series_is_misconfigured() -> None:
    diagnostics = build_service_metrics_observability_diagnostics(
        mode="ingress-derived",
        missing_metrics=["p95LatencyMs"],
        source_available=True,
        service_series_available=False,
    )

    assert diagnostics["status"] == "misconfigured"
    assert diagnostics["reason"] == "ingress_metrics_series_missing"
    assert diagnostics["authority"] == "ingress"


def test_no_http_mode_marks_metrics_as_unsupported() -> None:
    diagnostics = build_service_metrics_observability_diagnostics(
        mode="no-http",
        missing_metrics=["p95LatencyMs", "errorRatePct"],
    )

    assert diagnostics["status"] == "unsupported"
    assert diagnostics["reason"] == "no_http_mode_declared"
    assert diagnostics["authority"] == "none"


def test_available_metrics_return_ok_status() -> None:
    diagnostics = build_service_metrics_observability_diagnostics(
        mode="ingress-derived",
        missing_metrics=[],
        source_available=True,
        service_series_available=True,
    )

    assert diagnostics["status"] == "ok"
    assert diagnostics["reason"] == "metrics_available"
    assert diagnostics["authority"] == "ingress"
