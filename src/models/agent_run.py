import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from src.models.agent_log import AgentLog
    from src.models.slack_notification import SlackNotification


class RunStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    success = "success"
    failed = "failed"
    cancelled = "cancelled"
    timed_out = "timed_out"


class AgentRun(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "agent_runs"
    __table_args__ = (
        Index(
            "idx_agent_runs_status",
            "status",
            postgresql_where="status IN ('queued', 'running')",
        ),
        Index("idx_agent_runs_org_id", "org_id"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    repo_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("repositories.id"))
    triggered_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id")
    )
    linear_issue_id: Mapped[str] = mapped_column(String(100))
    linear_issue_identifier: Mapped[str] = mapped_column(String(20))
    linear_issue_title: Mapped[str | None] = mapped_column(String(500))
    linear_issue_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus, name="run_status"),
        default=RunStatus.queued,
    )
    branch_name: Mapped[str | None] = mapped_column(String(255))
    pr_number: Mapped[int | None] = mapped_column(Integer)
    pr_url: Mapped[str | None] = mapped_column(Text)
    pr_sha: Mapped[str | None] = mapped_column(String(40))
    claude_session_id: Mapped[str | None] = mapped_column(String(255))
    container_id: Mapped[str | None] = mapped_column(String(255))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    timeout_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)
    claude_summary: Mapped[str | None] = mapped_column(Text)

    # PR-comment follow-up fields
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agent_runs.id"), nullable=True
    )
    trigger_type: Mapped[str] = mapped_column(
        String(50), default="linear_assignment", server_default="linear_assignment"
    )
    trigger_comment_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    organization = relationship("Organization", back_populates="agent_runs")
    repository = relationship("Repository", back_populates="agent_runs")
    triggered_by = relationship("User")
    logs: Mapped[list["AgentLog"]] = relationship(back_populates="run")
    slack_notifications: Mapped[list["SlackNotification"]] = relationship(
        back_populates="run"
    )
