from app.api.endpoints.scaffold import (
    generate_scaffold_files_and_updates,
    inspect_service_registry_sync_namespace_coverage,
    parse_service_registry_sync_namespaces,
    update_service_registry_sync_namespaces,
)
from app.api.schemas.onboarding import ServiceOnboardingVerification
from app.api.schemas.migration import AdoptServiceRequest
from app.api.schemas.scaffold import ScaffoldServiceRequest
from app.scaffold_service import ScaffoldServiceInput, build_catalog_entry_addition
from app.services.scaffold_admin_service import ScaffoldAdminService, ScaffoldAdminServiceDeps
from fastapi import HTTPException
import pytest
import yaml


_CATALOG_SYNC_CRONJOB = """
apiVersion: batch/v1
kind: CronJob
spec:
  jobTemplate:
    spec:
      template:
        spec:
          containers:
            - name: catalog-sync
              env:
                - name: PORTAL_ENV
                  value: dev
                - name: SERVICE_REGISTRY_SYNC_NAMESPACES
                  value: homelab-api,homelab-web
""".lstrip()

_CATALOG_SYNC_CRONJOB_NO_SYNC_ENV = """
apiVersion: batch/v1
kind: CronJob
spec:
  jobTemplate:
    spec:
      template:
        spec:
          containers:
            - name: catalog-sync
              env:
                - name: PORTAL_ENV
                  value: dev
""".lstrip()


def _sample_verification(service_id: str, namespace: str, argo_application: str) -> list[ServiceOnboardingVerification]:
    return [
        ServiceOnboardingVerification(
            serviceId=service_id,
            namespace=namespace,
            argoApplication=argo_application,
            workloadKind="deployment",
            workloadName=service_id,
            serviceName=service_id,
            overallStatus="declared_not_applied",
            summary="Workloads declaration exists, but live cluster resources are not present yet.",
            checks=[],
        )
    ]


def test_update_service_registry_sync_namespaces_adds_namespace_once() -> None:
    updated = update_service_registry_sync_namespaces(_CATALOG_SYNC_CRONJOB, "portfolio-next")

    assert "value: homelab-api,homelab-web,portfolio-next" in updated


def test_update_service_registry_sync_namespaces_does_not_duplicate_existing_namespace() -> None:
    updated = update_service_registry_sync_namespaces(_CATALOG_SYNC_CRONJOB, "homelab-web")

    assert "value: homelab-api,homelab-web" in updated
    assert "homelab-web,homelab-web" not in updated


def test_parse_service_registry_sync_namespaces_reads_csv_allowlist() -> None:
    assert parse_service_registry_sync_namespaces(_CATALOG_SYNC_CRONJOB) == [
        "homelab-api",
        "homelab-web",
    ]


def test_inspect_service_registry_sync_namespace_coverage_reports_present_namespace() -> None:
    coverage = inspect_service_registry_sync_namespace_coverage(
        _CATALOG_SYNC_CRONJOB,
        "homelab-web",
    )

    assert coverage == {
        "namespace": "homelab-web",
        "covered": True,
        "reason": "namespace_present",
        "configuredNamespaces": ["homelab-api", "homelab-web"],
        "sourcePath": "apps/homelab-api/base/catalog-sync-cronjob.yaml",
        "sourceEnvVar": "SERVICE_REGISTRY_SYNC_NAMESPACES",
    }


def test_inspect_service_registry_sync_namespace_coverage_reports_missing_namespace() -> None:
    coverage = inspect_service_registry_sync_namespace_coverage(
        _CATALOG_SYNC_CRONJOB,
        "portfolio-next",
    )

    assert coverage == {
        "namespace": "portfolio-next",
        "covered": False,
        "reason": "namespace_missing_from_allowlist",
        "configuredNamespaces": ["homelab-api", "homelab-web"],
        "sourcePath": "apps/homelab-api/base/catalog-sync-cronjob.yaml",
        "sourceEnvVar": "SERVICE_REGISTRY_SYNC_NAMESPACES",
    }


def test_inspect_service_registry_sync_namespace_coverage_reports_missing_env_var() -> None:
    coverage = inspect_service_registry_sync_namespace_coverage(
        _CATALOG_SYNC_CRONJOB_NO_SYNC_ENV,
        "portfolio-next",
    )

    assert coverage == {
        "namespace": "portfolio-next",
        "covered": False,
        "reason": "env_var_missing_or_empty",
        "configuredNamespaces": [],
        "sourcePath": "apps/homelab-api/base/catalog-sync-cronjob.yaml",
        "sourceEnvVar": "SERVICE_REGISTRY_SYNC_NAMESPACES",
    }


