from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass
from typing import Literal


SERVICE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,62}$")

_TEMPLATES: dict[str, dict[str, object]] = {
    "python-fastapi": {
        "container_port": 8000,
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
    template: Literal["python-fastapi", "static-nginx"]
    namespace: str
    dev_host: str
    prod_host: str
    workloads_repo_url: str


def validate_service_name(name: str) -> None:
    if not SERVICE_NAME_PATTERN.match(name):
        raise ScaffoldError(
            f"Service name {name!r} must match ^[a-z][a-z0-9-]{{1,62}}$. "
            "Use lowercase letters, digits, and hyphens only.",
            status_code=422,
        )


def generate_gitops_new_files(inp: ScaffoldServiceInput) -> dict[str, str]:
    """Return all NEW files keyed by path relative to the gitops root."""
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


def update_kustomization_resources(existing: str, resource_name: str) -> str:
    """Insert resource_name into the resources: block (sorted, deduplicated)."""
    lines = existing.splitlines()
    has_resources = any(line.strip() == "resources:" for line in lines)
    if not has_resources:
        raise ScaffoldError(
            "Expected resources: block in environments/dev/workloads/kustomization.yaml.",
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


def build_catalog_entry_addition(existing_services_yaml: str, inp: ScaffoldServiceInput) -> str:
    """Append a catalog entry to services.yaml content and return the new content."""
    if f"service_id: {inp.name}\n" in existing_services_yaml:
        raise ScaffoldError(
            f"Service {inp.name!r} already exists in services.yaml. Choose a different name.",
            status_code=409,
        )
    if "services:" not in existing_services_yaml:
        raise ScaffoldError("Expected top-level services: list in services.yaml.", status_code=502)

    observability_mode = str(_TEMPLATES[inp.template]["default_observability_mode"])
    display_name = " ".join(word.capitalize() for word in inp.name.split("-"))
    entry = (
        f"  - service_id: {inp.name}\n"
        f"    name: {_yaml_string(display_name)}\n"
        f"    owner: {_yaml_string(inp.owner or inp.owner_email)}\n"
        f"    owner_email: {_yaml_string(inp.owner_email)}\n"
        f"    repo_url: {_yaml_string(inp.repo_url)}\n"
        f"    runbook_url: {_yaml_string(inp.repo_url)}\n"
        f"    description: {_yaml_string(inp.description)}\n"
        "    observability:\n"
        f"      mode: {observability_mode}\n"
        "    envs:\n"
        "      - name: dev\n"
        f"        namespace: {inp.namespace}\n"
        f"        argo_app: {inp.name}-dev\n"
        "      - name: prod\n"
        f"        namespace: {inp.namespace}\n"
        f"        argo_app: {inp.name}-prod\n"
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
