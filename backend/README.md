# Homelab Portal Backend

FastAPI backend scaffold for task `T1.2.1`.

## Endpoints

- `GET /health`
- `POST /auth/login`
- `GET /projects?env=dev`
- `GET /services?env=dev&namespace=homelab-api`
- `GET /services/{serviceId}?env=dev`
- `GET /catalog/reconciliation?env=dev`
- `POST /service-registry/sync?source=cluster_services|gitops_apps&env=dev`
- `GET /service-registry/diagnostics?env=dev`
- `GET /services/{serviceId}/metrics/summary?range=1h|24h|7d`
- `GET /services/{serviceId}/health/timeline?range=24h|7d&step=5m..1h`
- `GET /alerts/active?env=dev&serviceId=...`
- `GET /monitoring/incidents` (compatibility envelope for existing frontend adapter)
- `GET /releases?env=dev&limit=50&serviceId=...`
- `GET /services/{serviceId}/logs/quickview?preset=errors&range=1h`

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
pytest
python scripts/generate_openapi.py
python scripts/migrate_projects_to_service_registry.py  # dry-run legacy projects backfill
python scripts/sync_project_registry.py  # sync GitOps-backed projects into project_registry
python scripts/project_source_cutover_smoke.py --api-base-url http://api.dev.homelab.local --auth-token dev-static-token --env dev
```

## Database Migrations (Alembic)

Set `DATABASE_URL` and run:

```bash
alembic upgrade head
```

Idempotency check (CI-friendly):

```bash
alembic upgrade head
alembic upgrade head
```

The second command is a no-op when schema is already at `head`.

## Monitoring endpoint config

Prometheus access:

- `PROMETHEUS_BASE_URL` (default: `http://prometheus.monitoring.svc.cluster.local:9090`)
- `PROMETHEUS_TIMEOUT_SECONDS` (default: `8`)

Health timeline status thresholds:

- `TIMELINE_DEGRADED_AVAILABILITY_MAX` (default: `0.995`)
- `TIMELINE_DOWN_AVAILABILITY_MAX` (default: `0.6`)
- `TIMELINE_DEGRADED_ERROR_RATE_MIN_PCT` (default: `1.0`)
- `TIMELINE_DOWN_ERROR_RATE_MIN_PCT` (default: `5.0`)
- `TIMELINE_DEGRADED_READINESS_MAX` (default: `0.98`)
- `TIMELINE_DOWN_READINESS_MAX` (default: `0.6`)

Release traceability metadata sources:

- `RELEASE_CI_METADATA_JSON`: JSON array of CI/build rows (`serviceId`, `env`, `commitSha`, `imageRef`, optional `expectedRevision`, `expectedImageRef`, `deployedAt`)
- `RELEASE_ARGO_METADATA_JSON`: JSON array of Argo rows (`serviceId`, `env`, `appName`, `syncStatus`, `healthStatus`, `revision`, optional `liveRevision`, `expectedRevision`, `imageRef`, `deployedAt`)

Deterministic drift rule for `/releases`:

1. `syncStatus == out_of_sync` => drifted
2. else `expectedRevision != liveRevision` when both values exist => drifted
3. else `expectedImageRef != liveImageRef` when both values exist => drifted
4. otherwise not drifted

Logs quick-view config:

- `LOKI_BASE_URL` (default: `http://loki.monitoring.svc.cluster.local:3100`)
- `LOGS_DEFAULT_NAMESPACE` (default: `default`)
- `LOGS_QUICKVIEW_RATE_LIMIT_PER_MIN` (default: `60`)

Quick-view supports only approved presets (`errors`, `restarts`, `warnings`) and does not allow arbitrary client queries.

Active alerts feed config:

- `ALERTMANAGER_BASE_URL` (default: `http://alertmanager-operated.monitoring.svc.cluster.local:9093`)
- `ALERT_SEVERITY_CRITICAL_VALUES` (CSV, default: `critical,error,page`)
- `ALERT_SEVERITY_WARNING_VALUES` (CSV, default: `warning,warn,info`)

Severity mapping for `/alerts/active` is normalized to `warning|critical` for consistency with frontend badge tones.

Service registry sync config:

- `PORTAL_ENV` (default: `dev`)
- `SERVICE_REGISTRY_SYNC_NAMESPACES` (CSV, default: `homelab-api,homelab-web`)
- `SERVICE_REGISTRY_SYNC_ARGO_NAMESPACE` (default: `argocd`)
- `KUBERNETES_API_URL` (optional override when not using in-cluster `KUBERNETES_SERVICE_HOST`/`PORT`)
- `KUBERNETES_BEARER_TOKEN` (optional override for service-account token)
- `REGISTRY_STALE_AFTER_MINUTES` (default: `30`) used by `/service-registry/diagnostics`

Cluster sync populates `service_registry` with `source=cluster_services`; `GET /services` reads only those live cluster-backed rows.
`GET /catalog/reconciliation` provides the deterministic bridge between GitOps projects and live cluster services.

GitOps project sync config:

- `GITOPS_WORKLOADS_REPO_PATH` (default: repo-local `workloads/`)
- `GITOPS_WORKLOADS_REPO_URL` (optional override for provenance in `sourceRef`)
- `GITOPS_WORKLOADS_REVISION` (optional override for provenance in `sourceRef`)

Observability hardening config:

- Allowed ranges:
  - `OBS_METRICS_ALLOWED_RANGES` (default: `1h,24h,7d`)
  - `OBS_TIMELINE_ALLOWED_RANGES` (default: `24h,7d`)
  - `OBS_LOGS_ALLOWED_RANGES` (default: `15m,1h,6h,24h`)
- Limits:
  - `OBS_TIMELINE_STEP_MIN` (default: `5m`)
  - `OBS_TIMELINE_STEP_MAX` (default: `1h`)
  - `OBS_TIMELINE_MAX_POINTS` (default: `1000`)
  - `OBS_LOGS_MAX_LINES` (default: `200`)
  - `OBS_ALERTS_MAX_ROWS` (default: `200`)
- Cache TTLs:
  - `OBS_METRICS_CACHE_TTL_SECONDS` (default: `20`)
  - `OBS_TIMELINE_CACHE_TTL_SECONDS` (default: `30`)
  - `OBS_LOGS_CACHE_TTL_SECONDS` (default: `15`)
  - `OBS_ALERTS_CACHE_TTL_SECONDS` (default: `15`)
- Query templates (see `docs/monitoring/query-templates.md`):
  - `OBS_QUERY_METRICS_UPTIME`
  - `OBS_QUERY_METRICS_P95_LATENCY`
  - `OBS_QUERY_METRICS_ERROR_RATE`
  - `OBS_QUERY_METRICS_RESTART_COUNT`
  - `OBS_QUERY_TIMELINE_AVAILABILITY`
  - `OBS_QUERY_TIMELINE_ERROR_RATE`
  - `OBS_QUERY_TIMELINE_READINESS`

## Container Image

Build and push a backend image (includes FastAPI + Alembic + SQLAlchemy + psycopg):

```bash
cd apps/portal/backend
./scripts/build_and_push_image.sh ghcr.io/wlodzimierrr/homelab-api 0.2.0
```

Or directly with Docker:

```bash
docker build -t ghcr.io/wlodzimierrr/homelab-api:0.2.0 apps/portal/backend
docker push ghcr.io/wlodzimierrr/homelab-api:0.2.0
```
