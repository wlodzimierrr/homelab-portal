from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass
from typing import Literal


# Scaffold generation is implemented as pure string builders so preview and submit
# can reuse the same manifest output without talking to the filesystem.
SERVICE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,62}$")

# Template metadata drives both generated manifests and catalog defaults such as
# observability mode, container naming, ports, and health probes.
_TEMPLATES: dict[str, dict[str, object]] = {
    "python-fastapi": {
        "container_port": 8000,
        "service_port": 80,
        "health_path": "/health",
        "readiness_path": "/health",
        "container_name": "app",
        "default_observability_mode": "app-native",
    },
    "python-django": {
        "container_port": 8000,
        "service_port": 80,
        "health_path": "/health/",
        "readiness_path": "/health/",
        "container_name": "app",
        "default_observability_mode": "app-native",
    },
    "python-flask": {
        "container_port": 5000,
        "service_port": 80,
        "health_path": "/health",
        "readiness_path": "/health",
        "container_name": "app",
        "default_observability_mode": "app-native",
    },
    "static-nginx": {
        "container_port": 80,
        "service_port": 80,
        "health_path": "/health",
        "readiness_path": "/health",
        "container_name": "web",
        "default_observability_mode": "ingress-derived",
    },
    "react": {
        "container_port": 80,
        "service_port": 80,
        "health_path": "/health",
        "readiness_path": "/health",
        "container_name": "web",
        "default_observability_mode": "ingress-derived",
    },
    "vue": {
        "container_port": 80,
        "service_port": 80,
        "health_path": "/",
        "readiness_path": "/",
        "container_name": "web",
        "default_observability_mode": "ingress-derived",
    },
    "wordpress": {
        "container_port": 80,
        "service_port": 80,
        "health_path": "/wp-login.php",
        "readiness_path": "/wp-login.php",
        "container_name": "web",
        "default_observability_mode": "ingress-derived",
    },
    "nextjs": {
        "container_port": 3000,
        "service_port": 80,
        "health_path": "/api/health",
        "readiness_path": "/api/health",
        "container_name": "web",
        "default_observability_mode": "app-native",
    },
    "node-express": {
        "container_port": 3000,
        "service_port": 80,
        "health_path": "/health",
        "readiness_path": "/health",
        "container_name": "app",
        "default_observability_mode": "app-native",
    },
    "node-nestjs": {
        "container_port": 3000,
        "service_port": 80,
        "health_path": "/health",
        "readiness_path": "/health",
        "container_name": "app",
        "default_observability_mode": "app-native",
    },
    "postgres": {
        "db_port": 5432,
        "db_image": "postgres:17-alpine",
        "db_engine": "postgres",
        "default_observability_mode": "no-http",
    },
    "mysql": {
        "db_port": 3306,
        "db_image": "mysql:8.0",
        "db_engine": "mysql",
        "default_observability_mode": "no-http",
    },
}


class ScaffoldError(Exception):
    """Raised for scaffold validation or generation failures."""

    def __init__(self, message: str, *, status_code: int = 422) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class ScaffoldServiceInput:
    name: str
    description: str
    image_repo: str
    repo_url: str
    owner_email: str
    owner: str
    template: Literal["python-fastapi", "python-django", "python-flask", "static-nginx", "react", "nextjs", "vue", "wordpress", "node-express", "node-nestjs", "postgres", "mysql"]
    namespace: str
    dev_host: str
    prod_host: str
    public_host: str
    workloads_repo_url: str
    db_username: str = "appuser"
    db_password: str = "changeme"
    db_name: str = "appdb"


_FRONTEND_TEMPLATES = frozenset({"react", "nextjs", "vue", "static-nginx"})
_BACKEND_TEMPLATES = frozenset({"python-fastapi", "python-django", "python-flask", "node-express", "node-nestjs"})
_DB_TEMPLATES = frozenset({"postgres", "mysql"})

BundleTopology = Literal["single-service", "frontend-backend", "frontend-backend-db"]


@dataclass(frozen=True)
class ScaffoldBundleInput:
    """Input for generating a multi-service project bundle."""

    name: str
    description: str
    owner_email: str
    owner: str
    namespace: str
    dev_host: str
    prod_host: str
    public_host: str
    workloads_repo_url: str
    repo_url: str
    topology: BundleTopology
    frontend_template: str
    frontend_image_repo: str
    backend_template: str
    backend_image_repo: str
    db_template: str | None = None
    db_username: str = "appuser"
    db_password: str = "changeme"
    db_name: str = "appdb"


def validate_service_name(name: str) -> None:
    if not SERVICE_NAME_PATTERN.match(name):
        raise ScaffoldError(
            f"Service name {name!r} must match ^[a-z][a-z0-9-]{{1,62}}$. "
            "Use lowercase letters, digits, and hyphens only.",
            status_code=422,
        )


# Dispatch to the correct manifest generator for the selected scaffold template.
def generate_gitops_new_files(inp: ScaffoldServiceInput) -> dict[str, str]:
    """Return all NEW files keyed by path relative to the gitops root."""
    # Databases and WordPress have bespoke layouts because they bundle extra stateful
    # resources. Everything else follows the shared app + overlay structure below.
    if inp.template in ("postgres", "mysql"):
        return _generate_database_gitops_files(inp)
    if inp.template == "wordpress":
        return _generate_wordpress_gitops_files(inp)

    t = _TEMPLATES[inp.template]
    container_port = int(t["container_port"])  # type: ignore[arg-type]
    service_port = int(t["service_port"])  # type: ignore[arg-type]
    container_name = str(t["container_name"])
    health_path = str(t["health_path"])
    readiness_path = str(t["readiness_path"])
    observability_mode = str(t["default_observability_mode"])

    files: dict[str, str] = {}

    base_prefix = f"apps/{inp.name}/base"
    for rel_path, content in _generate_base_files(
        inp, container_port, service_port, container_name, health_path, readiness_path, observability_mode
    ).items():
        files[f"{base_prefix}/{rel_path}"] = content

    dev_prefix = f"apps/{inp.name}/envs/dev"
    for rel_path, content in _generate_overlay_files(
        inp, "dev", container_name, service_port
    ).items():
        files[f"{dev_prefix}/{rel_path}"] = content

    prod_prefix = f"apps/{inp.name}/envs/prod"
    for rel_path, content in _generate_overlay_files(
        inp, "prod", container_name, service_port
    ).items():
        files[f"{prod_prefix}/{rel_path}"] = content

    files[f"environments/dev/workloads/{inp.name}-app.yaml"] = _generate_application_manifest(
        app_name=f"{inp.name}-dev",
        project_name=inp.name,
        path=f"apps/{inp.name}/envs/dev",
        namespace=inp.namespace,
        repo_url=inp.workloads_repo_url,
    )
    files[f"environments/prod/workloads/{inp.name}-app.yaml"] = (
        """# Generated for future prod activation.
# Keep environments/prod/workloads/kustomization.yaml empty while single-cluster safety mode is active.
"""
        + _generate_application_manifest(
            app_name=f"{inp.name}-prod",
            project_name=inp.name,
            path=f"apps/{inp.name}/envs/prod",
            namespace=inp.namespace,
            repo_url=inp.workloads_repo_url,
        )
    )

    return files


def update_kustomization_resources(existing: str, resource_name: str) -> str:
    """Insert resource_name into the resources: block (sorted, deduplicated)."""
    lines = existing.splitlines()
    has_resources = any(line.strip() == "resources:" for line in lines)
    if not has_resources:
        raise ScaffoldError(
            "Expected resources: block in workloads kustomization.yaml.",
            status_code=502,
        )

    resource_lines = [line.strip() for line in lines if line.strip().startswith("- ")]
    if f"- {resource_name}" in resource_lines:
        return existing

    resource_lines.append(f"- {resource_name}")
    resource_lines = sorted(dict.fromkeys(resource_lines))

    rebuilt: list[str] = []
    in_resources = False
    for line in lines:
        stripped = line.strip()
        if stripped == "resources:":
            rebuilt.append(line)
            for resource_line in resource_lines:
                rebuilt.append(f"  {resource_line}")
            in_resources = True
            continue
        if in_resources and stripped.startswith("- "):
            continue
        in_resources = False
        rebuilt.append(line)

    result = "\n".join(rebuilt)
    if not result.endswith("\n"):
        result += "\n"
    return result


# Catalog updates are generated from the same template metadata as manifests so the
# service registry and GitOps layout stay in sync.
def build_catalog_entry_addition(existing_services_yaml: str, inp: ScaffoldServiceInput) -> str:
    """Append a catalog entry to services.yaml content and return the new content."""
    if f"service_id: {inp.name}\n" in existing_services_yaml:
        raise ScaffoldError(
            f"Service {inp.name!r} already exists in services.yaml. Choose a different name.",
            status_code=409,
        )
    if "services:" not in existing_services_yaml:
        raise ScaffoldError("Expected top-level services: list in services.yaml.", status_code=502)

    # Database templates only register a prod environment today; application templates
    # emit both dev and prod and can optionally publish a prod public hostname.
    observability_mode = str(_TEMPLATES[inp.template]["default_observability_mode"])
    display_name = " ".join(word.capitalize() for word in inp.name.split("-"))
    repo_url = inp.repo_url or inp.workloads_repo_url
    is_database = inp.template in ("postgres", "mysql")
    if is_database:
        envs_section = (
            "    envs:\n"
            "      - name: prod\n"
            f"        namespace: {inp.namespace}\n"
            f"        argo_app: {inp.name}-prod\n"
        )
    else:
        prod_public_host_line = f"        public_host: {_yaml_string(inp.public_host)}\n" if inp.public_host else ""
        envs_section = (
            "    envs:\n"
            "      - name: dev\n"
            f"        namespace: {inp.namespace}\n"
            f"        argo_app: {inp.name}-dev\n"
            "      - name: prod\n"
            f"        namespace: {inp.namespace}\n"
            f"        argo_app: {inp.name}-prod\n"
            f"{prod_public_host_line}"
        )
    entry = (
        f"  - service_id: {inp.name}\n"
        f"    name: {_yaml_string(display_name)}\n"
        f"    owner: {_yaml_string(inp.owner or inp.owner_email)}\n"
        f"    owner_email: {_yaml_string(inp.owner_email)}\n"
        f"    repo_url: {_yaml_string(repo_url)}\n"
        f"    runbook_url: {_yaml_string(repo_url)}\n"
        f"    description: {_yaml_string(inp.description)}\n"
        "    observability:\n"
        f"      mode: {observability_mode}\n"
        f"{envs_section}"
    )
    suffix = "" if existing_services_yaml.endswith("\n") else "\n"
    return existing_services_yaml + suffix + entry