def test_generate_scaffold_files_and_updates_adds_sync_namespace_file(monkeypatch) -> None:
    reads: list[tuple[str, str, str]] = []

    class _FakeGitProvider:
        def read_file(self, repo, branch, file_path):
            reads.append((repo, branch, file_path))
            return {
                "environments/dev/workloads/kustomization.yaml": "resources: []\n",
                "bootstrap/project-homelab.yaml": "spec: {}\n",
                "services.yaml": "services: []\n",
                "apps/homelab-api/base/catalog-sync-cronjob.yaml": _CATALOG_SYNC_CRONJOB,
            }[file_path]

    monkeypatch.setattr(
        "app.main.build_default_git_provider", lambda: _FakeGitProvider()
    )
    monkeypatch.setattr(
        "app.api.endpoints.scaffold.generate_gitops_new_files",
        lambda inp, **_: {f"apps/{inp.name}/{inp.name}-app.yaml": "kind: Application\n"},
    )
    monkeypatch.setattr(
        "app.api.endpoints.scaffold.update_kustomization_resources",
        lambda _raw, new_resource: f"resources:\n- {new_resource}\n",
    )
    monkeypatch.setattr(
        "app.api.endpoints.scaffold.build_catalog_entry_addition",
        lambda _raw, inp: f"services:\n- service_id: {inp.name}\n",
    )
    monkeypatch.setattr(
        "app.api.endpoints.scaffold.build_appproject_addition",
        lambda _raw, inp: f"metadata:\n  name: {inp.name}\n",
    )

    new_files, modified_files = generate_scaffold_files_and_updates(
        ScaffoldServiceRequest(
            name="Demo",
            description="Demo service",
            imageRepo="ghcr.io/example/demo",
            repoUrl="https://github.com/example/demo",
            ownerEmail="owner@example.com",
        ),
        "wlodzimierrr/homelab-workloads",
        "main",
        _FakeGitProvider(),
    )

    assert new_files == {"apps/demo/demo-app.yaml": "kind: Application\n"}
    assert modified_files == {
        "bootstrap/project-homelab.yaml": "metadata:\n  name: demo\n",
        "apps/homelab-api/base/catalog-sync-cronjob.yaml": update_service_registry_sync_namespaces(
            _CATALOG_SYNC_CRONJOB,
            "demo",
        ),
        "environments/dev/workloads/kustomization.yaml": "resources:\n- demo-app.yaml\n",
        "services.yaml": "services:\n- service_id: demo\n",
    }
    assert inspect_service_registry_sync_namespace_coverage(
        modified_files["apps/homelab-api/base/catalog-sync-cronjob.yaml"],
        "demo",
    )["covered"] is True


def test_generate_scaffold_files_and_updates_preserves_public_host_for_ingress(monkeypatch) -> None:
    captured: dict[str, ScaffoldServiceInput] = {}

    class _FakeGitProvider:
        def read_file(self, _repo, _branch, file_path):
            return {
                "environments/dev/workloads/kustomization.yaml": "resources: []\n",
                "bootstrap/project-homelab.yaml": "spec: {}\n",
                "services.yaml": "services: []\n",
                "apps/homelab-api/base/catalog-sync-cronjob.yaml": _CATALOG_SYNC_CRONJOB,
            }[file_path]

    def fake_generate(inp, **_kwargs):
        captured["input"] = inp
        return {f"apps/{inp.name}/{inp.name}-app.yaml": "kind: Application\n"}

    monkeypatch.setattr("app.api.endpoints.scaffold.generate_gitops_new_files", fake_generate)
    monkeypatch.setattr(
        "app.api.endpoints.scaffold.update_kustomization_resources",
        lambda _raw, new_resource: f"resources:\n- {new_resource}\n",
    )
    monkeypatch.setattr(
        "app.api.endpoints.scaffold.build_catalog_entry_addition",
        lambda _raw, inp: f"services:\n- service_id: {inp.name}\n",
    )
    monkeypatch.setattr(
        "app.api.endpoints.scaffold.build_appproject_addition",
        lambda _raw, inp: f"metadata:\n  name: {inp.name}\n",
    )

    generate_scaffold_files_and_updates(
        ScaffoldServiceRequest(
            name="comparebuilding-web",
            description="Compare building products webpage",
            imageRepo="ghcr.io/wlodzimierrr/compare_frontend:latest",
            repoUrl="https://github.com/wlodzimierrr/CompareBuildingProducts_Web",
            ownerEmail="owner@example.com",
            publicHost="comparebuilding.wlodzimierrr.pl",
        ),
        "wlodzimierrr/homelab-workloads",
        "main",
        _FakeGitProvider(),
    )

    assert captured["input"].prod_host == ""
    assert captured["input"].public_host == "comparebuilding.wlodzimierrr.pl"


