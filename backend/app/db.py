from __future__ import annotations

import os

DEFAULT_DATABASE_URL = "postgresql+psycopg://postgres:postgres@localhost:5432/homelab"


def get_database_url() -> str:
    """Return the database DSN used by app code and Alembic."""
    return os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)


def get_psycopg_database_url() -> str:
    """Return a psycopg-compatible DSN derived from DATABASE_URL."""
    dsn = get_database_url()
    if dsn.startswith("postgresql+psycopg://"):
        return dsn.replace("postgresql+psycopg://", "postgresql://", 1)
    return dsn
