from __future__ import annotations

import textwrap


def dedent(value: str) -> str:
    return textwrap.dedent(value).strip() + "\n"


def render_template(template: str, **values: str) -> str:
    return dedent(template.format(**values))


def yaml_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def indent_block(value: str, spaces: int) -> str:
    prefix = " " * spaces
    return "\n".join(f"{prefix}{line}" if line else line for line in value.splitlines())


def generate_application_manifest(
    *,
    app_name: str,
    project_name: str,
    path: str,
    namespace: str,
    repo_url: str,
) -> str:
    return render_template(
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


def generate_appproject_manifest(
    *,
    name: str,
    namespace: str,
    description: str,
    repo_url: str,
) -> str:
    return render_template(
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
