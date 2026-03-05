from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re


@dataclass(frozen=True)
class LegacyProjectRow:
    project_id: str
    name: str
    environment: str


@dataclass(frozen=True)
class RegistryRow:
    service_id: str
    service_name: str
    namespace: str
    env: str
    app_label: str
    argo_app_name: str | None
    source: str
    source_ref: str | None
    last_synced_at: datetime | None


@dataclass(frozen=True)
class MigrationTargetRow:
    service_id: str
    service_name: str
    namespace: str
    env: str
    app_label: str
    argo_app_name: str | None
    source: str
    source_ref: str
    last_synced_at: datetime
    legacy_project_id: str
    mapping_rule: str


@dataclass(frozen=True)
class MigrationPlanRow:
    action: str
    target: MigrationTargetRow
    previous: RegistryRow | None
    changed_fields: tuple[str, ...]


def normalize_service_id(value: str) -> str:
    normalized = value.strip().lower()
    normalized = re.sub(r"[^a-z0-9._-]+", "-", normalized)
    normalized = normalized.strip("-")
    return normalized or "unknown-service"


def choose_canonical_service_id(project_id: str, project_name: str) -> tuple[str, str]:
    from_id = normalize_service_id(project_id)
    from_name = normalize_service_id(project_name)

    if re.match(r"^proj($|[-_])", from_id):
        return from_name, "name_for_legacy_proj_prefix"
    if from_id in {"proj", "project", "unknown-service"}:
        return from_name, "name_for_generic_id"
    return from_id, "id_preferred"


def build_targets_from_legacy_projects(
    legacy_rows: list[LegacyProjectRow],
    *,
    default_namespace: str,
    source_ref: str = "legacy_projects_migration",
) -> list[MigrationTargetRow]:
    now = datetime.now(tz=timezone.utc)
    targets: list[MigrationTargetRow] = []
    for row in legacy_rows:
        service_id, mapping_rule = choose_canonical_service_id(row.project_id, row.name)
        targets.append(
            MigrationTargetRow(
                service_id=service_id,
                service_name=row.name,
                namespace=default_namespace,
                env=row.environment,
                app_label=service_id,
                argo_app_name=None,
                source="migration",
                source_ref=source_ref,
                last_synced_at=now,
                legacy_project_id=row.project_id,
                mapping_rule=mapping_rule,
            )
        )
    return targets


def build_migration_plan(
    targets: list[MigrationTargetRow],
    existing_rows: list[RegistryRow],
) -> list[MigrationPlanRow]:
    existing_index = {(row.service_id, row.env): row for row in existing_rows}
    existing_name_index = {
        (row.service_name, row.namespace, row.env): row for row in existing_rows
    }
    plan: list[MigrationPlanRow] = []

    for target in targets:
        key = (target.service_id, target.env)
        previous = existing_index.get(key)

        name_key = (target.service_name, target.namespace, target.env)
        name_conflict_row = existing_name_index.get(name_key)
        if (
            previous is None
            and name_conflict_row is not None
            and name_conflict_row.service_id != target.service_id
        ):
            plan.append(
                MigrationPlanRow(
                    action="update_rekey",
                    target=target,
                    previous=name_conflict_row,
                    changed_fields=(
                        "service_id",
                        "app_label",
                        "source",
                        "source_ref",
                        "last_synced_at",
                    ),
                )
            )
            continue

        if previous is None:
            plan.append(
                MigrationPlanRow(
                    action="insert",
                    target=target,
                    previous=None,
                    changed_fields=(
                        "service_name",
                        "namespace",
                        "app_label",
                        "source",
                        "source_ref",
                        "last_synced_at",
                    ),
                )
            )
            continue

        changed_fields: list[str] = []
        if previous.service_name != target.service_name:
            changed_fields.append("service_name")
        if previous.namespace != target.namespace:
            changed_fields.append("namespace")
        if previous.app_label != target.app_label:
            changed_fields.append("app_label")
        if previous.argo_app_name != target.argo_app_name:
            changed_fields.append("argo_app_name")
        if previous.source != target.source:
            changed_fields.append("source")
        if (previous.source_ref or None) != target.source_ref:
            changed_fields.append("source_ref")

        if changed_fields:
            plan.append(
                MigrationPlanRow(
                    action="update",
                    target=target,
                    previous=previous,
                    changed_fields=tuple(changed_fields),
                )
            )
        else:
            plan.append(
                MigrationPlanRow(
                    action="noop",
                    target=target,
                    previous=previous,
                    changed_fields=tuple(),
                )
            )
    return plan
