"""Add result and result_reason columns to deployments table.

Revision ID: 20260316_0010
Revises: 20260316_0009
Create Date: 2026-03-16
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260316_0010"
down_revision = "20260316_0009"
branch_labels = None
depends_on = None

# Retention policy: deployment records are retained for a minimum of 180 days.
# Records older than 180 days may be archived or purged by a scheduled job.
# The ix_deployments_requested_at index supports efficient range queries for
# retention enforcement.


def upgrade() -> None:
    op.add_column(
        "deployments",
        sa.Column("result", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "deployments",
        sa.Column("result_reason", sa.Text(), nullable=True),
    )
    op.create_check_constraint(
        "ck_deployments_result",
        "deployments",
        "result IS NULL OR result IN ('success', 'failure', 'cancelled', 'skipped')",
    )
    op.create_index("ix_deployments_result", "deployments", ["result"])


def downgrade() -> None:
    op.drop_index("ix_deployments_result", table_name="deployments")
    op.drop_constraint("ck_deployments_result", "deployments", type_="check")
    op.drop_column("deployments", "result_reason")
    op.drop_column("deployments", "result")
