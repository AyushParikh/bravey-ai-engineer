import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, Depends, Header, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_db
from src.config import settings
from src.models.agent_run import AgentRun, RunStatus
from src.models.organization import Organization
from src.models.repository import Repository
from src.models.webhook_event import WebhookEvent
from src.schemas.linear import LinearWebhookPayload
from src.services import linear_service, sqs_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/linear")
async def linear_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    linear_signature: str = Header(alias="Linear-Signature", default=""),
    linear_delivery: str = Header(alias="Linear-Delivery", default=""),
):
    raw_body = await request.body()

    # Parse payload first to get organizationId for signature verification
    try:
        payload_dict = json.loads(raw_body)
        payload = LinearWebhookPayload(**payload_dict)
    except Exception:
        return Response(status_code=400, content="Invalid payload")

    # Look up organization
    result = await db.execute(
        select(Organization).where(
            Organization.linear_org_id == payload.organizationId
        )
    )
    org = result.scalar_one_or_none()
    if not org:
        return Response(status_code=404, content="Organization not found")

    # Verify HMAC signature
    if not org.linear_webhook_secret or not linear_service.verify_signature(
        raw_body, linear_signature, org.linear_webhook_secret
    ):
        # Persist event even if signature fails
        event = WebhookEvent(
            org_id=org.id,
            source="linear",
            event_type=payload.type,
            delivery_id=linear_delivery,
            payload=payload_dict,
            signature_ok=False,
            processed=False,
        )
        db.add(event)
        await db.commit()
        return Response(status_code=401, content="Invalid signature")

    # Verify timestamp freshness
    if not linear_service.verify_timestamp(payload.webhookTimestamp):
        return Response(status_code=400, content="Stale webhook")

    # Persist webhook event
    event = WebhookEvent(
        org_id=org.id,
        source="linear",
        event_type=payload.type,
        delivery_id=linear_delivery,
        payload=payload_dict,
        signature_ok=True,
        processed=False,
    )
    db.add(event)
    await db.flush()

    # Check trigger condition: assignee just changed TO the Bravey bot user.
    # We require updatedFrom to contain an assigneeId field (meaning the
    # assignee actually changed), and the *previous* assignee must not have
    # been the bot. This prevents re-triggering when the bot itself updates
    # the issue state/comments (which also fires an "update" webhook but
    # without an assigneeId in updatedFrom).
    assignee_changed = (
        payload.updatedFrom is not None
        and "assigneeId" in (payload.updatedFrom.model_fields_set or set())
    )
    triggered = (
        payload.action == "update"
        and payload.data.assigneeId == org.linear_bravey_user_id
        and assignee_changed
        and payload.updatedFrom.assigneeId != org.linear_bravey_user_id
    )

    if not triggered:
        event.processed = True
        await db.commit()
        return Response(status_code=200, content="OK - not triggered")

    # Check for existing active run for this issue (prevent duplicates)
    # Use advisory lock based on hash of issue ID to prevent race conditions
    from sqlalchemy import text

    lock_key = hash(payload.data.id) % (2**31)
    lock_result = await db.execute(text(f"SELECT pg_try_advisory_xact_lock({lock_key})"))
    got_lock = lock_result.scalar()
    if not got_lock:
        event.processed = True
        await db.commit()
        return Response(status_code=200, content="OK - concurrent processing")

    from datetime import datetime, timedelta, timezone

    recent_cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
    existing_run = await db.execute(
        select(AgentRun).where(
            AgentRun.org_id == org.id,
            AgentRun.linear_issue_id == payload.data.id,
            (
                AgentRun.status.in_(["queued", "running"])
                | (
                    (AgentRun.status == "success")
                    & (AgentRun.completed_at >= recent_cutoff)
                )
            ),
        )
    )
    if existing_run.scalar_one_or_none():
        event.processed = True
        await db.commit()
        return Response(status_code=200, content="OK - run already in progress")

    # Find the repository for this org (pick the first active one for now)
    repo_result = await db.execute(
        select(Repository).where(
            Repository.org_id == org.id,
            Repository.is_active.is_(True),
        ).limit(1)
    )
    repo = repo_result.scalar_one_or_none()
    if not repo:
        logger.error(f"No active repository found for org {org.id}")
        event.processed = True
        await db.commit()
        return Response(status_code=200, content="OK - no active repository")

    # Create agent run
    run = AgentRun(
        org_id=org.id,
        repo_id=repo.id,
        linear_issue_id=payload.data.id,
        linear_issue_identifier=payload.data.identifier,
        linear_issue_title=payload.data.title,
        linear_issue_url=payload.data.url,
        status=RunStatus.queued,
    )
    db.add(run)
    await db.flush()

    # Link event to run
    event.run_id = run.id
    event.processed = True
    await db.commit()

    # Enqueue to SQS
    try:
        sqs_service.send_run_message(str(run.id))
    except Exception:
        logger.exception(f"Failed to enqueue run {run.id} to SQS")

    return Response(status_code=200, content="OK")


@router.post("/github")
async def github_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_hub_signature_256: str = Header(alias="X-Hub-Signature-256", default=""),
    x_github_event: str = Header(alias="X-GitHub-Event", default=""),
    x_github_delivery: str = Header(alias="X-GitHub-Delivery", default=""),
):
    raw_body = await request.body()

    # Verify GitHub webhook signature
    if settings.github_webhook_secret:
        expected = (
            "sha256="
            + hmac.new(
                settings.github_webhook_secret.encode("utf-8"),
                raw_body,
                hashlib.sha256,
            ).hexdigest()
        )
        if not hmac.compare_digest(expected, x_hub_signature_256):
            return Response(status_code=401, content="Invalid signature")

    try:
        payload = json.loads(raw_body)
    except Exception:
        return Response(status_code=400, content="Invalid payload")

    # Handle pull_request.closed (merged) — update Linear to Done
    if x_github_event == "pull_request" and payload.get("action") == "closed":
        pr = payload.get("pull_request", {})
        if not pr.get("merged"):
            return Response(status_code=200, content="OK - PR closed without merge")

        # Find the agent run by PR number and repo
        pr_number = pr.get("number")
        repo_full_name = payload.get("repository", {}).get("full_name", "")

        result = await db.execute(
            select(AgentRun)
            .join(Repository, AgentRun.repo_id == Repository.id)
            .where(
                AgentRun.pr_number == pr_number,
                Repository.full_name == repo_full_name,
            )
        )
        run = result.scalar_one_or_none()
        if not run:
            return Response(status_code=200, content="OK - no matching run")

        # Persist webhook event
        event = WebhookEvent(
            org_id=run.org_id,
            source="github",
            event_type=f"pull_request.{payload['action']}",
            delivery_id=x_github_delivery,
            payload=payload,
            signature_ok=True,
            processed=True,
            run_id=run.id,
        )
        db.add(event)
        await db.commit()

        # Update Linear issue state to Done is handled elsewhere (onboarding stores done state)
        # For now, just log the event
        logger.info(
            f"PR #{pr_number} merged for run {run.id}, "
            f"Linear issue {run.linear_issue_identifier}"
        )

    return Response(status_code=200, content="OK")