def test_generate_scaffold_files_and_updates_encrypts_wordpress_secrets(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _FakeGitProvider:
        def read_file(self, repo, branch, file_path):
            return {
                "environments/dev/workloads/kustomization.yaml": "resources: []\n",
                "bootstrap/project-homelab.yaml": "spec: {}\n",
                "services.yaml": "services: []\n",
                "apps/homelab-api/base/catalog-sync-cronjob.yaml": _CATALOG_SYNC_CRONJOB,
                ".sops.yaml": "creation_rules:\n  - path_regex: .*\\.enc\\.yaml$\n",
            }[file_path]

    def fake_generate(inp, wordpress_secret_encrypter=None):
        assert wordpress_secret_encrypter is not None
        captured["encrypted"] = wordpress_secret_encrypter(
            "apps/demo/envs/dev/wordpress-db-secret.enc.yaml",
            "kind: Secret\nstringData:\n  WORDPRESS_DB_PASSWORD: plaintext\n",
        )
        return {"apps/demo/envs/dev/wordpress-db-secret.enc.yaml": captured["encrypted"]}

    monkeypatch.setattr(
        "app.api.endpoints.scaffold.generate_gitops_new_files",
        fake_generate,
    )
    monkeypatch.setattr(
        "app.api.endpoints.scaffold.encrypt_secret_manifest",
        lambda plain_manifest, *, target_file_path, sops_config_contents: (
            captured.update(
                {
                    "plain_manifest": plain_manifest,
                    "target_file_path": target_file_path,
                    "sops_config_contents": sops_config_contents,
                }
            )
            or f"encrypted::{target_file_path}"
        ),
    )
    monkeypatch.setattr(
        "app.api.endpoints.scaffold.update_kustomization_resources",
        lambda _raw, new_resource: f"resources:\n- {new_resource}\n",
    )
    monkeypatch.setattr(
        "app.api.endpoints.scaffold.build_catalog_entry_addition",
        lambda _raw, inp: f"services:\n- service_id: {inp.name}\n",
    )
    monkeypatch.setattr(
        "app.api.endpoints.scaffold.build_appproject_addition",
        lambda _raw, inp: f"metadata:\n  name: {inp.name}\n",
    )

    new_files, _ = generate_scaffold_files_and_updates(
        ScaffoldServiceRequest(
            name="Demo",
            description="Demo service",
            imageRepo="wordpress:latest",
            repoUrl="https://github.com/example/demo",
            ownerEmail="owner@example.com",
            template="wordpress",
        ),
        "wlodzimierrr/homelab-workloads",
        "main",
        _FakeGitProvider(),
    )

    assert new_files == {
        "apps/demo/envs/dev/wordpress-db-secret.enc.yaml": (
            "encrypted::apps/demo/envs/dev/wordpress-db-secret.enc.yaml"
        )
    }
    assert captured["target_file_path"] == "apps/demo/envs/dev/wordpress-db-secret.enc.yaml"
    assert captured["sops_config_contents"] == "creation_rules:\n  - path_regex: .*\\.enc\\.yaml$\n"
    assert "WORDPRESS_DB_PASSWORD: plaintext" in str(captured["plain_manifest"])


def test_scaffold_submit_opens_pr_and_returns_commit_summary(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _FakeGitProvider:
        def read_file(self, _repo, _branch, file_path):
            return {
                "environments/dev/workloads/kustomization.yaml": "resources: []\n",
                "bootstrap/project-homelab.yaml": "spec: {}\n",
                "services.yaml": "services: []\n",
                "apps/homelab-api/base/catalog-sync-cronjob.yaml": _CATALOG_SYNC_CRONJOB,
            }[file_path]

        def create_branch(self, repo, from_branch, new_branch):
            captured["create_branch"] = (repo, from_branch, new_branch)
            return {"branch": new_branch}

        def commit_to_branch(self, repo, branch, files_dict, message):
            captured["commit"] = {
                "repo": repo,
                "branch": branch,
                "files": dict(files_dict),
                "message": message,
            }
            return {"branch": branch}

        def open_pr(self, repo, from_branch, to_branch, title, description):
            captured["pr"] = {
                "repo": repo,
                "from_branch": from_branch,
                "to_branch": to_branch,
                "title": title,
                "description": description,
            }
            return {
                "number": 17,
                "url": "https://github.com/example/homelab-workloads/pull/17",
            }

    monkeypatch.setattr(
        "app.api.endpoints.scaffold.generate_gitops_new_files",
        lambda inp, **_: {f"apps/{inp.name}/{inp.name}-app.yaml": "kind: Application\n"},
    )
    monkeypatch.setattr(
        "app.api.endpoints.scaffold.update_kustomization_resources",
        lambda _raw, new_resource: f"resources:\n- {new_resource}\n",
    )
    monkeypatch.setattr(
        "app.api.endpoints.scaffold.build_catalog_entry_addition",
        lambda _raw, inp: f"services:\n- service_id: {inp.name}\n",
    )
    monkeypatch.setattr(
        "app.api.endpoints.scaffold.build_appproject_addition",
        lambda _raw, inp: f"metadata:\n  name: {inp.name}\n",
    )

    service = ScaffoldAdminService(
        ScaffoldAdminServiceDeps(
            workloads_repo_slug=lambda: "wlodzimierrr/homelab-workloads",
            workloads_base_branch=lambda: "main",
            build_config_edit_branch_name=None,
            build_config_edit_pr_body=None,
            build_secret_edit_branch_name=None,
            build_secret_edit_pr_body=None,
            generate_scaffold_files_and_updates=generate_scaffold_files_and_updates,
            read_current_public_host_from_services_yaml=None,
            read_current_host_from_patch_ingress=None,
            update_services_yaml_public_host=None,
            update_patch_ingress_host=None,
            workloads_catalog_path="services.yaml",
            workloads_catalog_sync_cronjob_path="apps/homelab-api/base/catalog-sync-cronjob.yaml",
            update_service_registry_sync_namespaces=update_service_registry_sync_namespaces,
            verify_service_onboarding_targets=lambda targets: _sample_verification(
                targets[0].service_id,
                targets[0].namespace,
                targets[0].argo_application,
            ),
            build_default_git_provider=lambda: _FakeGitProvider(),
        ),
    )

    response = service.scaffold_submit(
        payload=ScaffoldServiceRequest(
            name="Demo",
            description="Demo service",
            imageRepo="ghcr.io/example/demo",
            repoUrl="https://github.com/example/demo",
            ownerEmail="owner@example.com",
        ),
    )

    assert response.pr_url == "https://github.com/example/homelab-workloads/pull/17"
    assert response.pr_number == 17
    assert response.branch_name.startswith("scaffold/demo-")
    assert response.deployment_verification[0].service_id == "demo"
    assert response.deployment_verification[0].namespace == "demo"
    assert response.deployment_verification[0].argo_application == "demo-dev"
    assert set(response.files_committed) == {
        "apps/demo/demo-app.yaml",
        "apps/homelab-api/base/catalog-sync-cronjob.yaml",
        "bootstrap/project-homelab.yaml",
        "environments/dev/workloads/kustomization.yaml",
        "services.yaml",
    }
    assert captured["create_branch"] == (
        "wlodzimierrr/homelab-workloads",
        "main",
        response.branch_name,
    )
    assert captured["pr"] == {
        "repo": "wlodzimierrr/homelab-workloads",
        "from_branch": response.branch_name,
        "to_branch": "main",
        "title": "feat(scaffold): add demo service",
        "description": captured["pr"]["description"],
    }
    assert (
        "Generated by the homelab portal scaffold wizard."
        in captured["pr"]["description"]
    )
    assert captured["commit"] == {
        "repo": "wlodzimierrr/homelab-workloads",
        "branch": response.branch_name,
        "files": {
            "apps/demo/demo-app.yaml": "kind: Application\n",
            "apps/homelab-api/base/catalog-sync-cronjob.yaml": update_service_registry_sync_namespaces(
                _CATALOG_SYNC_CRONJOB,
                "demo",
            ),
            "bootstrap/project-homelab.yaml": "metadata:\n  name: demo\n",
            "environments/dev/workloads/kustomization.yaml": "resources:\n- demo-app.yaml\n",
            "services.yaml": "services:\n- service_id: demo\n",
        },
        "message": "feat(scaffold): add demo service manifests and catalog entry",
    }
    assert inspect_service_registry_sync_namespace_coverage(
        captured["commit"]["files"]["apps/homelab-api/base/catalog-sync-cronjob.yaml"],
        "demo",
    )["covered"] is True


def test_adopt_service_updates_sync_namespace_allowlist() -> None:
    captured: dict[str, object] = {}
    services_yaml = """
services:
  - service_id: demo
    name: Demo
    envs:
      - name: dev
        namespace: demo-space
""".lstrip()

    class _FakeGitProvider:
        def read_file(self, _repo, _branch, file_path):
            return {
                "services.yaml": services_yaml,
                "apps/homelab-api/base/catalog-sync-cronjob.yaml": _CATALOG_SYNC_CRONJOB,
            }[file_path]

        def create_branch(self, repo, from_branch, new_branch):
            captured["create_branch"] = (repo, from_branch, new_branch)
            return {"branch": new_branch}

        def commit_to_branch(self, repo, branch, files_dict, message):
            captured["commit"] = {
                "repo": repo,
                "branch": branch,
                "files": dict(files_dict),
                "message": message,
            }
            return {"branch": branch}

        def open_pr(self, repo, from_branch, to_branch, title, description):
            captured["pr"] = {
                "repo": repo,
                "from_branch": from_branch,
                "to_branch": to_branch,
                "title": title,
                "description": description,
            }
            return {
                "number": 21,
                "url": "https://github.com/example/homelab-workloads/pull/21",
            }

    service = ScaffoldAdminService(
        ScaffoldAdminServiceDeps(
            workloads_repo_slug=lambda: "wlodzimierrr/homelab-workloads",
            workloads_base_branch=lambda: "main",
            build_config_edit_branch_name=None,
            build_config_edit_pr_body=None,
            build_secret_edit_branch_name=None,
            build_secret_edit_pr_body=None,
            generate_scaffold_files_and_updates=None,
            read_current_public_host_from_services_yaml=None,
            read_current_host_from_patch_ingress=None,
            update_services_yaml_public_host=None,
            update_patch_ingress_host=None,
            workloads_catalog_path="services.yaml",
            workloads_catalog_sync_cronjob_path="apps/homelab-api/base/catalog-sync-cronjob.yaml",
            update_service_registry_sync_namespaces=update_service_registry_sync_namespaces,
            verify_service_onboarding_targets=lambda targets: _sample_verification(
                targets[0].service_id,
                targets[0].namespace,
                targets[0].argo_application,
            ),
            build_default_git_provider=lambda: _FakeGitProvider(),
        ),
    )

    response = service.adopt_service(
        service_id="demo",
        payload=AdoptServiceRequest(projectId="demo-project"),
    )

    assert response.status == "accepted"
    assert response.project_id == "demo-project"
    assert response.pr_number == 21
    assert response.deployment_verification[0].service_id == "demo"
    assert response.deployment_verification[0].namespace == "demo-space"
    assert response.deployment_verification[0].argo_application == "demo-dev"
    assert "project_id: demo-project" in captured["commit"]["files"]["services.yaml"]
    assert "value: homelab-api,homelab-web,demo-space" in captured["commit"]["files"][
        "apps/homelab-api/base/catalog-sync-cronjob.yaml"
    ]
    assert "ensures the service namespace is included" in captured["pr"]["description"]
    assert inspect_service_registry_sync_namespace_coverage(
        captured["commit"]["files"]["apps/homelab-api/base/catalog-sync-cronjob.yaml"],
        "demo-space",
    )["covered"] is True


def test_decommission_service_creates_pr_for_self_owned_service() -> None:
    captured: dict[str, object] = {}
    services_yaml = """
services:
  - service_id: demo
    name: Demo
    envs:
      - name: dev
        namespace: demo
        argo_app: demo-dev
      - name: prod
        namespace: demo
        argo_app: demo-prod
""".lstrip()
    project_homelab = """
apiVersion: argoproj.io/v1alpha1
kind: AppProject
metadata:
  name: demo
  namespace: argocd
spec:
  description: demo resources
---
apiVersion: argoproj.io/v1alpha1
kind: AppProject
metadata:
  name: keep-me
  namespace: argocd
spec:
  description: keep me
""".lstrip()
    dev_kustomization = "resources:\n- homelab-api-app.yaml\n- demo-app.yaml\n"
    prod_kustomization = "resources:\n- demo-app.yaml\n"

    class _FakeGitProvider:
        def read_file(self, _repo, _branch, file_path):
            return {
                "services.yaml": services_yaml,
                "bootstrap/project-homelab.yaml": project_homelab,
                "environments/dev/workloads/kustomization.yaml": dev_kustomization,
                "environments/prod/workloads/kustomization.yaml": prod_kustomization,
            }[file_path]

        def list_files(self, _repo, _branch, prefix=""):
            files = [
                "services.yaml",
                "bootstrap/project-homelab.yaml",
                "environments/dev/workloads/kustomization.yaml",
                "environments/prod/workloads/kustomization.yaml",
                "environments/dev/workloads/demo-app.yaml",
                "environments/prod/workloads/demo-app.yaml",
                "apps/demo/base/kustomization.yaml",
                "apps/demo/base/deployment.yaml",
                "apps/demo/base/service.yaml",
            ]
            normalized = prefix.strip().lstrip("/")
            if not normalized:
                return files
            return [path for path in files if path == normalized or path.startswith(f"{normalized}/")]

        def create_branch(self, repo, from_branch, new_branch):
            captured["create_branch"] = (repo, from_branch, new_branch)
            return {"branch": new_branch}

        def commit_to_branch(self, repo, branch, files_dict, message):
            captured["commit"] = {
                "repo": repo,
                "branch": branch,
                "files": dict(files_dict),
                "message": message,
            }
            return {"branch": branch}

        def open_pr(self, repo, from_branch, to_branch, title, description):
            captured["pr"] = {
                "repo": repo,
                "from_branch": from_branch,
                "to_branch": to_branch,
                "title": title,
                "description": description,
            }
            return {
                "number": 42,
                "url": "https://github.com/example/homelab-workloads/pull/42",
            }

    service = ScaffoldAdminService(
        ScaffoldAdminServiceDeps(
            workloads_repo_slug=lambda: "wlodzimierrr/homelab-workloads",
            workloads_base_branch=lambda: "main",
            build_config_edit_branch_name=None,
            build_config_edit_pr_body=None,
            build_secret_edit_branch_name=None,
            build_secret_edit_pr_body=None,
            generate_scaffold_files_and_updates=None,
            read_current_public_host_from_services_yaml=None,
            read_current_host_from_patch_ingress=None,
            update_services_yaml_public_host=None,
            update_patch_ingress_host=None,
            workloads_catalog_path="services.yaml",
            workloads_catalog_sync_cronjob_path="apps/homelab-api/base/catalog-sync-cronjob.yaml",
            update_service_registry_sync_namespaces=update_service_registry_sync_namespaces,
            verify_service_onboarding_targets=lambda targets: _sample_verification(
                targets[0].service_id,
                targets[0].namespace,
                targets[0].argo_application,
            ),
            build_default_git_provider=lambda: _FakeGitProvider(),
        ),
    )

    response = service.decommission_service(service_id="demo", admin_user="alice")

    assert response.status == "accepted"
    assert response.service_id == "demo"
    assert response.requested_by == "alice"
    assert response.pr_number == 42
    assert response.project_id is None
    assert response.preserved_artifacts == ["source-repository", "ghcr-package"]
    assert "Argo can prune the service resources" in response.message
    assert captured["pr"]["title"] == "chore(decommission): remove demo from workloads"
    assert "Not removed in v1" in captured["pr"]["description"]
    assert captured["commit"]["message"] == "chore(decommission): remove demo from workloads"
    assert "service_id: demo" not in captured["commit"]["files"]["services.yaml"]
    assert "demo-app.yaml" not in captured["commit"]["files"]["environments/dev/workloads/kustomization.yaml"]
    assert "demo-app.yaml" not in captured["commit"]["files"]["environments/prod/workloads/kustomization.yaml"]
    assert "name: demo" not in captured["commit"]["files"]["bootstrap/project-homelab.yaml"]
    assert captured["commit"]["files"]["environments/dev/workloads/demo-app.yaml"] is None
    assert captured["commit"]["files"]["environments/prod/workloads/demo-app.yaml"] is None
    assert captured["commit"]["files"]["apps/demo/base/kustomization.yaml"] is None
    assert captured["commit"]["files"]["apps/demo/base/deployment.yaml"] is None
    assert captured["commit"]["files"]["apps/demo/base/service.yaml"] is None
    assert "bootstrap/project-homelab.yaml" in response.updated_paths
    assert "services.yaml" in response.updated_paths
    assert "apps/demo/base/deployment.yaml" in response.removed_paths
    assert "environments/dev/workloads/demo-app.yaml" in response.removed_paths


def test_decommission_service_rejects_project_linked_service() -> None:
    services_yaml = """
services:
  - service_id: oauth2-proxy
    project_id: homelab-web
    name: OAuth2 Proxy
    envs:
      - name: dev
        namespace: homelab-web
        argo_app: homelab-web-dev
""".lstrip()

    class _FakeGitProvider:
        def read_file(self, _repo, _branch, file_path):
            assert file_path == "services.yaml"
            return services_yaml

        def list_files(self, _repo, _branch, prefix=""):
            return ["services.yaml"]

    service = ScaffoldAdminService(
        ScaffoldAdminServiceDeps(
            workloads_repo_slug=lambda: "wlodzimierrr/homelab-workloads",
            workloads_base_branch=lambda: "main",
            build_config_edit_branch_name=None,
            build_config_edit_pr_body=None,
            build_secret_edit_branch_name=None,
            build_secret_edit_pr_body=None,
            generate_scaffold_files_and_updates=None,
            read_current_public_host_from_services_yaml=None,
            read_current_host_from_patch_ingress=None,
            update_services_yaml_public_host=None,
            update_patch_ingress_host=None,
            workloads_catalog_path="services.yaml",
            workloads_catalog_sync_cronjob_path="apps/homelab-api/base/catalog-sync-cronjob.yaml",
            update_service_registry_sync_namespaces=update_service_registry_sync_namespaces,
            verify_service_onboarding_targets=lambda targets: _sample_verification(
                targets[0].service_id,
                targets[0].namespace,
                targets[0].argo_application,
            ),
            build_default_git_provider=lambda: _FakeGitProvider(),
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        service.decommission_service(service_id="oauth2-proxy", admin_user="alice")

    assert exc_info.value.status_code == 409
    assert "shared project components" in str(exc_info.value.detail)


def test_decommission_service_removes_only_project_component_owned_resources() -> None:
    captured: dict[str, object] = {}
    services_yaml = """
services:
  - service_id: portal-project-worker
    project_id: portal-project
    name: Portal Project Worker
    envs:
      - name: dev
        namespace: portal-project
        argo_app: portal-project-dev
        workload_ref: apps/portal-project/base/worker-deployment.yaml
      - name: prod
        namespace: portal-project
        argo_app: portal-project-prod
        workload_ref: apps/portal-project/base/worker-deployment.yaml
  - service_id: portal-project-frontend
    project_id: portal-project
    name: Portal Project Frontend
    envs:
      - name: dev
        namespace: portal-project
        argo_app: portal-project-dev
""".lstrip()
    base_kustomization = """
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - namespace.yaml
  - frontend-deployment.yaml
  - frontend-service.yaml
  - serviceaccount-worker.yaml
  - worker-deployment.yaml
  - worker-service.yaml
  - servicemonitor-worker.yaml
""".lstrip()
    dev_kustomization = """
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../../base
patches:
  - path: patch-frontend-deployment.yaml
  - path: patch-worker-deployment.yaml
""".lstrip()
    prod_kustomization = dev_kustomization
    project_homelab = """
apiVersion: argoproj.io/v1alpha1
kind: AppProject
metadata:
  name: portal-project
  namespace: argocd
spec: {}
""".lstrip()

    class _FakeGitProvider:
        def read_file(self, _repo, _branch, file_path):
            return {
                "services.yaml": services_yaml,
                "apps/portal-project/base/kustomization.yaml": base_kustomization,
                "apps/portal-project/envs/dev/kustomization.yaml": dev_kustomization,
                "apps/portal-project/envs/prod/kustomization.yaml": prod_kustomization,
                "bootstrap/project-homelab.yaml": project_homelab,
            }[file_path]

        def list_files(self, _repo, _branch, prefix=""):
            files = [
                "services.yaml",
                "bootstrap/project-homelab.yaml",
                "environments/dev/workloads/portal-project-app.yaml",
                "apps/portal-project/base/kustomization.yaml",
                "apps/portal-project/base/frontend-deployment.yaml",
                "apps/portal-project/base/frontend-service.yaml",
                "apps/portal-project/base/serviceaccount-worker.yaml",
                "apps/portal-project/base/worker-deployment.yaml",
                "apps/portal-project/base/worker-service.yaml",
                "apps/portal-project/base/servicemonitor-worker.yaml",
                "apps/portal-project/envs/dev/kustomization.yaml",
                "apps/portal-project/envs/dev/patch-frontend-deployment.yaml",
                "apps/portal-project/envs/dev/patch-worker-deployment.yaml",
                "apps/portal-project/envs/prod/kustomization.yaml",
                "apps/portal-project/envs/prod/patch-worker-deployment.yaml",
            ]
            normalized = prefix.strip().lstrip("/")
            if not normalized:
                return files
            return [path for path in files if path == normalized or path.startswith(f"{normalized}/")]

        def create_branch(self, repo, from_branch, new_branch):
            captured["create_branch"] = (repo, from_branch, new_branch)
            return {"branch": new_branch}

        def commit_to_branch(self, repo, branch, files_dict, message):
            captured["commit"] = {
                "repo": repo,
                "branch": branch,
                "files": dict(files_dict),
                "message": message,
            }
            return {"branch": branch}

        def open_pr(self, repo, from_branch, to_branch, title, description):
            captured["pr"] = {
                "repo": repo,
                "from_branch": from_branch,
                "to_branch": to_branch,
                "title": title,
                "description": description,
            }
            return {
                "number": 77,
                "url": "https://github.com/example/homelab-workloads/pull/77",
            }

    service = ScaffoldAdminService(
        ScaffoldAdminServiceDeps(
            workloads_repo_slug=lambda: "wlodzimierrr/homelab-workloads",
            workloads_base_branch=lambda: "main",
            build_config_edit_branch_name=None,
            build_config_edit_pr_body=None,
            build_secret_edit_branch_name=None,
            build_secret_edit_pr_body=None,
            generate_scaffold_files_and_updates=None,
            read_current_public_host_from_services_yaml=None,
            read_current_host_from_patch_ingress=None,
            update_services_yaml_public_host=None,
            update_patch_ingress_host=None,
            workloads_catalog_path="services.yaml",
            workloads_catalog_sync_cronjob_path="apps/homelab-api/base/catalog-sync-cronjob.yaml",
            update_service_registry_sync_namespaces=update_service_registry_sync_namespaces,
            verify_service_onboarding_targets=lambda targets: _sample_verification(
                targets[0].service_id,
                targets[0].namespace,
                targets[0].argo_application,
            ),
            build_default_git_provider=lambda: _FakeGitProvider(),
        ),
    )

    response = service.decommission_service(service_id="portal-project-worker", admin_user="alice")

    assert response.status == "accepted"
    assert response.service_id == "portal-project-worker"
    assert response.project_id == "portal-project"
    assert response.pr_number == 77
    assert "only this service will be removed from the shared project" in response.message
    assert captured["pr"]["title"] == "chore(decommission): remove portal-project-worker from project portal-project"
    assert "shared Argo Application manifests" in captured["pr"]["description"]
    assert "service_id: portal-project-worker" not in captured["commit"]["files"]["services.yaml"]
    assert "worker-deployment.yaml" not in captured["commit"]["files"]["apps/portal-project/base/kustomization.yaml"]
    assert "serviceaccount-worker.yaml" not in captured["commit"]["files"]["apps/portal-project/base/kustomization.yaml"]
    assert "patch-worker-deployment.yaml" not in captured["commit"]["files"]["apps/portal-project/envs/dev/kustomization.yaml"]
    assert captured["commit"]["files"]["apps/portal-project/base/worker-deployment.yaml"] is None
    assert captured["commit"]["files"]["apps/portal-project/base/worker-service.yaml"] is None
    assert captured["commit"]["files"]["apps/portal-project/base/serviceaccount-worker.yaml"] is None
    assert captured["commit"]["files"]["apps/portal-project/base/servicemonitor-worker.yaml"] is None
    assert captured["commit"]["files"]["apps/portal-project/envs/dev/patch-worker-deployment.yaml"] is None
    assert captured["commit"]["files"]["apps/portal-project/envs/prod/patch-worker-deployment.yaml"] is None
    assert "environments/dev/workloads/portal-project-app.yaml" not in captured["commit"]["files"]
    assert "bootstrap/project-homelab.yaml" not in captured["commit"]["files"]
    assert "apps/portal-project/base/frontend-deployment.yaml" not in captured["commit"]["files"]


def test_decommission_service_rejects_bundle_core_component() -> None:
    services_yaml = """
services:
  - service_id: portal-project-frontend
    project_id: portal-project
    name: Portal Project Frontend
    envs:
      - name: dev
        namespace: portal-project
        argo_app: portal-project-dev
        workload_ref: apps/portal-project/base/frontend-deployment.yaml
""".lstrip()

    class _FakeGitProvider:
        def read_file(self, _repo, _branch, file_path):
            assert file_path == "services.yaml"
            return services_yaml

        def list_files(self, _repo, _branch, prefix=""):
            return ["services.yaml"]

    service = ScaffoldAdminService(
        ScaffoldAdminServiceDeps(
            workloads_repo_slug=lambda: "wlodzimierrr/homelab-workloads",
            workloads_base_branch=lambda: "main",
            build_config_edit_branch_name=None,
            build_config_edit_pr_body=None,
            build_secret_edit_branch_name=None,
            build_secret_edit_pr_body=None,
            generate_scaffold_files_and_updates=None,
            read_current_public_host_from_services_yaml=None,
            read_current_host_from_patch_ingress=None,
            update_services_yaml_public_host=None,
            update_patch_ingress_host=None,
            workloads_catalog_path="services.yaml",
            workloads_catalog_sync_cronjob_path="apps/homelab-api/base/catalog-sync-cronjob.yaml",
            update_service_registry_sync_namespaces=update_service_registry_sync_namespaces,
            verify_service_onboarding_targets=lambda targets: _sample_verification(
                targets[0].service_id,
                targets[0].namespace,
                targets[0].argo_application,
            ),
            build_default_git_provider=lambda: _FakeGitProvider(),
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        service.decommission_service(service_id="portal-project-frontend", admin_user="alice")

    assert exc_info.value.status_code == 409
    assert "bundle core component" in str(exc_info.value.detail).lower()


def test_remove_service_from_catalog_preserves_append_compatible_services_yaml_format() -> None:
    services_yaml = """
services:
  - service_id: scaffold-smoke
    name: scaffold-smoke
    owner: wlodzimierrr
    repo_url: https://github.com/wlodzimierrr/scaffold-smoke
    runbook_url: https://github.com/wlodzimierrr/scaffold-smoke
    description: smoke test
    observability:
      mode: ingress-derived
    envs:
      - name: dev
        namespace: scaffold-smoke
        argo_app: scaffold-smoke-dev
  - service_id: portfolio-next
    name: Portfolio Next
    owner: wlodzimierrr
    owner_email: szerszywlodzimierz@gmail.com
    repo_url: https://github.com/wlodzimierrr/portfolio-next
    runbook_url: https://github.com/wlodzimierrr/portfolio-next
    description: portfolio website
    observability:
      mode: ingress-derived
    envs:
      - name: dev
        namespace: portfolio-next
        argo_app: portfolio-next-dev
      - name: prod
        namespace: portfolio-next
        argo_app: portfolio-next-prod
        public_host: portfolio-next.homelab.local
""".lstrip()

    removed = ScaffoldAdminService._remove_service_from_catalog(services_yaml, "scaffold-smoke")
    assert "services:\n  - service_id: portfolio-next\n" in removed

    appended = build_catalog_entry_addition(
        removed,
        ScaffoldServiceInput(
            name="scaffold-gen-test",
            description="Scaffold smoke validation service",
            image_repo="ghcr.io/example/scaffold-gen-test",
            repo_url="https://github.com/example/scaffold-gen-test",
            owner_email="ops@example.com",
            owner="",
            template="python-fastapi",
            namespace="scaffold-gen-test",
            dev_host="scaffold-gen-test.dev.homelab.local",
            prod_host="",
            public_host="scaffold-gen-test.example.com",
            workloads_repo_url="https://github.com/example/workloads.git",
        ),
    )

    parsed = yaml.safe_load(appended)
    services = parsed["services"]
    assert [service["service_id"] for service in services] == [
        "portfolio-next",
        "scaffold-gen-test",
    ]

    portfolio_next = services[0]
    assert [env["name"] for env in portfolio_next["envs"]] == ["dev", "prod"]

    scaffold_gen_test = services[1]
    assert [env["name"] for env in scaffold_gen_test["envs"]] == ["dev", "prod"]
