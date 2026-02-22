import uuid

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin, UUIDMixin


class UsageRecord(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "usage_records"
    __table_args__ = (
        UniqueConstraint("org_id", "period", name="uq_usage_org_period"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    period: Mapped[str] = mapped_column(String(7))  # "YYYY-MM"
    agent_runs_count: Mapped[int] = mapped_column(Integer, default=0)
