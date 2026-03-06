"""Create canonical project_registry table.

Revision ID: 20260305_0003
Revises: 20260305_0002
Create Date: 2026-03-05
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260305_0003"
down_revision = "20260305_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_registry",
        sa.Column("project_id", sa.String(length=128), nullable=False),
        sa.Column("project_name", sa.String(length=255), nullable=False),
        sa.Column("namespace", sa.String(length=128), nullable=False),
        sa.Column("env", sa.String(length=32), nullable=False),
        sa.Column("app_label", sa.String(length=128), nullable=False),
        sa.Column(
            "source",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'unknown'"),
        ),
        sa.Column("source_ref", sa.String(length=255), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("project_id", "env", name="pk_project_registry"),
        sa.UniqueConstraint(
            "project_name",
            "namespace",
            "env",
            name="uq_project_registry_name_namespace_env",
        ),
    )
    op.create_index("ix_project_registry_env", "project_registry", ["env"])
    op.create_index(
        "ix_project_registry_source_last_synced_at",
        "project_registry",
        ["source", "last_synced_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_project_registry_source_last_synced_at",
        table_name="project_registry",
    )
    op.drop_index("ix_project_registry_env", table_name="project_registry")
    op.drop_table("project_registry")
