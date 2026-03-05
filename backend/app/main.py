from datetime import datetime, timedelta, timezone
import json
import logging
import math
import os
import re
from uuid import uuid4
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, ConfigDict

from app.db import get_psycopg_database_url

app = FastAPI(title="Homelab Backend API", version="0.1.0")
logger = logging.getLogger("homelab.backend.monitoring")

bearer_auth = HTTPBearer(auto_error=False)


class HealthResponse(BaseModel):
    status: str = "ok"


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: str


class Project(BaseModel):
    id: str
    name: str
    environment: str


class ProjectsResponse(BaseModel):
    projects: list[Project]


class CreateProjectRequest(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    environment: str = Field(min_length=1)


class ServiceMetricsSummaryResponse(BaseModel):
    service_id: str = Field(alias="serviceId")
    uptime_pct: float | None = Field(default=None, alias="uptimePct")
    p95_latency_ms: float | None = Field(default=None, alias="p95LatencyMs")
    error_rate_pct: float | None = Field(default=None, alias="errorRatePct")
    restart_count: float | None = Field(default=None, alias="restartCount")
    window_start: str = Field(alias="windowStart")
    window_end: str = Field(alias="windowEnd")
    generated_at: str = Field(alias="generatedAt")
    no_data: dict[str, bool] = Field(alias="noData")

    model_config = ConfigDict(populate_by_name=True)


DEFAULT_PROJECTS = [
    Project(id="proj-dev", name="Homelab App", environment="dev"),
    Project(id="proj-prod", name="Homelab App", environment="prod"),
]


def require_bearer_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_auth),
) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
        )

    if credentials.credentials != "dev-static-token":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    return credentials.credentials


def _parse_csv_header(value: str | None) -> set[str]:
    if not value:
        return set()
    return {item.strip() for item in value.split(",") if item.strip()}


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_auth),
    x_auth_user: str | None = Header(None, alias="X-Auth-Request-User"),
    x_auth_groups: str | None = Header(None, alias="X-Auth-Request-Groups"),
) -> tuple[str, set[str]]:
    if x_auth_user:
        return x_auth_user, _parse_csv_header(x_auth_groups)
    return require_bearer_token(credentials), set()


def require_admin(
    identity: tuple[str, set[str]] = Depends(get_current_user),
) -> str:
    user, groups = identity
    if user == "dev-static-token":
        return user

    admin_users = _parse_csv_header(os.getenv("PORTAL_ADMIN_USERS", "admin"))
    admin_groups = _parse_csv_header(
        os.getenv("PORTAL_ADMIN_GROUPS", "team-admins")
    )
    if user in admin_users or groups.intersection(admin_groups):
        return user

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="User is not authorized for admin actions",
    )


def _with_connection() -> psycopg.Connection:
    return psycopg.connect(get_psycopg_database_url())


