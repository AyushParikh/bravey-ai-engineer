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
from src.models.repository import Repository
from src.models.user import User
from src.services import github_service, linear_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations", tags=["integrations"])

LINEAR_AUTHORIZE_URL = "https://linear.app/oauth/authorize"
LINEAR_TOKEN_URL = "https://api.linear.app/oauth/token"

SLACK_AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"
SLACK_TOKEN_URL = "https://slack.com/api/oauth.v2.access"


# --- Slack OAuth ---


@router.get("/slack/connect")
async def slack_connect(
    user_org: tuple[User, Organization] = Depends(get_current_user_with_org),
):
    user, org = user_org
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    state = f"{org.id}:{uuid.uuid4()}"
    params = {
        "client_id": settings.slack_client_id,
        "scope": "chat:write,channels:join,users:read,users:read.email",
        "redirect_uri": settings.slack_redirect_uri,
        "state": state,
    }
    return {"authorization_url": f"{SLACK_AUTHORIZE_URL}?{urlencode(params)}", "state": state}


@router.get("/slack/callback")
async def slack_callback(
    code: str,
    state: str = "",
    db: AsyncSession = Depends(get_db),
):
    try:
        org_id_str = state.split(":")[0]
        org_id = uuid.UUID(org_id_str)
    except (ValueError, IndexError):
        raise HTTPException(status_code=400, detail="Invalid state parameter")

    result = await db.execute(
        select(Organization).where(Organization.id == org_id)
    )
    org = result.scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    try:
        resp = httpx.post(
            SLACK_TOKEN_URL,
            data={
                "client_id": settings.slack_client_id,
                "client_secret": settings.slack_client_secret,
                "code": code,
                "redirect_uri": settings.slack_redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        resp.raise_for_status()
        token_data = resp.json()
    except Exception:
        logger.exception("Failed to exchange Slack OAuth code")
        raise HTTPException(status_code=400, detail="Failed to exchange code for token")

    if not token_data.get("ok"):
        raise HTTPException(
            status_code=400,
            detail=f"Slack OAuth error: {token_data.get('error', 'unknown')}",
        )

    org.slack_bot_token = token_data["access_token"]
    org.slack_team_id = token_data.get("team", {}).get("id")
    await db.commit()

    return {
        "status": "connected",
        "slack_team_id": org.slack_team_id,
    }


@router.get("/slack/status")
async def slack_status(
    user_org: tuple[User, Organization] = Depends(get_current_user_with_org),
):
    _, org = user_org
    return {
        "connected": org.slack_bot_token is not None,
        "slack_team_id": org.slack_team_id,
        "default_channel_id": org.slack_default_channel_id,
    }


class SlackChannelRequest(BaseModel):
    channel_id: str


@router.post("/slack/channel")
async def slack_set_channel(
    body: SlackChannelRequest,
    user_org: tuple[User, Organization] = Depends(get_current_user_with_org),
    db: AsyncSession = Depends(get_db),
):
    user, org = user_org
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    if not org.slack_bot_token:
        raise HTTPException(status_code=400, detail="Connect Slack first")

    org.slack_default_channel_id = body.channel_id
    await db.commit()
    return {"status": "ok", "default_channel_id": org.slack_default_channel_id}


@router.post("/slack/disconnect")
async def slack_disconnect(
    user_org: tuple[User, Organization] = Depends(get_current_user_with_org),
    db: AsyncSession = Depends(get_db),
):
    user, org = user_org
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    org.slack_bot_token = None
    org.slack_team_id = None
    org.slack_default_channel_id = None
    await db.commit()
    return {"status": "disconnected"}


# --- Linear OAuth ---


@router.get("/linear/connect")
async def linear_connect(
    user_org: tuple[User, Organization] = Depends(get_current_user_with_org),
):
    """Step 1: Admin authorizes Linear (creates webhook, stores API token)."""
    user, org = user_org
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )

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
    # Extract org_id and flow type from state
    # State format: "org_id:nonce" (admin connect) or "bot:org_id:nonce" (bot install)
    try:
        parts = state.split(":")
        if parts[0] == "bot":
            return await _handle_bot_callback(code, parts[1], db)
        else:
            return await _handle_admin_callback(code, parts[0], db)
    except (ValueError, IndexError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid state parameter",
        )


async def _handle_admin_callback(code: str, org_id_str: str, db: AsyncSession):
    """Handle the admin OAuth callback — store token and create webhook."""
    org_id = uuid.UUID(org_id_str)
    result = await db.execute(
        select(Organization).where(Organization.id == org_id)
    )
    org = result.scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")

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
        raise HTTPException(status_code=400, detail="Failed to exchange code for token")

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
        raise HTTPException(status_code=400, detail="Failed to fetch Linear organization info")

    org.linear_access_token = access_token
    org.linear_org_id = linear_org.get("id")

    # Create webhook (requires admin-level token)
    webhook_secret = secrets.token_hex(32)
    try:
        webhook_url = f"{settings.frontend_url.rstrip('/')}/webhooks/linear"

        # Delete any existing webhook for this URL first
        existing = linear_service.graphql_request(
            access_token,
            "query { webhooks { nodes { id url } } }",
        )
        for wh in ((existing.get("data") or {}).get("webhooks") or {}).get("nodes") or []:
            if wh.get("url") == webhook_url:
                linear_service.graphql_request(
                    access_token,
                    "mutation($id: String!) { webhookDelete(id: $id) { success } }",
                    {"id": wh["id"]},
                )
                logger.info(f"Deleted existing Linear webhook {wh['id']}")

        wh_result = linear_service.graphql_request(
            access_token,
            """
            mutation CreateWebhook($url: String!, $secret: String!) {
                webhookCreate(input: {
                    url: $url
                    secret: $secret
                    resourceTypes: ["Issue"]
                    allPublicTeams: true
                    enabled: true
                }) {
                    success
                    webhook { id }
                }
            }
            """,
            {"url": webhook_url, "secret": webhook_secret},
        )
        wh_data = (wh_result.get("data") or {}).get("webhookCreate") or {}
        if wh_data.get("success"):
            org.linear_webhook_id = wh_data["webhook"]["id"]
            org.linear_webhook_secret = webhook_secret
        else:
            errors = wh_result.get("errors", [])
            logger.error(f"Webhook creation failed: {errors}")
    except Exception:
        logger.exception("Failed to create Linear webhook")

    await db.commit()

    next_step = ""
    if not org.linear_webhook_id:
        next_step = "Webhook creation failed — check logs."
    elif not org.linear_bravey_user_id:
        next_step = "Install the bot: GET /integrations/linear/install-bot"

    return {
        "status": "connected",
        "linear_org_id": org.linear_org_id,
        "webhook_configured": org.linear_webhook_id is not None,
        "bot_installed": org.linear_bravey_user_id is not None,
        "next_step": next_step,
    }


@router.get("/linear/install-bot")
async def linear_install_bot(
    user_org: tuple[User, Organization] = Depends(get_current_user_with_org),
):
    """Step 2: Install Bravey as a bot user in the workspace (actor=app)."""
    user, org = user_org
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )

    if not org.linear_access_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Connect Linear first via /integrations/linear/connect",
        )

    state = f"bot:{org.id}:{uuid.uuid4()}"
    params = {
        "client_id": settings.linear_client_id,
        "response_type": "code",
        "scope": "read,write,issues:create,comments:create,app:assignable",
        "actor": "app",
        "state": state,
        "redirect_uri": settings.linear_redirect_uri,
        "prompt": "consent",
    }
    return {"authorization_url": f"{LINEAR_AUTHORIZE_URL}?{urlencode(params)}", "state": state}


