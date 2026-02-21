"""Multi-tenant auth

Revision ID: 002
Revises: 001
Create Date: 2026-02-20
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- users: add auth columns ---
    op.add_column("users", sa.Column("github_id", sa.BigInteger(), nullable=True))
    op.add_column("users", sa.Column("github_login", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("avatar_url", sa.String(512), nullable=True))
    op.add_column("users", sa.Column("name", sa.String(255), nullable=True))

    # Make github_id unique (after backfill if needed)
    op.create_unique_constraint("uq_users_github_id", "users", ["github_id"])

    # Make org_id nullable
    op.alter_column("users", "org_id", existing_type=sa.Uuid(), nullable=True)

    # --- organization_invites ---
    op.create_table(
        "organization_invites",
        sa.Column("id", sa.Uuid(), nullable=False, default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("role", sa.String(50), nullable=False, server_default="member"),
        sa.Column("invited_by_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token", sa.String(255), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token"),
    )


def downgrade() -> None:
    op.drop_table("organization_invites")
    op.alter_column("users", "org_id", existing_type=sa.Uuid(), nullable=False)
    op.drop_constraint("uq_users_github_id", "users", type_="unique")
    op.drop_column("users", "name")
    op.drop_column("users", "avatar_url")
    op.drop_column("users", "github_login")
    op.drop_column("users", "github_id")
