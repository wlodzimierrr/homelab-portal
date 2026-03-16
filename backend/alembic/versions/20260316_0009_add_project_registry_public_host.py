"""Add public_host to project_registry.

Revision ID: 20260316_0009
Revises: 20260311_0008
Create Date: 2026-03-16
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260316_0009"
down_revision = "20260311_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "project_registry",
        sa.Column("public_host", sa.String(length=253), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("project_registry", "public_host")
