"""Add RLS to subscriptions table with org-scoped access

Revision ID: 013
Revises: 012
Create Date: 2026-03-04
"""

from typing import Sequence, Union

from alembic import op

revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE subscriptions ENABLE ROW LEVEL SECURITY")
    op.execute(
        "ALTER TABLE subscriptions FORCE ROW LEVEL SECURITY"
    )
    op.execute(
        "CREATE POLICY subscriptions_org_read ON subscriptions "
        "FOR SELECT USING ("
        "org_id::text = current_setting('app.current_org_id', true))"
    )
    op.execute(
        "CREATE POLICY subscriptions_service_all ON subscriptions "
        "FOR ALL USING ("
        "current_setting('app.current_org_id', true) IS NULL)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY subscriptions_service_all ON subscriptions")
    op.execute("DROP POLICY subscriptions_org_read ON subscriptions")
    op.execute("ALTER TABLE subscriptions NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE subscriptions DISABLE ROW LEVEL SECURITY")
