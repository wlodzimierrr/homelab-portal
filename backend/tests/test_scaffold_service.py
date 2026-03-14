import pytest

from app.scaffold_service import (
    ScaffoldError,
    ScaffoldServiceInput,
    build_appproject_addition,
    build_catalog_entry_addition,
    generate_gitops_new_files,
    update_kustomization_resources,
    validate_service_name,
)


def _make_input(**overrides: object) -> ScaffoldServiceInput:
    defaults: dict[str, object] = {
        "name": "my-svc",
        "description": "A test service",
        "image_repo": "ghcr.io/example/my-svc",
        "repo_url": "https://github.com/example/my-svc",
        "owner_email": "ops@example.com",
        "owner": "",
        "template": "python-fastapi",
        "namespace": "my-svc",
        "dev_host": "my-svc.dev.homelab.local",
        "prod_host": "my-svc.homelab.local",
        "workloads_repo_url": "https://github.com/example/workloads.git",
    }
    defaults.update(overrides)
    return ScaffoldServiceInput(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# validate_service_name
# ---------------------------------------------------------------------------


def test_validate_service_name_accepts_valid_names() -> None:
    for name in ("my-app", "a1", "hello-world-123", "x" * 63):
        validate_service_name(name)  # should not raise


def test_validate_service_name_rejects_uppercase() -> None:
    with pytest.raises(ScaffoldError):
        validate_service_name("MyApp")


def test_validate_service_name_rejects_leading_digit() -> None:
    with pytest.raises(ScaffoldError):
        validate_service_name("1app")


def test_validate_service_name_rejects_spaces() -> None:
    with pytest.raises(ScaffoldError):
        validate_service_name("my app")


def test_validate_service_name_rejects_too_long() -> None:
    with pytest.raises(ScaffoldError):
        validate_service_name("a" * 64)


# ---------------------------------------------------------------------------
# generate_gitops_new_files
# ---------------------------------------------------------------------------


def test_generate_gitops_new_files_python_fastapi_file_count() -> None:
    files = generate_gitops_new_files(_make_input())
    # 10 base files (inc. servicemonitor for app-native) + 2 dev overlay + 3 prod overlay + 2 argo app = 17
    assert len(files) == 17


def test_generate_gitops_new_files_static_nginx_no_servicemonitor() -> None:
    files = generate_gitops_new_files(_make_input(template="static-nginx"))
    assert not any("servicemonitor" in path for path in files)
    assert len(files) == 16


def test_generate_gitops_new_files_paths_contain_service_name() -> None:
    files = generate_gitops_new_files(_make_input(name="test-svc"))
    for path in files:
        assert "test-svc" in path or path.startswith("environments/")


def test_generate_gitops_new_files_dev_argo_app_manifest() -> None:
    files = generate_gitops_new_files(_make_input())
    dev_app = files["environments/dev/workloads/my-svc-app.yaml"]
    assert "my-svc-dev" in dev_app
    assert "apps/my-svc/envs/dev" in dev_app


def test_generate_gitops_new_files_prod_argo_app_has_comment() -> None:
    files = generate_gitops_new_files(_make_input())
    prod_app = files["environments/prod/workloads/my-svc-app.yaml"]
    assert "single-cluster safety mode" in prod_app


def test_generate_gitops_new_files_image_repo_in_base_deployment() -> None:
    files = generate_gitops_new_files(_make_input(image_repo="ghcr.io/x/svc"))
    deployment = files["apps/my-svc/base/deployment.yaml"]
    assert "ghcr.io/x/svc" in deployment


def test_generate_gitops_new_files_dev_host_in_base_ingress() -> None:
    files = generate_gitops_new_files(_make_input(dev_host="svc.dev.local"))
    ingress = files["apps/my-svc/base/ingress.yaml"]
    assert "svc.dev.local" in ingress


def test_generate_gitops_new_files_prod_host_in_prod_patch_ingress() -> None:
    files = generate_gitops_new_files(_make_input(prod_host="svc.prod.local"))
    patch = files["apps/my-svc/envs/prod/patch-ingress.yaml"]
    assert "svc.prod.local" in patch


def test_generate_gitops_new_files_workloads_repo_url_in_argo_app() -> None:
    files = generate_gitops_new_files(_make_input(workloads_repo_url="https://github.com/org/wl.git"))
    dev_app = files["environments/dev/workloads/my-svc-app.yaml"]
    assert "https://github.com/org/wl.git" in dev_app


# ---------------------------------------------------------------------------
# update_kustomization_resources
# ---------------------------------------------------------------------------

_KUSTOMIZATION = """\
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - homelab-api-app.yaml
  - homelab-web-app.yaml
"""


def test_update_kustomization_resources_appends_new_resource() -> None:
    result = update_kustomization_resources(_KUSTOMIZATION, "my-svc-app.yaml")
    assert "- my-svc-app.yaml" in result
    assert "- homelab-api-app.yaml" in result


def test_update_kustomization_resources_result_sorted() -> None:
    result = update_kustomization_resources(_KUSTOMIZATION, "aaa-app.yaml")
    lines = [line.strip() for line in result.splitlines() if line.strip().startswith("- ")]
    assert lines == sorted(lines)


def test_update_kustomization_resources_idempotent() -> None:
    result = update_kustomization_resources(_KUSTOMIZATION, "homelab-api-app.yaml")
    assert result == _KUSTOMIZATION


def test_update_kustomization_resources_raises_without_resources_block() -> None:
    bad = "apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\n"
    with pytest.raises(ScaffoldError):
        update_kustomization_resources(bad, "my-svc-app.yaml")


# ---------------------------------------------------------------------------
# build_catalog_entry_addition
# ---------------------------------------------------------------------------

_SERVICES_YAML = """\
services:
  - service_id: homelab-api
    name: 'Homelab API'
    owner: 'wlodzimierrr'
    owner_email: 'ops@example.com'
    repo_url: 'https://github.com/x/homelab'
    runbook_url: 'https://github.com/x/homelab'
    description: 'The API'
    observability:
      mode: app-native
    envs:
      - name: dev
        namespace: homelab-api
        argo_app: homelab-api-dev
"""


def test_build_catalog_entry_addition_appends_new_entry() -> None:
    inp = _make_input()
    result = build_catalog_entry_addition(_SERVICES_YAML, inp)
    assert "service_id: my-svc" in result
    assert "homelab-api" in result  # original preserved


def test_build_catalog_entry_addition_includes_namespace() -> None:
    inp = _make_input(namespace="my-ns")
    result = build_catalog_entry_addition(_SERVICES_YAML, inp)
    assert "namespace: my-ns" in result


def test_build_catalog_entry_addition_raises_on_duplicate() -> None:
    inp = _make_input(name="homelab-api")
    with pytest.raises(ScaffoldError) as exc_info:
        build_catalog_entry_addition(_SERVICES_YAML, inp)
    assert exc_info.value.status_code == 409


def test_build_catalog_entry_addition_raises_without_services_key() -> None:
    inp = _make_input()
    with pytest.raises(ScaffoldError) as exc_info:
        build_catalog_entry_addition("# empty\n", inp)
    assert exc_info.value.status_code == 502


def test_build_catalog_entry_addition_observability_mode_fastapi() -> None:
    inp = _make_input(template="python-fastapi")
    result = build_catalog_entry_addition(_SERVICES_YAML, inp)
    assert "mode: app-native" in result


def test_build_catalog_entry_addition_observability_mode_nginx() -> None:
    inp = _make_input(template="static-nginx")
    result = build_catalog_entry_addition(_SERVICES_YAML, inp)
    assert "mode: ingress-derived" in result


# ---------------------------------------------------------------------------
# build_appproject_addition
# ---------------------------------------------------------------------------

_APPPROJECT_YAML = """\
apiVersion: argoproj.io/v1alpha1
kind: AppProject
metadata:
  name: homelab-api
  namespace: argocd
spec:
  description: homelab-api resources
"""


def test_build_appproject_addition_appends_new_project() -> None:
    inp = _make_input()
    result = build_appproject_addition(_APPPROJECT_YAML, inp)
    assert "name: my-svc" in result
    assert "name: homelab-api" in result  # original preserved


def test_build_appproject_addition_includes_namespace_in_destinations() -> None:
    inp = _make_input(namespace="my-ns")
    result = build_appproject_addition(_APPPROJECT_YAML, inp)
    assert "namespace: my-ns" in result


def test_build_appproject_addition_raises_on_duplicate() -> None:
    inp = _make_input(name="homelab-api")
    with pytest.raises(ScaffoldError) as exc_info:
        build_appproject_addition(_APPPROJECT_YAML, inp)
    assert exc_info.value.status_code == 409


def test_build_appproject_addition_includes_workloads_repo_url() -> None:
    inp = _make_input(workloads_repo_url="https://github.com/org/wl.git")
    result = build_appproject_addition(_APPPROJECT_YAML, inp)
    assert "https://github.com/org/wl.git" in result


# ---------------------------------------------------------------------------
# generate_gitops_new_files — postgres template
# ---------------------------------------------------------------------------


def test_postgres_template_base_files_present() -> None:
    inp = _make_input(template="postgres")
    files = generate_gitops_new_files(inp)
    assert "apps/my-svc/base/statefulset.yaml" in files
    assert "apps/my-svc/base/service.yaml" in files
    assert "apps/my-svc/base/credentials-secret.yaml" in files
    assert "apps/my-svc/base/networkpolicy-allow-ingress.yaml" in files


def test_postgres_template_no_deployment_or_ingress() -> None:
    inp = _make_input(template="postgres")
    files = generate_gitops_new_files(inp)
    assert "apps/my-svc/base/deployment.yaml" not in files
    assert "apps/my-svc/base/ingress.yaml" not in files
    assert "apps/my-svc/base/servicemonitor.yaml" not in files


def test_postgres_template_statefulset_uses_pg17() -> None:
    inp = _make_input(template="postgres")
    files = generate_gitops_new_files(inp)
    assert "postgres:17-alpine" in files["apps/my-svc/base/statefulset.yaml"]


def test_postgres_template_kustomization_resources() -> None:
    inp = _make_input(template="postgres")
    files = generate_gitops_new_files(inp)
    kust = files["apps/my-svc/base/kustomization.yaml"]
    assert "statefulset.yaml" in kust
    assert "service.yaml" in kust
    assert "credentials-secret.yaml" in kust


def test_postgres_template_argocd_apps_generated() -> None:
    inp = _make_input(template="postgres")
    files = generate_gitops_new_files(inp)
    assert "environments/dev/workloads/my-svc-app.yaml" in files
    assert "environments/prod/workloads/my-svc-app.yaml" in files


# ---------------------------------------------------------------------------
# generate_gitops_new_files — mysql template
# ---------------------------------------------------------------------------


def test_mysql_template_base_files_present() -> None:
    inp = _make_input(template="mysql")
    files = generate_gitops_new_files(inp)
    assert "apps/my-svc/base/statefulset.yaml" in files
    assert "apps/my-svc/base/service.yaml" in files
    assert "apps/my-svc/base/credentials-secret.yaml" in files


def test_mysql_template_no_deployment_or_ingress() -> None:
    inp = _make_input(template="mysql")
    files = generate_gitops_new_files(inp)
    assert "apps/my-svc/base/deployment.yaml" not in files
    assert "apps/my-svc/base/ingress.yaml" not in files


def test_mysql_template_statefulset_uses_mysql8() -> None:
    inp = _make_input(template="mysql")
    files = generate_gitops_new_files(inp)
    assert "mysql:8.0" in files["apps/my-svc/base/statefulset.yaml"]


def test_mysql_template_credentials_secret_has_mysql_keys() -> None:
    inp = _make_input(template="mysql")
    files = generate_gitops_new_files(inp)
    secret = files["apps/my-svc/base/credentials-secret.yaml"]
    assert "MYSQL_ROOT_PASSWORD" in secret
    assert "MYSQL_USER" in secret
    assert "MYSQL_DATABASE" in secret


# ---------------------------------------------------------------------------
# build_catalog_entry_addition — database templates use no-http mode
# ---------------------------------------------------------------------------


def test_catalog_entry_postgres_uses_no_http_observability() -> None:
    inp = _make_input(template="postgres")
    result = build_catalog_entry_addition(_SERVICES_YAML, inp)
    assert "mode: no-http" in result


def test_catalog_entry_mysql_uses_no_http_observability() -> None:
    inp = _make_input(template="mysql")
    result = build_catalog_entry_addition(_SERVICES_YAML, inp)
    assert "mode: no-http" in result
