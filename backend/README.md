# Homelab Portal Backend

FastAPI backend scaffold for task `T1.2.1`.

## Endpoints

- `GET /health`
- `POST /auth/login`
- `GET /projects`

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

