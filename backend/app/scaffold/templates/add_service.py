from __future__ import annotations

from app.scaffold.models import DB_TEMPLATES, ScaffoldAddServiceInput, ScaffoldError, TEMPLATES, validate_service_name
from app.scaffold.render import indent_block, render_template, yaml_string


def validate_add_service(
    inp: ScaffoldAddServiceInput,
    existing_base_kustomization: str,
    existing_services_yaml: str,
) -> None:
    service_id = inp.service_id
    validate_service_name(service_id)

    if f"service_id: {service_id}\n" in existing_services_yaml:
        raise ScaffoldError(
            f"Service {service_id!r} already exists in services.yaml.",
            status_code=409,
        )

    is_db = inp.template in DB_TEMPLATES
    if is_db:
        check_resources = [
            f"{inp.service_name}-statefulset.yaml",
            f"{inp.service_name}-service.yaml",
        ]
    else:
        check_resources = [
            f"{inp.service_name}-deployment.yaml",
            f"{inp.service_name}-service.yaml",
        ]
    for resource in check_resources:
        if f"- {resource}" in existing_base_kustomization:
            raise ScaffoldError(
                f"Resource {resource!r} already exists in the project kustomization.",
                status_code=409,
            )


def generate_gitops_add_service_files(inp: ScaffoldAddServiceInput) -> tuple[dict[str, str], list[str]]:
    """Generate files for adding a service to an existing project."""
    service_id = inp.service_id
    validate_service_name(service_id)

    is_db = inp.template in DB_TEMPLATES
    template = TEMPLATES[inp.template]
    observability_mode = str(template["default_observability_mode"])

    files: dict[str, str] = {}
    base_prefix = f"apps/{inp.project_id}/base"
    new_resources: list[str] = []

    files[f"{base_prefix}/serviceaccount-{inp.service_name}.yaml"] = render_template(
        """
        apiVersion: v1
        kind: ServiceAccount
        metadata:
          name: {service_id}
          namespace: {namespace}
          labels:
            app.kubernetes.io/name: {project_id}
            app.kubernetes.io/component: {service_name}
        """,
        service_id=service_id,
        namespace=inp.namespace,
        project_id=inp.project_id,
        service_name=inp.service_name,
    )
    new_resources.append(f"serviceaccount-{inp.service_name}.yaml")

    if is_db:
        db_port = int(template["db_port"])  # type: ignore[arg-type]
        db_engine = str(template["db_engine"])
        container_name = str(template.get("container_name", str(template.get("db_image", "")).split(":")[0]))
        _generate_add_service_db_files(
            files,
            new_resources,
            inp,
            base_prefix,
            db_port,
            db_engine,
            container_name,
            service_id,
        )
    else:
        container_port = int(template["container_port"])  # type: ignore[arg-type]
        service_port = int(template["service_port"])  # type: ignore[arg-type]
        container_name = str(template["container_name"])
        health_path = str(template["health_path"])
        readiness_path = str(template["readiness_path"])
        _generate_add_service_app_files(
            files,
            new_resources,
            inp,
            base_prefix,
            container_port,
            service_port,
            container_name,
            health_path,
            readiness_path,
            observability_mode,
            service_id,
        )

        for env_name in ("dev", "prod"):
            _generate_add_service_overlay_files(files, inp, env_name, container_name, service_id)

    return files, new_resources


