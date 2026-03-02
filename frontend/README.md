# Homelab Portal Frontend

Minimal web UI for Homelab Portal:

- login against backend (`/api/auth/login`)
- create project metadata (`POST /api/projects`)
- list project metadata (`GET /api/projects`)

## Roadmap Note

This implementation is a temporary thin UI for integration validation.
The final frontend version will use:

- React
- Vite
- Tailwind CSS
- shadcn/ui

## Container Image

Build and push:

```bash
cd apps/portal/frontend
./scripts/build_and_push_image.sh ghcr.io/wlodzimierrr/homelab-web 0.1.0
```

Or directly with Docker:

```bash
docker build -t ghcr.io/wlodzimierrr/homelab-web:0.1.0 apps/portal/frontend
docker push ghcr.io/wlodzimierrr/homelab-web:0.1.0
```