def build_appproject_addition(existing_project_yaml: str, inp: ScaffoldServiceInput) -> str:
    """Append an AppProject entry to project-homelab.yaml content and return the new content."""
    if f"name: {inp.name}" in existing_project_yaml:
        raise ScaffoldError(
            f"AppProject {inp.name!r} already exists in bootstrap/project-homelab.yaml.",
            status_code=409,
        )

    appproject = _generate_appproject_manifest(
        name=inp.name,
        namespace=inp.namespace,
        description=f"{inp.description} resources in {inp.namespace} namespace only",
        repo_url=inp.workloads_repo_url,
    )
    suffix = "" if existing_project_yaml.endswith("\n") else "\n"
    return existing_project_yaml + suffix + "---\n" + appproject


# The helpers below intentionally return YAML text instead of structured objects so
# previews match the eventual PR content byte-for-byte.
# ---------------------------------------------------------------------------
# Internal generation helpers (pure string functions)
# ---------------------------------------------------------------------------


def _dedent(value: str) -> str:
    return textwrap.dedent(value).strip() + "\n"


def _render_template(template: str, **values: str) -> str:
    return _dedent(template.format(**values))


def _yaml_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _indent_block(value: str, spaces: int) -> str:
    prefix = " " * spaces
    return "\n".join(f"{prefix}{line}" if line else line for line in value.splitlines())


# ---------------------------------------------------------------------------
# Database template generators (standalone postgres / mysql service)
# ---------------------------------------------------------------------------


# Standalone database templates differ from app templates: one prod environment,
# stateful storage, and ingress-derived observability.
def _generate_database_gitops_files(inp: ScaffoldServiceInput) -> dict[str, str]:
    """Return all NEW files for a standalone postgres or mysql template."""
    files: dict[str, str] = {}

    base_prefix = f"apps/{inp.name}/base"
    for rel_path, content in _generate_database_base_files(inp).items():
        files[f"{base_prefix}/{rel_path}"] = content

    env_prefix = f"apps/{inp.name}/envs/prod"
    files[f"{env_prefix}/kustomization.yaml"] = _render_template(
        """
        apiVersion: kustomize.config.k8s.io/v1beta1
        kind: Kustomization
        resources:
          - ../../base
        commonLabels:
          homelab.env: {env_name}
        """,
        env_name="prod",
    )

    files[f"environments/prod/workloads/{inp.name}-app.yaml"] = _generate_application_manifest(
        app_name=f"{inp.name}-prod",
        project_name=inp.name,
        path=f"apps/{inp.name}/envs/prod",
        namespace=inp.namespace,
        repo_url=inp.workloads_repo_url,
    )

    return files


def _generate_database_base_files(inp: ScaffoldServiceInput) -> dict[str, str]:
    t = _TEMPLATES[inp.template]
    db_port = int(t["db_port"])  # type: ignore[arg-type]
    db_image = str(t["db_image"])
    db_engine = str(t["db_engine"])
    is_postgres = db_engine == "postgres"

    resources = [
        "namespace.yaml",
        "serviceaccount.yaml",
        "credentials-secret.yaml",
        "statefulset.yaml",
        "service.yaml",
        "networkpolicy-default-deny.yaml",
        "networkpolicy-allow-dns-egress.yaml",
        "networkpolicy-allow-ingress.yaml",
    ]

    if is_postgres:
        secret_content = _render_template(
            """
            # SOPS-encrypted Secret stub — fill values then run: sops -e -i credentials-secret.yaml
            # See docs/runbooks/sops-secrets.md
            apiVersion: v1
            kind: Secret
            metadata:
              name: {name}-credentials
              namespace: {namespace}
            type: Opaque
            stringData:
              POSTGRES_USER: {db_username}
              POSTGRES_PASSWORD: {db_password}
              POSTGRES_DB: {db_name}
            sops:
            """,
            name=inp.name,
            namespace=inp.namespace,
            db_username=inp.db_username,
            db_password=inp.db_password,
            db_name=inp.db_name,
        )
        statefulset_content = _render_template(
            """
            apiVersion: apps/v1
            kind: StatefulSet
            metadata:
              name: {name}
              namespace: {namespace}
              labels:
                app.kubernetes.io/name: {name}
                app.kubernetes.io/instance: {name}
            spec:
              serviceName: {name}
              replicas: 1
              selector:
                matchLabels:
                  app.kubernetes.io/name: {name}
              template:
                metadata:
                  labels:
                    app.kubernetes.io/name: {name}
                spec:
                  serviceAccountName: {name}
                  containers:
                    - name: postgres
                      image: {db_image}
                      imagePullPolicy: IfNotPresent
                      ports:
                        - containerPort: 5432
                          name: postgres
                      env:
                        - name: POSTGRES_USER
                          valueFrom:
                            secretKeyRef:
                              name: {name}-credentials
                              key: POSTGRES_USER
                        - name: POSTGRES_PASSWORD
                          valueFrom:
                            secretKeyRef:
                              name: {name}-credentials
                              key: POSTGRES_PASSWORD
                        - name: POSTGRES_DB
                          valueFrom:
                            secretKeyRef:
                              name: {name}-credentials
                              key: POSTGRES_DB
                      volumeMounts:
                        - name: data
                          mountPath: /var/lib/postgresql/data
                          subPath: postgres
                      resources:
                        requests:
                          cpu: 100m
                          memory: 256Mi
                        limits:
                          cpu: 500m
                          memory: 512Mi
              volumeClaimTemplates:
                - metadata:
                    name: data
                  spec:
                    accessModes:
                      - ReadWriteOnce
                    resources:
                      requests:
                        storage: 10Gi
            """,
            name=inp.name,
            namespace=inp.namespace,
            db_image=db_image,
        )
    else:  # mysql
        secret_content = _render_template(
            """
            # SOPS-encrypted Secret stub — fill values then run: sops -e -i credentials-secret.yaml
            # See docs/runbooks/sops-secrets.md
            apiVersion: v1
            kind: Secret
            metadata:
              name: {name}-credentials
              namespace: {namespace}
            type: Opaque
            stringData:
              MYSQL_ROOT_PASSWORD: {db_password}
              MYSQL_USER: {db_username}
              MYSQL_PASSWORD: {db_password}
              MYSQL_DATABASE: {db_name}
            sops:
            """,
            name=inp.name,
            namespace=inp.namespace,
            db_username=inp.db_username,
            db_password=inp.db_password,
            db_name=inp.db_name,
        )
        statefulset_content = _render_template(
            """
            apiVersion: apps/v1
            kind: StatefulSet
            metadata:
              name: {name}
              namespace: {namespace}
              labels:
                app.kubernetes.io/name: {name}
                app.kubernetes.io/instance: {name}
            spec:
              serviceName: {name}
              replicas: 1
              selector:
                matchLabels:
                  app.kubernetes.io/name: {name}
              template:
                metadata:
                  labels:
                    app.kubernetes.io/name: {name}
                spec:
                  serviceAccountName: {name}
                  containers:
                    - name: mysql
                      image: {db_image}
                      imagePullPolicy: IfNotPresent
                      ports:
                        - containerPort: 3306
                          name: mysql
                      env:
                        - name: MYSQL_ROOT_PASSWORD
                          valueFrom:
                            secretKeyRef:
                              name: {name}-credentials
                              key: MYSQL_ROOT_PASSWORD
                        - name: MYSQL_USER
                          valueFrom:
                            secretKeyRef:
                              name: {name}-credentials
                              key: MYSQL_USER
                        - name: MYSQL_PASSWORD
                          valueFrom:
                            secretKeyRef:
                              name: {name}-credentials
                              key: MYSQL_PASSWORD
                        - name: MYSQL_DATABASE
                          valueFrom:
                            secretKeyRef:
                              name: {name}-credentials
                              key: MYSQL_DATABASE
                      volumeMounts:
                        - name: data
                          mountPath: /var/lib/mysql
                      resources:
                        requests:
                          cpu: 100m
                          memory: 256Mi
                        limits:
                          cpu: 500m
                          memory: 512Mi
              volumeClaimTemplates:
                - metadata:
                    name: data
                  spec:
                    accessModes:
                      - ReadWriteOnce
                    resources:
                      requests:
                        storage: 10Gi
            """,
            name=inp.name,
            namespace=inp.namespace,
            db_image=db_image,
        )

    return {
        "kustomization.yaml": (
            "apiVersion: kustomize.config.k8s.io/v1beta1\n"
            "kind: Kustomization\n"
            "resources:\n"
            + "".join(f"  - {r}\n" for r in resources)
        ),
        "namespace.yaml": _render_template(
            """
            apiVersion: v1
            kind: Namespace
            metadata:
              name: {namespace}
              labels:
                app.kubernetes.io/name: {name}
            """,
            namespace=inp.namespace,
            name=inp.name,
        ),
        "serviceaccount.yaml": _render_template(
            """
            apiVersion: v1
            kind: ServiceAccount
            metadata:
              name: {name}
              namespace: {namespace}
              labels:
                app.kubernetes.io/name: {name}
            """,
            name=inp.name,
            namespace=inp.namespace,
        ),
        "credentials-secret.yaml": secret_content,
        "statefulset.yaml": statefulset_content,
        "service.yaml": _render_template(
            """
            apiVersion: v1
            kind: Service
            metadata:
              name: {name}
              namespace: {namespace}
              labels:
                app.kubernetes.io/name: {name}
                app.kubernetes.io/instance: {name}
            spec:
              type: ClusterIP
              selector:
                app.kubernetes.io/name: {name}
              ports:
                - name: {db_engine}
                  port: {db_port}
                  targetPort: {db_port}
            """,
            name=inp.name,
            namespace=inp.namespace,
            db_engine=db_engine,
            db_port=str(db_port),
        ),
        "networkpolicy-default-deny.yaml": _render_template(
            """
            apiVersion: networking.k8s.io/v1
            kind: NetworkPolicy
            metadata:
              name: default-deny
              namespace: {namespace}
            spec:
              podSelector: {{}}
              policyTypes:
                - Ingress
                - Egress
            """,
            namespace=inp.namespace,
        ),
        "networkpolicy-allow-dns-egress.yaml": _render_template(
            """
            apiVersion: networking.k8s.io/v1
            kind: NetworkPolicy
            metadata:
              name: allow-dns-egress
              namespace: {namespace}
            spec:
              podSelector: {{}}
              policyTypes:
                - Egress
              egress:
                - to:
                    - namespaceSelector:
                        matchLabels:
                          kubernetes.io/metadata.name: kube-system
                  ports:
                    - protocol: UDP
                      port: 53
                    - protocol: TCP
                      port: 53
            """,
            namespace=inp.namespace,
        ),
        "networkpolicy-allow-ingress.yaml": _render_template(
            """
            apiVersion: networking.k8s.io/v1
            kind: NetworkPolicy
            metadata:
              name: allow-db-ingress
              namespace: {namespace}
            spec:
              podSelector:
                matchLabels:
                  app.kubernetes.io/name: {name}
              policyTypes:
                - Ingress
              ingress:
                - ports:
                    - protocol: TCP
                      port: {db_port}
            """,
            namespace=inp.namespace,
            name=inp.name,
            db_port=str(db_port),
        ),
    }


