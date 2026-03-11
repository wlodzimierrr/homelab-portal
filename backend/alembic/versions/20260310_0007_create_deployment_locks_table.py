"""Create deployment locks table.

Revision ID: 20260310_0007
Revises: 20260310_0006
Create Date: 2026-03-10
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260310_0007"
down_revision = "20260310_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "deployment_locks",
        sa.Column("service_id", sa.String(length=255), primary_key=True, nullable=False),
        sa.Column("env", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("deployment_id", sa.String(length=64), nullable=False),
        sa.Column("request_key", sa.String(length=255), nullable=False),
        sa.Column("action_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("argo_app", sa.String(length=255), nullable=True),
        sa.Column("requested_by", sa.String(length=255), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("pr_url", sa.String(length=512), nullable=True),
        sa.Column("pr_number", sa.Integer(), nullable=True),
        sa.Column("git_ref", sa.String(length=255), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("deploy_reason", sa.Text(), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint(
            "action_type IN ('deploy', 'promote', 'rollback', 'config-change')",
            name="ck_deployment_locks_action_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'deploying')",
            name="ck_deployment_locks_status",
        ),
    )
    op.create_index("ix_deployment_locks_request_key", "deployment_locks", ["request_key"], unique=True)
    op.create_index("ix_deployment_locks_expires_at", "deployment_locks", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_deployment_locks_expires_at", table_name="deployment_locks")
    op.drop_index("ix_deployment_locks_request_key", table_name="deployment_locks")
    op.drop_table("deployment_locks")
