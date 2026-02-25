import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base


class StripeCustomer(Base):
    __tablename__ = "stripe_customer"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    plan: Mapped[str | None] = mapped_column(Text, nullable=True)
    stripe_id: Mapped[str] = mapped_column(String(255), nullable=False)

    user = relationship("User", foreign_keys=[user_id])