async def _handle_bot_callback(code: str, org_id_str: str, db: AsyncSession):
    """Handle the bot install callback — store bot user ID."""
    org_id = uuid.UUID(org_id_str)
    result = await db.execute(
        select(Organization).where(Organization.id == org_id)
    )
    org = result.scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    # Exchange code for bot token
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
        logger.exception("Failed to exchange Linear bot OAuth code")
        raise HTTPException(status_code=400, detail="Failed to exchange code for token")

    bot_token = token_data["access_token"]

    # Get the bot's user ID (viewer query returns the app's identity)
    try:
        viewer_info = linear_service.graphql_request(
            bot_token,
            "query { viewer { id name displayName } }",
        )
        viewer = (viewer_info.get("data") or {}).get("viewer") or {}
        bot_user_id = viewer.get("id")
    except Exception:
        logger.exception("Failed to fetch bot user info")
        raise HTTPException(status_code=400, detail="Failed to fetch bot user info")

    if not bot_user_id:
        raise HTTPException(status_code=400, detail="Could not determine bot user ID")

    org.linear_bravey_user_id = bot_user_id
    org.linear_bot_token = bot_token
    await db.commit()

    return {
        "status": "bot_installed",
        "bot_user_id": bot_user_id,
        "bot_name": viewer.get("displayName") or viewer.get("name"),
    }


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


