from app.projects_backfill import (
    LegacyProjectRow,
    RegistryRow,
    build_migration_plan,
    build_targets_from_legacy_projects,
    choose_canonical_service_id,
)


def test_choose_canonical_service_id_prefers_name_for_legacy_proj_ids() -> None:
    service_id, rule = choose_canonical_service_id("proj-web", "Portal Project")
    assert service_id == "portal-project"
    assert rule == "name_for_legacy_proj_prefix"


def test_choose_canonical_service_id_prefers_id_when_specific() -> None:
    service_id, rule = choose_canonical_service_id("homelab-api", "Homelab API")
    assert service_id == "homelab-api"
    assert rule == "id_preferred"


def test_build_migration_plan_marks_insert_update_noop() -> None:
    targets = build_targets_from_legacy_projects(
        [
            LegacyProjectRow(project_id="proj-web", name="Portal Project", environment="dev"),
            LegacyProjectRow(project_id="homelab-api", name="Homelab API", environment="dev"),
            LegacyProjectRow(project_id="proj-admin", name="Allowed", environment="dev"),
        ],
        default_namespace="default",
    )
    existing_rows = [
        RegistryRow(
            service_id="portal-project",
            service_name="Old Portal",
            namespace="default",
            env="dev",
            app_label="portal-project",
            argo_app_name=None,
            source="manual",
            source_ref="old",
            last_synced_at=None,
        ),
        RegistryRow(
            service_id="homelab-api",
            service_name="Homelab API",
            namespace="default",
            env="dev",
            app_label="homelab-api",
            argo_app_name=None,
            source="migration",
            source_ref="legacy_projects_migration",
            last_synced_at=None,
        ),
        RegistryRow(
            service_id="proj-admin",
            service_name="Allowed",
            namespace="default",
            env="dev",
            app_label="proj-admin",
            argo_app_name=None,
            source="manual",
            source_ref="legacy",
            last_synced_at=None,
        ),
    ]

    plan = build_migration_plan(targets, existing_rows)
    actions = {(item.target.service_id, item.action) for item in plan}
    assert ("portal-project", "update") in actions
    assert ("homelab-api", "noop") in actions
    assert ("allowed", "update_rekey") in actions