def _seed_projects_if_empty(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM projects")
        count = cur.fetchone()[0]
        if count > 0:
            return

        for project in DEFAULT_PROJECTS:
            cur.execute(
                """
                INSERT INTO projects (id, name, environment)
                VALUES (%s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (project.id, project.name, project.environment),
            )


def _prometheus_base_url() -> str:
    return os.getenv(
        "PROMETHEUS_BASE_URL",
        "http://prometheus.monitoring.svc.cluster.local:9090",
    ).rstrip("/")


def _prometheus_timeout_seconds() -> float:
    raw = os.getenv("PROMETHEUS_TIMEOUT_SECONDS", "8")
    try:
        value = float(raw)
    except ValueError:
        return 8.0
    return value if value > 0 else 8.0


def _query_prometheus_scalar(query: str, metric_name: str) -> float | None:
    encoded = urlparse.urlencode({"query": query})
    endpoint = f"{_prometheus_base_url()}/api/v1/query?{encoded}"
    correlation_id = str(uuid4())

    try:
        with urlrequest.urlopen(
            endpoint,
            timeout=_prometheus_timeout_seconds(),
        ) as response:
            payload = json.loads(response.read())
    except urlerror.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:400]
        logger.error(
            "prometheus_http_error correlation_id=%s metric=%s status=%s body=%s",
            correlation_id,
            metric_name,
            exc.code,
            body,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Monitoring provider query failed. correlation_id={correlation_id}",
        ) from exc
    except Exception as exc:
        logger.error(
            "prometheus_query_error correlation_id=%s metric=%s error=%s",
            correlation_id,
            metric_name,
            str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Monitoring provider query failed. correlation_id={correlation_id}",
        ) from exc

    if payload.get("status") != "success":
        logger.error(
            "prometheus_bad_payload correlation_id=%s metric=%s payload_status=%s",
            correlation_id,
            metric_name,
            payload.get("status"),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Monitoring provider query failed. correlation_id={correlation_id}",
        )

    results = payload.get("data", {}).get("result", [])
    if not results:
        return None

    sample = results[0].get("value")
    if (
        not isinstance(sample, list)
        or len(sample) < 2
        or not isinstance(sample[1], str)
    ):
        return None

    try:
        value = float(sample[1])
    except ValueError:
        return None

    if not math.isfinite(value):
        return None
    return value


def _build_service_metrics_queries(
    namespace: str,
    app_label: str,
    selected_range: str,
) -> dict[str, str]:
    pod_pattern = re.escape(app_label)
    return {
        "uptimePct": (
            f'100 * avg_over_time(up{{namespace="{namespace}", app="{app_label}"}}'
            f"[{selected_range}])"
        ),
        "p95LatencyMs": (
            f'1000 * histogram_quantile(0.95, sum by (le) (rate('
            f'http_request_duration_seconds_bucket{{namespace="{namespace}", app="{app_label}"}}[5m])))'
        ),
        "errorRatePct": (
            f'100 * (sum(rate(http_requests_total{{namespace="{namespace}", app="{app_label}", status=~"5.."}}[5m]))'
            f' / sum(rate(http_requests_total{{namespace="{namespace}", app="{app_label}"}}[5m])))'
        ),
        "restartCount": (
            f'sum(increase(kube_pod_container_status_restarts_total{{namespace="{namespace}", pod=~"{pod_pattern}.*"}}'
            f"[{selected_range}]))"
        ),
    }


@app.get("/health", response_model=HealthResponse, tags=["system"])
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.post("/auth/login", response_model=LoginResponse, tags=["auth"])
def login(payload: LoginRequest) -> LoginResponse:
    if payload.username != "admin" or payload.password != "changeme":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    expires_at = datetime.now(tz=timezone.utc) + timedelta(hours=1)
    return LoginResponse(
        access_token="dev-static-token",
        expires_at=expires_at.isoformat(),
    )


@app.get("/projects", response_model=ProjectsResponse, tags=["metadata"])
def list_projects(_: tuple[str, set[str]] = Depends(get_current_user)) -> ProjectsResponse:
    with _with_connection() as conn:
        _seed_projects_if_empty(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, environment FROM projects ORDER BY id ASC"
            )
            rows = cur.fetchall()

    return ProjectsResponse(
        projects=[
            Project(id=row[0], name=row[1], environment=row[2])
            for row in rows
        ]
    )


@app.post(
    "/projects",
    response_model=Project,
    status_code=status.HTTP_201_CREATED,
    tags=["metadata"],
)
def create_project(
    payload: CreateProjectRequest,
    _: str = Depends(require_admin),
) -> Project:
    with _with_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO projects (id, name, environment)
                VALUES (%s, %s, %s)
                ON CONFLICT (id) DO UPDATE
                SET name = EXCLUDED.name,
                    environment = EXCLUDED.environment
                RETURNING id, name, environment
                """,
                (payload.id, payload.name, payload.environment),
            )
            row = cur.fetchone()

    return Project(id=row[0], name=row[1], environment=row[2])


@app.get(
    "/services/{service_id}/metrics/summary",
    response_model=ServiceMetricsSummaryResponse,
    tags=["monitoring"],
)
def get_service_metrics_summary(
    service_id: str,
    selected_range: str = Query(
        default="24h",
        alias="range",
        pattern="^(1h|24h|7d)$",
    ),
    _: tuple[str, set[str]] = Depends(get_current_user),
) -> ServiceMetricsSummaryResponse:
    now = datetime.now(tz=timezone.utc)
    durations = {
        "1h": timedelta(hours=1),
        "24h": timedelta(hours=24),
        "7d": timedelta(days=7),
    }
    window_start = now - durations[selected_range]
    namespace = "default"
    app_label = service_id

    queries = _build_service_metrics_queries(namespace, app_label, selected_range)
    values: dict[str, float | None] = {}
    no_data: dict[str, bool] = {}

    for field_name, query in queries.items():
        value = _query_prometheus_scalar(query, field_name)
        values[field_name] = value
        no_data[field_name] = value is None

    return ServiceMetricsSummaryResponse(
        serviceId=service_id,
        uptimePct=values["uptimePct"],
        p95LatencyMs=values["p95LatencyMs"],
        errorRatePct=values["errorRatePct"],
        restartCount=values["restartCount"],
        windowStart=window_start.isoformat(),
        windowEnd=now.isoformat(),
        generatedAt=now.isoformat(),
        noData=no_data,
    )


@app.get(
    "/services/{service_id}/metrics-summary",
    response_model=ServiceMetricsSummaryResponse,
    tags=["monitoring"],
)
def get_service_metrics_summary_legacy(
    service_id: str,
    selected_range: str = Query(
        default="24h",
        alias="range",
        pattern="^(1h|24h|7d)$",
    ),
    identity: tuple[str, set[str]] = Depends(get_current_user),
) -> ServiceMetricsSummaryResponse:
    return get_service_metrics_summary(
        service_id=service_id,
        selected_range=selected_range,
        _=identity,
    )
