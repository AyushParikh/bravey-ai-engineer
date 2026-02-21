import logging
import secrets
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
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

    state = str(uuid.uuid4())
    params = {
        "client_id": settings.linear_client_id,
        "response_type": "code",
        "scope": "read,write,issues:create,comments:create",
        "state": state,
        "redirect_uri": settings.linear_redirect_uri,
        "prompt": "consent",
    }
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return {"authorization_url": f"{LINEAR_AUTHORIZE_URL}?{qs}", "state": state}


@router.get("/linear/callback")
async def linear_callback(
    code: str,
    state: str | None = None,
    user_org: tuple[User, Organization] = Depends(get_current_user_with_org),
    db: AsyncSession = Depends(get_db),
):
    user, org = user_org
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
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
            "query { organization { id name } viewer { id name email } }",
        )
        linear_org = org_info.get("data", {}).get("organization", {})
        viewer = org_info.get("data", {}).get("viewer", {})
    except Exception:
        logger.exception("Failed to fetch Linear organization info")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to fetch Linear organization info",
        )

    # Store credentials on org
    org.linear_access_token = access_token
    org.linear_org_id = linear_org.get("id")
    org.linear_bravey_user_id = viewer.get("id")

    # Create webhook for Linear
    webhook_secret = secrets.token_hex(32)
    try:
        webhook_url = f"{settings.frontend_url.rstrip('/')}/webhooks/linear"
        # If we have an API URL different from frontend, use it
        # For now, derive from the backend
        result = linear_service.graphql_request(
            access_token,
            """
            mutation CreateWebhook($url: String!, $secret: String!, $teamId: String) {
                webhookCreate(input: {
                    url: $url
                    secret: $secret
                    teamId: $teamId
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
        webhook_data = result.get("data", {}).get("webhookCreate", {})
        if webhook_data.get("success"):
            org.linear_webhook_id = webhook_data["webhook"]["id"]
            org.linear_webhook_secret = webhook_secret
    except Exception:
        logger.exception("Failed to create Linear webhook")
        # Non-fatal — credentials still stored

    await db.commit()
    return {"status": "connected", "linear_org_id": org.linear_org_id}


@router.get("/linear/status")
async def linear_status(
    user_org: tuple[User, Organization] = Depends(get_current_user_with_org),
):
    _, org = user_org
    return {
        "connected": org.linear_access_token is not None,
        "linear_org_id": org.linear_org_id,
        "webhook_configured": org.linear_webhook_id is not None,
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
