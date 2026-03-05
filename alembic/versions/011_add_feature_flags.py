"""Add feature_flags table

Revision ID: 011
Revises: 010
Create Date: 2026-03-04
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "feature_flags",
        sa.Column("name", sa.String(100), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
    )

    op.execute(
        "INSERT INTO feature_flags (name, enabled, description) VALUES "
        "('linear_integration', false, 'Linear issue tracker integration'), "
        "('jira_integration', false, 'Jira issue tracker integration')"
    )


def downgrade() -> None:
    op.drop_table("feature_flags")
