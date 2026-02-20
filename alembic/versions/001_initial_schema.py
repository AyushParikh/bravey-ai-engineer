"""Initial schema

Revision ID: 001
Revises:
Create Date: 2026-02-19
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- organizations ---
    op.create_table(
        "organizations",
        sa.Column("id", sa.Uuid(), nullable=False, default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("linear_org_id", sa.String(100), nullable=True),
        sa.Column("linear_webhook_id", sa.String(100), nullable=True),
        sa.Column("linear_webhook_secret", sa.String(), nullable=True),
        sa.Column("linear_access_token", sa.String(), nullable=True),
        sa.Column("linear_bravey_user_id", sa.String(100), nullable=True),
        sa.Column("linear_in_review_state_id", sa.String(100), nullable=True),
        sa.Column("github_installation_id", sa.BigInteger(), nullable=True),
        sa.Column("github_app_private_key", sa.String(), nullable=True),
        sa.Column("slack_team_id", sa.String(100), nullable=True),
        sa.Column("slack_bot_token", sa.String(), nullable=True),
        sa.Column("slack_default_channel_id", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
        sa.UniqueConstraint("linear_org_id"),
    )

    # --- users ---
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False, default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("linear_user_id", sa.String(100), nullable=True),
        sa.Column("github_username", sa.String(100), nullable=True),
        sa.Column("slack_user_id", sa.String(100), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("role", sa.String(50), nullable=False, server_default="member"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    # --- repositories ---
    op.create_table(
        "repositories",
        sa.Column("id", sa.Uuid(), nullable=False, default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("github_repo_id", sa.BigInteger(), nullable=False),
        sa.Column("github_owner", sa.String(255), nullable=False),
        sa.Column("github_repo_name", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(512), nullable=False),
        sa.Column("default_branch", sa.String(100), nullable=False, server_default="main"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("github_repo_id"),
    )

    # --- agent_runs ---
    run_status = postgresql.ENUM(
        "queued", "running", "success", "failed", "cancelled", "timed_out",
        name="run_status",
        create_type=False,
    )
    run_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Uuid(), nullable=False, default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("repo_id", sa.Uuid(), sa.ForeignKey("repositories.id"), nullable=False),
        sa.Column("triggered_by_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("linear_issue_id", sa.String(100), nullable=False),
        sa.Column("linear_issue_identifier", sa.String(20), nullable=False),
        sa.Column("linear_issue_title", sa.String(500), nullable=True),
        sa.Column("linear_issue_url", sa.Text(), nullable=True),
        sa.Column("status", run_status, nullable=False, server_default="queued"),
        sa.Column("branch_name", sa.String(255), nullable=True),
        sa.Column("pr_number", sa.Integer(), nullable=True),
        sa.Column("pr_url", sa.Text(), nullable=True),
        sa.Column("pr_sha", sa.String(40), nullable=True),
        sa.Column("claude_session_id", sa.String(255), nullable=True),
        sa.Column("container_id", sa.String(255), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("timeout_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("claude_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_agent_runs_status",
        "agent_runs",
        ["status"],
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_index("idx_agent_runs_org_id", "agent_runs", ["org_id"])

    # --- webhook_events ---
    op.create_table(
        "webhook_events",
        sa.Column("id", sa.Uuid(), nullable=False, default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("delivery_id", sa.String(100), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("signature_ok", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("processed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("agent_runs.id"), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_webhook_events_unprocessed",
        "webhook_events",
        ["org_id", "processed"],
        postgresql_where=sa.text("processed = false"),
    )

    # --- log_level enum ---
    log_level = postgresql.ENUM("info", "warn", "error", "debug", name="log_level", create_type=False)
    log_level.create(op.get_bind(), checkfirst=True)

    # --- agent_logs ---
    op.create_table(
        "agent_logs",
        sa.Column("id", sa.Uuid(), nullable=False, default=sa.text("gen_random_uuid()")),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("agent_runs.id"), nullable=False),
        sa.Column("level", log_level, nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_agent_logs_run_id_created", "agent_logs", ["run_id", "created_at"])

    # --- notification_type enum ---
    notification_type = postgresql.ENUM(
        "started", "completed", "failed", "pr_opened",
        name="notification_type",
        create_type=False,
    )
    notification_type.create(op.get_bind(), checkfirst=True)

    # --- slack_notifications ---
    op.create_table(
        "slack_notifications",
        sa.Column("id", sa.Uuid(), nullable=False, default=sa.text("gen_random_uuid()")),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("agent_runs.id"), nullable=False),
        sa.Column("channel_id", sa.String(100), nullable=False),
        sa.Column("message_ts", sa.String(50), nullable=False),
        sa.Column("type", notification_type, nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_slack_notifications_run_id", "slack_notifications", ["run_id"])


def downgrade() -> None:
    op.drop_table("slack_notifications")
    op.drop_table("agent_logs")
    op.drop_table("webhook_events")
    op.drop_table("agent_runs")
    op.drop_table("repositories")
    op.drop_table("users")
    op.drop_table("organizations")

    op.execute("DROP TYPE IF EXISTS notification_type")
    op.execute("DROP TYPE IF EXISTS log_level")
    op.execute("DROP TYPE IF EXISTS run_status")
