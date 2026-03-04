"""Add Slack mention fields to agent_runs

Revision ID: 010
Revises: 009
Create Date: 2026-03-04
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("slack_channel_id", sa.String(100), nullable=True))
    op.add_column("agent_runs", sa.Column("slack_thread_ts", sa.String(50), nullable=True))
    op.add_column("agent_runs", sa.Column("slack_message_text", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_runs", "slack_message_text")
    op.drop_column("agent_runs", "slack_thread_ts")
    op.drop_column("agent_runs", "slack_channel_id")
