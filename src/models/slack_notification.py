import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, UUIDMixin


class NotificationType(str, enum.Enum):
    started = "started"
    completed = "completed"
    failed = "failed"
    pr_opened = "pr_opened"


class SlackNotification(Base, UUIDMixin):
    __tablename__ = "slack_notifications"
    __table_args__ = (
        Index("idx_slack_notifications_run_id", "run_id"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_runs.id"))
    channel_id: Mapped[str] = mapped_column(String(100))
    message_ts: Mapped[str] = mapped_column(String(50))
    type: Mapped[NotificationType] = mapped_column(
        Enum(NotificationType, name="notification_type")
    )
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    run = relationship("AgentRun", back_populates="slack_notifications")
