"""Purge legacy non-canonical service_registry rows.

Revision ID: 20260306_0004
Revises: 20260305_0003
Create Date: 2026-03-06
"""

from __future__ import annotations

from alembic import op

revision = "20260306_0004"
down_revision = "20260305_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM service_registry
        WHERE source <> 'cluster_services'
        """
    )


def downgrade() -> None:
    # Irreversible cleanup of stale/manual registry artifacts.
    pass
