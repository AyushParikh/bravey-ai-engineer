import uuid

from sqlalchemy import Boolean, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, UUIDMixin

from datetime import datetime
from sqlalchemy import DateTime, func


class WebhookEvent(Base, UUIDMixin):
    __tablename__ = "webhook_events"
    __table_args__ = (
        Index(
            "idx_webhook_events_unprocessed",
            "org_id",
            "processed",
            postgresql_where="processed = false",
        ),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    source: Mapped[str] = mapped_column(String(20))  # "linear" or "github"
    event_type: Mapped[str] = mapped_column(String(100))
    delivery_id: Mapped[str | None] = mapped_column(String(100))
    payload: Mapped[dict] = mapped_column(JSONB)
    signature_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_runs.id"))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    organization = relationship("Organization", back_populates="webhook_events")
    run = relationship("AgentRun")
