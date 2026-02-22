import logging

import stripe
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_current_user_with_org, get_db, require_admin
from src.config import settings
from src.models.organization import Organization
from src.models.plan import Plan
from src.models.user import User
from src.schemas.billing import (
    BillingStatusResponse,
    CheckoutRequest,
    CheckoutResponse,
    PlanPublic,
    PortalRequest,
    PortalResponse,
)
from src.services import billing_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])


@router.get("/plans", response_model=list[PlanPublic])
async def list_plans(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Plan).where(Plan.is_public.is_(True)).order_by(Plan.sort_order)
    )
    return result.scalars().all()


@router.get("/status", response_model=BillingStatusResponse)
async def billing_status(
    user_org: tuple[User, Organization] = Depends(get_current_user_with_org),
    db: AsyncSession = Depends(get_db),
):
    _, org = user_org
    plan, subscription = await billing_service.get_org_plan(db, org.id)
    usage = await billing_service.get_usage_count(db, org.id)
    return BillingStatusResponse(
        plan_slug=plan.slug,
        plan_name=plan.name,
        agent_runs_limit=plan.agent_runs_limit,
        agent_runs_used=usage,
        subscription_status=subscription.status.value if subscription else "active",
        cancel_at_period_end=subscription.cancel_at_period_end if subscription else False,
        current_period_end=subscription.current_period_end if subscription else None,
    )


@router.post("/checkout", response_model=CheckoutResponse)
async def create_checkout(
    body: CheckoutRequest,
    user_org: tuple[User, Organization] = Depends(get_current_user_with_org),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    _, org = user_org
    url = await billing_service.create_checkout_session(
        db, org, body.plan_slug, body.success_url, body.cancel_url
    )
    return CheckoutResponse(checkout_url=url)


@router.post("/portal", response_model=PortalResponse)
async def create_portal(
    body: PortalRequest,
    user_org: tuple[User, Organization] = Depends(get_current_user_with_org),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    _, org = user_org
    url = await billing_service.create_portal_session(db, org, body.return_url)
    return PortalResponse(portal_url=url)


@router.post("/webhooks/stripe")
async def stripe_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    raw_body = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(
            raw_body, sig_header, settings.stripe_webhook_secret
        )
    except (ValueError, stripe.SignatureVerificationError):
        logger.warning("Invalid Stripe webhook signature")
        return Response(status_code=400, content="Invalid signature")

    await billing_service.handle_subscription_event(db, event)
    return Response(status_code=200, content="OK")
