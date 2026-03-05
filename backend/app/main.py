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
from app.health_timeline import (
    TimelinePoint,
    classify_timeline_status,
    compact_timeline_points,
    load_timeline_thresholds,
    now_utc,
    parse_range,
    parse_step,
)
from app.release_traceability import (
    build_release_traceability_rows,
    load_argo_metadata_rows,
    load_ci_metadata_rows,
)

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


class ServiceHealthTimelineSegmentResponse(BaseModel):
    start: str
    end: str
    status: str
    reason: str | None = None


class ReleaseArgoStateResponse(BaseModel):
    app_name: str = Field(alias="appName")
    sync_status: str = Field(alias="syncStatus")
    health_status: str = Field(alias="healthStatus")
    revision: str | None = None

    model_config = ConfigDict(populate_by_name=True)


class ReleaseDriftStateResponse(BaseModel):
    is_drifted: bool = Field(alias="isDrifted")
    expected_revision: str | None = Field(default=None, alias="expectedRevision")
    live_revision: str | None = Field(default=None, alias="liveRevision")

    model_config = ConfigDict(populate_by_name=True)


class ReleaseTraceabilityResponse(BaseModel):
    service_id: str = Field(alias="serviceId")
    env: str
    commit_sha: str | None = Field(default=None, alias="commitSha")
    image_ref: str | None = Field(default=None, alias="imageRef")
    deployed_at: str | None = Field(default=None, alias="deployedAt")
    argo: ReleaseArgoStateResponse
    drift: ReleaseDriftStateResponse

    model_config = ConfigDict(populate_by_name=True)


class ReleaseDashboardCompatRow(BaseModel):
    service_id: str = Field(alias="serviceId")
    service_name: str = Field(alias="serviceName")
    environment: str
    commit_sha: str | None = Field(default=None, alias="commitSha")
    image: str | None = None
    sync: str
    health: str
    drift: bool
    deployed_at: str | None = Field(default=None, alias="deployedAt")

    model_config = ConfigDict(populate_by_name=True)


class ReleaseDashboardCompatResponse(BaseModel):
    releases: list[ReleaseDashboardCompatRow]


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


def _load_project_rows() -> list[dict[str, str]]:
    with _with_connection() as conn:
        _seed_projects_if_empty(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT name, environment FROM projects ORDER BY name ASC, environment ASC"
            )
            rows = cur.fetchall()

    return [
        {
            "service_id": row[0],
            "env": row[1],
        }
        for row in rows
    ]


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


def _query_prometheus_range(
    query: str,
    metric_name: str,
    *,
    start: datetime,
    end: datetime,
    step_seconds: int,
) -> dict[int, float]:
    encoded = urlparse.urlencode(
        {
            "query": query,
            "start": f"{start.timestamp():.3f}",
            "end": f"{end.timestamp():.3f}",
            "step": str(step_seconds),
        }
    )
    endpoint = f"{_prometheus_base_url()}/api/v1/query_range?{encoded}"
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
            "prometheus_range_http_error correlation_id=%s metric=%s status=%s body=%s",
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
            "prometheus_range_query_error correlation_id=%s metric=%s error=%s",
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
            "prometheus_range_bad_payload correlation_id=%s metric=%s payload_status=%s",
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
        return {}

    # Use first series because query should be pre-aggregated.
    series_values = results[0].get("values")
    if not isinstance(series_values, list):
        return {}

    points: dict[int, float] = {}
    for sample in series_values:
        if (
            not isinstance(sample, list)
            or len(sample) < 2
            or not isinstance(sample[0], (int, float))
            or not isinstance(sample[1], str)
        ):
            continue
        try:
            value = float(sample[1])
        except ValueError:
            continue
        if not math.isfinite(value):
            continue
        points[int(sample[0])] = value
    return points


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


def _build_health_timeline_queries(namespace: str, app_label: str) -> dict[str, str]:
    deployment_name = app_label
    return {
        "availability": (
            f'avg_over_time(up{{namespace="{namespace}", app="{app_label}"}}[5m])'
        ),
        "errorRatePct": (
            f'100 * (sum(rate(http_requests_total{{namespace="{namespace}", app="{app_label}", status=~"5.."}}[5m]))'
            f' / sum(rate(http_requests_total{{namespace="{namespace}", app="{app_label}"}}[5m])))'
        ),
        "readiness": (
            f'avg_over_time(kube_deployment_status_replicas_available{{namespace="{namespace}", deployment="{deployment_name}"}}[5m])'
            f' / clamp_min(avg_over_time(kube_deployment_spec_replicas{{namespace="{namespace}", deployment="{deployment_name}"}}[5m]), 1)'
        ),
    }


def _validate_step_for_range(range_value: str, step_value: str) -> int:
    try:
        step_delta = parse_step(step_value)
        window_delta = parse_range(range_value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    min_step = timedelta(minutes=5)
    max_step = timedelta(hours=1)
    if step_delta < min_step or step_delta > max_step:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="step must be between 5m and 1h",
        )

    points = int(window_delta.total_seconds() / step_delta.total_seconds())
    if points > 1000:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="step produces too many samples for selected range",
        )

    return int(step_delta.total_seconds())


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


