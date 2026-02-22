"""Add parent_run_id, trigger_type, trigger_comment_id to agent_runs

Revision ID: 005
Revises: 004
Create Date: 2026-02-21
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("parent_run_id", sa.UUID(), sa.ForeignKey("agent_runs.id"), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "trigger_type",
            sa.String(50),
            nullable=False,
            server_default="linear_assignment",
        ),
    )
    op.add_column(
        "agent_runs",
        sa.Column("trigger_comment_id", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "trigger_comment_id")
    op.drop_column("agent_runs", "trigger_type")
    op.drop_column("agent_runs", "parent_run_id")