@router.get("/linear/members")
async def linear_members(
    user_org: tuple[User, Organization] = Depends(get_current_user_with_org),
):
    """List all Linear workspace members."""
    _, org = user_org
    if not org.linear_access_token:
        raise HTTPException(status_code=400, detail="Linear not connected")

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
    users = ((result.get("data") or {}).get("users") or {}).get("nodes") or []
    return {"members": users}


class BotUserRequest(BaseModel):
    linear_user_id: str


@router.post("/linear/bot-user")
async def set_bot_user(
    body: BotUserRequest,
    user_org: tuple[User, Organization] = Depends(get_current_user_with_org),
    db: AsyncSession = Depends(get_db),
):
    """Manually set the bot user ID (alternative to install-bot flow)."""
    user, org = user_org
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    org.linear_bravey_user_id = body.linear_user_id
    await db.commit()
    return {"status": "ok", "linear_bravey_user_id": org.linear_bravey_user_id}


@router.post("/linear/disconnect")
async def linear_disconnect(
    user_org: tuple[User, Organization] = Depends(get_current_user_with_org),
    db: AsyncSession = Depends(get_db),
):
    user, org = user_org
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    org.linear_access_token = None
    org.linear_org_id = None
    org.linear_webhook_id = None
    org.linear_webhook_secret = None
    org.linear_bravey_user_id = None
    org.linear_bot_token = None

    await db.commit()
    return {"status": "disconnected"}


# --- GitHub App Installation ---


@router.get("/github/install")
async def github_install_url(
    user_org: tuple[User, Organization] = Depends(get_current_user_with_org),
):
    user, _ = user_org
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    # Look up the app slug via the GitHub API
    app_jwt = github_service.generate_jwt(settings.github_app_id, settings.github_app_private_key)
    resp = httpx.get(
        "https://api.github.com/app",
        headers={
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github+json",
        },
        timeout=15,
    )
    resp.raise_for_status()
    app_slug = resp.json()["slug"]
    install_url = f"https://github.com/apps/{app_slug}/installations/new"
    return {"install_url": install_url}


@router.post("/github/claim")
async def claim_github_installation(
    installation_id: int,
    user_org: tuple[User, Organization] = Depends(get_current_user_with_org),
    db: AsyncSession = Depends(get_db),
):
    user, org = user_org
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

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


@router.get("/github/repos")
async def list_github_repos(
    user_org: tuple[User, Organization] = Depends(get_current_user_with_org),
):
    """List repositories accessible to the GitHub App installation."""
    _, org = user_org
    if not org.github_installation_id:
        raise HTTPException(status_code=400, detail="GitHub App not installed")

    token = github_service.get_installation_token(
        settings.github_app_id,
        settings.github_app_private_key,
        org.github_installation_id,
    )
    resp = httpx.get(
        "https://api.github.com/installation/repositories",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        params={"per_page": 100},
        timeout=15,
    )
    resp.raise_for_status()
    repos = resp.json().get("repositories", [])
    return {
        "repositories": [
            {
                "id": r["id"],
                "full_name": r["full_name"],
                "default_branch": r.get("default_branch", "main"),
            }
            for r in repos
        ]
    }


class AddRepoRequest(BaseModel):
    github_repo_id: int
    full_name: str
    default_branch: str = "main"


@router.post("/github/repos")
async def add_github_repo(
    body: AddRepoRequest,
    user_org: tuple[User, Organization] = Depends(get_current_user_with_org),
    db: AsyncSession = Depends(get_db),
):
    """Register a repository for this organization."""
    user, org = user_org
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    # Check if already registered
    existing = await db.execute(
        select(Repository).where(Repository.github_repo_id == body.github_repo_id)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Repository already registered")

    parts = body.full_name.split("/", 1)
    repo = Repository(
        org_id=org.id,
        github_repo_id=body.github_repo_id,
        github_owner=parts[0],
        github_repo_name=parts[1] if len(parts) > 1 else parts[0],
        full_name=body.full_name,
        default_branch=body.default_branch,
    )
    db.add(repo)
    await db.commit()
    await db.refresh(repo)

    return {
        "status": "added",
        "id": str(repo.id),
        "full_name": repo.full_name,
    }
