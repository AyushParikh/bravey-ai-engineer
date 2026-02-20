import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, UUIDMixin


class LogLevel(str, enum.Enum):
    info = "info"
    warn = "warn"
    error = "error"
    debug = "debug"


class AgentLog(Base, UUIDMixin):
    __tablename__ = "agent_logs"
    __table_args__ = (
        Index("idx_agent_logs_run_id_created", "run_id", "created_at"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_runs.id"))
    level: Mapped[LogLevel] = mapped_column(Enum(LogLevel, name="log_level"))
    message: Mapped[str] = mapped_column(Text)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    run = relationship("AgentRun", back_populates="logs")
