import enum
import uuid

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin, UUIDMixin


class SubscriptionStatus(str, enum.Enum):
    active = "active"
    past_due = "past_due"
    canceled = "canceled"
    trialing = "trialing"
    incomplete = "incomplete"


class Subscription(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "subscriptions"

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plans.id"))
    stripe_customer_id: Mapped[str | None] = mapped_column(String(100))
    stripe_subscription_id: Mapped[str | None] = mapped_column(
        String(100), unique=True
    )
    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus, name="subscription_status"),
        default=SubscriptionStatus.active,
    )
    current_period_start: Mapped[None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    current_period_end: Mapped[None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False)

    plan = relationship("Plan")
    organization = relationship("Organization", back_populates="subscription")
