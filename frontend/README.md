# Homelab Portal Frontend

React + Vite + TypeScript frontend for the Homelab Portal.

## Local development

```bash
cd apps/portal/frontend
npm install
npm run dev
```

Vite dev proxy forwards `/api/*` to `http://localhost:8000` by default (with `/api` stripped).
Override target when needed:

```bash
VITE_API_PROXY_TARGET=http://localhost:8081 npm run dev
```

Frontend runtime config (see `src/lib/config.ts`):

- `VITE_API_BASE_URL` (default: `/api`)
- `VITE_ARGO_BASE_URL` (default: `http://argo.dev.homelab.local`)
- `VITE_GRAFANA_BASE_URL` (default: `http://grafana.dev.homelab.local`)

## Available scripts

- `npm run dev` - start Vite dev server
- `npm run build` - type-check and create production build in `dist/`
- `npm run lint` - run ESLint
- `npm run format` - check formatting with Prettier
- `npm run format:write` - apply Prettier formatting

## Container image

```bash
cd apps/portal/frontend
./scripts/build_and_push_image.sh ghcr.io/wlodzimierrr/homelab-web 0.1.0
```

The Docker build uses a multi-stage build:
- Stage 1: build static assets with Node (`npm run build`)
- Stage 2: serve `dist/` with Nginx on port `80`

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
