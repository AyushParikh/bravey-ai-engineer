import uuid
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from src.models.agent_run import AgentRun


class Repository(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "repositories"

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    github_repo_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    github_owner: Mapped[str] = mapped_column(String(255))
    github_repo_name: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(512))
    default_branch: Mapped[str] = mapped_column(String(100), default="main")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    organization = relationship("Organization", back_populates="repositories")
    agent_runs: Mapped[list["AgentRun"]] = relationship(back_populates="repository")
