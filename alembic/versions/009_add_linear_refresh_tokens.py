"""Add Linear refresh token columns to organizations

Revision ID: 009
Revises: 008
Create Date: 2026-02-23
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("linear_refresh_token", sa.Text(), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("linear_token_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("linear_bot_refresh_token", sa.Text(), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("linear_bot_token_expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("organizations", "linear_bot_token_expires_at")
    op.drop_column("organizations", "linear_bot_refresh_token")
    op.drop_column("organizations", "linear_token_expires_at")
    op.drop_column("organizations", "linear_refresh_token")