# WordPress is treated as its own special bundle because it pairs a web tier with
# an in-cluster MySQL dependency and persistent content storage.
def _generate_wordpress_gitops_files(inp: ScaffoldServiceInput) -> dict[str, str]:
    files: dict[str, str] = {}

    base_prefix = f"apps/{inp.name}/base"
    for rel_path, content in _generate_wordpress_base_files(inp).items():
        files[f"{base_prefix}/{rel_path}"] = content

    dev_prefix = f"apps/{inp.name}/envs/dev"
    for rel_path, content in _generate_wordpress_overlay_files(inp, "dev").items():
        files[f"{dev_prefix}/{rel_path}"] = content

    prod_prefix = f"apps/{inp.name}/envs/prod"
    for rel_path, content in _generate_wordpress_overlay_files(inp, "prod").items():
        files[f"{prod_prefix}/{rel_path}"] = content

    files[f"environments/dev/workloads/{inp.name}-app.yaml"] = _generate_application_manifest(
        app_name=f"{inp.name}-dev",
        project_name=inp.name,
        path=f"apps/{inp.name}/envs/dev",
        namespace=inp.namespace,
        repo_url=inp.workloads_repo_url,
    )
    files[f"environments/prod/workloads/{inp.name}-app.yaml"] = (
        "# Generated for future prod activation.\n"
        "# Keep environments/prod/workloads/kustomization.yaml empty while single-cluster safety mode is active.\n"
        + _generate_application_manifest(
            app_name=f"{inp.name}-prod",
            project_name=inp.name,
            path=f"apps/{inp.name}/envs/prod",
            namespace=inp.namespace,
            repo_url=inp.workloads_repo_url,
        )
    )

    return files



