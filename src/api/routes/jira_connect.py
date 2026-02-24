import logging

from fastapi import APIRouter, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_db
from src.config import settings
from src.models.organization import Organization
from src.schemas.jira import JiraInstallPayload
from src.services import jira_service

from fastapi import Depends

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jira", tags=["jira-connect"])


@router.get("/atlassian-connect.json")
async def atlassian_connect_descriptor(request: Request):
    """Serve the Atlassian Connect app descriptor."""
    base_url = str(request.base_url).rstrip("/")

    descriptor = {
        "name": "Bravey",
        "description": "AI-powered software engineer that picks up Jira issues and opens PRs.",
        "key": settings.jira_connect_app_key,
        "baseUrl": base_url,
        "vendor": {
            "name": "Bravey",
            "url": "https://bravey.co",
        },
        "authentication": {"type": "jwt"},
        "lifecycle": {
            "installed": "/jira/lifecycle/installed",
            "uninstalled": "/jira/lifecycle/uninstalled",
        },
        "scopes": ["READ", "WRITE", "ACT_AS_USER"],
        "modules": {
            "webhooks": [
                {
                    "event": "jira:issue_created",
                    "url": "/webhooks/jira",
                },
                {
                    "event": "jira:issue_updated",
                    "url": "/webhooks/jira",
                },
            ]
        },
        "apiVersion": 1,
        "apiMigrations": {"gdpr": True},
    }
    return descriptor


@router.post("/lifecycle/installed")
async def lifecycle_installed(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Called when an org installs the Bravey Connect app."""
    try:
        body = await request.json()
        payload = JiraInstallPayload(**body)
    except Exception:
        logger.exception("Invalid install payload")
        return Response(status_code=400, content="Invalid payload")

    # Check if this clientKey is already registered
    result = await db.execute(
        select(Organization).where(
            Organization.jira_client_key == payload.clientKey
        )
    )
    existing_org = result.scalar_one_or_none()

    if existing_org:
        # Update existing installation (re-install)
        existing_org.jira_shared_secret = payload.sharedSecret
        existing_org.jira_base_url = payload.baseUrl
    else:
        # Find an org without Jira connected that we can link.
        # For now, store the installation — the admin will link it via the
        # configure endpoints. We create a lookup entry so we can find it later.
        # Check if any org has Linear connected — enforce either/or on the
        # integrations.py side, not here. Just store it on the first unclaimed org.

        # Look for an org that has no jira_client_key set.
        # In production, the onboarding flow will have already created the org.
        # The admin will call POST /integrations/jira/claim to link their org.
        # For now, we'll store it and let the claim endpoint handle linking.
        logger.info(
            "New Jira Connect installation: clientKey=%s, baseUrl=%s",
            payload.clientKey,
            payload.baseUrl,
        )
        # Store as unclaimed — will be picked up via integrations endpoint
        # We can't know which org this belongs to yet from the lifecycle alone.
        # Return 200 so Jira knows installation succeeded.
        # The admin will claim it via POST /integrations/jira/claim
        #
        # Alternative: if there's exactly one org with no Jira, auto-assign.
        result = await db.execute(
            select(Organization).where(
                Organization.jira_client_key.is_(None)
            ).limit(1)
        )
        org = result.scalar_one_or_none()
        if not org:
            logger.warning("No available org to link Jira installation to")
            return {"status": "installed", "note": "No org available to auto-link"}

        # Either/or guard: reject if Linear is connected
        if org.linear_access_token:
            logger.warning(
                "Org %s already has Linear connected, cannot install Jira", org.id
            )
            return Response(
                status_code=400,
                content="Organization already has Linear connected. Disconnect Linear first.",
            )

        org.jira_client_key = payload.clientKey
        org.jira_shared_secret = payload.sharedSecret
        org.jira_base_url = payload.baseUrl
        existing_org = org

    # Auto-detect bot user
    if existing_org and settings.jira_bot_email:
        try:
            bot_user = jira_service.find_user_by_email(
                payload.clientKey,
                payload.sharedSecret,
                payload.baseUrl,
                settings.jira_bot_email,
            )
            if bot_user:
                existing_org.jira_bravey_account_id = bot_user["accountId"]
                logger.info(
                    "Auto-detected bot user: %s (%s)",
                    bot_user.get("displayName"),
                    bot_user["accountId"],
                )
        except Exception:
            logger.exception("Failed to auto-detect bot user")

    await db.commit()
    return {"status": "installed"}


@router.post("/lifecycle/uninstalled")
async def lifecycle_uninstalled(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Called when an org uninstalls the Bravey Connect app."""
    try:
        body = await request.json()
        client_key = body.get("clientKey")
    except Exception:
        return Response(status_code=400, content="Invalid payload")

    if not client_key:
        return Response(status_code=400, content="Missing clientKey")

    result = await db.execute(
        select(Organization).where(
            Organization.jira_client_key == client_key
        )
    )
    org = result.scalar_one_or_none()
    if org:
        org.jira_client_key = None
        org.jira_shared_secret = None
        org.jira_base_url = None
        org.jira_bravey_account_id = None
        org.jira_in_progress_status_id = None
        org.jira_in_review_status_id = None
        await db.commit()
        logger.info("Jira Connect app uninstalled for org %s", org.id)

    return {"status": "uninstalled"}
