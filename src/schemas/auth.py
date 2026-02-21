import uuid

from pydantic import BaseModel


class UserPublic(BaseModel):
    id: uuid.UUID
    github_id: int
    github_login: str
    avatar_url: str | None = None
    name: str | None = None
    email: str | None = None
    role: str
    org_id: uuid.UUID | None = None

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserPublic
