from app.api.endpoints.scaffold import (
    generate_scaffold_files_and_updates,
    inspect_service_registry_sync_namespace_coverage,
    parse_service_registry_sync_namespaces,
    update_service_registry_sync_namespaces,
)
from app.api.schemas.migration import AdoptServiceRequest
from app.api.schemas.scaffold import ScaffoldServiceRequest
from app.services.scaffold_admin_service import ScaffoldAdminService, ScaffoldAdminServiceDeps


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
        lambda inp: {f"apps/{inp.name}/{inp.name}-app.yaml": "kind: Application\n"},
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
    assert reads == [
        (
            "wlodzimierrr/homelab-workloads",
            "main",
            "environments/dev/workloads/kustomization.yaml",
        ),
        (
            "wlodzimierrr/homelab-workloads",
            "main",
            "bootstrap/project-homelab.yaml",
        ),
        (
            "wlodzimierrr/homelab-workloads",
            "main",
            "services.yaml",
        ),
        (
            "wlodzimierrr/homelab-workloads",
            "main",
            "apps/homelab-api/base/catalog-sync-cronjob.yaml",
        ),
    ]


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
        lambda inp: {f"apps/{inp.name}/{inp.name}-app.yaml": "kind: Application\n"},
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
    assert "project_id: demo-project" in captured["commit"]["files"]["services.yaml"]
    assert "value: homelab-api,homelab-web,demo-space" in captured["commit"]["files"][
        "apps/homelab-api/base/catalog-sync-cronjob.yaml"
    ]
    assert "ensures the service namespace is included" in captured["pr"]["description"]
    assert inspect_service_registry_sync_namespace_coverage(
        captured["commit"]["files"]["apps/homelab-api/base/catalog-sync-cronjob.yaml"],
        "demo-space",
    )["covered"] is True
