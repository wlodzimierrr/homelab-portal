from __future__ import annotations

from app.scaffold.models import ScaffoldBundleInput, ScaffoldError, TEMPLATES, validate_service_name
from app.scaffold.render import generate_application_manifest, indent_block, render_template, yaml_string


def _render_public_ingress_patch(*, name: str, namespace: str, host: str) -> str:
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


def _render_public_http_ingress(*, name: str, namespace: str, host: str) -> str:
    return render_template(
        """
        apiVersion: networking.k8s.io/v1
        kind: Ingress
        metadata:
          name: {name}-http
          namespace: {namespace}
          annotations:
            traefik.ingress.kubernetes.io/router.entrypoints: web
        spec:
          ingressClassName: traefik
          rules:
            - host: {host}
              http:
                paths:
                  - path: /
                    pathType: Prefix
                    backend:
                      service:
                        name: {name}-frontend
                        port:
                          number: 80
        """,
        name=name,
        namespace=namespace,
        host=host,
    )


def _render_acme_http01_solver_network_policy(*, namespace: str) -> str:
    return render_template(
        """
        apiVersion: networking.k8s.io/v1
        kind: NetworkPolicy
        metadata:
          name: allow-acme-http01-solver
          namespace: {namespace}
        spec:
          podSelector:
            matchLabels:
              acme.cert-manager.io/http01-solver: "true"
          policyTypes:
            - Ingress
          ingress:
            - from:
                - namespaceSelector:
                    matchLabels:
                      kubernetes.io/metadata.name: kube-system
              ports:
                - protocol: TCP
                  port: 8089
        """,
        namespace=namespace,
    )


