# Homelab Portal

Application repository for the Homelab portal services.

## Structure

- `backend/` FastAPI API service
- `frontend/` UI application (to be implemented)

## Backend Development

```bash
python3 -m venv backend/.venv
source backend/.venv/bin/activate
pip install -e ./backend[dev]
cd backend
pytest
python scripts/generate_openapi.py
```
