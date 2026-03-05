"""Add RLS to feature_flags table with public read access

Revision ID: 012
Revises: 011
Create Date: 2026-03-04
"""

from typing import Sequence, Union

from alembic import op

revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE feature_flags ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY feature_flags_public_read ON feature_flags "
        "FOR SELECT USING (true)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY feature_flags_public_read ON feature_flags")
    op.execute("ALTER TABLE feature_flags DISABLE ROW LEVEL SECURITY")