@app.get(
    "/services/{service_id}/health/timeline",
    response_model=list[ServiceHealthTimelineSegmentResponse],
    tags=["monitoring"],
)
def get_service_health_timeline(
    service_id: str,
    selected_range: str = Query(
        default="24h",
        alias="range",
        pattern="^(24h|7d)$",
    ),
    step: str = Query(default="5m", pattern="^([1-9][0-9]*)(m|h)$"),
    _: tuple[str, set[str]] = Depends(get_current_user),
) -> list[ServiceHealthTimelineSegmentResponse]:
    step_seconds = _validate_step_for_range(selected_range, step)
    end = now_utc()
    window = parse_range(selected_range)
    start = end - window

    namespace = "default"
    app_label = service_id
    queries = _build_health_timeline_queries(namespace, app_label)

    availability_points = _query_prometheus_range(
        queries["availability"],
        "availability",
        start=start,
        end=end,
        step_seconds=step_seconds,
    )
    error_points = _query_prometheus_range(
        queries["errorRatePct"],
        "errorRatePct",
        start=start,
        end=end,
        step_seconds=step_seconds,
    )
    readiness_points = _query_prometheus_range(
        queries["readiness"],
        "readiness",
        start=start,
        end=end,
        step_seconds=step_seconds,
    )

    all_timestamps = sorted(
        set(availability_points.keys())
        .union(error_points.keys())
        .union(readiness_points.keys())
    )

    thresholds = load_timeline_thresholds()
    points: list[TimelinePoint] = []
    for ts in all_timestamps:
        status, reason = classify_timeline_status(
            availability=availability_points.get(ts),
            error_rate_pct=error_points.get(ts),
            readiness=readiness_points.get(ts),
            thresholds=thresholds,
        )
        points.append(
            TimelinePoint(
                timestamp=datetime.fromtimestamp(ts, tz=timezone.utc),
                status=status,
                reason=reason,
            )
        )

    segments = compact_timeline_points(
        points,
        window_start=start,
        window_end=end,
        step=timedelta(seconds=step_seconds),
    )
    return [
        ServiceHealthTimelineSegmentResponse(
            start=segment.start.isoformat(),
            end=segment.end.isoformat(),
            status=segment.status,
            reason=segment.reason,
        )
        for segment in segments
    ]


@app.get(
    "/releases",
    response_model=list[ReleaseTraceabilityResponse],
    tags=["monitoring"],
)
def get_release_traceability(
    env: str | None = Query(default=None),
    service_id: str | None = Query(default=None, alias="serviceId"),
    limit: int = Query(default=50, ge=1, le=200),
    _: tuple[str, set[str]] = Depends(get_current_user),
) -> list[ReleaseTraceabilityResponse]:
    rows = build_release_traceability_rows(
        project_rows=_load_project_rows(),
        ci_rows=load_ci_metadata_rows(),
        argo_rows=load_argo_metadata_rows(),
        env_filter=env,
        service_id_filter=service_id,
        limit=limit,
    )
    return [
        ReleaseTraceabilityResponse(
            serviceId=row["serviceId"],
            env=row["env"],
            commitSha=row["commitSha"],
            imageRef=row["imageRef"],
            deployedAt=row["deployedAt"],
            argo=ReleaseArgoStateResponse(
                appName=str(row["argo"]["appName"]),
                syncStatus=str(row["argo"]["syncStatus"]),
                healthStatus=str(row["argo"]["healthStatus"]),
                revision=row["argo"]["revision"]
                if isinstance(row["argo"]["revision"], str)
                else None,
            ),
            drift=ReleaseDriftStateResponse(
                isDrifted=bool(row["drift"]["isDrifted"]),
                expectedRevision=row["drift"]["expectedRevision"]
                if isinstance(row["drift"]["expectedRevision"], str)
                else None,
                liveRevision=row["drift"]["liveRevision"]
                if isinstance(row["drift"]["liveRevision"], str)
                else None,
            ),
        )
        for row in rows
    ]


@app.get(
    "/release-dashboard",
    response_model=ReleaseDashboardCompatResponse,
    tags=["monitoring"],
)
def get_release_dashboard_compat(
    env: str | None = Query(default=None),
    service_id: str | None = Query(default=None, alias="serviceId"),
    limit: int = Query(default=50, ge=1, le=200),
    identity: tuple[str, set[str]] = Depends(get_current_user),
) -> ReleaseDashboardCompatResponse:
    rows = get_release_traceability(
        env=env,
        service_id=service_id,
        limit=limit,
        _=identity,
    )
    return ReleaseDashboardCompatResponse(
        releases=[
            ReleaseDashboardCompatRow(
                serviceId=row.service_id,
                serviceName=row.service_id,
                environment=row.env,
                commitSha=row.commit_sha,
                image=row.image_ref,
                sync=row.argo.sync_status,
                health=row.argo.health_status,
                drift=row.drift.is_drifted,
                deployedAt=row.deployed_at,
            )
            for row in rows
        ]
    )
