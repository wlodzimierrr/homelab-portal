"""Create deployments table.

Revision ID: 20260310_0006
Revises: 20260309_0005
Create Date: 2026-03-10
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260310_0006"
down_revision = "20260309_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "deployments",
        sa.Column("deployment_id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("service_id", sa.String(length=255), nullable=False),
        sa.Column("env", sa.String(length=64), nullable=False),
        sa.Column("action_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("requested_by", sa.String(length=255), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("pr_url", sa.String(length=512), nullable=True),
        sa.Column("pr_number", sa.Integer(), nullable=True),
        sa.Column("merge_sha", sa.String(length=64), nullable=True),
        sa.Column("target_image", sa.String(length=512), nullable=True),
        sa.Column("previous_image", sa.String(length=512), nullable=True),
        sa.Column("argo_app", sa.String(length=255), nullable=True),
        sa.Column("sync_status", sa.String(length=64), nullable=True),
        sa.Column("health_status", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deploy_window_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deploy_window_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deploy_reason", sa.Text(), nullable=True),
        sa.Column("compare_url", sa.String(length=512), nullable=True),
        sa.Column("git_ref", sa.String(length=255), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("request_key", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint(
            "action_type IN ('deploy', 'promote', 'rollback', 'config-change')",
            name="ck_deployments_action_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'deploying', 'live', 'failed')",
            name="ck_deployments_status",
        ),
    )
    op.create_index("ix_deployments_service_id", "deployments", ["service_id"])
    op.create_index("ix_deployments_env", "deployments", ["env"])
    op.create_index("ix_deployments_requested_at", "deployments", ["requested_at"])
    op.create_index("ix_deployments_status", "deployments", ["status"])
    op.create_index("ix_deployments_request_key", "deployments", ["request_key"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_deployments_request_key", table_name="deployments")
    op.drop_index("ix_deployments_status", table_name="deployments")
    op.drop_index("ix_deployments_requested_at", table_name="deployments")
    op.drop_index("ix_deployments_env", table_name="deployments")
    op.drop_index("ix_deployments_service_id", table_name="deployments")
    op.drop_table("deployments")