def _generate_add_service_app_files(
    files: dict[str, str],
    new_resources: list[str],
    inp: ScaffoldAddServiceInput,
    base_prefix: str,
    container_port: int,
    service_port: int,
    container_name: str,
    health_path: str,
    readiness_path: str,
    observability_mode: str,
    service_id: str,
) -> None:
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

    deployment_lines = [
        "apiVersion: apps/v1",
        "kind: Deployment",
        "metadata:",
        f"  name: {service_id}",
        f"  namespace: {inp.namespace}",
        "  labels:",
        f"    app.kubernetes.io/name: {inp.project_id}",
        f"    app.kubernetes.io/instance: {inp.project_id}",
        f"    app.kubernetes.io/component: {inp.service_name}",
        "spec:",
        "  replicas: 1",
        "  selector:",
        "    matchLabels:",
        f"      app.kubernetes.io/name: {inp.project_id}",
        f"      app.kubernetes.io/component: {inp.service_name}",
        "  template:",
        "    metadata:",
        "      labels:",
        f"        app.kubernetes.io/name: {inp.project_id}",
        f"        app.kubernetes.io/component: {inp.service_name}",
        "    spec:",
        f"      serviceAccountName: {service_id}",
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
    files[f"{base_prefix}/{inp.service_name}-deployment.yaml"] = "\n".join(deployment_lines) + "\n"
    new_resources.append(f"{inp.service_name}-deployment.yaml")

    files[f"{base_prefix}/{inp.service_name}-service.yaml"] = render_template(
        """
        apiVersion: v1
        kind: Service
        metadata:
          name: {service_id}
          namespace: {namespace}
          labels:
            app.kubernetes.io/name: {project_id}
            app.kubernetes.io/instance: {project_id}
            app.kubernetes.io/component: {service_name}
        spec:
          type: ClusterIP
          selector:
            app.kubernetes.io/name: {project_id}
            app.kubernetes.io/component: {service_name}
          ports:
            - name: http
              port: {service_port}
              targetPort: http
        """,
        service_id=service_id,
        namespace=inp.namespace,
        project_id=inp.project_id,
        service_name=inp.service_name,
        service_port=str(service_port),
    )
    new_resources.append(f"{inp.service_name}-service.yaml")

    if observability_mode == "app-native":
        files[f"{base_prefix}/servicemonitor-{inp.service_name}.yaml"] = render_template(
            """
            apiVersion: monitoring.coreos.com/v1
            kind: ServiceMonitor
            metadata:
              name: {service_id}
              namespace: {namespace}
              labels:
                app.kubernetes.io/name: {project_id}
                app.kubernetes.io/component: {service_name}
            spec:
              selector:
                matchLabels:
                  app.kubernetes.io/name: {project_id}
                  app.kubernetes.io/component: {service_name}
              endpoints:
                - port: http
                  path: /metrics
                  interval: 30s
            """,
            service_id=service_id,
            namespace=inp.namespace,
            project_id=inp.project_id,
            service_name=inp.service_name,
        )
        new_resources.append(f"servicemonitor-{inp.service_name}.yaml")


def _generate_add_service_db_files(
    files: dict[str, str],
    new_resources: list[str],
    inp: ScaffoldAddServiceInput,
    base_prefix: str,
    db_port: int,
    db_engine: str,
    container_name: str,
    service_id: str,
) -> None:
    if db_engine == "postgres":
        env_block = render_template(
            """
            env:
              - name: POSTGRES_USER
                valueFrom:
                  secretKeyRef:
                    name: {service_id}-credentials
                    key: POSTGRES_USER
              - name: POSTGRES_PASSWORD
                valueFrom:
                  secretKeyRef:
                    name: {service_id}-credentials
                    key: POSTGRES_PASSWORD
              - name: POSTGRES_DB
                valueFrom:
                  secretKeyRef:
                    name: {service_id}-credentials
                    key: POSTGRES_DB
            """,
            service_id=service_id,
        )
        secret_data = render_template(
            """
            POSTGRES_USER: {db_username}
            POSTGRES_PASSWORD: {db_password}
            POSTGRES_DB: {db_name}
            """,
            db_username=inp.db_username,
            db_password=inp.db_password,
            db_name=inp.db_name,
        )
    else:
        env_block = render_template(
            """
            env:
              - name: MYSQL_ROOT_PASSWORD
                valueFrom:
                  secretKeyRef:
                    name: {service_id}-credentials
                    key: MYSQL_ROOT_PASSWORD
              - name: MYSQL_USER
                valueFrom:
                  secretKeyRef:
                    name: {service_id}-credentials
                    key: MYSQL_USER
              - name: MYSQL_PASSWORD
                valueFrom:
                  secretKeyRef:
                    name: {service_id}-credentials
                    key: MYSQL_PASSWORD
              - name: MYSQL_DATABASE
                valueFrom:
                  secretKeyRef:
                    name: {service_id}-credentials
                    key: MYSQL_DATABASE
            """,
            service_id=service_id,
        )
        secret_data = render_template(
            """
            MYSQL_ROOT_PASSWORD: {db_password}
            MYSQL_USER: {db_username}
            MYSQL_PASSWORD: {db_password}
            MYSQL_DATABASE: {db_name}
            """,
            db_username=inp.db_username,
            db_password=inp.db_password,
            db_name=inp.db_name,
        )

    files[f"{base_prefix}/{inp.service_name}-credentials-secret.yaml"] = (
        render_template(
            """
            apiVersion: v1
            kind: Secret
            metadata:
              name: {service_id}-credentials
              namespace: {namespace}
              labels:
                app.kubernetes.io/name: {project_id}
                app.kubernetes.io/component: {service_name}
            type: Opaque
            stringData:
            """,
            service_id=service_id,
            namespace=inp.namespace,
            project_id=inp.project_id,
            service_name=inp.service_name,
        ).rstrip()
        + "\n"
        + indent_block(secret_data.rstrip(), 2)
        + "\n"
    )
    new_resources.append(f"{inp.service_name}-credentials-secret.yaml")

    image = f"{container_name}:latest"
    statefulset_lines = [
        "apiVersion: apps/v1",
        "kind: StatefulSet",
        "metadata:",
        f"  name: {service_id}",
        f"  namespace: {inp.namespace}",
        "  labels:",
        f"    app.kubernetes.io/name: {inp.project_id}",
        f"    app.kubernetes.io/component: {inp.service_name}",
        "spec:",
        "  replicas: 1",
        f"  serviceName: {service_id}",
        "  selector:",
        "    matchLabels:",
        f"      app.kubernetes.io/name: {inp.project_id}",
        f"      app.kubernetes.io/component: {inp.service_name}",
        "  template:",
        "    metadata:",
        "      labels:",
        f"        app.kubernetes.io/name: {inp.project_id}",
        f"        app.kubernetes.io/component: {inp.service_name}",
        "    spec:",
        f"      serviceAccountName: {service_id}",
        "      containers:",
        f"        - name: {container_name}",
        f"          image: {image}",
        "          ports:",
        "            - name: db",
        f"              containerPort: {db_port}",
    ]
    statefulset_lines.extend(indent_block(env_block.rstrip(), 10).splitlines())
    statefulset_lines.extend(
        [
            "          resources:",
            "            requests:",
            "              cpu: 100m",
            "              memory: 256Mi",
            "            limits:",
            "              cpu: 500m",
            "              memory: 512Mi",
            "          volumeMounts:",
            "            - name: data",
            "              mountPath: /var/lib/postgresql/data"
            if db_engine == "postgres"
            else "              mountPath: /var/lib/mysql",
            "  volumeClaimTemplates:",
            "    - metadata:",
            "        name: data",
            "      spec:",
            "        accessModes: [ReadWriteOnce]",
            "        resources:",
            "          requests:",
            "            storage: 1Gi",
        ]
    )
    files[f"{base_prefix}/{inp.service_name}-statefulset.yaml"] = "\n".join(statefulset_lines) + "\n"
    new_resources.append(f"{inp.service_name}-statefulset.yaml")

    files[f"{base_prefix}/{inp.service_name}-service.yaml"] = render_template(
        """
        apiVersion: v1
        kind: Service
        metadata:
          name: {service_id}
          namespace: {namespace}
          labels:
            app.kubernetes.io/name: {project_id}
            app.kubernetes.io/component: {service_name}
        spec:
          type: ClusterIP
          selector:
            app.kubernetes.io/name: {project_id}
            app.kubernetes.io/component: {service_name}
          ports:
            - name: db
              port: {db_port}
              targetPort: db
        """,
        service_id=service_id,
        namespace=inp.namespace,
        project_id=inp.project_id,
        service_name=inp.service_name,
        db_port=str(db_port),
    )
    new_resources.append(f"{inp.service_name}-service.yaml")


def _generate_add_service_overlay_files(
    files: dict[str, str],
    inp: ScaffoldAddServiceInput,
    env_name: str,
    container_name: str,
    service_id: str,
) -> None:
    if env_name == "prod":
        replicas, cpu_req, mem_req, cpu_lim, mem_lim = "2", "100m", "128Mi", "500m", "512Mi"
    else:
        replicas, cpu_req, mem_req, cpu_lim, mem_lim = "1", "50m", "64Mi", "300m", "256Mi"

    env_prefix = f"apps/{inp.project_id}/envs/{env_name}"
    files[f"{env_prefix}/patch-{inp.service_name}-deployment.yaml"] = render_template(
        """
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: {service_id}
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
        service_id=service_id,
        namespace=inp.namespace,
        replicas=replicas,
        container_name=container_name,
        image_repo=inp.image_repo,
        env_name=env_name,
        cpu_req=cpu_req,
        mem_req=mem_req,
        cpu_lim=cpu_lim,
        mem_lim=mem_lim,
    )


def build_catalog_add_service_entry(existing_services_yaml: str, inp: ScaffoldAddServiceInput) -> str:
    """Append a catalog entry for a new service in an existing project."""
    service_id = inp.service_id
    if f"service_id: {service_id}\n" in existing_services_yaml:
        raise ScaffoldError(
            f"Service {service_id!r} already exists in services.yaml.",
            status_code=409,
        )
    if "services:" not in existing_services_yaml:
        raise ScaffoldError("Expected top-level services: list in services.yaml.", status_code=502)

    display_name = " ".join(word.capitalize() for word in inp.project_id.split("-")) + f" {inp.service_name.capitalize()}"
    repo_url = inp.repo_url or inp.workloads_repo_url
    observability_mode = str(TEMPLATES[inp.template]["default_observability_mode"])
    workload_kind = "statefulset" if inp.template in DB_TEMPLATES else "deployment"
    workload_ref = f"apps/{inp.project_id}/base/{inp.service_name}-{workload_kind}.yaml"

    entry = (
        f"  - service_id: {service_id}\n"
        f"    project_id: {inp.project_id}\n"
        f"    name: {yaml_string(display_name)}\n"
        f"    owner: {yaml_string(inp.owner or inp.owner_email)}\n"
        f"    owner_email: {yaml_string(inp.owner_email)}\n"
        f"    repo_url: {yaml_string(repo_url)}\n"
        f"    runbook_url: {yaml_string(repo_url)}\n"
        f"    description: {yaml_string(inp.description)}\n"
        "    observability:\n"
        f"      mode: {observability_mode}\n"
        "    envs:\n"
        "      - name: dev\n"
        f"        namespace: {inp.namespace}\n"
        f"        app_label: {service_id}\n"
        f"        argo_app: {inp.project_id}-dev\n"
        f"        workload_ref: {workload_ref}\n"
        "      - name: prod\n"
        f"        namespace: {inp.namespace}\n"
        f"        app_label: {service_id}\n"
        f"        argo_app: {inp.project_id}-prod\n"
        f"        workload_ref: {workload_ref}\n"
    )
    suffix = "" if existing_services_yaml.endswith("\n") else "\n"
    return existing_services_yaml + suffix + entry
