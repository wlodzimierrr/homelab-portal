"""Add project_id to service_registry for explicit project-to-service linkage.

Revision ID: 20260325_0011
Revises: 20260316_0010
Create Date: 2026-03-25
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260325_0011"
down_revision = "20260316_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "service_registry",
        sa.Column("project_id", sa.String(length=128), nullable=True),
    )
    op.create_index(
        "ix_service_registry_project_id_env",
        "service_registry",
        ["project_id", "env"],
    )


def downgrade() -> None:
    op.drop_index("ix_service_registry_project_id_env", table_name="service_registry")
    op.drop_column("service_registry", "project_id")
