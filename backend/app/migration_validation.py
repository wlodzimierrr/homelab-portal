"""Validate whether a service can be safely migrated to a target namespace.

Pure validation logic — reads YAML manifests and detects conflicts.
Does not touch the database or cluster.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

import yaml


@dataclass(frozen=True)
class MigrationConflict:
    kind: str
    severity: Literal["error", "warning"]
    message: str
    details: dict[str, str] | None = None


@dataclass
class MigrationValidationResult:
    service_id: str
    current_namespace: str
    target_namespace: str
    conflicts: list[MigrationConflict] = field(default_factory=list)

    @property
    def is_safe(self) -> bool:
        return not any(c.severity == "error" for c in self.conflicts)


def validate_migration(
    *,
    service_id: str,
    current_namespace: str,
    target_namespace: str,
    service_manifests: dict[str, str],
    target_project_manifests: dict[str, str],
    target_project_argo_app_name: str | None = None,
    source_argo_app_name: str | None = None,
) -> MigrationValidationResult:
    """Run all conflict checks for a namespace migration."""
    result = MigrationValidationResult(
        service_id=service_id,
        current_namespace=current_namespace,
        target_namespace=target_namespace,
    )

    if current_namespace == target_namespace:
        return result

    result.conflicts.extend(
        check_ingress_conflicts(service_manifests, target_project_manifests)
    )
    result.conflicts.extend(
        check_pvc_scope(service_manifests)
    )
    result.conflicts.extend(
        check_secret_scope(service_manifests, target_namespace)
    )
    result.conflicts.extend(
        check_service_discovery_references(
            service_manifests, current_namespace, target_namespace
        )
    )
    result.conflicts.extend(
        check_argo_app_conflicts(
            source_argo_app_name, target_project_argo_app_name
        )
    )

    return result


def check_ingress_conflicts(
    service_manifests: dict[str, str],
    target_project_manifests: dict[str, str],
) -> list[MigrationConflict]:
    """Detect host+path collisions between source and target ingress rules."""
    conflicts: list[MigrationConflict] = []

    source_routes = _extract_ingress_routes(service_manifests)
    target_routes = _extract_ingress_routes(target_project_manifests)

    for host, path in source_routes:
        if (host, path) in target_routes:
            conflicts.append(
                MigrationConflict(
                    kind="ingress_host_conflict",
                    severity="error",
                    message=f"Ingress route {host}{path} already exists in target project.",
                    details={"host": host, "path": path},
                )
            )

    return conflicts


def check_pvc_scope(
    service_manifests: dict[str, str],
) -> list[MigrationConflict]:
    """Flag PVCs that cannot be moved across namespaces."""
    conflicts: list[MigrationConflict] = []

    for path, content in service_manifests.items():
        try:
            for doc in yaml.safe_load_all(content):
                if not isinstance(doc, dict):
                    continue
                kind = doc.get("kind", "")
                if kind == "PersistentVolumeClaim":
                    name = doc.get("metadata", {}).get("name", "unknown")
                    conflicts.append(
                        MigrationConflict(
                            kind="pvc_scope",
                            severity="error",
                            message=f"PVC '{name}' in {path} is namespace-scoped and cannot be moved. Data must be migrated manually.",
                            details={"pvc_name": name, "file": path},
                        )
                    )
                # Also check StatefulSet volumeClaimTemplates
                if kind == "StatefulSet":
                    templates = (
                        doc.get("spec", {}).get("volumeClaimTemplates") or []
                    )
                    for tpl in templates:
                        pvc_name = tpl.get("metadata", {}).get("name", "unknown")
                        conflicts.append(
                            MigrationConflict(
                                kind="pvc_scope",
                                severity="error",
                                message=f"StatefulSet in {path} has volumeClaimTemplate '{pvc_name}' — PVCs cannot be moved.",
                                details={"pvc_name": pvc_name, "file": path},
                            )
                        )
        except yaml.YAMLError:
            continue

    return conflicts


def check_secret_scope(
    service_manifests: dict[str, str],
    target_namespace: str,
) -> list[MigrationConflict]:
    """Warn about secrets that need to be recreated in the target namespace."""
    conflicts: list[MigrationConflict] = []
    secret_refs: set[str] = set()

    for content in service_manifests.values():
        try:
            for doc in yaml.safe_load_all(content):
                if not isinstance(doc, dict):
                    continue
                _collect_secret_refs(doc, secret_refs)
        except yaml.YAMLError:
            continue

    for ref in sorted(secret_refs):
        conflicts.append(
            MigrationConflict(
                kind="secret_scope",
                severity="warning",
                message=f"Secret '{ref}' must exist in namespace '{target_namespace}' after migration.",
                details={"secret_name": ref, "target_namespace": target_namespace},
            )
        )

    return conflicts


def check_service_discovery_references(
    service_manifests: dict[str, str],
    current_namespace: str,
    target_namespace: str,
) -> list[MigrationConflict]:
    """Warn about env vars referencing the old namespace DNS name."""
    conflicts: list[MigrationConflict] = []
    # Match patterns like svc-name.old-namespace.svc.cluster.local
    # or just svc-name.old-namespace
    pattern = re.compile(
        rf"[a-z0-9-]+\.{re.escape(current_namespace)}(?:\.svc(?:\.cluster\.local)?)?",
        re.IGNORECASE,
    )

    for path, content in service_manifests.items():
        matches = pattern.findall(content)
        for match in matches:
            conflicts.append(
                MigrationConflict(
                    kind="service_discovery",
                    severity="warning",
                    message=f"DNS reference '{match}' in {path} will break after migration to '{target_namespace}'.",
                    details={"reference": match, "file": path},
                )
            )

    return conflicts


def check_argo_app_conflicts(
    source_argo_app_name: str | None,
    target_project_argo_app_name: str | None,
) -> list[MigrationConflict]:
    """Check if the source service has its own ArgoCD app that would conflict."""
    conflicts: list[MigrationConflict] = []

    if (
        source_argo_app_name
        and target_project_argo_app_name
        and source_argo_app_name != target_project_argo_app_name
    ):
        conflicts.append(
            MigrationConflict(
                kind="argo_app_conflict",
                severity="warning",
                message=(
                    f"Source ArgoCD app '{source_argo_app_name}' differs from target "
                    f"project app '{target_project_argo_app_name}'. The source app "
                    f"should be removed after migration."
                ),
                details={
                    "source_app": source_argo_app_name,
                    "target_app": target_project_argo_app_name,
                },
            )
        )

    return conflicts


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_ingress_routes(
    manifests: dict[str, str],
) -> set[tuple[str, str]]:
    """Extract (host, path) pairs from Ingress manifests."""
    routes: set[tuple[str, str]] = set()
    for content in manifests.values():
        try:
            for doc in yaml.safe_load_all(content):
                if not isinstance(doc, dict):
                    continue
                if doc.get("kind") != "Ingress":
                    continue
                for rule in doc.get("spec", {}).get("rules") or []:
                    host = rule.get("host", "*")
                    for http_path in (rule.get("http", {}).get("paths") or []):
                        path = http_path.get("path", "/")
                        routes.add((host, path))
        except yaml.YAMLError:
            continue
    return routes


def _collect_secret_refs(obj: object, refs: set[str]) -> None:
    """Recursively find secretKeyRef names in a Kubernetes manifest."""
    if isinstance(obj, dict):
        secret_ref = obj.get("secretKeyRef")
        if isinstance(secret_ref, dict) and "name" in secret_ref:
            refs.add(secret_ref["name"])
        # Also check volume secret references
        if obj.get("secret") and isinstance(obj["secret"], dict):
            secret_name = obj["secret"].get("secretName")
            if secret_name:
                refs.add(secret_name)
        for value in obj.values():
            _collect_secret_refs(value, refs)
    elif isinstance(obj, list):
        for item in obj:
            _collect_secret_refs(item, refs)
