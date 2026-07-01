from __future__ import annotations

import secrets
from collections.abc import Callable

from app.scaffold.models import ScaffoldError, ScaffoldServiceInput, TEMPLATES
from app.scaffold.render import (
    generate_application_manifest,
    generate_appproject_manifest,
    indent_block,
    render_template,
    yaml_string,
)


WordpressSecretEncrypter = Callable[[str, str], str]


def _render_public_ingress_patch(*, name: str, namespace: str, host: str, service_port: int | None = None) -> str:
    if service_port is not None:
        return render_template(
            """
            apiVersion: networking.k8s.io/v1
            kind: Ingress
            metadata:
              name: {name}
              namespace: {namespace}
              annotations:
                cert-manager.io/cluster-issuer: letsencrypt-http01
                traefik.ingress.kubernetes.io/router.entrypoints: websecure
                traefik.ingress.kubernetes.io/router.tls: "true"
            spec:
              tls:
                - hosts:
                    - {host}
                  secretName: {name}-tls
              rules:
                - host: {host}
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
            name=name,
            namespace=namespace,
            host=host,
            service_port=str(service_port),
        )

    return render_template(
        """
        apiVersion: networking.k8s.io/v1
        kind: Ingress
        metadata:
          name: {name}
          namespace: {namespace}
          annotations:
            cert-manager.io/cluster-issuer: letsencrypt-http01
            traefik.ingress.kubernetes.io/router.entrypoints: websecure
            traefik.ingress.kubernetes.io/router.tls: "true"
        spec:
          tls:
            - hosts:
                - {host}
              secretName: {name}-tls
          rules:
            - host: {host}
        """,
        name=name,
        namespace=namespace,
        host=host,
    )


def generate_gitops_new_files(
    inp: ScaffoldServiceInput,
    *,
    wordpress_secret_encrypter: WordpressSecretEncrypter | None = None,
) -> dict[str, str]:
    """Return all NEW files keyed by path relative to the gitops root."""
    if inp.template in ("postgres", "mysql"):
        return _generate_database_gitops_files(inp)
    if inp.template == "wordpress":
        return _generate_wordpress_gitops_files(
            inp,
            wordpress_secret_encrypter=wordpress_secret_encrypter,
        )

    template = TEMPLATES[inp.template]
    container_port = int(template["container_port"])  # type: ignore[arg-type]
    service_port = int(template["service_port"])  # type: ignore[arg-type]
    container_name = str(template["container_name"])
    health_path = str(template["health_path"])
    readiness_path = str(template["readiness_path"])
    observability_mode = str(template["default_observability_mode"])

    files: dict[str, str] = {}

    base_prefix = f"apps/{inp.name}/base"
    for rel_path, content in _generate_base_files(
        inp,
        container_port,
        service_port,
        container_name,
        health_path,
        readiness_path,
        observability_mode,
    ).items():
        files[f"{base_prefix}/{rel_path}"] = content

    dev_prefix = f"apps/{inp.name}/envs/dev"
    for rel_path, content in _generate_overlay_files(inp, "dev", container_name, service_port).items():
        files[f"{dev_prefix}/{rel_path}"] = content

    prod_prefix = f"apps/{inp.name}/envs/prod"
    for rel_path, content in _generate_overlay_files(inp, "prod", container_name, service_port).items():
        files[f"{prod_prefix}/{rel_path}"] = content

    files[f"environments/dev/workloads/{inp.name}-app.yaml"] = generate_application_manifest(
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
        + generate_application_manifest(
            app_name=f"{inp.name}-prod",
            project_name=inp.name,
            path=f"apps/{inp.name}/envs/prod",
            namespace=inp.namespace,
            repo_url=inp.workloads_repo_url,
        )
    )

    return files


def build_catalog_entry_addition(existing_services_yaml: str, inp: ScaffoldServiceInput) -> str:
    """Append a catalog entry to services.yaml content and return the new content."""
    if f"service_id: {inp.name}\n" in existing_services_yaml:
        raise ScaffoldError(
            f"Service {inp.name!r} already exists in services.yaml. Choose a different name.",
            status_code=409,
        )
    if "services:" not in existing_services_yaml:
        raise ScaffoldError("Expected top-level services: list in services.yaml.", status_code=502)

    observability_mode = str(TEMPLATES[inp.template]["default_observability_mode"])
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
        prod_public_host_line = (
            f"        public_host: {yaml_string(inp.public_host)}\n" if inp.public_host else ""
        )
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
        f"    name: {yaml_string(display_name)}\n"
        f"    owner: {yaml_string(inp.owner or inp.owner_email)}\n"
        f"    owner_email: {yaml_string(inp.owner_email)}\n"
        f"    repo_url: {yaml_string(repo_url)}\n"
        f"    runbook_url: {yaml_string(repo_url)}\n"
        f"    description: {yaml_string(inp.description)}\n"
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

    appproject = generate_appproject_manifest(
        name=inp.name,
        namespace=inp.namespace,
        description=f"{inp.description} resources in {inp.namespace} namespace only",
        repo_url=inp.workloads_repo_url,
    )
    suffix = "" if existing_project_yaml.endswith("\n") else "\n"
    return existing_project_yaml + suffix + "---\n" + appproject


def _generate_database_gitops_files(inp: ScaffoldServiceInput) -> dict[str, str]:
    files: dict[str, str] = {}

    base_prefix = f"apps/{inp.name}/base"
    for rel_path, content in _generate_database_base_files(inp).items():
        files[f"{base_prefix}/{rel_path}"] = content

    env_prefix = f"apps/{inp.name}/envs/prod"
    files[f"{env_prefix}/kustomization.yaml"] = render_template(
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

    files[f"environments/prod/workloads/{inp.name}-app.yaml"] = generate_application_manifest(
        app_name=f"{inp.name}-prod",
        project_name=inp.name,
        path=f"apps/{inp.name}/envs/prod",
        namespace=inp.namespace,
        repo_url=inp.workloads_repo_url,
    )

    return files


def _generate_database_base_files(inp: ScaffoldServiceInput) -> dict[str, str]:
    template = TEMPLATES[inp.template]
    db_port = int(template["db_port"])  # type: ignore[arg-type]
    db_image = str(template["db_image"])
    db_engine = str(template["db_engine"])
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
        secret_content = render_template(
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
        statefulset_content = render_template(
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
    else:
        secret_content = render_template(
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
        statefulset_content = render_template(
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
            + "".join(f"  - {resource}\n" for resource in resources)
        ),
        "namespace.yaml": render_template(
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
        "serviceaccount.yaml": render_template(
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
        "service.yaml": render_template(
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
        "networkpolicy-default-deny.yaml": render_template(
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
        "networkpolicy-allow-dns-egress.yaml": render_template(
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
        "networkpolicy-allow-ingress.yaml": render_template(
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


def _generate_wordpress_gitops_files(
    inp: ScaffoldServiceInput,
    *,
    wordpress_secret_encrypter: WordpressSecretEncrypter | None = None,
) -> dict[str, str]:
    files: dict[str, str] = {}

    base_prefix = f"apps/{inp.name}/base"
    for rel_path, content in _generate_wordpress_base_files(inp).items():
        files[f"{base_prefix}/{rel_path}"] = content

    dev_prefix = f"apps/{inp.name}/envs/dev"
    for rel_path, content in _generate_wordpress_overlay_files(
        inp,
        "dev",
        wordpress_secret_encrypter=wordpress_secret_encrypter,
    ).items():
        files[f"{dev_prefix}/{rel_path}"] = content

    prod_prefix = f"apps/{inp.name}/envs/prod"
    for rel_path, content in _generate_wordpress_overlay_files(
        inp,
        "prod",
        wordpress_secret_encrypter=wordpress_secret_encrypter,
    ).items():
        files[f"{prod_prefix}/{rel_path}"] = content

    files[f"environments/dev/workloads/{inp.name}-app.yaml"] = generate_application_manifest(
        app_name=f"{inp.name}-dev",
        project_name=inp.name,
        path=f"apps/{inp.name}/envs/dev",
        namespace=inp.namespace,
        repo_url=inp.workloads_repo_url,
    )
    files[f"environments/prod/workloads/{inp.name}-app.yaml"] = (
        "# Generated for future prod activation.\n"
        "# Keep environments/prod/workloads/kustomization.yaml empty while single-cluster safety mode is active.\n"
        + generate_application_manifest(
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
        )
        + "\n",
        "namespace.yaml": render_template(
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
        "serviceaccount.yaml": render_template(
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
        "persistentvolumeclaim.yaml": render_template(
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
        "deployment.yaml": render_template(
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
        "service.yaml": render_template(
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
        "ingress.yaml": render_template(
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
        "mysql-service.yaml": render_template(
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
        "mysql-statefulset.yaml": render_template(
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
        "networkpolicy-default-deny.yaml": render_template(
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
        "networkpolicy-allow-dns-egress.yaml": render_template(
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
        "networkpolicy-allow-ingress.yaml": render_template(
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
        "networkpolicy-allow-mysql-egress.yaml": render_template(
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
        "networkpolicy-allow-mysql-ingress.yaml": render_template(
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


def _generate_wordpress_overlay_files(
    inp: ScaffoldServiceInput,
    env_name: str,
    *,
    wordpress_secret_encrypter: WordpressSecretEncrypter | None = None,
) -> dict[str, str]:
    db_secret_name = f"{inp.name}-wordpress-db"
    target_secret_path = f"apps/{inp.name}/envs/{env_name}/wordpress-db-secret.enc.yaml"
    encrypted_secret = (
        wordpress_secret_encrypter(
            target_secret_path,
            _render_wordpress_db_secret_manifest(inp, env_name),
        )
        if wordpress_secret_encrypter is not None
        else _render_wordpress_db_secret_stub(db_secret_name, inp.namespace)
    )
    files = {
        "kustomization.yaml": render_template(
            """
            apiVersion: kustomize.config.k8s.io/v1beta1
            kind: Kustomization
            resources:
              - ../../base
            generators:
              - wordpress-db-secret-generator.yaml
            commonLabels:
              homelab.env: {env_name}
            patches:
              - path: patch-deployment.yaml
            """,
            env_name=env_name,
        ),
        "wordpress-db-secret-generator.yaml": render_template(
            """
            apiVersion: viaduct.ai/v1
            kind: ksops
            metadata:
              name: wordpress-db-secret-generator
              annotations:
                config.kubernetes.io/function: |
                  exec:
                    path: ksops
            files:
              - wordpress-db-secret.enc.yaml
            """
        ),
        "wordpress-db-secret.enc.yaml": encrypted_secret,
        "patch-deployment.yaml": render_template(
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
        files["kustomization.yaml"] = render_template(
            """
            apiVersion: kustomize.config.k8s.io/v1beta1
            kind: Kustomization
            resources:
              - ../../base
            generators:
              - wordpress-db-secret-generator.yaml
            commonLabels:
              homelab.env: prod
            patches:
              - path: patch-deployment.yaml
              - path: patch-ingress.yaml
            """
        )
        files["patch-ingress.yaml"] = render_template(
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


def _render_wordpress_db_secret_manifest(inp: ScaffoldServiceInput, env_name: str) -> str:
    db_secret_name = f"{inp.name}-wordpress-db"
    wordpress_password = _generate_wordpress_secret_value(env_name)
    mysql_root_password = _generate_wordpress_secret_value(f"{env_name}-root")
    return render_template(
        """
        apiVersion: v1
        kind: Secret
        metadata:
          name: {db_secret_name}
          namespace: {namespace}
        type: Opaque
        stringData:
          WORDPRESS_DB_USER: appuser
          WORDPRESS_DB_PASSWORD: {wordpress_password}
          WORDPRESS_DB_NAME: appdb
          MYSQL_ROOT_PASSWORD: {mysql_root_password}
        """,
        db_secret_name=db_secret_name,
        namespace=inp.namespace,
        wordpress_password=wordpress_password,
        mysql_root_password=mysql_root_password,
    )


def _generate_wordpress_secret_value(suffix: str) -> str:
    token = secrets.token_urlsafe(24).replace("-", "a").replace("_", "b")
    return f"wp-{suffix}-{token}"


def _render_wordpress_db_secret_stub(db_secret_name: str, namespace: str) -> str:
    return render_template(
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
        namespace=namespace,
    )


def _generate_base_files(
    inp: ScaffoldServiceInput,
    container_port: int,
    service_port: int,
    container_name: str,
    health_path: str,
    readiness_path: str,
    observability_mode: str,
) -> dict[str, str]:
    serviceaccount = render_template(
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

    probes = render_template(
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

    base_image_tag = "latest"
    base_image_pull_policy = "Always"

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
        f"          image: {inp.image_repo}:{base_image_tag}",
        f"          imagePullPolicy: {base_image_pull_policy}",
        "          ports:",
        "            - name: http",
        f"              containerPort: {container_port}",
        "          env:",
        "            - name: APP_ENV",
        "              value: base",
    ]

    deployment_lines.extend(indent_block(probes.rstrip(), 10).splitlines())
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
            + "".join(f"  - {resource}\n" for resource in resources)
        ),
        "namespace.yaml": render_template(
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
        "service.yaml": render_template(
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
        "ingress.yaml": render_template(
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
        "networkpolicy-default-deny.yaml": render_template(
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
        "networkpolicy-allow-dns-egress.yaml": render_template(
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
        "networkpolicy-allow-ingress.yaml": render_template(
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
        files["servicemonitor.yaml"] = render_template(
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
            "latest",
        )
    else:
        replicas, cpu_req, mem_req, cpu_lim, mem_lim, image_tag = (
            "1",
            "50m",
            "64Mi",
            "300m",
            "256Mi",
            "latest",
        )
    image_pull_policy = "Always" if image_tag == "latest" else "IfNotPresent"

    ingress_host = inp.prod_host if env_name == "prod" and inp.prod_host else inp.public_host
    files = {
        "kustomization.yaml": render_template(
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
        "patch-deployment.yaml": render_template(
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
                      imagePullPolicy: {image_pull_policy}
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
            image_pull_policy=image_pull_policy,
            env_name=env_name,
            cpu_req=cpu_req,
            mem_req=mem_req,
            cpu_lim=cpu_lim,
            mem_lim=mem_lim,
        ),
    }

    if env_name == "dev" and inp.public_host:
        files["kustomization.yaml"] = render_template(
            """
            apiVersion: kustomize.config.k8s.io/v1beta1
            kind: Kustomization
            resources:
              - ../../base
            commonLabels:
              homelab.env: dev
            patches:
              - path: patch-deployment.yaml
              - path: patch-ingress.yaml
            """
        )
        files["patch-ingress.yaml"] = _render_public_ingress_patch(
            name=inp.name,
            namespace=inp.namespace,
            host=inp.public_host,
            service_port=service_port,
        )

    if env_name == "prod":
        files["kustomization.yaml"] = render_template(
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
        files["patch-ingress.yaml"] = render_template(
            """
            apiVersion: networking.k8s.io/v1
            kind: Ingress
            metadata:
              name: {name}
              namespace: {namespace}
            spec:
              rules:
                - host: {ingress_host}
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
            ingress_host=ingress_host,
            service_port=str(service_port),
        )

    return files
