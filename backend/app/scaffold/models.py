from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


SERVICE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,62}$")

# Template metadata stays centralized so standalone, bundle, and add-service
# generators derive ports, probes, and observability defaults from one source.
TEMPLATES: dict[str, dict[str, object]] = {
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
    template: Literal[
        "python-fastapi",
        "python-django",
        "python-flask",
        "static-nginx",
        "react",
        "nextjs",
        "vue",
        "wordpress",
        "node-express",
        "node-nestjs",
        "postgres",
        "mysql",
    ]
    namespace: str
    dev_host: str
    prod_host: str
    public_host: str
    workloads_repo_url: str
    db_username: str = "appuser"
    db_password: str = "changeme"
    db_name: str = "appdb"


FRONTEND_TEMPLATES = frozenset({"react", "nextjs", "vue", "static-nginx"})
BACKEND_TEMPLATES = frozenset(
    {"python-fastapi", "python-django", "python-flask", "node-express", "node-nestjs"}
)
DB_TEMPLATES = frozenset({"postgres", "mysql"})

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


@dataclass(frozen=True)
class ScaffoldAddServiceInput:
    """Input for adding a service to an existing project."""

    project_id: str
    service_name: str
    description: str
    owner_email: str
    owner: str
    namespace: str
    template: str
    image_repo: str
    repo_url: str
    dev_host: str
    prod_host: str
    public_host: str
    workloads_repo_url: str
    db_username: str = "appuser"
    db_password: str = "changeme"
    db_name: str = "appdb"

    @property
    def service_id(self) -> str:
        return f"{self.project_id}-{self.service_name}"


def validate_service_name(name: str) -> None:
    if not SERVICE_NAME_PATTERN.match(name):
        raise ScaffoldError(
            f"Service name {name!r} must match ^[a-z][a-z0-9-]{{1,62}}$. "
            "Use lowercase letters, digits, and hyphens only.",
            status_code=422,
        )