def _generate_wordpress_base_files(inp: ScaffoldServiceInput) -> dict[str, str]:
    db_secret_name = f"{inp.name}-wordpress-db"
    db_service_name = f"{inp.name}-mysql"
    resources = [
        "namespace.yaml",
        "serviceaccount.yaml",
        "wordpress-db-secret.enc.yaml",
        "persistentvolumeclaim.yaml",
        "deployment.yaml",
        "service.yaml",
        "ingress.yaml",
        "mysql-service.yaml",
        "mysql-statefulset.yaml",
        "networkpolicy-default-deny.yaml",
        "networkpolicy-allow-dns-egress.yaml",
        "networkpolicy-allow-ingress.yaml",
        "networkpolicy-allow-mysql-egress.yaml",
        "networkpolicy-allow-mysql-ingress.yaml",
    ]

    return {
        "kustomization.yaml": "\n".join(
            [
                "apiVersion: kustomize.config.k8s.io/v1beta1",
                "kind: Kustomization",
                "resources:",
                *[f"  - {resource}" for resource in resources],
            ]
        ) + "\n",
        "namespace.yaml": _render_template(
            """
            apiVersion: v1
            kind: Namespace
            metadata:
              name: {namespace}
              labels:
                app.kubernetes.io/name: {name}
            """,
            namespace=inp.namespace,
            name=inp.name,
        ),
        "serviceaccount.yaml": _render_template(
            """
            apiVersion: v1
            kind: ServiceAccount
            metadata:
              name: {name}
              namespace: {namespace}
              labels:
                app.kubernetes.io/name: {name}
                app.kubernetes.io/component: web
            """,
            name=inp.name,
            namespace=inp.namespace,
        ),
        "wordpress-db-secret.enc.yaml": _render_template(
            """
            # SOPS-encrypted Secret stub for WordPress + MySQL credentials.
            # Rotate by editing the placeholder values and re-encrypting with SOPS.
            # See docs/runbooks/sops-secrets.md for the full workflow.
            apiVersion: v1
            kind: Secret
            metadata:
              name: {db_secret_name}
              namespace: {namespace}
            type: Opaque
            stringData:
              WORDPRESS_DB_USER: ENC[AES256_GCM,data:xxx,iv:xxx,tag:xxx,type:str]
              WORDPRESS_DB_PASSWORD: ENC[AES256_GCM,data:xxx,iv:xxx,tag:xxx,type:str]
              WORDPRESS_DB_NAME: ENC[AES256_GCM,data:xxx,iv:xxx,tag:xxx,type:str]
              MYSQL_ROOT_PASSWORD: ENC[AES256_GCM,data:xxx,iv:xxx,tag:xxx,type:str]
            sops:
              kms: []
              gcp_kms: []
              azure_kv: []
              hc_vault: []
              age:
                - recipient: age1xxx
                  enc: |
                    -----BEGIN AGE ENCRYPTED FILE-----
                    ...
                    -----END AGE ENCRYPTED FILE-----
              lastmodified: "2026-03-25T00:00:00Z"
              mac: ENC[AES256_GCM,data:xxx,iv:xxx,tag:xxx,type:str]
              pgp: []
              encrypted_regex: ^(stringData|data)$
              version: 3.8.1
            """,
            db_secret_name=db_secret_name,
            namespace=inp.namespace,
        ),
        "persistentvolumeclaim.yaml": _render_template(
            """
            apiVersion: v1
            kind: PersistentVolumeClaim
            metadata:
              name: {name}-wp-content
              namespace: {namespace}
              labels:
                app.kubernetes.io/name: {name}
                app.kubernetes.io/component: web
            spec:
              accessModes:
                - ReadWriteOnce
              resources:
                requests:
                  storage: 10Gi
            """,
            name=inp.name,
            namespace=inp.namespace,
        ),
        "deployment.yaml": _render_template(
            """
            apiVersion: apps/v1
            kind: Deployment
            metadata:
              name: {name}
              namespace: {namespace}
              labels:
                app.kubernetes.io/name: {name}
                app.kubernetes.io/instance: {name}
                app.kubernetes.io/component: web
            spec:
              replicas: 1
              selector:
                matchLabels:
                  app.kubernetes.io/name: {name}
                  app.kubernetes.io/component: web
              template:
                metadata:
                  labels:
                    app.kubernetes.io/name: {name}
                    app.kubernetes.io/component: web
                spec:
                  serviceAccountName: {name}
                  containers:
                    - name: web
                      image: {image_repo}
                      imagePullPolicy: IfNotPresent
                      ports:
                        - name: http
                          containerPort: 80
                      env:
                        - name: WORDPRESS_DB_HOST
                          value: {db_service_name}:3306
                        - name: WORDPRESS_DB_USER
                          valueFrom:
                            secretKeyRef:
                              name: {db_secret_name}
                              key: WORDPRESS_DB_USER
                        - name: WORDPRESS_DB_PASSWORD
                          valueFrom:
                            secretKeyRef:
                              name: {db_secret_name}
                              key: WORDPRESS_DB_PASSWORD
                        - name: WORDPRESS_DB_NAME
                          valueFrom:
                            secretKeyRef:
                              name: {db_secret_name}
                              key: WORDPRESS_DB_NAME
                      readinessProbe:
                        httpGet:
                          path: /wp-login.php
                          port: http
                        initialDelaySeconds: 10
                        periodSeconds: 10
                      livenessProbe:
                        httpGet:
                          path: /wp-login.php
                          port: http
                        initialDelaySeconds: 20
                        periodSeconds: 20
                      volumeMounts:
                        - name: wp-content
                          mountPath: /var/www/html/wp-content
                      resources:
                        requests:
                          cpu: 100m
                          memory: 256Mi
                        limits:
                          cpu: 500m
                          memory: 512Mi
                  volumes:
                    - name: wp-content
                      persistentVolumeClaim:
                        claimName: {name}-wp-content
            """,
            name=inp.name,
            namespace=inp.namespace,
            image_repo=inp.image_repo,
            db_service_name=db_service_name,
            db_secret_name=db_secret_name,
        ),
        "service.yaml": _render_template(
            """
            apiVersion: v1
            kind: Service
            metadata:
              name: {name}
              namespace: {namespace}
              labels:
                app.kubernetes.io/name: {name}
                app.kubernetes.io/instance: {name}
                app.kubernetes.io/component: web
            spec:
              type: ClusterIP
              selector:
                app.kubernetes.io/name: {name}
                app.kubernetes.io/component: web
              ports:
                - name: http
                  port: 80
                  targetPort: http
            """,
            name=inp.name,
            namespace=inp.namespace,
        ),
        "ingress.yaml": _render_template(
            """
            apiVersion: networking.k8s.io/v1
            kind: Ingress
            metadata:
              name: {name}
              namespace: {namespace}
              labels:
                app.kubernetes.io/name: {name}
              annotations:
                traefik.ingress.kubernetes.io/router.entrypoints: web
            spec:
              ingressClassName: traefik
              rules:
                - host: {dev_host}
                  http:
                    paths:
                      - path: /
                        pathType: Prefix
                        backend:
                          service:
                            name: {name}
                            port:
                              number: 80
            """,
            name=inp.name,
            namespace=inp.namespace,
            dev_host=inp.dev_host,
        ),
        "mysql-service.yaml": _render_template(
            """
            apiVersion: v1
            kind: Service
            metadata:
              name: {db_service_name}
              namespace: {namespace}
              labels:
                app.kubernetes.io/name: {name}
                app.kubernetes.io/component: mysql
            spec:
              clusterIP: None
              selector:
                app.kubernetes.io/name: {name}
                app.kubernetes.io/component: mysql
              ports:
                - name: mysql
                  port: 3306
                  targetPort: mysql
            """,
            db_service_name=db_service_name,
            namespace=inp.namespace,
            name=inp.name,
        ),
        "mysql-statefulset.yaml": _render_template(
            """
            apiVersion: apps/v1
            kind: StatefulSet
            metadata:
              name: {db_service_name}
              namespace: {namespace}
              labels:
                app.kubernetes.io/name: {name}
                app.kubernetes.io/component: mysql
            spec:
              serviceName: {db_service_name}
              replicas: 1
              selector:
                matchLabels:
                  app.kubernetes.io/name: {name}
                  app.kubernetes.io/component: mysql
              template:
                metadata:
                  labels:
                    app.kubernetes.io/name: {name}
                    app.kubernetes.io/component: mysql
                spec:
                  containers:
                    - name: mysql
                      image: mysql:8.0
                      imagePullPolicy: IfNotPresent
                      ports:
                        - name: mysql
                          containerPort: 3306
                      env:
                        - name: MYSQL_ROOT_PASSWORD
                          valueFrom:
                            secretKeyRef:
                              name: {db_secret_name}
                              key: MYSQL_ROOT_PASSWORD
                        - name: MYSQL_USER
                          valueFrom:
                            secretKeyRef:
                              name: {db_secret_name}
                              key: WORDPRESS_DB_USER
                        - name: MYSQL_PASSWORD
                          valueFrom:
                            secretKeyRef:
                              name: {db_secret_name}
                              key: WORDPRESS_DB_PASSWORD
                        - name: MYSQL_DATABASE
                          valueFrom:
                            secretKeyRef:
                              name: {db_secret_name}
                              key: WORDPRESS_DB_NAME
                      startupProbe:
                        exec:
                          command:
                            - mysqladmin
                            - ping
                            - -h
                            - 127.0.0.1
                        periodSeconds: 2
                        timeoutSeconds: 2
                        failureThreshold: 30
                      readinessProbe:
                        exec:
                          command:
                            - mysqladmin
                            - ping
                            - -h
                            - 127.0.0.1
                        periodSeconds: 5
                        timeoutSeconds: 3
                        failureThreshold: 6
                      volumeMounts:
                        - name: mysql-data
                          mountPath: /var/lib/mysql
                      resources:
                        requests:
                          cpu: 100m
                          memory: 256Mi
                        limits:
                          cpu: 500m
                          memory: 512Mi
              volumeClaimTemplates:
                - metadata:
                    name: mysql-data
                  spec:
                    accessModes:
                      - ReadWriteOnce
                    resources:
                      requests:
                        storage: 10Gi
            """,
            db_service_name=db_service_name,
            namespace=inp.namespace,
            name=inp.name,
            db_secret_name=db_secret_name,
        ),
        "networkpolicy-default-deny.yaml": _render_template(
            """
            apiVersion: networking.k8s.io/v1
            kind: NetworkPolicy
            metadata:
              name: default-deny
              namespace: {namespace}
            spec:
              podSelector: {{}}
              policyTypes:
                - Ingress
                - Egress
            """,
            namespace=inp.namespace,
        ),
        "networkpolicy-allow-dns-egress.yaml": _render_template(
            """
            apiVersion: networking.k8s.io/v1
            kind: NetworkPolicy
            metadata:
              name: allow-dns-egress
              namespace: {namespace}
            spec:
              podSelector: {{}}
              policyTypes:
                - Egress
              egress:
                - to:
                    - namespaceSelector:
                        matchLabels:
                          kubernetes.io/metadata.name: kube-system
                  ports:
                    - protocol: UDP
                      port: 53
                    - protocol: TCP
                      port: 53
            """,
            namespace=inp.namespace,
        ),
        "networkpolicy-allow-ingress.yaml": _render_template(
            """
            apiVersion: networking.k8s.io/v1
            kind: NetworkPolicy
            metadata:
              name: allow-ingress-from-traefik
              namespace: {namespace}
            spec:
              podSelector:
                matchLabels:
                  app.kubernetes.io/name: {name}
                  app.kubernetes.io/component: web
              policyTypes:
                - Ingress
              ingress:
                - from:
                    - namespaceSelector:
                        matchLabels:
                          kubernetes.io/metadata.name: kube-system
                  ports:
                    - protocol: TCP
                      port: 80
            """,
            namespace=inp.namespace,
            name=inp.name,
        ),
        "networkpolicy-allow-mysql-egress.yaml": _render_template(
            """
            apiVersion: networking.k8s.io/v1
            kind: NetworkPolicy
            metadata:
              name: allow-mysql-egress
              namespace: {namespace}
            spec:
              podSelector:
                matchLabels:
                  app.kubernetes.io/name: {name}
                  app.kubernetes.io/component: web
              policyTypes:
                - Egress
              egress:
                - to:
                    - podSelector:
                        matchLabels:
                          app.kubernetes.io/name: {name}
                          app.kubernetes.io/component: mysql
                  ports:
                    - protocol: TCP
                      port: 3306
            """,
            namespace=inp.namespace,
            name=inp.name,
        ),
        "networkpolicy-allow-mysql-ingress.yaml": _render_template(
            """
            apiVersion: networking.k8s.io/v1
            kind: NetworkPolicy
            metadata:
              name: allow-mysql-ingress
              namespace: {namespace}
            spec:
              podSelector:
                matchLabels:
                  app.kubernetes.io/name: {name}
                  app.kubernetes.io/component: mysql
              policyTypes:
                - Ingress
              ingress:
                - from:
                    - podSelector:
                        matchLabels:
                          app.kubernetes.io/name: {name}
                          app.kubernetes.io/component: web
                  ports:
                    - protocol: TCP
                      port: 3306
            """,
            namespace=inp.namespace,
            name=inp.name,
        ),
    }


def _generate_wordpress_overlay_files(inp: ScaffoldServiceInput, env_name: str) -> dict[str, str]:
    files = {
        "kustomization.yaml": _render_template(
            """
            apiVersion: kustomize.config.k8s.io/v1beta1
            kind: Kustomization
            resources:
              - ../../base
            commonLabels:
              homelab.env: {env_name}
            patches:
              - path: patch-deployment.yaml
            """,
            env_name=env_name,
        ),
        "patch-deployment.yaml": _render_template(
            """
            apiVersion: apps/v1
            kind: Deployment
            metadata:
              name: {name}
              namespace: {namespace}
            spec:
              replicas: {replicas}
              template:
                spec:
                  containers:
                    - name: web
                      resources:
                        requests:
                          cpu: {cpu_request}
                          memory: {memory_request}
                        limits:
                          cpu: {cpu_limit}
                          memory: {memory_limit}
            """,
            name=inp.name,
            namespace=inp.namespace,
            replicas="2" if env_name == "prod" else "1",
            cpu_request="200m" if env_name == "prod" else "100m",
            memory_request="512Mi" if env_name == "prod" else "256Mi",
            cpu_limit="1000m" if env_name == "prod" else "500m",
            memory_limit="1Gi" if env_name == "prod" else "512Mi",
        ),
    }

    if env_name == "prod":
        files["kustomization.yaml"] = _render_template(
            """
            apiVersion: kustomize.config.k8s.io/v1beta1
            kind: Kustomization
            resources:
              - ../../base
            commonLabels:
              homelab.env: prod
            patches:
              - path: patch-deployment.yaml
              - path: patch-ingress.yaml
            """
        )
        files["patch-ingress.yaml"] = _render_template(
            """
            apiVersion: networking.k8s.io/v1
            kind: Ingress
            metadata:
              name: {name}
              namespace: {namespace}
            spec:
              rules:
                - host: {prod_host}
            """,
            name=inp.name,
            namespace=inp.namespace,
            prod_host=inp.prod_host,
        )

    return files

