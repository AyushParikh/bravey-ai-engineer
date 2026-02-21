import uuid
from datetime import datetime

from pydantic import BaseModel


class OrganizationCreate(BaseModel):
    name: str
    slug: str


class OrganizationPublic(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    github_installation_id: int | None = None
    linear_org_id: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class MemberPublic(BaseModel):
    id: uuid.UUID
    github_login: str
    avatar_url: str | None = None
    name: str | None = None
    email: str | None = None
    role: str

    model_config = {"from_attributes": True}


class InviteCreate(BaseModel):
    email: str
    role: str = "member"


class InvitePublic(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    token: str
    expires_at: datetime
    accepted_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
