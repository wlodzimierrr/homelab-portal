import pytest

from app.scaffold.models import TEMPLATE_DEFAULT_OBSERVABILITY_MODE, TEMPLATES
from app.scaffold_service import (
    ScaffoldAddServiceInput,
    ScaffoldBundleInput,
    ScaffoldError,
    ScaffoldServiceInput,
    build_appproject_addition,
    build_catalog_add_service_entry,
    build_catalog_bundle_entries,
    build_catalog_entry_addition,
    generate_gitops_add_service_files,
    generate_gitops_bundle_files,
    generate_gitops_new_files,
    update_kustomization_resources,
    update_overlay_kustomization_patches,
    validate_add_service,
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
        "prod_host": "",
        "public_host": "my-svc.homelab.local",
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


def test_template_observability_contract_matrix_is_explicit() -> None:
    assert TEMPLATE_DEFAULT_OBSERVABILITY_MODE == {
        "python-fastapi": "ingress-derived",
        "python-django": "ingress-derived",
        "python-flask": "ingress-derived",
        "static-nginx": "ingress-derived",
        "react": "ingress-derived",
        "vue": "ingress-derived",
        "wordpress": "ingress-derived",
        "nextjs": "ingress-derived",
        "node-express": "app-native",
        "node-nestjs": "ingress-derived",
        "postgres": "no-http",
        "mysql": "no-http",
    }

    assert set(TEMPLATE_DEFAULT_OBSERVABILITY_MODE) == set(TEMPLATES)
    for template_id, metadata in TEMPLATES.items():
        assert metadata["default_observability_mode"] == TEMPLATE_DEFAULT_OBSERVABILITY_MODE[template_id]


# ---------------------------------------------------------------------------
# generate_gitops_new_files
# ---------------------------------------------------------------------------


def test_generate_gitops_new_files_python_fastapi_file_count() -> None:
    files = generate_gitops_new_files(_make_input())
    assert len(files) == 16


def test_generate_gitops_new_files_python_fastapi_has_no_servicemonitor() -> None:
    files = generate_gitops_new_files(_make_input())
    assert not any("servicemonitor" in path for path in files)


def test_generate_gitops_new_files_static_nginx_no_servicemonitor() -> None:
    files = generate_gitops_new_files(_make_input(template="static-nginx"))
    assert not any("servicemonitor" in path for path in files)
    assert len(files) == 16


def test_vue_template_file_count_matches_static_nginx() -> None:
    static_files = generate_gitops_new_files(_make_input(template="static-nginx"))
    vue_files = generate_gitops_new_files(_make_input(template="vue"))
    assert len(vue_files) == len(static_files)


def test_vue_template_has_no_servicemonitor() -> None:
    files = generate_gitops_new_files(_make_input(template="vue"))
    assert not any("servicemonitor" in path for path in files)


def test_vue_template_uses_root_health_path() -> None:
    files = generate_gitops_new_files(_make_input(template="vue"))
    deployment = files["apps/my-svc/base/deployment.yaml"]
    assert "path: /" in deployment


def test_vue_template_container_port_80() -> None:
    files = generate_gitops_new_files(_make_input(template="vue"))
    deployment = files["apps/my-svc/base/deployment.yaml"]
    assert "containerPort: 80" in deployment


def test_vue_template_container_name_is_web() -> None:
    files = generate_gitops_new_files(_make_input(template="vue"))
    deployment = files["apps/my-svc/base/deployment.yaml"]
    assert "name: web" in deployment


def test_catalog_entry_vue_uses_ingress_derived_observability() -> None:
    inp = _make_input(template="vue")
    result = build_catalog_entry_addition(_SERVICES_YAML, inp)
    assert "mode: ingress-derived" in result


def test_catalog_entry_vue_has_dev_and_prod_envs() -> None:
    inp = _make_input(template="vue")
    new_entry = build_catalog_entry_addition(_SERVICES_YAML, inp)[len(_SERVICES_YAML):]
    assert "name: dev" in new_entry
    assert "name: prod" in new_entry


def test_react_template_file_count_matches_static_nginx() -> None:
    static_files = generate_gitops_new_files(_make_input(template="static-nginx"))
    react_files = generate_gitops_new_files(_make_input(template="react"))
    assert len(react_files) == len(static_files)


def test_react_template_has_no_servicemonitor() -> None:
    files = generate_gitops_new_files(_make_input(template="react"))
    assert not any("servicemonitor" in path for path in files)


def test_react_template_health_path() -> None:
    files = generate_gitops_new_files(_make_input(template="react"))
    deployment = files["apps/my-svc/base/deployment.yaml"]
    assert "path: /health" in deployment


def test_react_template_container_port_80() -> None:
    files = generate_gitops_new_files(_make_input(template="react"))
    deployment = files["apps/my-svc/base/deployment.yaml"]
    assert "containerPort: 80" in deployment


def test_react_template_container_name_is_web() -> None:
    files = generate_gitops_new_files(_make_input(template="react"))
    deployment = files["apps/my-svc/base/deployment.yaml"]
    assert "name: web" in deployment


def test_catalog_entry_react_uses_ingress_derived_observability() -> None:
    inp = _make_input(template="react")
    result = build_catalog_entry_addition(_SERVICES_YAML, inp)
    assert "mode: ingress-derived" in result


def test_catalog_entry_react_has_dev_and_prod_envs() -> None:
    inp = _make_input(template="react")
    new_entry = build_catalog_entry_addition(_SERVICES_YAML, inp)[len(_SERVICES_YAML):]
    assert "name: dev" in new_entry
    assert "name: prod" in new_entry


def test_react_template_network_policy_port_80() -> None:
    files = generate_gitops_new_files(_make_input(template="react"))
    netpol = files["apps/my-svc/base/networkpolicy-allow-ingress.yaml"]
    assert "port: 80" in netpol


def test_nextjs_template_file_count() -> None:
    files = generate_gitops_new_files(_make_input(template="nextjs"))
    assert len(files) == 16


def test_nextjs_template_has_no_servicemonitor() -> None:
    files = generate_gitops_new_files(_make_input(template="nextjs"))
    assert not any("servicemonitor" in path for path in files)


def test_nextjs_template_health_path() -> None:
    files = generate_gitops_new_files(_make_input(template="nextjs"))
    deployment = files["apps/my-svc/base/deployment.yaml"]
    assert "path: /" in deployment


def test_nextjs_template_container_port_3000() -> None:
    files = generate_gitops_new_files(_make_input(template="nextjs"))
    deployment = files["apps/my-svc/base/deployment.yaml"]
    assert "containerPort: 3000" in deployment


def test_nextjs_template_container_name_is_web() -> None:
    files = generate_gitops_new_files(_make_input(template="nextjs"))
    deployment = files["apps/my-svc/base/deployment.yaml"]
    assert "name: web" in deployment


def test_nextjs_template_network_policy_port_3000() -> None:
    files = generate_gitops_new_files(_make_input(template="nextjs"))
    netpol = files["apps/my-svc/base/networkpolicy-allow-ingress.yaml"]
    assert "port: 3000" in netpol


def test_catalog_entry_nextjs_uses_ingress_derived_observability() -> None:
    inp = _make_input(template="nextjs")
    result = build_catalog_entry_addition(_SERVICES_YAML, inp)
    assert "mode: ingress-derived" in result


def test_catalog_entry_nextjs_has_dev_and_prod_envs() -> None:
    inp = _make_input(template="nextjs")
    new_entry = build_catalog_entry_addition(_SERVICES_YAML, inp)[len(_SERVICES_YAML):]
    assert "name: dev" in new_entry
    assert "name: prod" in new_entry


def test_wordpress_template_generates_expected_file_count() -> None:
    files = generate_gitops_new_files(_make_input(template="wordpress", image_repo="wordpress:latest"))
    assert len(files) == 25



def test_wordpress_template_has_no_servicemonitor() -> None:
    files = generate_gitops_new_files(_make_input(template="wordpress", image_repo="wordpress:latest"))
    assert not any("servicemonitor" in path for path in files)



def test_wordpress_template_uses_wp_login_probes() -> None:
    files = generate_gitops_new_files(_make_input(template="wordpress", image_repo="wordpress:latest"))
    deployment = files["apps/my-svc/base/deployment.yaml"]
    assert "path: /wp-login.php" in deployment



def test_wordpress_template_includes_persistent_volume_claim() -> None:
    files = generate_gitops_new_files(_make_input(template="wordpress", image_repo="wordpress:latest"))
    assert "apps/my-svc/base/persistentvolumeclaim.yaml" in files
    pvc = files["apps/my-svc/base/persistentvolumeclaim.yaml"]
    assert "my-svc-wp-content" in pvc



def test_wordpress_template_includes_mysql_bundle_files() -> None:
    files = generate_gitops_new_files(_make_input(template="wordpress", image_repo="wordpress:latest"))
    assert "apps/my-svc/base/mysql-service.yaml" in files
    assert "apps/my-svc/base/mysql-statefulset.yaml" in files
    assert "apps/my-svc/envs/dev/wordpress-db-secret.enc.yaml" in files
    assert "apps/my-svc/envs/prod/wordpress-db-secret.enc.yaml" in files
    assert "apps/my-svc/envs/dev/wordpress-db-secret-generator.yaml" in files



def test_wordpress_template_secret_stub_has_sops_block() -> None:
    files = generate_gitops_new_files(_make_input(template="wordpress", image_repo="wordpress:latest"))
    secret_stub = files["apps/my-svc/envs/dev/wordpress-db-secret.enc.yaml"]
    assert "kind: Secret" in secret_stub
    assert "sops:" in secret_stub
    assert "WORDPRESS_DB_PASSWORD" in secret_stub
    assert "MYSQL_ROOT_PASSWORD" in secret_stub



def test_catalog_entry_wordpress_uses_ingress_derived_observability() -> None:
    inp = _make_input(template="wordpress", image_repo="wordpress:latest")
    result = build_catalog_entry_addition(_SERVICES_YAML, inp)
    assert "mode: ingress-derived" in result



def test_catalog_entry_wordpress_has_dev_and_prod_envs() -> None:
    inp = _make_input(template="wordpress", image_repo="wordpress:latest")
    new_entry = build_catalog_entry_addition(_SERVICES_YAML, inp)[len(_SERVICES_YAML):]
    assert "name: dev" in new_entry
    assert "name: prod" in new_entry


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


def test_generate_gitops_new_files_bootstrap_image_uses_latest_tag() -> None:
    files = generate_gitops_new_files(_make_input(image_repo="ghcr.io/x/svc"))
    deployment = files["apps/my-svc/base/deployment.yaml"]
    assert "image: ghcr.io/x/svc:latest" in deployment
    assert "imagePullPolicy: Always" in deployment


def test_generate_gitops_new_files_dev_overlay_uses_latest_tag() -> None:
    files = generate_gitops_new_files(_make_input(image_repo="ghcr.io/x/svc"))
    patch = files["apps/my-svc/envs/dev/patch-deployment.yaml"]
    assert "image: ghcr.io/x/svc:latest" in patch
    assert "imagePullPolicy: Always" in patch


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
    assert "mode: ingress-derived" in result


def test_build_catalog_entry_addition_observability_mode_nginx() -> None:
    inp = _make_input(template="static-nginx")
    result = build_catalog_entry_addition(_SERVICES_YAML, inp)
    assert "mode: ingress-derived" in result


def test_build_catalog_entry_addition_writes_public_host_for_prod_env() -> None:
    inp = _make_input(public_host="my-svc.example.com")
    result = build_catalog_entry_addition(_SERVICES_YAML, inp)
    assert "public_host: 'my-svc.example.com'" in result


def test_build_catalog_entry_addition_omits_public_host_when_empty() -> None:
    inp = _make_input(public_host="")
    result = build_catalog_entry_addition(_SERVICES_YAML, inp)
    assert "public_host" not in result


def test_generate_gitops_new_files_public_host_in_prod_patch_ingress() -> None:
    files = generate_gitops_new_files(_make_input(public_host="svc.example.com"))
    patch = files["apps/my-svc/envs/prod/patch-ingress.yaml"]
    assert "svc.example.com" in patch


def test_generate_gitops_new_files_prod_patch_ingress_falls_back_to_prod_host_when_public_host_empty() -> None:
    files = generate_gitops_new_files(_make_input(public_host="", prod_host="svc.internal.local"))
    patch = files["apps/my-svc/envs/prod/patch-ingress.yaml"]
    assert "svc.internal.local" in patch


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
    assert "environments/dev/workloads/my-svc-app.yaml" not in files
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


def test_postgres_template_credentials_secret_has_sops_block() -> None:
    inp = _make_input(template="postgres")
    secret = generate_gitops_new_files(inp)["apps/my-svc/base/credentials-secret.yaml"]
    assert "sops:" in secret


def test_mysql_template_credentials_secret_has_sops_block() -> None:
    inp = _make_input(template="mysql")
    secret = generate_gitops_new_files(inp)["apps/my-svc/base/credentials-secret.yaml"]
    assert "sops:" in secret


# ---------------------------------------------------------------------------
# build_catalog_entry_addition — database templates use no-http mode, single env
# ---------------------------------------------------------------------------


def test_catalog_entry_postgres_uses_no_http_observability() -> None:
    inp = _make_input(template="postgres")
    result = build_catalog_entry_addition(_SERVICES_YAML, inp)
    assert "mode: no-http" in result


def test_catalog_entry_mysql_uses_no_http_observability() -> None:
    inp = _make_input(template="mysql")
    result = build_catalog_entry_addition(_SERVICES_YAML, inp)
    assert "mode: no-http" in result


def test_catalog_entry_postgres_single_env() -> None:
    inp = _make_input(template="postgres")
    new_entry = build_catalog_entry_addition(_SERVICES_YAML, inp)[len(_SERVICES_YAML):]
    assert "name: prod" in new_entry
    assert "name: dev" not in new_entry


def test_catalog_entry_mysql_single_env() -> None:
    inp = _make_input(template="mysql")
    new_entry = build_catalog_entry_addition(_SERVICES_YAML, inp)[len(_SERVICES_YAML):]
    assert "name: prod" in new_entry
    assert "name: dev" not in new_entry


# ---------------------------------------------------------------------------
# generate_gitops_new_files — python-django template
# ---------------------------------------------------------------------------


def test_django_template_file_count_matches_fastapi() -> None:
    """Django is ingress-derived like FastAPI, so file count is the same."""
    fastapi_files = generate_gitops_new_files(_make_input(template="python-fastapi"))
    django_files = generate_gitops_new_files(_make_input(template="python-django"))
    assert len(django_files) == len(fastapi_files)


def test_django_template_has_no_servicemonitor() -> None:
    files = generate_gitops_new_files(_make_input(template="python-django"))
    assert not any("servicemonitor" in path for path in files)


def test_django_template_health_path_trailing_slash() -> None:
    files = generate_gitops_new_files(_make_input(template="python-django"))
    deployment = files["apps/my-svc/base/deployment.yaml"]
    assert "/health/" in deployment


def test_django_template_container_port_8000() -> None:
    files = generate_gitops_new_files(_make_input(template="python-django"))
    deployment = files["apps/my-svc/base/deployment.yaml"]
    assert "containerPort: 8000" in deployment


def test_django_template_container_name_is_app() -> None:
    files = generate_gitops_new_files(_make_input(template="python-django"))
    deployment = files["apps/my-svc/base/deployment.yaml"]
    assert "name: app" in deployment


def test_django_template_dev_and_prod_overlays() -> None:
    files = generate_gitops_new_files(_make_input(template="python-django"))
    assert "apps/my-svc/envs/dev/kustomization.yaml" in files
    assert "apps/my-svc/envs/prod/kustomization.yaml" in files
    assert "apps/my-svc/envs/prod/patch-ingress.yaml" in files


def test_catalog_entry_django_uses_ingress_derived_observability() -> None:
    inp = _make_input(template="python-django")
    result = build_catalog_entry_addition(_SERVICES_YAML, inp)
    assert "mode: ingress-derived" in result


def test_catalog_entry_django_has_dev_and_prod_envs() -> None:
    inp = _make_input(template="python-django")
    new_entry = build_catalog_entry_addition(_SERVICES_YAML, inp)[len(_SERVICES_YAML):]
    assert "name: dev" in new_entry
    assert "name: prod" in new_entry


# ---------------------------------------------------------------------------
# generate_gitops_new_files — python-flask template
# ---------------------------------------------------------------------------


def test_flask_template_file_count_matches_fastapi() -> None:
    """Flask is ingress-derived like FastAPI, so file count is the same."""
    fastapi_files = generate_gitops_new_files(_make_input(template="python-fastapi"))
    flask_files = generate_gitops_new_files(_make_input(template="python-flask"))
    assert len(flask_files) == len(fastapi_files)


def test_flask_template_has_no_servicemonitor() -> None:
    files = generate_gitops_new_files(_make_input(template="python-flask"))
    assert not any("servicemonitor" in path for path in files)


def test_flask_template_container_port_5000() -> None:
    files = generate_gitops_new_files(_make_input(template="python-flask"))
    deployment = files["apps/my-svc/base/deployment.yaml"]
    assert "containerPort: 5000" in deployment


def test_flask_template_health_path() -> None:
    files = generate_gitops_new_files(_make_input(template="python-flask"))
    deployment = files["apps/my-svc/base/deployment.yaml"]
    assert "/health" in deployment


def test_flask_template_network_policy_port_5000() -> None:
    files = generate_gitops_new_files(_make_input(template="python-flask"))
    np = files["apps/my-svc/base/networkpolicy-allow-ingress.yaml"]
    assert "5000" in np


def test_catalog_entry_flask_uses_ingress_derived_observability() -> None:
    inp = _make_input(template="python-flask")
    result = build_catalog_entry_addition(_SERVICES_YAML, inp)
    assert "mode: ingress-derived" in result


def test_catalog_entry_flask_has_dev_and_prod_envs() -> None:
    inp = _make_input(template="python-flask")
    new_entry = build_catalog_entry_addition(_SERVICES_YAML, inp)[len(_SERVICES_YAML):]
    assert "name: dev" in new_entry
    assert "name: prod" in new_entry


# ---------------------------------------------------------------------------
# generate_gitops_new_files — node-express template
# ---------------------------------------------------------------------------


def test_express_template_file_count_matches_fastapi() -> None:
    express_files = generate_gitops_new_files(_make_input(template="node-express"))
    assert len(express_files) == 17


def test_express_template_has_servicemonitor() -> None:
    files = generate_gitops_new_files(_make_input(template="node-express"))
    assert any("servicemonitor" in path for path in files)


def test_express_template_container_port_3000() -> None:
    files = generate_gitops_new_files(_make_input(template="node-express"))
    deployment = files["apps/my-svc/base/deployment.yaml"]
    assert "containerPort: 3000" in deployment


def test_express_template_health_path() -> None:
    files = generate_gitops_new_files(_make_input(template="node-express"))
    deployment = files["apps/my-svc/base/deployment.yaml"]
    assert "/health" in deployment


def test_express_template_network_policy_port_3000() -> None:
    files = generate_gitops_new_files(_make_input(template="node-express"))
    np = files["apps/my-svc/base/networkpolicy-allow-ingress.yaml"]
    assert "3000" in np


def test_express_template_dev_and_prod_overlays() -> None:
    files = generate_gitops_new_files(_make_input(template="node-express"))
    assert "apps/my-svc/envs/dev/kustomization.yaml" in files
    assert "apps/my-svc/envs/prod/kustomization.yaml" in files
    assert "apps/my-svc/envs/prod/patch-ingress.yaml" in files


def test_catalog_entry_express_uses_app_native_observability() -> None:
    inp = _make_input(template="node-express")
    result = build_catalog_entry_addition(_SERVICES_YAML, inp)
    assert "mode: app-native" in result


def test_catalog_entry_express_has_dev_and_prod_envs() -> None:
    inp = _make_input(template="node-express")
    new_entry = build_catalog_entry_addition(_SERVICES_YAML, inp)[len(_SERVICES_YAML):]
    assert "name: dev" in new_entry
    assert "name: prod" in new_entry


# ---------------------------------------------------------------------------
# generate_gitops_new_files — node-nestjs template
# ---------------------------------------------------------------------------


def test_nestjs_template_file_count_matches_fastapi() -> None:
    """NestJS is ingress-derived like FastAPI, so file count is the same."""
    fastapi_files = generate_gitops_new_files(_make_input(template="python-fastapi"))
    nestjs_files = generate_gitops_new_files(_make_input(template="node-nestjs"))
    assert len(nestjs_files) == len(fastapi_files)


def test_nestjs_template_has_no_servicemonitor() -> None:
    files = generate_gitops_new_files(_make_input(template="node-nestjs"))
    assert not any("servicemonitor" in path for path in files)


def test_nestjs_template_container_port_3000() -> None:
    files = generate_gitops_new_files(_make_input(template="node-nestjs"))
    deployment = files["apps/my-svc/base/deployment.yaml"]
    assert "containerPort: 3000" in deployment


def test_nestjs_template_health_path() -> None:
    files = generate_gitops_new_files(_make_input(template="node-nestjs"))
    deployment = files["apps/my-svc/base/deployment.yaml"]
    assert "/health" in deployment


def test_nestjs_template_network_policy_port_3000() -> None:
    files = generate_gitops_new_files(_make_input(template="node-nestjs"))
    np = files["apps/my-svc/base/networkpolicy-allow-ingress.yaml"]
    assert "3000" in np


def test_nestjs_template_dev_and_prod_overlays() -> None:
    files = generate_gitops_new_files(_make_input(template="node-nestjs"))
    assert "apps/my-svc/envs/dev/kustomization.yaml" in files
    assert "apps/my-svc/envs/prod/kustomization.yaml" in files
    assert "apps/my-svc/envs/prod/patch-ingress.yaml" in files


def test_catalog_entry_nestjs_uses_ingress_derived_observability() -> None:
    inp = _make_input(template="node-nestjs")
    result = build_catalog_entry_addition(_SERVICES_YAML, inp)
    assert "mode: ingress-derived" in result


def test_catalog_entry_nestjs_has_dev_and_prod_envs() -> None:
    inp = _make_input(template="node-nestjs")
    new_entry = build_catalog_entry_addition(_SERVICES_YAML, inp)[len(_SERVICES_YAML):]
    assert "name: dev" in new_entry
    assert "name: prod" in new_entry


# ---------------------------------------------------------------------------
# Bundle topology – generate_gitops_bundle_files
# ---------------------------------------------------------------------------


def _make_bundle_input(**overrides: object) -> ScaffoldBundleInput:
    defaults: dict[str, object] = {
        "name": "my-proj",
        "description": "A test bundle project",
        "owner_email": "ops@example.com",
        "owner": "",
        "namespace": "my-proj",
        "dev_host": "my-proj.dev.homelab.local",
        "prod_host": "",
        "public_host": "my-proj.homelab.local",
        "workloads_repo_url": "https://github.com/example/workloads.git",
        "repo_url": "https://github.com/example/my-proj",
        "topology": "frontend-backend",
        "frontend_template": "react",
        "frontend_image_repo": "ghcr.io/example/my-proj-frontend",
        "backend_template": "python-fastapi",
        "backend_image_repo": "ghcr.io/example/my-proj-backend",
    }
    defaults.update(overrides)
    return ScaffoldBundleInput(**defaults)  # type: ignore[arg-type]


def test_bundle_frontend_backend_file_count() -> None:
    files = generate_gitops_bundle_files(_make_bundle_input())
    # Base: kustomization, namespace, 2 SA, 2 deployment, 2 service, ingress,
    #   3 netpol (default-deny, allow-dns, allow-ingress),
    #   2 netpol (frontend↔backend), 0 servicemonitors (react=ingress-derived, fastapi=ingress-derived)
    assert "apps/my-proj/base/kustomization.yaml" in files
    assert "apps/my-proj/base/namespace.yaml" in files
    assert "apps/my-proj/base/frontend-deployment.yaml" in files
    assert "apps/my-proj/base/backend-deployment.yaml" in files
    assert "apps/my-proj/base/frontend-service.yaml" in files
    assert "apps/my-proj/base/backend-service.yaml" in files
    assert "apps/my-proj/base/ingress.yaml" in files
    assert "apps/my-proj/base/networkpolicy-default-deny.yaml" in files
    assert "apps/my-proj/base/networkpolicy-allow-dns-egress.yaml" in files
    assert "apps/my-proj/base/networkpolicy-allow-ingress.yaml" in files
    assert "apps/my-proj/base/networkpolicy-allow-frontend-to-backend.yaml" in files
    assert "apps/my-proj/base/networkpolicy-allow-backend-from-frontend.yaml" in files
    # No db files for frontend-backend topology
    assert "apps/my-proj/base/db-statefulset.yaml" not in files
    assert "apps/my-proj/base/db-service.yaml" not in files


def test_bundle_frontend_backend_db_has_db_files() -> None:
    files = generate_gitops_bundle_files(_make_bundle_input(
        topology="frontend-backend-db",
        db_template="postgres",
    ))
    assert "apps/my-proj/base/db-credentials-secret.yaml" in files
    assert "apps/my-proj/base/db-statefulset.yaml" in files
    assert "apps/my-proj/base/db-service.yaml" in files
    assert "apps/my-proj/base/networkpolicy-allow-backend-to-db.yaml" in files
    assert "apps/my-proj/base/networkpolicy-allow-db-from-backend.yaml" in files


def test_bundle_frontend_deployment_has_backend_url() -> None:
    files = generate_gitops_bundle_files(_make_bundle_input())
    frontend_deploy = files["apps/my-proj/base/frontend-deployment.yaml"]
    assert "BACKEND_URL" in frontend_deploy
    assert "my-proj-backend" in frontend_deploy


def test_bundle_backend_deployment_with_db_has_database_url() -> None:
    files = generate_gitops_bundle_files(_make_bundle_input(
        topology="frontend-backend-db",
        db_template="postgres",
    ))
    backend_deploy = files["apps/my-proj/base/backend-deployment.yaml"]
    assert "DATABASE_URL" in backend_deploy


def test_bundle_component_labels() -> None:
    files = generate_gitops_bundle_files(_make_bundle_input())
    frontend_deploy = files["apps/my-proj/base/frontend-deployment.yaml"]
    backend_deploy = files["apps/my-proj/base/backend-deployment.yaml"]
    assert "app.kubernetes.io/component: frontend" in frontend_deploy
    assert "app.kubernetes.io/component: backend" in backend_deploy
    assert "app.kubernetes.io/name: my-proj" in frontend_deploy
    assert "app.kubernetes.io/name: my-proj" in backend_deploy


def test_bundle_ingress_routes_to_frontend() -> None:
    files = generate_gitops_bundle_files(_make_bundle_input())
    ingress = files["apps/my-proj/base/ingress.yaml"]
    assert "my-proj-frontend" in ingress


def test_bundle_overlay_files_exist() -> None:
    files = generate_gitops_bundle_files(_make_bundle_input())
    assert "apps/my-proj/envs/dev/kustomization.yaml" in files
    assert "apps/my-proj/envs/prod/kustomization.yaml" in files
    assert "apps/my-proj/envs/dev/patch-frontend-deployment.yaml" in files
    assert "apps/my-proj/envs/dev/patch-backend-deployment.yaml" in files


def test_bundle_argo_application_manifests() -> None:
    files = generate_gitops_bundle_files(_make_bundle_input())
    assert "environments/dev/workloads/my-proj-app.yaml" in files
    assert "environments/prod/workloads/my-proj-app.yaml" in files


def test_bundle_servicemonitor_app_native_backend() -> None:
    """Express backend is app-native, so a ServiceMonitor should be generated."""
    files = generate_gitops_bundle_files(_make_bundle_input(
        backend_template="node-express",
    ))
    assert "apps/my-proj/base/servicemonitor-backend.yaml" in files


def test_bundle_no_servicemonitor_sidecar_frontend() -> None:
    """React frontend is sidecar-only, no ServiceMonitor for frontend."""
    files = generate_gitops_bundle_files(_make_bundle_input(
        frontend_template="react",
    ))
    assert "apps/my-proj/base/servicemonitor-frontend.yaml" not in files


def test_bundle_no_servicemonitor_nextjs_frontend() -> None:
    """Next.js frontend is ingress-derived by default, so no ServiceMonitor is generated."""
    files = generate_gitops_bundle_files(_make_bundle_input(
        frontend_template="nextjs",
    ))
    assert "apps/my-proj/base/servicemonitor-frontend.yaml" not in files


def test_bundle_no_servicemonitor_ingress_backend() -> None:
    files = generate_gitops_bundle_files(_make_bundle_input(
        backend_template="python-fastapi",
    ))
    assert "apps/my-proj/base/servicemonitor-backend.yaml" not in files


def test_bundle_name_validation() -> None:
    with pytest.raises(ScaffoldError):
        generate_gitops_bundle_files(_make_bundle_input(name="INVALID"))


# ---------------------------------------------------------------------------
# Bundle topology – build_catalog_bundle_entries
# ---------------------------------------------------------------------------


def test_catalog_bundle_entries_appends_frontend_and_backend() -> None:
    inp = _make_bundle_input()
    result = build_catalog_bundle_entries(_SERVICES_YAML, inp)
    assert "service_id: my-proj-frontend" in result
    assert "service_id: my-proj-backend" in result
    assert "homelab-api" in result  # original preserved


def test_catalog_bundle_entries_have_project_id() -> None:
    inp = _make_bundle_input()
    result = build_catalog_bundle_entries(_SERVICES_YAML, inp)
    assert "project_id: my-proj" in result


def test_catalog_bundle_entries_dev_and_prod_envs() -> None:
    inp = _make_bundle_input()
    new_entries = build_catalog_bundle_entries(_SERVICES_YAML, inp)[len(_SERVICES_YAML):]
    assert new_entries.count("name: dev") == 2
    assert new_entries.count("name: prod") == 2


def test_catalog_bundle_entries_shared_namespace() -> None:
    inp = _make_bundle_input()
    new_entries = build_catalog_bundle_entries(_SERVICES_YAML, inp)[len(_SERVICES_YAML):]
    # Both frontend and backend share the project namespace
    assert new_entries.count("namespace: my-proj") == 4  # 2 envs x 2 services


def test_catalog_bundle_rejects_duplicate_service_id() -> None:
    existing = _SERVICES_YAML + "  - service_id: my-proj-frontend\n    name: 'Existing'\n"
    with pytest.raises(ScaffoldError):
        build_catalog_bundle_entries(existing, _make_bundle_input())


# ---------------------------------------------------------------------------
# Add-to-project – generate_gitops_add_service_files
# ---------------------------------------------------------------------------


def _make_add_service_input(**overrides: object) -> ScaffoldAddServiceInput:
    defaults: dict[str, object] = {
        "project_id": "my-proj",
        "service_name": "worker",
        "description": "A worker service",
        "owner_email": "ops@example.com",
        "owner": "",
        "namespace": "my-proj",
        "template": "python-fastapi",
        "image_repo": "ghcr.io/example/my-proj-worker",
        "repo_url": "https://github.com/example/my-proj",
        "dev_host": "my-proj.dev.homelab.local",
        "prod_host": "",
        "public_host": "my-proj.homelab.local",
        "workloads_repo_url": "https://github.com/example/workloads.git",
    }
    defaults.update(overrides)
    return ScaffoldAddServiceInput(**defaults)  # type: ignore[arg-type]


def test_add_service_creates_deployment_and_service() -> None:
    files, resources = generate_gitops_add_service_files(_make_add_service_input())
    assert "apps/my-proj/base/worker-deployment.yaml" in files
    assert "apps/my-proj/base/worker-service.yaml" in files
    assert "apps/my-proj/base/serviceaccount-worker.yaml" in files
    assert "worker-deployment.yaml" in resources
    assert "worker-service.yaml" in resources
    assert "serviceaccount-worker.yaml" in resources


def test_add_service_does_not_create_namespace() -> None:
    files, _ = generate_gitops_add_service_files(_make_add_service_input())
    assert not any("namespace.yaml" in path for path in files)


def test_add_service_does_not_create_default_deny_netpol() -> None:
    files, _ = generate_gitops_add_service_files(_make_add_service_input())
    assert not any("networkpolicy-default-deny" in path for path in files)
    assert not any("networkpolicy-allow-dns-egress" in path for path in files)


def test_add_service_does_not_create_argo_application() -> None:
    files, _ = generate_gitops_add_service_files(_make_add_service_input())
    assert not any("environments/" in path for path in files)


def test_add_service_component_labels() -> None:
    files, _ = generate_gitops_add_service_files(_make_add_service_input())
    deployment = files["apps/my-proj/base/worker-deployment.yaml"]
    assert "app.kubernetes.io/name: my-proj" in deployment
    assert "app.kubernetes.io/component: worker" in deployment


def test_add_service_service_id_in_deployment() -> None:
    files, _ = generate_gitops_add_service_files(_make_add_service_input())
    deployment = files["apps/my-proj/base/worker-deployment.yaml"]
    assert "name: my-proj-worker" in deployment


def test_add_service_overlay_patches() -> None:
    files, _ = generate_gitops_add_service_files(_make_add_service_input())
    assert "apps/my-proj/envs/dev/patch-worker-deployment.yaml" in files
    assert "apps/my-proj/envs/prod/patch-worker-deployment.yaml" in files


def test_add_service_servicemonitor_app_native() -> None:
    files, resources = generate_gitops_add_service_files(_make_add_service_input(
        template="node-express",
    ))
    assert "apps/my-proj/base/servicemonitor-worker.yaml" in files
    assert "servicemonitor-worker.yaml" in resources


def test_add_service_no_servicemonitor_sidecar() -> None:
    files, resources = generate_gitops_add_service_files(_make_add_service_input(
        template="react",
    ))
    assert "apps/my-proj/base/servicemonitor-worker.yaml" not in files
    assert "servicemonitor-worker.yaml" not in resources


def test_add_service_no_servicemonitor_ingress_backend() -> None:
    files, resources = generate_gitops_add_service_files(_make_add_service_input(
        template="python-fastapi",
    ))
    assert "apps/my-proj/base/servicemonitor-worker.yaml" not in files
    assert "servicemonitor-worker.yaml" not in resources


def test_add_service_database_template() -> None:
    files, resources = generate_gitops_add_service_files(_make_add_service_input(
        template="postgres",
        service_name="db",
    ))
    assert "apps/my-proj/base/db-statefulset.yaml" in files
    assert "apps/my-proj/base/db-service.yaml" in files
    assert "apps/my-proj/base/db-credentials-secret.yaml" in files
    assert "db-statefulset.yaml" in resources
    # DB templates should NOT generate overlay patches
    assert not any("envs/" in path for path in files)


def test_add_service_name_validation() -> None:
    with pytest.raises(ScaffoldError):
        generate_gitops_add_service_files(_make_add_service_input(
            project_id="my-proj",
            service_name="INVALID",
        ))


# ---------------------------------------------------------------------------
# Add-to-project – validate_add_service
# ---------------------------------------------------------------------------


_EXISTING_BASE_KUSTOMIZATION = """\
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - backend-deployment.yaml
  - backend-service.yaml
  - frontend-deployment.yaml
  - frontend-service.yaml
  - namespace.yaml
"""


def test_validate_add_service_passes_for_new_service() -> None:
    validate_add_service(
        _make_add_service_input(),
        _EXISTING_BASE_KUSTOMIZATION,
        _SERVICES_YAML,
    )  # should not raise


def test_validate_add_service_rejects_duplicate_service_id() -> None:
    existing_catalog = _SERVICES_YAML + "  - service_id: my-proj-worker\n    name: 'Existing'\n"
    with pytest.raises(ScaffoldError):
        validate_add_service(
            _make_add_service_input(),
            _EXISTING_BASE_KUSTOMIZATION,
            existing_catalog,
        )


def test_validate_add_service_rejects_duplicate_resource() -> None:
    kustomization = _EXISTING_BASE_KUSTOMIZATION + "  - worker-deployment.yaml\n"
    with pytest.raises(ScaffoldError):
        validate_add_service(
            _make_add_service_input(),
            kustomization,
            _SERVICES_YAML,
        )


# ---------------------------------------------------------------------------
# Add-to-project – build_catalog_add_service_entry
# ---------------------------------------------------------------------------


def test_catalog_add_service_entry_has_project_id() -> None:
    result = build_catalog_add_service_entry(_SERVICES_YAML, _make_add_service_input())
    assert "project_id: my-proj" in result


def test_catalog_add_service_entry_has_correct_service_id() -> None:
    result = build_catalog_add_service_entry(_SERVICES_YAML, _make_add_service_input())
    assert "service_id: my-proj-worker" in result


def test_catalog_add_service_entry_shares_argo_app() -> None:
    result = build_catalog_add_service_entry(_SERVICES_YAML, _make_add_service_input())
    new_entry = result[len(_SERVICES_YAML):]
    assert "argo_app: my-proj-dev" in new_entry
    assert "argo_app: my-proj-prod" in new_entry


def test_catalog_add_service_entry_includes_workload_ref() -> None:
    result = build_catalog_add_service_entry(_SERVICES_YAML, _make_add_service_input())
    new_entry = result[len(_SERVICES_YAML):]
    assert "workload_ref: apps/my-proj/base/worker-deployment.yaml" in new_entry


def test_catalog_add_service_entry_dev_and_prod_envs() -> None:
    result = build_catalog_add_service_entry(_SERVICES_YAML, _make_add_service_input())
    new_entry = result[len(_SERVICES_YAML):]
    assert "name: dev" in new_entry
    assert "name: prod" in new_entry


def test_catalog_add_service_entry_rejects_duplicate() -> None:
    existing = _SERVICES_YAML + "  - service_id: my-proj-worker\n    name: 'Existing'\n"
    with pytest.raises(ScaffoldError):
        build_catalog_add_service_entry(existing, _make_add_service_input())


# ---------------------------------------------------------------------------
# update_overlay_kustomization_patches
# ---------------------------------------------------------------------------


_EXISTING_OVERLAY_KUSTOMIZATION = """\
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../../base
commonLabels:
  homelab.env: dev
patches:
  - path: patch-backend-deployment.yaml
  - path: patch-frontend-deployment.yaml
"""


def test_overlay_kustomization_adds_new_patch() -> None:
    result = update_overlay_kustomization_patches(
        _EXISTING_OVERLAY_KUSTOMIZATION,
        "patch-worker-deployment.yaml",
    )
    assert "- path: patch-worker-deployment.yaml" in result
    assert "- path: patch-backend-deployment.yaml" in result
    assert "- path: patch-frontend-deployment.yaml" in result


def test_overlay_kustomization_deduplicates() -> None:
    result = update_overlay_kustomization_patches(
        _EXISTING_OVERLAY_KUSTOMIZATION,
        "patch-backend-deployment.yaml",
    )
    assert result.count("patch-backend-deployment.yaml") == 1


def test_overlay_kustomization_sorts_patches() -> None:
    result = update_overlay_kustomization_patches(
        _EXISTING_OVERLAY_KUSTOMIZATION,
        "patch-alpha-deployment.yaml",
    )
    lines = [line.strip() for line in result.splitlines() if line.strip().startswith("- path:")]
    assert lines == sorted(lines)
