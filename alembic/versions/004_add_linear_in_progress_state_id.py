"""Add linear_in_progress_state_id to organizations

Revision ID: 004
Revises: 003
Create Date: 2026-02-21
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("linear_in_progress_state_id", sa.String(100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("organizations", "linear_in_progress_state_id")
