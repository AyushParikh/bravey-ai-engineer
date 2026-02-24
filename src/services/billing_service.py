import logging
import uuid
from datetime import datetime, timezone

import httpx
import stripe
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.models.organization import Organization
from src.models.plan import Plan
from src.models.subscription import Subscription, SubscriptionStatus
from src.models.usage_record import UsageRecord

logger = logging.getLogger(__name__)


def _current_period() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


async def get_free_plan(db: AsyncSession) -> Plan:
    result = await db.execute(select(Plan).where(Plan.slug == "free"))
    plan = result.scalar_one_or_none()
    if not plan:
        raise RuntimeError("Free plan not found in database")
    return plan


async def get_org_plan(
    db: AsyncSession, org_id: uuid.UUID
) -> tuple[Plan, Subscription | None]:
    result = await db.execute(
        select(Subscription, Plan)
        .join(Plan, Subscription.plan_id == Plan.id)
        .where(
            Subscription.org_id == org_id,
            Subscription.status.in_(
                [SubscriptionStatus.active, SubscriptionStatus.trialing]
            ),
        )
        .order_by(Subscription.created_at.desc())
        .limit(1)
    )
    row = result.first()
    if row:
        return row.Plan, row.Subscription
    return await get_free_plan(db), None


async def get_usage_count(db: AsyncSession, org_id: uuid.UUID) -> int:
    period = _current_period()
    result = await db.execute(
        select(UsageRecord.agent_runs_count).where(
            UsageRecord.org_id == org_id,
            UsageRecord.period == period,
        )
    )
    count = result.scalar_one_or_none()
    return count or 0


async def check_usage_limit(
    db: AsyncSession, org_id: uuid.UUID
) -> tuple[bool, str, Plan | None, int]:
    plan, _ = await get_org_plan(db, org_id)
    if plan.agent_runs_limit == -1:
        return True, "", plan, 0
    usage = await get_usage_count(db, org_id)
    if usage >= plan.agent_runs_limit:
        return False, (
            f"Usage limit reached: {usage}/{plan.agent_runs_limit} "
            f"agent runs this month on the {plan.name} plan"
        ), plan, usage
    return True, "", plan, usage


async def notify_usage_limit_reached(
    org: Organization,
    plan: Plan,
    usage: int,
    source: str,
    issue_id: str | None = None,
    issue_key: str | None = None,
    pr_number: int | None = None,
    repo_full_name: str | None = None,
) -> None:
    """Post a comment on the issue/PR, send a Slack message, and unassign Bravey.

    source: "linear", "jira", or "github"
    """
    from src.services import github_service, jira_service, linear_service, slack_service

    limit = plan.agent_runs_limit
    message = (
        f"Bravey cannot pick up this ticket — your organization has used "
        f"{usage}/{limit} agent runs this month on the {plan.name} plan.\n\n"
        f"Upgrade your plan at {settings.frontend_url}/billing"
    )

    # --- Comment on the issue / PR ---
    try:
        if source == "linear" and issue_id:
            token = org.linear_bot_token or org.linear_access_token
            if token:
                linear_service.create_comment(token, issue_id, message)
                linear_service.unassign_issue(token, issue_id)

        elif source == "jira" and issue_id:
            if org.jira_client_key and org.jira_shared_secret and org.jira_base_url:
                jira_service.add_comment(
                    org.jira_client_key, org.jira_shared_secret,
                    org.jira_base_url, issue_id, message,
                )
                jira_service.unassign_issue(
                    org.jira_client_key, org.jira_shared_secret,
                    org.jira_base_url, issue_id,
                )

        elif source == "github" and pr_number and repo_full_name:
            if org.github_installation_id:
                gh_token = github_service.get_installation_token(
                    settings.github_app_id,
                    settings.github_app_private_key,
                    org.github_installation_id,
                )
                owner, repo = repo_full_name.split("/", 1)
                github_service.create_issue_comment(
                    gh_token, owner, repo, pr_number, message,
                )
    except Exception:
        logger.exception(
            "Failed to post usage-limit comment on %s issue %s",
            source, issue_id or issue_key or pr_number,
        )

    # --- Slack notification ---
    try:
        if org.slack_bot_token and org.slack_default_channel_id:
            identifier = issue_key or issue_id or (f"PR #{pr_number}" if pr_number else "unknown")
            slack_service.join_channel(org.slack_bot_token, org.slack_default_channel_id)
            httpx.post(
                "https://slack.com/api/chat.postMessage",
                json={
                    "channel": org.slack_default_channel_id,
                    "text": f"Usage limit reached for {identifier}",
                    "blocks": [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": (
                                    f":warning: *Usage limit reached*\n\n"
                                    f"Bravey cannot pick up *{identifier}* — "
                                    f"your organization has used {usage}/{limit} "
                                    f"agent runs this month on the {plan.name} plan.\n\n"
                                    f"<{settings.frontend_url}/billing|Upgrade your plan>"
                                ),
                            },
                        },
                    ],
                },
                headers={
                    "Authorization": f"Bearer {org.slack_bot_token}",
                    "Content-Type": "application/json",
                },
                timeout=10,
            )
    except Exception:
        logger.exception("Failed to send Slack usage-limit notification")