# Shared app-template generator: service account, deployment, service, ingress,
# network policies, and optional ServiceMonitor for app-native observability.
def _generate_base_files(
    inp: ScaffoldServiceInput,
    container_port: int,
    service_port: int,
    container_name: str,
    health_path: str,
    readiness_path: str,
    observability_mode: str,
) -> dict[str, str]:
    serviceaccount = _render_template(
        """
        apiVersion: v1
        kind: ServiceAccount
        metadata:
          name: {name}
          namespace: {namespace}
          labels:
            app.kubernetes.io/name: {name}
        """,
        name=inp.name,
        namespace=inp.namespace,
    )

    probes = _render_template(
        """
        readinessProbe:
          httpGet:
            path: {readiness_path}
            port: http
          initialDelaySeconds: 5
          periodSeconds: 10
        livenessProbe:
          httpGet:
            path: {health_path}
            port: http
          initialDelaySeconds: 10
          periodSeconds: 20
        """,
        readiness_path=readiness_path,
        health_path=health_path,
    )

    deployment_lines = [
        "apiVersion: apps/v1",
        "kind: Deployment",
        "metadata:",
        f"  name: {inp.name}",
        f"  namespace: {inp.namespace}",
        "  labels:",
        f"    app.kubernetes.io/name: {inp.name}",
        f"    app.kubernetes.io/instance: {inp.name}",
        "spec:",
        "  replicas: 1",
        "  selector:",
        "    matchLabels:",
        f"      app.kubernetes.io/name: {inp.name}",
        "  template:",
        "    metadata:",
        "      labels:",
        f"        app.kubernetes.io/name: {inp.name}",
        "    spec:",
        f"      serviceAccountName: {inp.name}",
        "      containers:",
        f"        - name: {container_name}",
        f"          image: {inp.image_repo}:0.1.0",
        "          imagePullPolicy: IfNotPresent",
        "          ports:",
        "            - name: http",
        f"              containerPort: {container_port}",
        "          env:",
        "            - name: APP_ENV",
        "              value: base",
    ]

    deployment_lines.extend(_indent_block(probes.rstrip(), 10).splitlines())
    deployment_lines.extend(
        [
            "          resources:",
            "            requests:",
            "              cpu: 50m",
            "              memory: 64Mi",
            "            limits:",
            "              cpu: 300m",
            "              memory: 256Mi",
        ]
    )

    resources = [
        "namespace.yaml",
        "serviceaccount.yaml",
        "deployment.yaml",
        "service.yaml",
        "ingress.yaml",
        "networkpolicy-default-deny.yaml",
        "networkpolicy-allow-dns-egress.yaml",
        "networkpolicy-allow-ingress.yaml",
    ]
    if observability_mode == "app-native":
        resources.insert(4, "servicemonitor.yaml")

    files: dict[str, str] = {
        "kustomization.yaml": (
            "apiVersion: kustomize.config.k8s.io/v1beta1\n"
            "kind: Kustomization\n"
            "resources:\n"
            + "".join(f"  - {r}\n" for r in resources)
        ),
        "namespace.yaml": _render_template(
            """
            apiVersion: v1
            kind: Namespace
            metadata:
              name: {namespace}
              labels:
                app.kubernetes.io/name: {name}
            """,
            namespace=inp.namespace,
            name=inp.name,
        ),
        "serviceaccount.yaml": serviceaccount,
        "deployment.yaml": "\n".join(deployment_lines) + "\n",
        "service.yaml": _render_template(
            """
            apiVersion: v1
            kind: Service
            metadata:
              name: {name}
              namespace: {namespace}
              labels:
                app.kubernetes.io/name: {name}
                app.kubernetes.io/instance: {name}
            spec:
              type: ClusterIP
              selector:
                app.kubernetes.io/name: {name}
              ports:
                - name: http
                  port: {service_port}
                  targetPort: http
            """,
            name=inp.name,
            namespace=inp.namespace,
            service_port=str(service_port),
        ),
        "ingress.yaml": _render_template(
            """
            apiVersion: networking.k8s.io/v1
            kind: Ingress
            metadata:
              name: {name}
              namespace: {namespace}
              labels:
                app.kubernetes.io/name: {name}
              annotations:
                traefik.ingress.kubernetes.io/router.entrypoints: web
            spec:
              ingressClassName: traefik
              rules:
                - host: {dev_host}
                  http:
                    paths:
                      - path: /
                        pathType: Prefix
                        backend:
                          service:
                            name: {name}
                            port:
                              number: {service_port}
            """,
            name=inp.name,
            namespace=inp.namespace,
            dev_host=inp.dev_host,
            service_port=str(service_port),
        ),
        "networkpolicy-default-deny.yaml": _render_template(
            """
            apiVersion: networking.k8s.io/v1
            kind: NetworkPolicy
            metadata:
              name: default-deny
              namespace: {namespace}
            spec:
              podSelector: {{}}
              policyTypes:
                - Ingress
                - Egress
            """,
            namespace=inp.namespace,
        ),
        "networkpolicy-allow-dns-egress.yaml": _render_template(
            """
            apiVersion: networking.k8s.io/v1
            kind: NetworkPolicy
            metadata:
              name: allow-dns-egress
              namespace: {namespace}
            spec:
              podSelector: {{}}
              policyTypes:
                - Egress
              egress:
                - to:
                    - namespaceSelector:
                        matchLabels:
                          kubernetes.io/metadata.name: kube-system
                  ports:
                    - protocol: UDP
                      port: 53
                    - protocol: TCP
                      port: 53
            """,
            namespace=inp.namespace,
        ),
        "networkpolicy-allow-ingress.yaml": _render_template(
            """
            apiVersion: networking.k8s.io/v1
            kind: NetworkPolicy
            metadata:
              name: allow-ingress-from-traefik
              namespace: {namespace}
            spec:
              podSelector:
                matchLabels:
                  app.kubernetes.io/name: {name}
              policyTypes:
                - Ingress
              ingress:
                - from:
                    - namespaceSelector:
                        matchLabels:
                          kubernetes.io/metadata.name: kube-system
                  ports:
                    - protocol: TCP
                      port: {container_port}
            """,
            namespace=inp.namespace,
            name=inp.name,
            container_port=str(container_port),
        ),
    }

    if observability_mode == "app-native":
        files["servicemonitor.yaml"] = _render_template(
            """
            apiVersion: monitoring.coreos.com/v1
            kind: ServiceMonitor
            metadata:
              name: {name}
              namespace: {namespace}
              labels:
                release: kube-prometheus-stack
            spec:
              selector:
                matchLabels:
                  app.kubernetes.io/name: {name}
              namespaceSelector:
                matchNames:
                  - {namespace}
              endpoints:
                - port: http
                  path: /metrics
                  interval: 30s
            """,
            name=inp.name,
            namespace=inp.namespace,
        )

    return files


# Overlays keep environment-specific scaling, labels, and prod ingress host tweaks
# separate from the shared base manifests.
def _generate_overlay_files(
    inp: ScaffoldServiceInput,
    env_name: str,
    container_name: str,
    service_port: int,
) -> dict[str, str]:
    if env_name == "prod":
        replicas, cpu_req, mem_req, cpu_lim, mem_lim, image_tag = (
            "2",
            "100m",
            "128Mi",
            "500m",
            "512Mi",
            "0.1.0",
        )
    else:
        replicas, cpu_req, mem_req, cpu_lim, mem_lim, image_tag = (
            "1",
            "50m",
            "64Mi",
            "300m",
            "256Mi",
            "0.1.0",
        )

    if env_name == "prod":
        kustomization = _render_template(
            """
            apiVersion: kustomize.config.k8s.io/v1beta1
            kind: Kustomization
            resources:
              - ../../base
            commonLabels:
              homelab.env: prod
            patches:
              - path: patch-deployment.yaml
              - path: patch-ingress.yaml
            """
        )
    else:
        kustomization = _render_template(
            """
            apiVersion: kustomize.config.k8s.io/v1beta1
            kind: Kustomization
            resources:
              - ../../base
            commonLabels:
              homelab.env: {env_name}
            patches:
              - path: patch-deployment.yaml
            """,
            env_name=env_name,
        )

    patch_deployment = _render_template(
        """
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: {name}
          namespace: {namespace}
        spec:
          replicas: {replicas}
          template:
            spec:
              containers:
                - name: {container_name}
                  image: {image_repo}:{image_tag}
                  env:
                    - name: APP_ENV
                      value: {env_name}
                  resources:
                    requests:
                      cpu: {cpu_req}
                      memory: {mem_req}
                    limits:
                      cpu: {cpu_lim}
                      memory: {mem_lim}
        """,
        name=inp.name,
        namespace=inp.namespace,
        replicas=replicas,
        container_name=container_name,
        image_repo=inp.image_repo,
        image_tag=image_tag,
        env_name=env_name,
        cpu_req=cpu_req,
        mem_req=mem_req,
        cpu_lim=cpu_lim,
        mem_lim=mem_lim,
    )

    files: dict[str, str] = {
        "kustomization.yaml": kustomization,
        "patch-deployment.yaml": patch_deployment,
    }

    if env_name == "prod":
        ingress_host = inp.prod_host or inp.public_host
        files["patch-ingress.yaml"] = _render_template(
            """
            apiVersion: networking.k8s.io/v1
            kind: Ingress
            metadata:
              name: {name}
              namespace: {namespace}
            spec:
              rules:
                - host: {ingress_host}
            """,
            name=inp.name,
            namespace=inp.namespace,
            ingress_host=ingress_host,
        )

    return files


# ---------------------------------------------------------------------------
# Bundle topology generators (frontend + backend in one namespace)
# ---------------------------------------------------------------------------


