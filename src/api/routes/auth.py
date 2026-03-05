import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_current_user, get_db
from src.config import settings
from src.models.user import User
from src.schemas.auth import TokenResponse, UserPublic
from src.services import github_oauth_service, jwt_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/github/login")
async def github_login(
    cli_redirect: str | None = None,
    user_id: str | None = None,
):
    nonce = str(uuid.uuid4())
    # state format: {nonce}|{cli_redirect}|{user_id}
    # trailing segments may be empty but separators are always present
    state = f"{nonce}|{cli_redirect or ''}|{user_id or ''}"
    url = github_oauth_service.get_authorization_url(state)
    return {"authorization_url": url, "state": state}


@router.get("/github/callback")
async def github_callback(
    code: str,
    state: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    # Exchange code for GitHub access token
    try:
        github_token = github_oauth_service.exchange_code_for_token(code)
    except Exception:
        logger.exception("Failed to exchange GitHub OAuth code")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to exchange code for token",
        )

    # Fetch GitHub user info
    try:
        gh_user = github_oauth_service.get_github_user(github_token)
    except Exception:
        logger.exception("Failed to fetch GitHub user")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to fetch GitHub user info",
        )

    # Get primary email
    email = gh_user.get("email")
    if not email:
        try:
            emails = github_oauth_service.get_github_emails(github_token)
            primary = next(
                (e for e in emails if e.get("primary") and e.get("verified")),
                None,
            )
            if primary:
                email = primary["email"]
        except Exception:
            pass

    github_id = gh_user["id"]
    github_login = gh_user["login"]
    avatar_url = gh_user.get("avatar_url")
    name = gh_user.get("name")

    # Parse state: {nonce}|{cli_redirect}|{supabase_id}
    cli_redirect = None
    supabase_id_str = None
    if state:
        parts = state.split("|", 2)
        if len(parts) >= 2:
            cli_redirect = parts[1] or None
        if len(parts) >= 3:
            supabase_id_str = parts[2] or None

    supabase_uuid: uuid.UUID | None = None
    if supabase_id_str:
        try:
            supabase_uuid = uuid.UUID(supabase_id_str)
        except ValueError:
            pass

    # The Supabase auth trigger pre-creates a users row with id = Supabase UUID.
    # Look up by id first, then fall back to github_id for users created before
    # this flow was introduced.
    user = None

    if supabase_uuid is not None:
        result = await db.execute(
            select(User).where(User.id == supabase_uuid)
        )
        user = result.scalar_one_or_none()

    if user is None:
        result = await db.execute(
            select(User).where(User.github_id == github_id)
        )
        user = result.scalar_one_or_none()

    if user is None:
        user = User(
            id=supabase_uuid,
            github_id=github_id,
            github_login=github_login,
            github_username=github_login,
            avatar_url=avatar_url,
            name=name,
            email=email,
            role="member",
        )
        db.add(user)
    else:
        # If the supabase-created user doesn't have this github_id yet,
        # check if another user row already owns it and merge.
        if user.github_id != github_id:
            result = await db.execute(
                select(User).where(User.github_id == github_id)
            )
            existing = result.scalar_one_or_none()
            if existing is not None:
                # Transfer org membership from the old row
                if existing.org_id and not user.org_id:
                    user.org_id = existing.org_id
                if existing.role == "admin":
                    user.role = existing.role
                await db.delete(existing)
                await db.flush()

        # Update profile with GitHub info
        user.github_id = github_id
        user.github_login = github_login
        user.github_username = github_login
        user.avatar_url = avatar_url
        user.name = name
        if email:
            user.email = email

    await db.commit()
    await db.refresh(user)

    # Issue JWT
    token = jwt_service.create_access_token(user.id)

    if cli_redirect:
        sep = "&" if "?" in cli_redirect else "?"
        redirect_url = f"{cli_redirect}{sep}token={token}"
    else:
        redirect_url = f"{settings.frontend_url}/auth/callback?token={token}"
    return RedirectResponse(url=redirect_url)


@router.get("/me", response_model=UserPublic)
async def get_me(user: User = Depends(get_current_user)):
    return user
