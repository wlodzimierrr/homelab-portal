# Homelab Portal

Application repository for the Homelab portal services.

## Structure

- `backend/` FastAPI API service
- `frontend/` React + Vite + TypeScript UI application

## Backend Development

```bash
python3 -m venv backend/.venv
source backend/.venv/bin/activate
pip install -e ./backend[dev]
cd backend
pytest
python scripts/generate_openapi.py
```

## Frontend Development

# Homelab Portal Frontend

React + Vite + TypeScript frontend for the Homelab Portal.

## Local development

```bash
cd apps/portal/frontend
npm install
npm run dev
```

Build production assets locally:

```bash
cd apps/portal/frontend
npm run build
```

Vite dev proxy forwards `/api/*` to `http://localhost:8000` by default (with `/api` stripped).
Override target when needed:

```bash
VITE_API_PROXY_TARGET=http://localhost:8081 npm run dev
```

Frontend runtime config (see `src/lib/config.ts`):

- `VITE_API_BASE_URL` (default: `/api`)
- `VITE_ARGO_BASE_URL` (default: empty)
- `VITE_GRAFANA_BASE_URL` (default: empty)
- `VITE_ARGO_APP_PATH_TEMPLATE` (default: `/applications/{serviceId}`)
- `VITE_GRAFANA_DASHBOARD_PATH_TEMPLATE` (default: `/d/service-overview?var-service={serviceId}`)
- `VITE_LOKI_LOGS_PATH_TEMPLATE` (default: `/explore?var-namespace={{namespace}}&var-app={{app_label}}&from=now-{{time_range}}&to=now`)

### Runtime config examples

Local dev (backend on localhost):

```bash
VITE_API_BASE_URL=http://localhost:8000/api \
VITE_ARGO_BASE_URL=http://argo.dev.homelab.local \
VITE_GRAFANA_BASE_URL=http://grafana.dev.homelab.local \
npm run dev
```

In-cluster (API via ingress/reverse proxy):

```bash
VITE_API_BASE_URL=/api \
VITE_ARGO_BASE_URL=https://argo.example.internal \
VITE_GRAFANA_BASE_URL=https://grafana.example.internal \
npm run dev
```

Notes:
- If `VITE_ARGO_BASE_URL` or `VITE_GRAFANA_BASE_URL` is empty, related external links are unavailable.
- Logs templates support both `{var}` and `{{var}}` placeholders, including `{{namespace}}`, `{{app_label}}`, and `{{time_range}}`.

### Services registry fallback (MVP)

The frontend uses an API-first services adapter and falls back to `services.sample.json` when `/api/projects` is unavailable or empty.

- Sample file path: `apps/portal/frontend/services.sample.json`
- Adapter path: `apps/portal/frontend/src/lib/adapters/services.ts`

### Release dashboard fallback (T4.3.1)

The dashboard uses API-first release metadata and falls back to local sample data when release metadata APIs are not available yet.

- Sample file path: `apps/portal/frontend/release-dashboard.sample.json`
- Adapter path: `apps/portal/frontend/src/lib/adapters/release-dashboard.ts`
- Current load order:
  - `GET /api/release-dashboard` (if implemented)
  - `GET /api/projects` (minimal status-only mapping)
  - local sample payload

## Available scripts

- `npm run dev` - start Vite dev server
- `npm run build` - type-check and create production build in `dist/`
- `npm run lint` - run ESLint
- `npm run format` - check formatting with Prettier
- `npm run format:write` - apply Prettier formatting

## Container image

Build image locally:

```bash
cd apps/portal/frontend
docker build -t homelab-portal-frontend:local .
```

Run image locally:

```bash
docker run --rm -p 8080:80 homelab-portal-frontend:local
```

The Docker build uses a multi-stage build:
- Stage 1: build static assets with Node (`npm run build`)
- Stage 2: serve `dist/` with Nginx on port `80`

Optional publish helper script:

```bash
cd apps/portal/frontend
./scripts/build_and_push_image.sh ghcr.io/wlodzimierrr/homelab-web 0.2.0
```

## Read-only behavior note

- Deploy controls are intentionally not implemented yet (read-only catalog/visibility first).
- Logs and some deployment/status metadata may be mocked or placeholder-based until backend integration is complete.
- If Grafana/Loki base URLs are not configured, external logs links remain unavailable/disabled by design.

## Command validation

Validated locally where possible by project workflow:

- `npm run dev`
- `npm run lint`
- `npm run build`
- `docker build -t homelab-portal-frontend:local .`
- `docker run --rm -p 8080:80 homelab-portal-frontend:local`

If your environment cannot run Node tooling (for example WSL1), run the same commands in a supported local shell/CI runner.

## Auth Storage Tradeoff

Current implementation stores JWT access token in `localStorage` (`portal-auth-token`).

- Pros:
  - Simple client-side implementation for SPA routing and API calls.
  - Easy to attach bearer token in a centralized fetch wrapper.
- Cons:
  - Vulnerable to token theft if XSS occurs.
  - No automatic CSRF mitigation from browser cookie policies.

Alternative: secure `HttpOnly` cookie-based auth.

- Pros:
  - Token is not accessible via JavaScript, reducing XSS token exfiltration risk.
  - Better alignment with server-managed session patterns.
- Cons:
  - Requires backend cookie issuance/refresh flow and CSRF defenses.
  - More moving pieces for local dev and cross-origin setups.

## Gated Promotion Workflow (staging/prod)

`apps/portal/.github/workflows/gated-promotion.yml` adds a manual pipeline for higher-environment promotion with an approval checkpoint.

- Trigger: `workflow_dispatch`
- Modes:
  - `promote`: copies image tags from `dev` overlays into target env overlays.
  - `rollback`: writes explicitly provided rollback tags into target env overlays.
- Target environments: `staging` or `prod`
- Policy checks before approval:
  - target overlay files exist
  - candidate tags:
    - `promote`: must match `sha-<40 hex>`
    - `rollback`: may be `sha-<40 hex>` or semver (for example `0.3.1`)
  - candidate tags exist in GHCR
- Approval gate:
  - job `approval-gate` uses environment `homelab-<target>-promotion`
  - configure required reviewers in GitHub repo settings for these environments
- Result:
  - after approval, workflow opens a PR in `wlodzimierrr/homelab-workloads`
  - changed files are constrained to expected env image patch files only
