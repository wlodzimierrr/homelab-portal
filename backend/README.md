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
