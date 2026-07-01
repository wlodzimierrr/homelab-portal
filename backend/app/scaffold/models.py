from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

from app.service_observability import ObservabilityMode


SERVICE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,62}$")
HOSTNAME_LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")

# This is the platform-level observability contract for scaffolded services.
# Service-page behavior should consume declared modes rather than guessing from
# template names or ad hoc frontend special cases.
TEMPLATE_DEFAULT_OBSERVABILITY_MODE: dict[str, ObservabilityMode] = {
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

# Template metadata stays centralized so standalone, bundle, and add-service
# generators derive ports, probes, and observability defaults from one source.
TEMPLATES: dict[str, dict[str, object]] = {
    "python-fastapi": {
        "container_port": 8000,
        "service_port": 80,
        "health_path": "/health",
        "readiness_path": "/health",
        "container_name": "app",
        "default_observability_mode": TEMPLATE_DEFAULT_OBSERVABILITY_MODE["python-fastapi"],
    },
    "python-django": {
        "container_port": 8000,
        "service_port": 80,
        "health_path": "/health/",
        "readiness_path": "/health/",
        "container_name": "app",
        "default_observability_mode": TEMPLATE_DEFAULT_OBSERVABILITY_MODE["python-django"],
    },
    "python-flask": {
        "container_port": 5000,
        "service_port": 80,
        "health_path": "/health",
        "readiness_path": "/health",
        "container_name": "app",
        "default_observability_mode": TEMPLATE_DEFAULT_OBSERVABILITY_MODE["python-flask"],
    },
    "static-nginx": {
        "container_port": 80,
        "service_port": 80,
        "health_path": "/health",
        "readiness_path": "/health",
        "container_name": "web",
        "default_observability_mode": TEMPLATE_DEFAULT_OBSERVABILITY_MODE["static-nginx"],
    },
    "react": {
        "container_port": 80,
        "service_port": 80,
        "health_path": "/health",
        "readiness_path": "/health",
        "container_name": "web",
        "default_observability_mode": TEMPLATE_DEFAULT_OBSERVABILITY_MODE["react"],
    },
    "vue": {
        "container_port": 80,
        "service_port": 80,
        "health_path": "/",
        "readiness_path": "/",
        "container_name": "web",
        "default_observability_mode": TEMPLATE_DEFAULT_OBSERVABILITY_MODE["vue"],
    },
    "wordpress": {
        "container_port": 80,
        "service_port": 80,
        "health_path": "/wp-login.php",
        "readiness_path": "/wp-login.php",
        "container_name": "web",
        "default_observability_mode": TEMPLATE_DEFAULT_OBSERVABILITY_MODE["wordpress"],
    },
    "nextjs": {
        "container_port": 3000,
        "service_port": 80,
        "health_path": "/",
        "readiness_path": "/",
        "container_name": "web",
        "default_observability_mode": TEMPLATE_DEFAULT_OBSERVABILITY_MODE["nextjs"],
    },
    "node-express": {
        "container_port": 3000,
        "service_port": 80,
        "health_path": "/health",
        "readiness_path": "/health",
        "container_name": "app",
        "default_observability_mode": TEMPLATE_DEFAULT_OBSERVABILITY_MODE["node-express"],
    },
    "node-nestjs": {
        "container_port": 3000,
        "service_port": 80,
        "health_path": "/health",
        "readiness_path": "/health",
        "container_name": "app",
        "default_observability_mode": TEMPLATE_DEFAULT_OBSERVABILITY_MODE["node-nestjs"],
    },
    "postgres": {
        "db_port": 5432,
        "db_image": "postgres:17-alpine",
        "db_engine": "postgres",
        "default_observability_mode": TEMPLATE_DEFAULT_OBSERVABILITY_MODE["postgres"],
    },
    "mysql": {
        "db_port": 3306,
        "db_image": "mysql:8.0",
        "db_engine": "mysql",
        "default_observability_mode": TEMPLATE_DEFAULT_OBSERVABILITY_MODE["mysql"],
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

    def __post_init__(self) -> None:
        object.__setattr__(self, "dev_host", normalize_hostname(self.dev_host, field_name="devHost"))
        object.__setattr__(self, "prod_host", normalize_hostname(self.prod_host, field_name="prodHost"))
        object.__setattr__(self, "public_host", normalize_hostname(self.public_host, field_name="publicHost"))


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

    def __post_init__(self) -> None:
        object.__setattr__(self, "dev_host", normalize_hostname(self.dev_host, field_name="devHost"))
        object.__setattr__(self, "prod_host", normalize_hostname(self.prod_host, field_name="prodHost"))
        object.__setattr__(self, "public_host", normalize_hostname(self.public_host, field_name="publicHost"))


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

    def __post_init__(self) -> None:
        object.__setattr__(self, "dev_host", normalize_hostname(self.dev_host, field_name="devHost"))
        object.__setattr__(self, "prod_host", normalize_hostname(self.prod_host, field_name="prodHost"))
        object.__setattr__(self, "public_host", normalize_hostname(self.public_host, field_name="publicHost"))


def normalize_hostname(value: str, *, field_name: str = "hostname") -> str:
    """Return a Kubernetes Ingress-safe hostname, accepting a harmless trailing slash."""
    raw = value.strip()
    if not raw:
        return ""

    candidate = raw
    if "://" in raw or any(char in raw for char in "/?#:"):
        try:
            parsed = urlsplit(raw if "://" in raw else f"//{raw}")
            port = parsed.port
        except ValueError as exc:
            raise ScaffoldError(
                f"{field_name} must be a DNS hostname without a port.",
                status_code=422,
            ) from exc
        if parsed.username or parsed.password or port is not None:
            raise ScaffoldError(
                f"{field_name} must be a DNS hostname without credentials or a port.",
                status_code=422,
            )
        if parsed.query or parsed.fragment or parsed.path not in ("", "/"):
            raise ScaffoldError(
                f"{field_name} must be a DNS hostname without a path, query, or fragment.",
                status_code=422,
            )
        candidate = parsed.hostname or ""

    hostname = candidate.strip().lower()
    labels = hostname.split(".")
    if (
        not hostname
        or len(hostname) > 253
        or any(not label or not HOSTNAME_LABEL_PATTERN.match(label) for label in labels)
    ):
        raise ScaffoldError(
            f"{field_name} must be a DNS hostname, for example comparebuilding.wlodzimierrr.pl.",
            status_code=422,
        )
    return hostname


def validate_service_name(name: str) -> None:
    if not SERVICE_NAME_PATTERN.match(name):
        raise ScaffoldError(
            f"Service name {name!r} must match ^[a-z][a-z0-9-]{{1,62}}$. "
            "Use lowercase letters, digits, and hyphens only.",
            status_code=422,
        )