async def increment_usage(db: AsyncSession, org_id: uuid.UUID) -> int:
    period = _current_period()
    result = await db.execute(
        text(
            """
            INSERT INTO usage_records (id, org_id, period, agent_runs_count, created_at, updated_at)
            VALUES (:id, :org_id, :period, 1, now(), now())
            ON CONFLICT ON CONSTRAINT uq_usage_org_period
            DO UPDATE SET agent_runs_count = usage_records.agent_runs_count + 1,
                          updated_at = now()
            RETURNING agent_runs_count
            """
        ),
        {"id": uuid.uuid4(), "org_id": org_id, "period": period},
    )
    count = result.scalar_one()
    await db.commit()
    return count


async def ensure_stripe_customer(
    db: AsyncSession, org: Organization
) -> str:
    if org.stripe_customer_id:
        return org.stripe_customer_id

    stripe.api_key = settings.stripe_secret_key
    customer = stripe.Customer.create(
        name=org.name,
        metadata={"org_id": str(org.id), "org_slug": org.slug},
    )
    org.stripe_customer_id = customer.id
    await db.flush()
    return customer.id


async def create_checkout_session(
    db: AsyncSession,
    org: Organization,
    plan_slug: str,
    success_url: str | None = None,
    cancel_url: str | None = None,
) -> str:
    result = await db.execute(select(Plan).where(Plan.slug == plan_slug))
    plan = result.scalar_one_or_none()
    if not plan or not plan.stripe_price_id:
        raise ValueError(f"Plan '{plan_slug}' not found or has no Stripe price")

    customer_id = await ensure_stripe_customer(db, org)
    await db.commit()

    stripe.api_key = settings.stripe_secret_key
    session = stripe.checkout.Session.create(
        customer=customer_id,
        mode="subscription",
        line_items=[{"price": plan.stripe_price_id, "quantity": 1}],
        success_url=success_url or f"{settings.frontend_url}/billing?success=true",
        cancel_url=cancel_url or f"{settings.frontend_url}/billing?canceled=true",
        metadata={"org_id": str(org.id), "plan_slug": plan_slug},
    )
    return session.url


async def create_portal_session(
    db: AsyncSession,
    org: Organization,
    return_url: str | None = None,
) -> str:
    customer_id = await ensure_stripe_customer(db, org)
    await db.commit()

    stripe.api_key = settings.stripe_secret_key
    session = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=return_url or f"{settings.frontend_url}/billing",
    )
    return session.url


async def handle_subscription_event(
    db: AsyncSession, event: dict
) -> None:
    event_type = event["type"]
    obj = event["data"]["object"]

    if event_type not in (
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    ):
        return

    stripe_customer_id = obj["customer"]
    stripe_subscription_id = obj["id"]
    status_str = obj["status"]

    # Map Stripe status to our enum
    status_map = {
        "active": SubscriptionStatus.active,
        "past_due": SubscriptionStatus.past_due,
        "canceled": SubscriptionStatus.canceled,
        "trialing": SubscriptionStatus.trialing,
        "incomplete": SubscriptionStatus.incomplete,
        "incomplete_expired": SubscriptionStatus.canceled,
        "unpaid": SubscriptionStatus.past_due,
    }
    status = status_map.get(status_str, SubscriptionStatus.incomplete)

    # Find org by stripe_customer_id
    result = await db.execute(
        select(Organization).where(
            Organization.stripe_customer_id == stripe_customer_id
        )
    )
    org = result.scalar_one_or_none()
    if not org:
        logger.warning("No org found for Stripe customer %s", stripe_customer_id)
        return

    # Find plan by stripe_price_id from the subscription items
    stripe_price_id = obj["items"]["data"][0]["price"]["id"]
    result = await db.execute(
        select(Plan).where(Plan.stripe_price_id == stripe_price_id)
    )
    plan = result.scalar_one_or_none()
    if not plan:
        logger.warning("No plan found for Stripe price %s", stripe_price_id)
        return

    # Upsert subscription
    result = await db.execute(
        select(Subscription).where(
            Subscription.stripe_subscription_id == stripe_subscription_id
        )
    )
    subscription = result.scalar_one_or_none()

    period_start = datetime.fromtimestamp(
        obj["current_period_start"], tz=timezone.utc
    ) if obj.get("current_period_start") else None
    period_end = datetime.fromtimestamp(
        obj["current_period_end"], tz=timezone.utc
    ) if obj.get("current_period_end") else None

    if subscription:
        subscription.plan_id = plan.id
        subscription.status = status
        subscription.current_period_start = period_start
        subscription.current_period_end = period_end
        subscription.cancel_at_period_end = obj.get("cancel_at_period_end", False)
    else:
        subscription = Subscription(
            org_id=org.id,
            plan_id=plan.id,
            stripe_customer_id=stripe_customer_id,
            stripe_subscription_id=stripe_subscription_id,
            status=status,
            current_period_start=period_start,
            current_period_end=period_end,
            cancel_at_period_end=obj.get("cancel_at_period_end", False),
        )
        db.add(subscription)

    await db.commit()
    logger.info(
        "Processed %s for org %s — plan=%s status=%s",
        event_type, org.id, plan.slug, status.value,
    )
