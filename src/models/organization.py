import uuid
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.encryption import EncryptedString
from src.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from src.models.agent_run import AgentRun
    from src.models.repository import Repository
    from src.models.subscription import Subscription
    from src.models.user import User
    from src.models.webhook_event import WebhookEvent


class Organization(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    linear_org_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    linear_webhook_id: Mapped[str | None] = mapped_column(String(100))
    linear_webhook_secret: Mapped[str | None] = mapped_column(EncryptedString())
    linear_access_token: Mapped[str | None] = mapped_column(EncryptedString())
    linear_bravey_user_id: Mapped[str | None] = mapped_column(String(100))
    linear_bot_token: Mapped[str | None] = mapped_column(EncryptedString())
    linear_in_progress_state_id: Mapped[str | None] = mapped_column(String(100))
    linear_in_review_state_id: Mapped[str | None] = mapped_column(String(100))
    github_installation_id: Mapped[int | None] = mapped_column(BigInteger)
    github_app_private_key: Mapped[str | None] = mapped_column(EncryptedString())
    slack_team_id: Mapped[str | None] = mapped_column(String(100))
    slack_bot_token: Mapped[str | None] = mapped_column(EncryptedString())
    slack_default_channel_id: Mapped[str | None] = mapped_column(String(100))
    stripe_customer_id: Mapped[str | None] = mapped_column(String(100))

    # Relationships
    users: Mapped[list["User"]] = relationship(back_populates="organization")
    repositories: Mapped[list["Repository"]] = relationship(back_populates="organization")
    webhook_events: Mapped[list["WebhookEvent"]] = relationship(back_populates="organization")
    agent_runs: Mapped[list["AgentRun"]] = relationship(back_populates="organization")
    subscription: Mapped["Subscription | None"] = relationship(
        back_populates="organization", uselist=False
    )