def generate_gitops_bundle_files(inp: ScaffoldBundleInput) -> dict[str, str]:
    """Return all NEW files for a multi-service project bundle."""
    validate_service_name(inp.name)

    ft = _TEMPLATES[inp.frontend_template]
    bt = _TEMPLATES[inp.backend_template]

    frontend_port = int(ft["container_port"])  # type: ignore[arg-type]
    frontend_service_port = int(ft["service_port"])  # type: ignore[arg-type]
    frontend_container = str(ft["container_name"])
    frontend_health = str(ft["health_path"])
    frontend_readiness = str(ft["readiness_path"])
    frontend_obs = str(ft["default_observability_mode"])

    backend_port = int(bt["container_port"])  # type: ignore[arg-type]
    backend_service_port = int(bt["service_port"])  # type: ignore[arg-type]
    backend_container = str(bt["container_name"])
    backend_health = str(bt["health_path"])
    backend_readiness = str(bt["readiness_path"])
    backend_obs = str(bt["default_observability_mode"])

    files: dict[str, str] = {}
    base_prefix = f"apps/{inp.name}/base"

    # --- Base kustomization resources ---
    resources = [
        "namespace.yaml",
        "serviceaccount-frontend.yaml",
        "serviceaccount-backend.yaml",
        "frontend-deployment.yaml",
        "frontend-service.yaml",
        "backend-deployment.yaml",
        "backend-service.yaml",
        "ingress.yaml",
        "networkpolicy-default-deny.yaml",
        "networkpolicy-allow-dns-egress.yaml",
        "networkpolicy-allow-ingress.yaml",
        "networkpolicy-allow-frontend-to-backend.yaml",
        "networkpolicy-allow-backend-from-frontend.yaml",
    ]
    if frontend_obs == "app-native":
        resources.append("servicemonitor-frontend.yaml")
    if backend_obs == "app-native":
        resources.append("servicemonitor-backend.yaml")

    has_db = inp.topology == "frontend-backend-db" and inp.db_template
    if has_db:
        resources.extend([
            "db-credentials-secret.yaml",
            "db-statefulset.yaml",
            "db-service.yaml",
            "networkpolicy-allow-backend-to-db.yaml",
            "networkpolicy-allow-db-from-backend.yaml",
        ])

    files[f"{base_prefix}/kustomization.yaml"] = (
        "apiVersion: kustomize.config.k8s.io/v1beta1\n"
        "kind: Kustomization\n"
        "resources:\n"
        + "".join(f"  - {r}\n" for r in resources)
    )

    # --- Namespace ---
    files[f"{base_prefix}/namespace.yaml"] = _render_template(
        """
        apiVersion: v1
        kind: Namespace
        metadata:
          name: {namespace}
          labels:
            app.kubernetes.io/name: {name}
        """,
        namespace=inp.namespace,
        name=inp.name,
    )

    # --- Service accounts ---
    for component in ("frontend", "backend"):
        files[f"{base_prefix}/serviceaccount-{component}.yaml"] = _render_template(
            """
            apiVersion: v1
            kind: ServiceAccount
            metadata:
              name: {name}-{component}
              namespace: {namespace}
              labels:
                app.kubernetes.io/name: {name}
                app.kubernetes.io/component: {component}
            """,
            name=inp.name,
            namespace=inp.namespace,
            component=component,
        )

    # --- Frontend deployment ---
    frontend_probes = _render_template(
        """
        readinessProbe:
          httpGet:
            path: {readiness_path}
            port: http
          initialDelaySeconds: 5
          periodSeconds: 10
        livenessProbe:
          httpGet:
            path: {health_path}
            port: http
          initialDelaySeconds: 10
          periodSeconds: 20
        """,
        readiness_path=frontend_readiness,
        health_path=frontend_health,
    )
    frontend_deploy_lines = [
        "apiVersion: apps/v1",
        "kind: Deployment",
        "metadata:",
        f"  name: {inp.name}-frontend",
        f"  namespace: {inp.namespace}",
        "  labels:",
        f"    app.kubernetes.io/name: {inp.name}",
        f"    app.kubernetes.io/instance: {inp.name}",
        f"    app.kubernetes.io/component: frontend",
        "spec:",
        "  replicas: 1",
        "  selector:",
        "    matchLabels:",
        f"      app.kubernetes.io/name: {inp.name}",
        f"      app.kubernetes.io/component: frontend",
        "  template:",
        "    metadata:",
        "      labels:",
        f"        app.kubernetes.io/name: {inp.name}",
        f"        app.kubernetes.io/component: frontend",
        "    spec:",
        f"      serviceAccountName: {inp.name}-frontend",
        "      containers:",
        f"        - name: {frontend_container}",
        f"          image: {inp.frontend_image_repo}:0.1.0",
        "          imagePullPolicy: IfNotPresent",
        "          ports:",
        "            - name: http",
        f"              containerPort: {frontend_port}",
        "          env:",
        "            - name: APP_ENV",
        "              value: base",
        f"            - name: BACKEND_URL",
        f"              value: http://{inp.name}-backend:{backend_service_port}",
    ]
    frontend_deploy_lines.extend(_indent_block(frontend_probes.rstrip(), 10).splitlines())
    frontend_deploy_lines.extend([
        "          resources:",
        "            requests:",
        "              cpu: 50m",
        "              memory: 64Mi",
        "            limits:",
        "              cpu: 300m",
        "              memory: 256Mi",
    ])
    files[f"{base_prefix}/frontend-deployment.yaml"] = "\n".join(frontend_deploy_lines) + "\n"

    # --- Frontend service ---
    files[f"{base_prefix}/frontend-service.yaml"] = _render_template(
        """
        apiVersion: v1
        kind: Service
        metadata:
          name: {name}-frontend
          namespace: {namespace}
          labels:
            app.kubernetes.io/name: {name}
            app.kubernetes.io/instance: {name}
            app.kubernetes.io/component: frontend
        spec:
          type: ClusterIP
          selector:
            app.kubernetes.io/name: {name}
            app.kubernetes.io/component: frontend
          ports:
            - name: http
              port: {service_port}
              targetPort: http
        """,
        name=inp.name,
        namespace=inp.namespace,
        service_port=str(frontend_service_port),
    )

    # --- Backend deployment ---
    backend_probes = _render_template(
        """
        readinessProbe:
          httpGet:
            path: {readiness_path}
            port: http
          initialDelaySeconds: 5
          periodSeconds: 10
        livenessProbe:
          httpGet:
            path: {health_path}
            port: http
          initialDelaySeconds: 10
          periodSeconds: 20
        """,
        readiness_path=backend_readiness,
        health_path=backend_health,
    )
    backend_env_lines = [
        "          env:",
        "            - name: APP_ENV",
        "              value: base",
    ]
    if has_db:
        dt = _TEMPLATES[inp.db_template]  # type: ignore[index]
        db_port = int(dt["db_port"])  # type: ignore[arg-type]
        db_engine = str(dt["db_engine"])
        if db_engine == "postgres":
            backend_env_lines.extend([
                "            - name: DATABASE_URL",
                f"              value: postgresql://$(DB_USER):$(DB_PASSWORD)@{inp.name}-db:{db_port}/$(DB_NAME)",
                "            - name: DB_USER",
                "              valueFrom:",
                "                secretKeyRef:",
                f"                  name: {inp.name}-db-credentials",
                "                  key: POSTGRES_USER",
                "            - name: DB_PASSWORD",
                "              valueFrom:",
                "                secretKeyRef:",
                f"                  name: {inp.name}-db-credentials",
                "                  key: POSTGRES_PASSWORD",
                "            - name: DB_NAME",
                "              valueFrom:",
                "                secretKeyRef:",
                f"                  name: {inp.name}-db-credentials",
                "                  key: POSTGRES_DB",
            ])
        else:
            backend_env_lines.extend([
                "            - name: DATABASE_URL",
                f"              value: mysql://$(DB_USER):$(DB_PASSWORD)@{inp.name}-db:{db_port}/$(DB_NAME)",
                "            - name: DB_USER",
                "              valueFrom:",
                "                secretKeyRef:",
                f"                  name: {inp.name}-db-credentials",
                "                  key: MYSQL_USER",
                "            - name: DB_PASSWORD",
                "              valueFrom:",
                "                secretKeyRef:",
                f"                  name: {inp.name}-db-credentials",
                "                  key: MYSQL_PASSWORD",
                "            - name: DB_NAME",
                "              valueFrom:",
                "                secretKeyRef:",
                f"                  name: {inp.name}-db-credentials",
                "                  key: MYSQL_DATABASE",
            ])

    backend_deploy_lines = [
        "apiVersion: apps/v1",
        "kind: Deployment",
        "metadata:",
        f"  name: {inp.name}-backend",
        f"  namespace: {inp.namespace}",
        "  labels:",
        f"    app.kubernetes.io/name: {inp.name}",
        f"    app.kubernetes.io/instance: {inp.name}",
        f"    app.kubernetes.io/component: backend",
        "spec:",
        "  replicas: 1",
        "  selector:",
        "    matchLabels:",
        f"      app.kubernetes.io/name: {inp.name}",
        f"      app.kubernetes.io/component: backend",
        "  template:",
        "    metadata:",
        "      labels:",
        f"        app.kubernetes.io/name: {inp.name}",
        f"        app.kubernetes.io/component: backend",
        "    spec:",
        f"      serviceAccountName: {inp.name}-backend",
        "      containers:",
        f"        - name: {backend_container}",
        f"          image: {inp.backend_image_repo}:0.1.0",
        "          imagePullPolicy: IfNotPresent",
        "          ports:",
        "            - name: http",
        f"              containerPort: {backend_port}",
    ]
    backend_deploy_lines.extend(backend_env_lines)
    backend_deploy_lines.extend(_indent_block(backend_probes.rstrip(), 10).splitlines())
    backend_deploy_lines.extend([
        "          resources:",
        "            requests:",
        "              cpu: 50m",
        "              memory: 64Mi",
        "            limits:",
        "              cpu: 300m",
        "              memory: 256Mi",
    ])
    files[f"{base_prefix}/backend-deployment.yaml"] = "\n".join(backend_deploy_lines) + "\n"

    # --- Backend service ---
    files[f"{base_prefix}/backend-service.yaml"] = _render_template(
        """
        apiVersion: v1
        kind: Service
        metadata:
          name: {name}-backend
          namespace: {namespace}
          labels:
            app.kubernetes.io/name: {name}
            app.kubernetes.io/instance: {name}
            app.kubernetes.io/component: backend
        spec:
          type: ClusterIP
          selector:
            app.kubernetes.io/name: {name}
            app.kubernetes.io/component: backend
          ports:
            - name: http
              port: {service_port}
              targetPort: http
        """,
        name=inp.name,
        namespace=inp.namespace,
        service_port=str(backend_service_port),
    )

    # --- Ingress (routes to frontend) ---
    files[f"{base_prefix}/ingress.yaml"] = _render_template(
        """
        apiVersion: networking.k8s.io/v1
        kind: Ingress
        metadata:
          name: {name}
          namespace: {namespace}
          labels:
            app.kubernetes.io/name: {name}
          annotations:
            traefik.ingress.kubernetes.io/router.entrypoints: web
        spec:
          ingressClassName: traefik
          rules:
            - host: {dev_host}
              http:
                paths:
                  - path: /
                    pathType: Prefix
                    backend:
                      service:
                        name: {name}-frontend
                        port:
                          number: {frontend_service_port}
        """,
        name=inp.name,
        namespace=inp.namespace,
        dev_host=inp.dev_host,
        frontend_service_port=str(frontend_service_port),
    )

    # --- Network policies ---
    files[f"{base_prefix}/networkpolicy-default-deny.yaml"] = _render_template(
        """
        apiVersion: networking.k8s.io/v1
        kind: NetworkPolicy
        metadata:
          name: default-deny
          namespace: {namespace}
        spec:
          podSelector: {{}}
          policyTypes:
            - Ingress
            - Egress
        """,
        namespace=inp.namespace,
    )
    files[f"{base_prefix}/networkpolicy-allow-dns-egress.yaml"] = _render_template(
        """
        apiVersion: networking.k8s.io/v1
        kind: NetworkPolicy
        metadata:
          name: allow-dns-egress
          namespace: {namespace}
        spec:
          podSelector: {{}}
          policyTypes:
            - Egress
          egress:
            - to:
                - namespaceSelector:
                    matchLabels:
                      kubernetes.io/metadata.name: kube-system
              ports:
                - protocol: UDP
                  port: 53
                - protocol: TCP
                  port: 53
        """,
        namespace=inp.namespace,
    )
    files[f"{base_prefix}/networkpolicy-allow-ingress.yaml"] = _render_template(
        """
        apiVersion: networking.k8s.io/v1
        kind: NetworkPolicy
        metadata:
          name: allow-ingress-from-traefik
          namespace: {namespace}
        spec:
          podSelector:
            matchLabels:
              app.kubernetes.io/name: {name}
              app.kubernetes.io/component: frontend
          policyTypes:
            - Ingress
          ingress:
            - from:
                - namespaceSelector:
                    matchLabels:
                      kubernetes.io/metadata.name: kube-system
              ports:
                - protocol: TCP
                  port: {frontend_port}
        """,
        namespace=inp.namespace,
        name=inp.name,
        frontend_port=str(frontend_port),
    )
    files[f"{base_prefix}/networkpolicy-allow-frontend-to-backend.yaml"] = _render_template(
        """
        apiVersion: networking.k8s.io/v1
        kind: NetworkPolicy
        metadata:
          name: allow-frontend-to-backend-egress
          namespace: {namespace}
        spec:
          podSelector:
            matchLabels:
              app.kubernetes.io/name: {name}
              app.kubernetes.io/component: frontend
          policyTypes:
            - Egress
          egress:
            - to:
                - podSelector:
                    matchLabels:
                      app.kubernetes.io/name: {name}
                      app.kubernetes.io/component: backend
              ports:
                - protocol: TCP
                  port: {backend_port}
        """,
        namespace=inp.namespace,
        name=inp.name,
        backend_port=str(backend_port),
    )
    files[f"{base_prefix}/networkpolicy-allow-backend-from-frontend.yaml"] = _render_template(
        """
        apiVersion: networking.k8s.io/v1
        kind: NetworkPolicy
        metadata:
          name: allow-backend-from-frontend-ingress
          namespace: {namespace}
        spec:
          podSelector:
            matchLabels:
              app.kubernetes.io/name: {name}
              app.kubernetes.io/component: backend
          policyTypes:
            - Ingress
          ingress:
            - from:
                - podSelector:
                    matchLabels:
                      app.kubernetes.io/name: {name}
                      app.kubernetes.io/component: frontend
              ports:
                - protocol: TCP
                  port: {backend_port}
        """,
        namespace=inp.namespace,
        name=inp.name,
        backend_port=str(backend_port),
    )

    # --- ServiceMonitors ---
    if frontend_obs == "app-native":
        files[f"{base_prefix}/servicemonitor-frontend.yaml"] = _render_template(
            """
            apiVersion: monitoring.coreos.com/v1
            kind: ServiceMonitor
            metadata:
              name: {name}-frontend
              namespace: {namespace}
              labels:
                release: kube-prometheus-stack
            spec:
              selector:
                matchLabels:
                  app.kubernetes.io/name: {name}
                  app.kubernetes.io/component: frontend
              namespaceSelector:
                matchNames:
                  - {namespace}
              endpoints:
                - port: http
                  path: /metrics
                  interval: 30s
            """,
            name=inp.name,
            namespace=inp.namespace,
        )
    if backend_obs == "app-native":
        files[f"{base_prefix}/servicemonitor-backend.yaml"] = _render_template(
            """
            apiVersion: monitoring.coreos.com/v1
            kind: ServiceMonitor
            metadata:
              name: {name}-backend
              namespace: {namespace}
              labels:
                release: kube-prometheus-stack
            spec:
              selector:
                matchLabels:
                  app.kubernetes.io/name: {name}
                  app.kubernetes.io/component: backend
              namespaceSelector:
                matchNames:
                  - {namespace}
              endpoints:
                - port: http
                  path: /metrics
                  interval: 30s
            """,
            name=inp.name,
            namespace=inp.namespace,
        )

    # --- Database resources (frontend-backend-db topology) ---
    if has_db:
        _generate_bundle_db_files(files, base_prefix, inp)

    # --- Overlays ---
    for env_name in ("dev", "prod"):
        env_prefix = f"apps/{inp.name}/envs/{env_name}"
        for rel_path, content in _generate_bundle_overlay_files(
            inp, env_name, frontend_container, backend_container, has_db,
        ).items():
            files[f"{env_prefix}/{rel_path}"] = content

    # --- ArgoCD Application manifests ---
    files[f"environments/dev/workloads/{inp.name}-app.yaml"] = _generate_application_manifest(
        app_name=f"{inp.name}-dev",
        project_name=inp.name,
        path=f"apps/{inp.name}/envs/dev",
        namespace=inp.namespace,
        repo_url=inp.workloads_repo_url,
    )
    files[f"environments/prod/workloads/{inp.name}-app.yaml"] = (
        "# Generated for future prod activation.\n"
        "# Keep environments/prod/workloads/kustomization.yaml empty while single-cluster safety mode is active.\n"
        + _generate_application_manifest(
            app_name=f"{inp.name}-prod",
            project_name=inp.name,
            path=f"apps/{inp.name}/envs/prod",
            namespace=inp.namespace,
            repo_url=inp.workloads_repo_url,
        )
    )

    return files


