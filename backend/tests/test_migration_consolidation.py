import pytest
import yaml

from app.migration_consolidation import (
    build_kustomization_resource_additions,
    generate_consolidation_changes,
    remap_manifest_paths,
    rewrite_namespace_in_manifest,
    update_services_yaml_namespace,
    update_services_yaml_project_id,
)


# ---------------------------------------------------------------------------
# rewrite_namespace_in_manifest
# ---------------------------------------------------------------------------


def test_rewrite_namespace_in_deployment() -> None:
    manifest = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-svc
  namespace: old-ns
spec:
  replicas: 1
"""
    result = rewrite_namespace_in_manifest(manifest, "new-ns")
    doc = yaml.safe_load(result)
    assert doc["metadata"]["namespace"] == "new-ns"
    assert doc["metadata"]["name"] == "my-svc"


def test_rewrite_namespace_in_service() -> None:
    manifest = """\
apiVersion: v1
kind: Service
metadata:
  name: my-svc
  namespace: old-ns
"""
    result = rewrite_namespace_in_manifest(manifest, "new-ns")
    doc = yaml.safe_load(result)
    assert doc["metadata"]["namespace"] == "new-ns"


# ---------------------------------------------------------------------------
# remap_manifest_paths
# ---------------------------------------------------------------------------


def test_remap_paths_adds_component_prefix() -> None:
    manifests = {
        "apps/my-svc/base/deployment.yaml": "content-1",
        "apps/my-svc/base/service.yaml": "content-2",
    }
    result = remap_manifest_paths(manifests, "my-svc", "my-project")
    assert "apps/my-project/base/my-svc-deployment.yaml" in result
    assert "apps/my-project/base/my-svc-service.yaml" in result


def test_remap_paths_preserves_already_prefixed() -> None:
    manifests = {
        "apps/my-svc/base/my-svc-deployment.yaml": "content",
    }
    result = remap_manifest_paths(manifests, "my-svc", "my-project")
    assert "apps/my-project/base/my-svc-deployment.yaml" in result


# ---------------------------------------------------------------------------
# update_services_yaml_project_id
# ---------------------------------------------------------------------------

_SERVICES_YAML = """\
services:
  - service_id: my-svc
    name: My Service
    envs:
      - name: dev
        namespace: my-svc
        argo_app: my-svc-dev
"""


def test_update_project_id_adds_field() -> None:
    result = update_services_yaml_project_id(_SERVICES_YAML, "my-svc", "my-project")
    data = yaml.safe_load(result)
    assert data["services"][0]["project_id"] == "my-project"


def test_update_project_id_updates_existing() -> None:
    yaml_with_project = _SERVICES_YAML.replace(
        "name: My Service",
        "name: My Service\n    project_id: old-project",
    )
    result = update_services_yaml_project_id(yaml_with_project, "my-svc", "new-project")
    data = yaml.safe_load(result)
    assert data["services"][0]["project_id"] == "new-project"


def test_update_project_id_unknown_service_raises() -> None:
    with pytest.raises(ValueError, match="not found"):
        update_services_yaml_project_id(_SERVICES_YAML, "unknown-svc", "my-project")


# ---------------------------------------------------------------------------
# update_services_yaml_namespace
# ---------------------------------------------------------------------------


def test_update_namespace_changes_env_entries() -> None:
    result = update_services_yaml_namespace(_SERVICES_YAML, "my-svc", "my-project")
    data = yaml.safe_load(result)
    assert data["services"][0]["envs"][0]["namespace"] == "my-project"


def test_update_namespace_also_sets_project_id() -> None:
    result = update_services_yaml_namespace(
        _SERVICES_YAML, "my-svc", "my-project", project_id="my-project"
    )
    data = yaml.safe_load(result)
    assert data["services"][0]["project_id"] == "my-project"
    assert data["services"][0]["envs"][0]["namespace"] == "my-project"


def test_update_namespace_unknown_service_raises() -> None:
    with pytest.raises(ValueError, match="not found"):
        update_services_yaml_namespace(_SERVICES_YAML, "unknown", "ns")


# ---------------------------------------------------------------------------
# build_kustomization_resource_additions
# ---------------------------------------------------------------------------


def test_kustomization_adds_new_resources() -> None:
    existing = """\
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - namespace.yaml
  - deployment.yaml
