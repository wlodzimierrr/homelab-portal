"""Add observability mode to project_registry.

Revision ID: 20260311_0008
Revises: 20260310_0007
Create Date: 2026-03-11
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260311_0008"
down_revision = "20260310_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "project_registry",
        sa.Column("observability_mode", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("project_registry", "observability_mode")