def _generate_bundle_db_files(
    files: dict[str, str],
    base_prefix: str,
    inp: ScaffoldBundleInput,
) -> None:
    """Add database StatefulSet, Service, Secret, and network policies to a bundle."""
    dt = _TEMPLATES[inp.db_template]  # type: ignore[index]
    db_port = int(dt["db_port"])  # type: ignore[arg-type]
    db_image = str(dt["db_image"])
    db_engine = str(dt["db_engine"])
    is_postgres = db_engine == "postgres"

    if is_postgres:
        secret_data = (
            f"  POSTGRES_USER: {inp.db_username}\n"
            f"  POSTGRES_PASSWORD: {inp.db_password}\n"
            f"  POSTGRES_DB: {inp.db_name}\n"
        )
        container_name = "postgres"
        port_name = "postgres"
        mount_path = "/var/lib/postgresql/data"
        mount_sub = "\n          subPath: postgres"
        env_keys = ["POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"]
    else:
        secret_data = (
            f"  MYSQL_ROOT_PASSWORD: {inp.db_password}\n"
            f"  MYSQL_USER: {inp.db_username}\n"
            f"  MYSQL_PASSWORD: {inp.db_password}\n"
            f"  MYSQL_DATABASE: {inp.db_name}\n"
        )
        container_name = "mysql"
        port_name = "mysql"
        mount_path = "/var/lib/mysql"
        mount_sub = ""
        env_keys = ["MYSQL_ROOT_PASSWORD", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DATABASE"]

    secret_name = f"{inp.name}-db-credentials"

    files[f"{base_prefix}/db-credentials-secret.yaml"] = _render_template(
        """
        # SOPS-encrypted Secret stub — fill values then run: sops -e -i db-credentials-secret.yaml
        apiVersion: v1
        kind: Secret
        metadata:
          name: {secret_name}
          namespace: {namespace}
          labels:
            app.kubernetes.io/name: {name}
            app.kubernetes.io/component: database
        type: Opaque
        stringData:
        """,
        secret_name=secret_name,
        namespace=inp.namespace,
        name=inp.name,
    ).rstrip() + "\n" + secret_data + "sops:\n"

    env_block = "".join(
        f"            - name: {key}\n"
        f"              valueFrom:\n"
        f"                secretKeyRef:\n"
        f"                  name: {secret_name}\n"
        f"                  key: {key}\n"
        for key in env_keys
    )

    files[f"{base_prefix}/db-statefulset.yaml"] = (
        "apiVersion: apps/v1\n"
        "kind: StatefulSet\n"
        "metadata:\n"
        f"  name: {inp.name}-db\n"
        f"  namespace: {inp.namespace}\n"
        "  labels:\n"
        f"    app.kubernetes.io/name: {inp.name}\n"
        f"    app.kubernetes.io/instance: {inp.name}\n"
        f"    app.kubernetes.io/component: database\n"
        "spec:\n"
        f"  serviceName: {inp.name}-db\n"
        "  replicas: 1\n"
        "  selector:\n"
        "    matchLabels:\n"
        f"      app.kubernetes.io/name: {inp.name}\n"
        f"      app.kubernetes.io/component: database\n"
        "  template:\n"
        "    metadata:\n"
        "      labels:\n"
        f"        app.kubernetes.io/name: {inp.name}\n"
        f"        app.kubernetes.io/component: database\n"
        "    spec:\n"
        f"      serviceAccountName: {inp.name}-backend\n"
        "      containers:\n"
        f"        - name: {container_name}\n"
        f"          image: {db_image}\n"
        "          imagePullPolicy: IfNotPresent\n"
        "          ports:\n"
        f"            - containerPort: {db_port}\n"
        f"              name: {port_name}\n"
        "          env:\n"
        f"{env_block}"
        "          volumeMounts:\n"
        "            - name: data\n"
        f"              mountPath: {mount_path}{mount_sub}\n"
        "          resources:\n"
        "            requests:\n"
        "              cpu: 100m\n"
        "              memory: 256Mi\n"
        "            limits:\n"
        "              cpu: 500m\n"
        "              memory: 512Mi\n"
        "  volumeClaimTemplates:\n"
        "    - metadata:\n"
        "        name: data\n"
        "      spec:\n"
        "        accessModes:\n"
        "          - ReadWriteOnce\n"
        "        resources:\n"
        "          requests:\n"
        "            storage: 10Gi\n"
    )

    files[f"{base_prefix}/db-service.yaml"] = _render_template(
        """
        apiVersion: v1
        kind: Service
        metadata:
          name: {name}-db
          namespace: {namespace}
          labels:
            app.kubernetes.io/name: {name}
            app.kubernetes.io/instance: {name}
            app.kubernetes.io/component: database
        spec:
          type: ClusterIP
          selector:
            app.kubernetes.io/name: {name}
            app.kubernetes.io/component: database
          ports:
            - name: {port_name}
              port: {db_port}
              targetPort: {db_port}
        """,
        name=inp.name,
        namespace=inp.namespace,
        port_name=port_name,
        db_port=str(db_port),
    )

    files[f"{base_prefix}/networkpolicy-allow-backend-to-db.yaml"] = _render_template(
        """
        apiVersion: networking.k8s.io/v1
        kind: NetworkPolicy
        metadata:
          name: allow-backend-to-db-egress
          namespace: {namespace}
        spec:
          podSelector:
            matchLabels:
              app.kubernetes.io/name: {name}
              app.kubernetes.io/component: backend
          policyTypes:
            - Egress
          egress:
            - to:
                - podSelector:
                    matchLabels:
                      app.kubernetes.io/name: {name}
                      app.kubernetes.io/component: database
              ports:
                - protocol: TCP
                  port: {db_port}
        """,
        namespace=inp.namespace,
        name=inp.name,
        db_port=str(db_port),
    )
    files[f"{base_prefix}/networkpolicy-allow-db-from-backend.yaml"] = _render_template(
        """
        apiVersion: networking.k8s.io/v1
        kind: NetworkPolicy
        metadata:
          name: allow-db-from-backend-ingress
          namespace: {namespace}
        spec:
          podSelector:
            matchLabels:
              app.kubernetes.io/name: {name}
              app.kubernetes.io/component: database
          policyTypes:
            - Ingress
          ingress:
            - from:
                - podSelector:
                    matchLabels:
                      app.kubernetes.io/name: {name}
                      app.kubernetes.io/component: backend
              ports:
                - protocol: TCP
                  port: {db_port}
        """,
        namespace=inp.namespace,
        name=inp.name,
        db_port=str(db_port),
    )


def _generate_bundle_overlay_files(
    inp: ScaffoldBundleInput,
    env_name: str,
    frontend_container: str,
    backend_container: str,
    has_db: bool,
) -> dict[str, str]:
    """Generate per-env overlay files for a bundle project."""
    if env_name == "prod":
        replicas, cpu_req, mem_req, cpu_lim, mem_lim = "2", "100m", "128Mi", "500m", "512Mi"
    else:
        replicas, cpu_req, mem_req, cpu_lim, mem_lim = "1", "50m", "64Mi", "300m", "256Mi"

    patches = [
        "  - path: patch-frontend-deployment.yaml",
        "  - path: patch-backend-deployment.yaml",
    ]
    if env_name == "prod":
        patches.append("  - path: patch-ingress.yaml")

    kustomization = (
        "apiVersion: kustomize.config.k8s.io/v1beta1\n"
        "kind: Kustomization\n"
        "resources:\n"
        "  - ../../base\n"
        "commonLabels:\n"
        f"  homelab.env: {env_name}\n"
        "patches:\n"
        + "\n".join(patches) + "\n"
    )

    files: dict[str, str] = {"kustomization.yaml": kustomization}

    for component, container_name, image_repo in [
        ("frontend", frontend_container, inp.frontend_image_repo),
        ("backend", backend_container, inp.backend_image_repo),
    ]:
        files[f"patch-{component}-deployment.yaml"] = _render_template(
            """
            apiVersion: apps/v1
            kind: Deployment
            metadata:
              name: {name}-{component}
              namespace: {namespace}
            spec:
              replicas: {replicas}
              template:
                spec:
                  containers:
                    - name: {container_name}
                      image: {image_repo}:0.1.0
                      env:
                        - name: APP_ENV
                          value: {env_name}
                      resources:
                        requests:
                          cpu: {cpu_req}
                          memory: {mem_req}
                        limits:
                          cpu: {cpu_lim}
                          memory: {mem_lim}
            """,
            name=inp.name,
            component=component,
            namespace=inp.namespace,
            replicas=replicas,
            container_name=container_name,
            image_repo=image_repo,
            env_name=env_name,
            cpu_req=cpu_req,
            mem_req=mem_req,
            cpu_lim=cpu_lim,
            mem_lim=mem_lim,
        )

    if env_name == "prod":
        ingress_host = inp.prod_host or inp.public_host
        files["patch-ingress.yaml"] = _render_template(
            """
            apiVersion: networking.k8s.io/v1
            kind: Ingress
            metadata:
              name: {name}
              namespace: {namespace}
            spec:
              rules:
                - host: {ingress_host}
            """,
            name=inp.name,
            namespace=inp.namespace,
            ingress_host=ingress_host,
        )

    return files


def build_catalog_bundle_entries(existing_services_yaml: str, inp: ScaffoldBundleInput) -> str:
    """Append catalog entries for a bundle project to services.yaml."""
    for suffix in ("frontend", "backend"):
        service_id = f"{inp.name}-{suffix}"
        if f"service_id: {service_id}\n" in existing_services_yaml:
            raise ScaffoldError(
                f"Service {service_id!r} already exists in services.yaml.",
                status_code=409,
            )
    if "services:" not in existing_services_yaml:
        raise ScaffoldError("Expected top-level services: list in services.yaml.", status_code=502)

    display_name_base = " ".join(word.capitalize() for word in inp.name.split("-"))
    repo_url = inp.repo_url or inp.workloads_repo_url
    result = existing_services_yaml
    if not result.endswith("\n"):
        result += "\n"

    for component, template_key, image_repo in [
        ("frontend", inp.frontend_template, inp.frontend_image_repo),
        ("backend", inp.backend_template, inp.backend_image_repo),
    ]:
        obs_mode = str(_TEMPLATES[template_key]["default_observability_mode"])
        service_id = f"{inp.name}-{component}"
        display_name = f"{display_name_base} {component.capitalize()}"
        prod_public_host_line = (
            f"        public_host: {_yaml_string(inp.public_host)}\n" if inp.public_host and component == "frontend" else ""
        )
        entry = (
            f"  - service_id: {service_id}\n"
            f"    project_id: {inp.name}\n"
            f"    name: {_yaml_string(display_name)}\n"
            f"    owner: {_yaml_string(inp.owner or inp.owner_email)}\n"
            f"    owner_email: {_yaml_string(inp.owner_email)}\n"
            f"    repo_url: {_yaml_string(repo_url)}\n"
            f"    runbook_url: {_yaml_string(repo_url)}\n"
            f"    description: {_yaml_string(inp.description + f' ({component})')}\n"
            "    observability:\n"
            f"      mode: {obs_mode}\n"
            "    envs:\n"
            "      - name: dev\n"
            f"        namespace: {inp.namespace}\n"
            f"        app_label: {service_id}\n"
            f"        argo_app: {inp.name}-dev\n"
            "      - name: prod\n"
            f"        namespace: {inp.namespace}\n"
            f"        app_label: {service_id}\n"
            f"        argo_app: {inp.name}-prod\n"
            f"{prod_public_host_line}"
        )
        result += entry

    return result


# Argo Application manifests are generated alongside workload files so the PR is
# enough to register the service in GitOps without any manual bootstrap step.
def _generate_application_manifest(
    *,
    app_name: str,
    project_name: str,
    path: str,
    namespace: str,
    repo_url: str,
) -> str:
    return _render_template(
        """
        apiVersion: argoproj.io/v1alpha1
        kind: Application
        metadata:
          name: {app_name}
          namespace: argocd
        spec:
          project: {project_name}
          source:
            repoURL: {repo_url}
            targetRevision: main
            path: {path}
          destination:
            server: https://kubernetes.default.svc
            namespace: {namespace}
          syncPolicy:
            automated:
              prune: true
              selfHeal: true
            syncOptions:
              - CreateNamespace=true
        """,
        app_name=app_name,
        project_name=project_name,
        path=path,
        namespace=namespace,
        repo_url=repo_url,
    )


def _generate_appproject_manifest(
    *,
    name: str,
    namespace: str,
    description: str,
    repo_url: str,
) -> str:
    return _render_template(
        """
        apiVersion: argoproj.io/v1alpha1
        kind: AppProject
        metadata:
          name: {name}
          namespace: argocd
        spec:
          description: {description}
          sourceRepos:
            - {repo_url}
          destinations:
            - namespace: {namespace}
              server: https://kubernetes.default.svc
          clusterResourceWhitelist:
            - group: ""
              kind: Namespace
        """,
        name=name,
        description=description,
        repo_url=repo_url,
        namespace=namespace,
    )
