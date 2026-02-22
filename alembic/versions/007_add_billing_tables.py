"""Add billing tables (plans, subscriptions, usage_records)

Revision ID: 007
Revises: 006
Create Date: 2026-02-22
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Plans table
    op.create_table(
        "plans",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("slug", sa.String(50), unique=True, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("stripe_product_id", sa.String(100), nullable=True),
        sa.Column("stripe_price_id", sa.String(100), nullable=True),
        sa.Column("agent_runs_limit", sa.Integer(), nullable=False, server_default="20"),
        sa.Column("price_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Subscription status enum
    subscription_status = sa.Enum(
        "active", "past_due", "canceled", "trialing", "incomplete",
        name="subscription_status",
    )
    subscription_status.create(op.get_bind(), checkfirst=True)

    # Subscriptions table
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("org_id", sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("plans.id"), nullable=False),
        sa.Column("stripe_customer_id", sa.String(100), nullable=True),
        sa.Column("stripe_subscription_id", sa.String(100), unique=True, nullable=True),
        sa.Column("status", subscription_status, nullable=False, server_default="active"),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_subscriptions_org_id", "subscriptions", ["org_id"])

    # Usage records table
    op.create_table(
        "usage_records",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("org_id", sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("agent_runs_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_usage_records_org_id", "usage_records", ["org_id"])
    op.create_unique_constraint("uq_usage_org_period", "usage_records", ["org_id", "period"])

    # Add stripe_customer_id to organizations
    op.add_column("organizations", sa.Column("stripe_customer_id", sa.String(100), nullable=True))

    # Seed default plans
    op.execute(
        """
        INSERT INTO plans (id, slug, name, description, agent_runs_limit, price_cents, is_public, sort_order)
        VALUES
            (gen_random_uuid(), 'free', 'Free', 'Get started with Bravey', 20, 0, true, 0),
            (gen_random_uuid(), 'core', 'Core', '10x more agent runs for growing teams', 200, 2000, true, 1),
            (gen_random_uuid(), 'team', 'Team', 'High-volume usage for larger teams', 1000, 9900, true, 2),
            (gen_random_uuid(), 'enterprise', 'Enterprise', 'Unlimited runs with custom pricing', -1, 0, false, 3)
        """
    )


def downgrade() -> None:
    op.drop_column("organizations", "stripe_customer_id")
    op.drop_table("usage_records")
    op.drop_table("subscriptions")
    sa.Enum(name="subscription_status").drop(op.get_bind(), checkfirst=True)
    op.drop_table("plans")
