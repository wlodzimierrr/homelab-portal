from __future__ import annotations

import os

DEFAULT_DATABASE_URL = "postgresql+psycopg://postgres:postgres@localhost:5432/homelab"


def get_database_url() -> str:
    """Return the database DSN used by app code and Alembic."""
    return os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