def generate_gitops_bundle_files(inp: ScaffoldBundleInput) -> dict[str, str]:
    """Return all NEW files for a multi-service project bundle."""
    validate_service_name(inp.name)

    frontend_template = TEMPLATES[inp.frontend_template]
    backend_template = TEMPLATES[inp.backend_template]

    frontend_port = int(frontend_template["container_port"])  # type: ignore[arg-type]
    frontend_service_port = int(frontend_template["service_port"])  # type: ignore[arg-type]
    frontend_container = str(frontend_template["container_name"])
    frontend_health = str(frontend_template["health_path"])
    frontend_readiness = str(frontend_template["readiness_path"])
    frontend_obs = str(frontend_template["default_observability_mode"])

    backend_port = int(backend_template["container_port"])  # type: ignore[arg-type]
    backend_service_port = int(backend_template["service_port"])  # type: ignore[arg-type]
    backend_container = str(backend_template["container_name"])
    backend_health = str(backend_template["health_path"])
    backend_readiness = str(backend_template["readiness_path"])
    backend_obs = str(backend_template["default_observability_mode"])

    files: dict[str, str] = {}
    base_prefix = f"apps/{inp.name}/base"

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
        resources.extend(
            [
                "db-credentials-secret.yaml",
                "db-statefulset.yaml",
                "db-service.yaml",
                "networkpolicy-allow-backend-to-db.yaml",
                "networkpolicy-allow-db-from-backend.yaml",
            ]
        )

    files[f"{base_prefix}/kustomization.yaml"] = (
        "apiVersion: kustomize.config.k8s.io/v1beta1\n"
        "kind: Kustomization\n"
        "resources:\n"
        + "".join(f"  - {resource}\n" for resource in resources)
    )

    files[f"{base_prefix}/namespace.yaml"] = render_template(
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

    for component in ("frontend", "backend"):
        files[f"{base_prefix}/serviceaccount-{component}.yaml"] = render_template(
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

    frontend_probes = render_template(
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
        "    app.kubernetes.io/component: frontend",
        "spec:",
        "  replicas: 1",
        "  selector:",
        "    matchLabels:",
        f"      app.kubernetes.io/name: {inp.name}",
        "      app.kubernetes.io/component: frontend",
        "  template:",
        "    metadata:",
        "      labels:",
        f"        app.kubernetes.io/name: {inp.name}",
        "        app.kubernetes.io/component: frontend",
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
        "            - name: BACKEND_URL",
        f"              value: http://{inp.name}-backend:{backend_service_port}",
    ]
    frontend_deploy_lines.extend(indent_block(frontend_probes.rstrip(), 10).splitlines())
    frontend_deploy_lines.extend(
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
    files[f"{base_prefix}/frontend-deployment.yaml"] = "\n".join(frontend_deploy_lines) + "\n"

    files[f"{base_prefix}/frontend-service.yaml"] = render_template(
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

    backend_probes = render_template(
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
        db_template = TEMPLATES[inp.db_template]  # type: ignore[index]
        db_port = int(db_template["db_port"])  # type: ignore[arg-type]
        db_engine = str(db_template["db_engine"])
        if db_engine == "postgres":
            backend_env_lines.extend(
                [
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
                ]
            )
        else:
            backend_env_lines.extend(
                [
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
                ]
            )

    backend_deploy_lines = [
        "apiVersion: apps/v1",
        "kind: Deployment",
        "metadata:",
        f"  name: {inp.name}-backend",
        f"  namespace: {inp.namespace}",
        "  labels:",
        f"    app.kubernetes.io/name: {inp.name}",
        f"    app.kubernetes.io/instance: {inp.name}",
        "    app.kubernetes.io/component: backend",
        "spec:",
        "  replicas: 1",
        "  selector:",
        "    matchLabels:",
        f"      app.kubernetes.io/name: {inp.name}",
        "      app.kubernetes.io/component: backend",
        "  template:",
        "    metadata:",
        "      labels:",
        f"        app.kubernetes.io/name: {inp.name}",
        "        app.kubernetes.io/component: backend",
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
    backend_deploy_lines.extend(indent_block(backend_probes.rstrip(), 10).splitlines())
    backend_deploy_lines.extend(
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
    files[f"{base_prefix}/backend-deployment.yaml"] = "\n".join(backend_deploy_lines) + "\n"

    files[f"{base_prefix}/backend-service.yaml"] = render_template(
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

    files[f"{base_prefix}/ingress.yaml"] = render_template(
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

    files[f"{base_prefix}/networkpolicy-default-deny.yaml"] = render_template(
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
    files[f"{base_prefix}/networkpolicy-allow-dns-egress.yaml"] = render_template(
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
    files[f"{base_prefix}/networkpolicy-allow-ingress.yaml"] = render_template(
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
    files[f"{base_prefix}/networkpolicy-allow-frontend-to-backend.yaml"] = render_template(
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
    files[f"{base_prefix}/networkpolicy-allow-backend-from-frontend.yaml"] = render_template(
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

    if frontend_obs == "app-native":
        files[f"{base_prefix}/servicemonitor-frontend.yaml"] = render_template(
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
        files[f"{base_prefix}/servicemonitor-backend.yaml"] = render_template(
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

    if has_db:
        _generate_bundle_db_files(files, base_prefix, inp)

    for env_name in ("dev", "prod"):
        env_prefix = f"apps/{inp.name}/envs/{env_name}"
        for rel_path, content in _generate_bundle_overlay_files(
            inp, env_name, frontend_container, backend_container, bool(has_db)
        ).items():
            files[f"{env_prefix}/{rel_path}"] = content

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


def _generate_bundle_db_files(files: dict[str, str], base_prefix: str, inp: ScaffoldBundleInput) -> None:
    db_template = TEMPLATES[inp.db_template]  # type: ignore[index]
    db_port = int(db_template["db_port"])  # type: ignore[arg-type]
    db_image = str(db_template["db_image"])
    db_engine = str(db_template["db_engine"])
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

    files[f"{base_prefix}/db-credentials-secret.yaml"] = (
        render_template(
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
        ).rstrip()
        + "\n"
        + secret_data
        + "sops:\n"
    )

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
        "    app.kubernetes.io/component: database\n"
        "spec:\n"
        f"  serviceName: {inp.name}-db\n"
        "  replicas: 1\n"
        "  selector:\n"
        "    matchLabels:\n"
        f"      app.kubernetes.io/name: {inp.name}\n"
        "      app.kubernetes.io/component: database\n"
        "  template:\n"
        "    metadata:\n"
        "      labels:\n"
        f"        app.kubernetes.io/name: {inp.name}\n"
        "        app.kubernetes.io/component: database\n"
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

    files[f"{base_prefix}/db-service.yaml"] = render_template(
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

    files[f"{base_prefix}/networkpolicy-allow-backend-to-db.yaml"] = render_template(
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
    files[f"{base_prefix}/networkpolicy-allow-db-from-backend.yaml"] = render_template(
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
    if env_name == "prod":
        replicas, cpu_req, mem_req, cpu_lim, mem_lim = "2", "100m", "128Mi", "500m", "512Mi"
    else:
        replicas, cpu_req, mem_req, cpu_lim, mem_lim = "1", "50m", "64Mi", "300m", "256Mi"

    patches = [
        "  - path: patch-frontend-deployment.yaml",
        "  - path: patch-backend-deployment.yaml",
    ]
    if env_name == "dev" and inp.public_host:
        patches.append("  - path: patch-ingress.yaml")
    elif env_name == "prod":
        patches.append("  - path: patch-ingress.yaml")

    label_block = (
        "labels:\n"
        "  - pairs:\n"
        f"      homelab.env: {env_name}\n"
        "    includeSelectors: false\n"
        "    includeTemplates: true\n"
        if env_name == "dev" and inp.public_host
        else f"commonLabels:\n  homelab.env: {env_name}\n"
    )
    kustomization = (
        "apiVersion: kustomize.config.k8s.io/v1beta1\n"
        "kind: Kustomization\n"
        "resources:\n"
        "  - ../../base\n"
        + ("  - ingress-http.yaml\n  - networkpolicy-allow-acme-http01-solver.yaml\n" if env_name == "dev" and inp.public_host else "")
        + label_block
        + "patches:\n"
        + "\n".join(patches)
        + "\n"
    )

    files: dict[str, str] = {"kustomization.yaml": kustomization}

    for component, container_name, image_repo in [
        ("frontend", frontend_container, inp.frontend_image_repo),
        ("backend", backend_container, inp.backend_image_repo),
    ]:
        files[f"patch-{component}-deployment.yaml"] = render_template(
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

    if env_name == "dev" and inp.public_host:
        files["patch-ingress.yaml"] = _render_public_ingress_patch(
            name=inp.name,
            namespace=inp.namespace,
            host=inp.public_host,
        )
        files["ingress-http.yaml"] = _render_public_http_ingress(
            name=inp.name,
            namespace=inp.namespace,
            host=inp.public_host,
        )
        files["networkpolicy-allow-acme-http01-solver.yaml"] = _render_acme_http01_solver_network_policy(
            namespace=inp.namespace,
        )
    elif env_name == "prod":
        ingress_host = inp.prod_host or inp.public_host
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

    for component, template_key in [
        ("frontend", inp.frontend_template),
        ("backend", inp.backend_template),
    ]:
        obs_mode = str(TEMPLATES[template_key]["default_observability_mode"])
        service_id = f"{inp.name}-{component}"
        display_name = f"{display_name_base} {component.capitalize()}"
        prod_public_host_line = (
            f"        public_host: {yaml_string(inp.public_host)}\n"
            if inp.public_host and component == "frontend"
            else ""
        )
        entry = (
            f"  - service_id: {service_id}\n"
            f"    project_id: {inp.name}\n"
            f"    name: {yaml_string(display_name)}\n"
            f"    owner: {yaml_string(inp.owner or inp.owner_email)}\n"
            f"    owner_email: {yaml_string(inp.owner_email)}\n"
            f"    repo_url: {yaml_string(repo_url)}\n"
            f"    runbook_url: {yaml_string(repo_url)}\n"
            f"    description: {yaml_string(inp.description + f' ({component})')}\n"
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
