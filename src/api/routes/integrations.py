import logging
import secrets
import uuid
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_current_user_with_org, get_db
from src.config import settings
from src.models.organization import Organization
from src.models.user import User
from src.services import linear_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations", tags=["integrations"])

LINEAR_AUTHORIZE_URL = "https://linear.app/oauth/authorize"
LINEAR_TOKEN_URL = "https://api.linear.app/oauth/token"


# --- Linear OAuth ---


@router.get("/linear/connect")
async def linear_connect(
    user_org: tuple[User, Organization] = Depends(get_current_user_with_org),
):
    user, org = user_org
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )

    # Encode org_id in state so the callback can find the org without auth
    state = f"{org.id}:{uuid.uuid4()}"
    params = {
        "client_id": settings.linear_client_id,
        "response_type": "code",
        "scope": "read,write,issues:create,comments:create,admin",
        "state": state,
        "redirect_uri": settings.linear_redirect_uri,
        "prompt": "consent",
    }
    return {"authorization_url": f"{LINEAR_AUTHORIZE_URL}?{urlencode(params)}", "state": state}


@router.get("/linear/callback")
async def linear_callback(
    code: str,
    state: str = "",
    db: AsyncSession = Depends(get_db),
):
    # Extract org_id from state
    try:
        org_id_str = state.split(":")[0]
        org_id = uuid.UUID(org_id_str)
    except (ValueError, IndexError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid state parameter",
        )

    result = await db.execute(
        select(Organization).where(Organization.id == org_id)
    )
    org = result.scalar_one_or_none()
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    # Exchange code for token
    try:
        resp = httpx.post(
            LINEAR_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": settings.linear_client_id,
                "client_secret": settings.linear_client_secret,
                "redirect_uri": settings.linear_redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        resp.raise_for_status()
        token_data = resp.json()
    except Exception:
        logger.exception("Failed to exchange Linear OAuth code")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to exchange code for token",
        )

    access_token = token_data["access_token"]

    # Get Linear org info
    try:
        org_info = linear_service.graphql_request(
            access_token,
            "query { organization { id name } }",
        )
        linear_org = (org_info.get("data") or {}).get("organization") or {}
    except Exception:
        logger.exception("Failed to fetch Linear organization info")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to fetch Linear organization info",
        )

    # Store credentials on org (don't set bravey_user_id here — admin sets it separately)
    org.linear_access_token = access_token
    org.linear_org_id = linear_org.get("id")

    # Create webhook for Linear (requires admin-level token)
    webhook_secret = secrets.token_hex(32)
    try:
        webhook_url = f"{settings.frontend_url.rstrip('/')}/webhooks/linear"
        wh_result = linear_service.graphql_request(
            access_token,
            """
            mutation CreateWebhook($url: String!, $secret: String!) {
                webhookCreate(input: {
                    url: $url
                    secret: $secret
                    resourceTypes: ["Issue"]
                    enabled: true
                }) {
                    success
                    webhook { id }
                }
            }
            """,
            {"url": webhook_url, "secret": webhook_secret},
        )
        wh_data = wh_result.get("data") or {}
        webhook_data = wh_data.get("webhookCreate") or {}
        if webhook_data.get("success"):
            org.linear_webhook_id = webhook_data["webhook"]["id"]
            org.linear_webhook_secret = webhook_secret
        else:
            errors = wh_result.get("errors", [])
            logger.error(f"Webhook creation failed: {errors}")
    except Exception:
        logger.exception("Failed to create Linear webhook")

    await db.commit()

    next_step = ""
    if not org.linear_webhook_id:
        next_step = "Webhook creation failed — authorize with a Linear workspace admin account."
    elif not org.linear_bravey_user_id:
        next_step = "Set the bot user: GET /integrations/linear/members then POST /integrations/linear/bot-user"

    return {
        "status": "connected",
        "linear_org_id": org.linear_org_id,
        "webhook_configured": org.linear_webhook_id is not None,
        "next_step": next_step,
    }


@router.get("/linear/members")
async def linear_members(
    user_org: tuple[User, Organization] = Depends(get_current_user_with_org),
):
    """List all Linear workspace members so admin can identify the bot user."""
    _, org = user_org
    if not org.linear_access_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Linear not connected",
        )

    result = linear_service.graphql_request(
        org.linear_access_token,
        """
        query {
            users {
                nodes {
                    id
                    name
                    displayName
                    email
                    active
                    admin
                }
            }
        }
        """,
    )
    users = result.get("data", {}).get("users", {}).get("nodes", [])
    return {"members": users}


class BotUserRequest(BaseModel):
    linear_user_id: str


@router.post("/linear/bot-user")
async def set_bot_user(
    body: BotUserRequest,
    user_org: tuple[User, Organization] = Depends(get_current_user_with_org),
    db: AsyncSession = Depends(get_db),
):
    """Set which Linear user is the Bravey bot (issues assigned to this user trigger runs)."""
    user, org = user_org
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )

    org.linear_bravey_user_id = body.linear_user_id
    await db.commit()
    return {"status": "ok", "linear_bravey_user_id": org.linear_bravey_user_id}


@router.get("/linear/status")
async def linear_status(
    user_org: tuple[User, Organization] = Depends(get_current_user_with_org),
):
    _, org = user_org
    return {
        "connected": org.linear_access_token is not None,
        "linear_org_id": org.linear_org_id,
        "webhook_configured": org.linear_webhook_id is not None,
        "bot_user_configured": org.linear_bravey_user_id is not None,
    }


@router.post("/linear/disconnect")
async def linear_disconnect(
    user_org: tuple[User, Organization] = Depends(get_current_user_with_org),
    db: AsyncSession = Depends(get_db),
):
    user, org = user_org
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )

    org.linear_access_token = None
    org.linear_org_id = None
    org.linear_webhook_id = None
    org.linear_webhook_secret = None
    org.linear_bravey_user_id = None

    await db.commit()
    return {"status": "disconnected"}


# --- GitHub App Installation ---


@router.get("/github/install")
async def github_install_url(
    user_org: tuple[User, Organization] = Depends(get_current_user_with_org),
):
    user, _ = user_org
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )

    install_url = f"https://github.com/apps/{settings.github_app_id}/installations/new"
    return {"install_url": install_url}


@router.post("/github/claim")
async def claim_github_installation(
    installation_id: int,
    user_org: tuple[User, Organization] = Depends(get_current_user_with_org),
    db: AsyncSession = Depends(get_db),
):
    user, org = user_org
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )

    org.github_installation_id = installation_id
    await db.commit()
    return {"status": "claimed", "installation_id": installation_id}


@router.get("/github/status")
async def github_status(
    user_org: tuple[User, Organization] = Depends(get_current_user_with_org),
):
    _, org = user_org
    return {
        "connected": org.github_installation_id is not None,
        "installation_id": org.github_installation_id,
    }
