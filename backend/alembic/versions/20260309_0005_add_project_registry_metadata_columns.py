"""Add Git-backed catalog metadata columns to project_registry.

Revision ID: 20260309_0005
Revises: 20260306_0004
Create Date: 2026-03-09
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260309_0005"
down_revision = "20260306_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("project_registry", sa.Column("owner", sa.String(length=255), nullable=True))
    op.add_column("project_registry", sa.Column("repo_url", sa.String(length=512), nullable=True))
    op.add_column("project_registry", sa.Column("runbook_url", sa.String(length=512), nullable=True))


def downgrade() -> None:
    op.drop_column("project_registry", "runbook_url")
    op.drop_column("project_registry", "repo_url")
    op.drop_column("project_registry", "owner")
