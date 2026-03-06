from __future__ import annotations

import json

from app.main import _registry_warning_after_minutes
from scripts import sync_catalog_registries


def test_registry_warning_after_minutes_defaults_below_stale(monkeypatch) -> None:
    monkeypatch.delenv("REGISTRY_WARN_AFTER_MINUTES", raising=False)

    value = _registry_warning_after_minutes(30)

    assert value >= 1
    assert value < 30


def test_registry_warning_after_minutes_uses_env_override(monkeypatch) -> None:
    monkeypatch.setenv("REGISTRY_WARN_AFTER_MINUTES", "12")

    value = _registry_warning_after_minutes(30)

    assert value == 12


def test_sync_catalog_registries_main_returns_zero_when_sources_succeed(
    monkeypatch,
    capsys,
) -> None:
    class _Conn:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("scripts.sync_catalog_registries.psycopg.connect", lambda *_args, **_kwargs: _Conn())
    monkeypatch.setattr("scripts.sync_catalog_registries.get_psycopg_database_url", lambda: "postgresql://example")
    monkeypatch.setattr(
        "scripts.sync_catalog_registries.sync_project_registry_from_gitops",
        lambda conn, env_name=None: {
            "source": "gitops_apps",
            "sourceFailures": [],
            "correlationId": "cid-projects",
        },
    )
    monkeypatch.setattr(
        "scripts.sync_catalog_registries.sync_service_registry_from_cluster",
        lambda conn, env_name=None: {
            "source": "cluster_services",
            "sourceFailures": [],
            "correlationId": "cid-services",
        },
    )

    exit_code = sync_catalog_registries.main()

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["hasFailures"] is False
    assert payload["sources"]["gitops_apps"]["correlationId"] == "cid-projects"
    assert payload["sources"]["cluster_services"]["correlationId"] == "cid-services"


def test_sync_catalog_registries_main_returns_non_zero_when_sources_fail(
    monkeypatch,
    capsys,
) -> None:
    class _Conn:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("scripts.sync_catalog_registries.psycopg.connect", lambda *_args, **_kwargs: _Conn())
    monkeypatch.setattr("scripts.sync_catalog_registries.get_psycopg_database_url", lambda: "postgresql://example")
    monkeypatch.setattr(
        "scripts.sync_catalog_registries.sync_project_registry_from_gitops",
        lambda conn, env_name=None: {
            "source": "gitops_apps",
            "sourceFailures": [{"source": "gitops", "scope": "dev", "error": "bad repo"}],
            "correlationId": "cid-projects",
        },
    )
    monkeypatch.setattr(
        "scripts.sync_catalog_registries.sync_service_registry_from_cluster",
        lambda conn, env_name=None: {
            "source": "cluster_services",
            "sourceFailures": [],
            "correlationId": "cid-services",
        },
    )

    exit_code = sync_catalog_registries.main()

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["hasFailures"] is True
    assert payload["sources"]["gitops_apps"]["sourceFailures"][0]["error"] == "bad repo"