"""
    result = build_kustomization_resource_additions(existing, ["service.yaml", "ingress.yaml"])
    data = yaml.safe_load(result)
    resources = data["resources"]
    assert "service.yaml" in resources
    assert "ingress.yaml" in resources
    assert resources == sorted(resources)


def test_kustomization_deduplicates() -> None:
    existing = """\
resources:
  - deployment.yaml
"""
    result = build_kustomization_resource_additions(existing, ["deployment.yaml", "service.yaml"])
    data = yaml.safe_load(result)
    assert data["resources"].count("deployment.yaml") == 1


# ---------------------------------------------------------------------------
# generate_consolidation_changes
# ---------------------------------------------------------------------------


def test_consolidation_produces_new_and_modified_files() -> None:
    service_manifests = {
        "apps/my-svc/base/deployment.yaml": """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-svc
  namespace: my-svc
""",
        "apps/my-svc/base/service.yaml": """\
apiVersion: v1
kind: Service
metadata:
  name: my-svc
  namespace: my-svc
""",
    }
    target_kustomization = """\
resources:
  - namespace.yaml
  - frontend-deployment.yaml
"""

    new_files, modified_files = generate_consolidation_changes(
        service_id="my-svc",
        target_project_id="my-project",
        target_namespace="my-project",
        service_base_manifests=service_manifests,
        target_kustomization_yaml=target_kustomization,
        services_yaml=_SERVICES_YAML,
    )

    # New files should have remapped paths with component prefix
    assert any("my-svc-deployment.yaml" in p for p in new_files)
    assert any("my-svc-service.yaml" in p for p in new_files)

    # Modified files should include kustomization and services.yaml
    assert "apps/my-project/base/kustomization.yaml" in modified_files
    assert "services.yaml" in modified_files

    # New files should have updated namespace
    for content in new_files.values():
        doc = yaml.safe_load(content)
        assert doc["metadata"]["namespace"] == "my-project"


def test_consolidation_skips_namespace_and_default_deny() -> None:
    service_manifests = {
        "apps/my-svc/base/namespace.yaml": "kind: Namespace\nmetadata:\n  name: my-svc",
        "apps/my-svc/base/networkpolicy-default-deny.yaml": "kind: NetworkPolicy",
        "apps/my-svc/base/networkpolicy-allow-dns-egress.yaml": "kind: NetworkPolicy",
        "apps/my-svc/base/deployment.yaml": """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-svc
  namespace: my-svc
""",
    }

    new_files, _ = generate_consolidation_changes(
        service_id="my-svc",
        target_project_id="my-project",
        target_namespace="my-project",
        service_base_manifests=service_manifests,
        target_kustomization_yaml="resources: []",
        services_yaml=_SERVICES_YAML,
    )

    filenames = [p.rsplit("/", 1)[-1] for p in new_files]
    assert "namespace.yaml" not in filenames
    assert "networkpolicy-default-deny.yaml" not in filenames
    assert "networkpolicy-allow-dns-egress.yaml" not in filenames
    assert any("deployment" in f for f in filenames)


def test_consolidation_marks_argo_app_for_deletion() -> None:
    service_manifests = {
        "apps/my-svc/base/deployment.yaml": """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-svc
  namespace: my-svc
""",
    }

    _, modified_files = generate_consolidation_changes(
        service_id="my-svc",
        target_project_id="my-project",
        target_namespace="my-project",
        service_base_manifests=service_manifests,
        target_kustomization_yaml="resources: []",
        services_yaml=_SERVICES_YAML,
        source_argo_app_paths=["environments/dev/workloads/my-svc-app.yaml"],
    )

    assert "environments/dev/workloads/my-svc-app.yaml" in modified_files
    assert modified_files["environments/dev/workloads/my-svc-app.yaml"] == ""
