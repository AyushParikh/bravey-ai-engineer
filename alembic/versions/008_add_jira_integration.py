"""Add Jira integration columns to organizations and agent_runs

Revision ID: 008
Revises: 007
Create Date: 2026-02-23
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Jira columns on organizations
    op.add_column(
        "organizations",
        sa.Column("jira_client_key", sa.String(200), unique=True, nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("jira_shared_secret", sa.Text(), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("jira_base_url", sa.String(500), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("jira_bravey_account_id", sa.String(100), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("jira_in_progress_status_id", sa.String(100), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("jira_in_review_status_id", sa.String(100), nullable=True),
    )

    # Jira issue fields on agent_runs
    op.add_column(
        "agent_runs",
        sa.Column("jira_issue_id", sa.String(100), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("jira_issue_key", sa.String(50), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("jira_issue_summary", sa.String(500), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("jira_issue_url", sa.Text(), nullable=True),
    )

    # Make linear fields nullable
    op.alter_column(
        "agent_runs",
        "linear_issue_id",
        existing_type=sa.String(100),
        nullable=True,
    )
    op.alter_column(
        "agent_runs",
        "linear_issue_identifier",
        existing_type=sa.String(20),
        nullable=True,
    )


def downgrade() -> None:
    # Restore linear fields to non-nullable
    op.alter_column(
        "agent_runs",
        "linear_issue_identifier",
        existing_type=sa.String(20),
        nullable=False,
    )
    op.alter_column(
        "agent_runs",
        "linear_issue_id",
        existing_type=sa.String(100),
        nullable=False,
    )

    # Drop Jira columns from agent_runs
    op.drop_column("agent_runs", "jira_issue_url")
    op.drop_column("agent_runs", "jira_issue_summary")
    op.drop_column("agent_runs", "jira_issue_key")
    op.drop_column("agent_runs", "jira_issue_id")

    # Drop Jira columns from organizations
    op.drop_column("organizations", "jira_in_review_status_id")
    op.drop_column("organizations", "jira_in_progress_status_id")
    op.drop_column("organizations", "jira_bravey_account_id")
    op.drop_column("organizations", "jira_base_url")
    op.drop_column("organizations", "jira_shared_secret")
    op.drop_column("organizations", "jira_client_key")
