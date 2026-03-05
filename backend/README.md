# Homelab Portal Backend

FastAPI backend scaffold for task `T1.2.1`.

## Endpoints

- `GET /health`
- `POST /auth/login`
- `GET /projects`
- `GET /services/{serviceId}/metrics/summary?range=1h|24h|7d`
- `GET /services/{serviceId}/health/timeline?range=24h|7d&step=5m..1h`
- `GET /releases?env=dev&limit=50&serviceId=...`

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
pytest
python scripts/generate_openapi.py
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
