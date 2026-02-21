import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_current_user, get_current_user_with_org, get_db
from src.models.organization import Organization
from src.models.organization_invite import OrganizationInvite
from src.models.user import User
from src.schemas.organization import (
    InviteCreate,
    InvitePublic,
    MemberPublic,
    OrganizationCreate,
    OrganizationPublic,
)

router = APIRouter(prefix="/organizations", tags=["organizations"])

INVITE_EXPIRY_DAYS = 7


@router.post("", response_model=OrganizationPublic, status_code=status.HTTP_201_CREATED)
async def create_organization(
    body: OrganizationCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.org_id is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User already belongs to an organization",
        )

    # Check slug uniqueness
    existing = await db.execute(
        select(Organization).where(Organization.slug == body.slug)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Organization slug already taken",
        )

    org = Organization(name=body.name, slug=body.slug)
    db.add(org)
    await db.flush()

    # Make user admin of the new org
    user.org_id = org.id
    user.role = "admin"

    await db.commit()
    await db.refresh(org)
    return org


@router.get("/me", response_model=OrganizationPublic)
async def get_my_organization(
    user_org: tuple[User, Organization] = Depends(get_current_user_with_org),
):
    _, org = user_org
    return org


@router.get("/me/members", response_model=list[MemberPublic])
async def list_members(
    user_org: tuple[User, Organization] = Depends(get_current_user_with_org),
    db: AsyncSession = Depends(get_db),
):
    _, org = user_org
    result = await db.execute(
        select(User).where(User.org_id == org.id)
    )
    return result.scalars().all()


@router.post("/me/invites", response_model=InvitePublic, status_code=status.HTTP_201_CREATED)
async def create_invite(
    body: InviteCreate,
    user_org: tuple[User, Organization] = Depends(get_current_user_with_org),
    db: AsyncSession = Depends(get_db),
):
    user, org = user_org
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )

    token = secrets.token_urlsafe(32)
    invite = OrganizationInvite(
        org_id=org.id,
        email=body.email,
        role=body.role,
        invited_by_user_id=user.id,
        token=token,
        expires_at=datetime.now(timezone.utc) + timedelta(days=INVITE_EXPIRY_DAYS),
    )
    db.add(invite)
    await db.commit()
    await db.refresh(invite)
    return invite


@router.post("/invites/{token}/accept", response_model=OrganizationPublic)
async def accept_invite(
    token: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(OrganizationInvite).where(OrganizationInvite.token == token)
    )
    invite = result.scalar_one_or_none()
    if invite is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invite not found",
        )

    if invite.accepted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invite already accepted",
        )

    if invite.expires_at < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invite has expired",
        )

    if user.org_id is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User already belongs to an organization",
        )

    # Accept invite
    invite.accepted_at = datetime.now(timezone.utc)
    user.org_id = invite.org_id
    user.role = invite.role

    await db.commit()

    # Return org
    org_result = await db.execute(
        select(Organization).where(Organization.id == invite.org_id)
    )
    return org_result.scalar_one()
