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
from src.schemas.jira import JiraWebhookPayload
from src.schemas.linear import LinearWebhookPayload
from src.services import billing_service, jira_service, linear_service, sqs_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


async def _handle_pr_comment(
    db: AsyncSession,
    event_type: str,
    delivery_id: str,
    payload: dict,
) -> None:
    """Handle issue_comment or pull_request_review_comment on a Bravey PR."""
    # Ignore comments from bots to prevent infinite loops
    sender = payload.get("sender", {})
    if sender.get("type") == "Bot":
        logger.info("Ignoring PR comment from bot user %s", sender.get("login"))
        return

    # Determine PR number and repo
    repo_full_name = payload.get("repository", {}).get("full_name", "")

    if event_type == "pull_request_review_comment":
        pr_number = payload.get("pull_request", {}).get("number")
        comment = payload.get("comment", {})
        comment_id = comment.get("id")
    else:
        # issue_comment — only handle if it's on a PR (has pull_request key)
        issue = payload.get("issue", {})
        if "pull_request" not in issue:
            return
        pr_number = issue.get("number")
        comment = payload.get("comment", {})
        comment_id = comment.get("id")

    if not pr_number or not comment_id:
        return

    # Find the parent AgentRun that opened this PR
    result = await db.execute(
        select(AgentRun)
        .join(Repository, AgentRun.repo_id == Repository.id)
        .where(
            AgentRun.pr_number == pr_number,
            Repository.full_name == repo_full_name,
            AgentRun.trigger_type.in_(["linear_assignment", "jira_assignment"]),
            AgentRun.status == RunStatus.success,
        )
        .order_by(AgentRun.created_at.desc())
        .limit(1)
    )
    parent_run = result.scalar_one_or_none()
    if not parent_run:
        logger.info(
            "No Bravey parent run found for PR #%s on %s", pr_number, repo_full_name
        )
        return

    # Persist webhook event
    event = WebhookEvent(
        org_id=parent_run.org_id,
        source="github",
        event_type=f"{event_type}.created",
        delivery_id=delivery_id,
        payload=payload,
        signature_ok=True,
        processed=False,
    )
    db.add(event)
    await db.flush()

    # Check usage limit before creating a run
    allowed, reason, plan, usage = await billing_service.check_usage_limit(db, parent_run.org_id)
    if not allowed:
        event.processed = True
        await db.commit()
        # Load org for notifications
        org_result = await db.execute(
            select(Organization).where(Organization.id == parent_run.org_id)
        )
        org = org_result.scalar_one_or_none()
        if org and plan:
            await billing_service.notify_usage_limit_reached(
                org=org,
                plan=plan,
                usage=usage,
                source="github",
                pr_number=pr_number,
                repo_full_name=repo_full_name,
            )
        logger.warning("PR comment run skipped for org %s: %s", parent_run.org_id, reason)
        return

    # Create a follow-up AgentRun (copy both Linear and Jira fields from parent)
    run = AgentRun(
        org_id=parent_run.org_id,
        repo_id=parent_run.repo_id,
        linear_issue_id=parent_run.linear_issue_id,
        linear_issue_identifier=parent_run.linear_issue_identifier,
        linear_issue_title=parent_run.linear_issue_title,
        linear_issue_url=parent_run.linear_issue_url,
        jira_issue_id=parent_run.jira_issue_id,
        jira_issue_key=parent_run.jira_issue_key,
        jira_issue_summary=parent_run.jira_issue_summary,
        jira_issue_url=parent_run.jira_issue_url,
        status=RunStatus.queued,
        branch_name=parent_run.branch_name,
        pr_number=parent_run.pr_number,
        pr_url=parent_run.pr_url,
        parent_run_id=parent_run.id,
        trigger_type="pr_comment",
        trigger_comment_id=comment_id,
    )
    db.add(run)
    await db.flush()

    event.run_id = run.id
    event.processed = True
    await db.commit()

    # Increment usage after successful run creation
    await billing_service.increment_usage(db, parent_run.org_id)

    # Enqueue to SQS
    try:
        sqs_service.send_run_message(str(run.id))
        logger.info(
            "Enqueued PR comment follow-up run %s for PR #%s", run.id, pr_number
        )
    except Exception:
        logger.exception(f"Failed to enqueue PR comment run {run.id} to SQS")


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

    # Check trigger condition: Bravey bot was assigned or delegated.
    # Supports both regular assignment (assigneeId) and agent delegation (delegateId).
    bot_id = org.linear_bravey_user_id
    assigned_to_bravey = payload.data.assigneeId == bot_id
    delegated_to_bravey = payload.data.delegateId == bot_id

    if payload.action == "create":
        triggered = assigned_to_bravey or delegated_to_bravey
    elif payload.action == "update":
        updated_fields = payload.updatedFrom.model_fields_set if payload.updatedFrom else set()

        assignee_changed = (
            "assigneeId" in updated_fields
            and payload.updatedFrom.assigneeId != bot_id
        )
        delegate_changed = (
            "delegateId" in updated_fields
            and payload.updatedFrom.delegateId != bot_id
        )

        triggered = (
            (assigned_to_bravey and assignee_changed)
            or (delegated_to_bravey and delegate_changed)
        )
    else:
        triggered = False

    if not triggered:
        event.processed = True
        await db.commit()
        return Response(status_code=200, content="OK - not triggered")

    # Check usage limit before creating a run
    allowed, reason, plan, usage = await billing_service.check_usage_limit(db, org.id)
    if not allowed:
        event.processed = True
        await db.commit()
        await billing_service.notify_usage_limit_reached(
            org=org,
            plan=plan,
            usage=usage,
            source="linear",
            issue_id=payload.data.id,
            issue_key=payload.data.identifier,
            is_delegate=delegated_to_bravey,
        )
        return Response(status_code=200, content="OK - usage limit reached")

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

    # Increment usage after successful run creation
    await billing_service.increment_usage(db, org.id)

    # Enqueue to SQS
    try:
        sqs_service.send_run_message(str(run.id))
    except Exception:
        logger.exception(f"Failed to enqueue run {run.id} to SQS")

    return Response(status_code=200, content="OK")


@router.post("/github/app")
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

    # Handle PR comment events — trigger Bravey follow-up runs
    if x_github_event in ("issue_comment", "pull_request_review_comment") and payload.get("action") == "created":
        await _handle_pr_comment(db, x_github_event, x_github_delivery, payload)
        return Response(status_code=200, content="OK")

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

    # Handle installation events — link installation_id to org
    if x_github_event == "installation" and payload.get("action") == "created":
        installation_id = payload.get("installation", {}).get("id")
        sender_login = payload.get("sender", {}).get("login")
        if installation_id and sender_login:
            from src.models.user import User

            # Find user by github_login, then update their org
            user_result = await db.execute(
                select(User).where(User.github_login == sender_login)
            )
            user = user_result.scalar_one_or_none()
            if user and user.org_id:
                org_result = await db.execute(
                    select(Organization).where(Organization.id == user.org_id)
                )
                org = org_result.scalar_one_or_none()
                if org:
                    org.github_installation_id = installation_id
                    await db.commit()
                    logger.info(
                        f"Linked GitHub installation {installation_id} to org {org.id}"
                    )

    return Response(status_code=200, content="OK")


@router.post("/jira")
async def jira_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Handle Jira webhooks from the Connect app."""
    raw_body = await request.body()

    try:
        payload_dict = json.loads(raw_body)
        payload = JiraWebhookPayload(**payload_dict)
    except Exception:
        return Response(status_code=400, content="Invalid payload")

    if not payload.issue:
        return Response(status_code=200, content="OK - no issue")

    # Extract client key from the Authorization header JWT claims
    auth_header = request.headers.get("Authorization", "")

    # Decode JWT without verification to get `iss` (clientKey),
    # then verify with the org's shared secret.
    import jwt as pyjwt

    token = auth_header
    if token.upper().startswith("JWT "):
        token = token[4:]

    try:
        unverified = pyjwt.decode(
            token, options={"verify_signature": False}, algorithms=["HS256"]
        )
        client_key = unverified.get("iss")
    except Exception:
        return Response(status_code=401, content="Invalid JWT")

    if not client_key:
        return Response(status_code=401, content="Missing client key in JWT")

    # Look up organization by client key
    result = await db.execute(
        select(Organization).where(Organization.jira_client_key == client_key)
    )
    org = result.scalar_one_or_none()
    if not org:
        return Response(status_code=404, content="Organization not found")

    # Verify the JWT with the org's shared secret
    claims = jira_service.verify_jwt(auth_header, org.jira_shared_secret)
    if not claims:
        event = WebhookEvent(
            org_id=org.id,
            source="jira",
            event_type=payload.webhookEvent,
            delivery_id="",
            payload=payload_dict,
            signature_ok=False,
            processed=False,
        )
        db.add(event)
        await db.commit()
        return Response(status_code=401, content="Invalid JWT signature")

    # Persist webhook event
    event = WebhookEvent(
        org_id=org.id,
        source="jira",
        event_type=payload.webhookEvent,
        delivery_id="",
        payload=payload_dict,
        signature_ok=True,
        processed=False,
    )
    db.add(event)
    await db.flush()

    # Check trigger condition: assignee changed to the Bravey bot
    bot_account_id = org.jira_bravey_account_id
    triggered = False

    if payload.changelog and bot_account_id:
        for item in payload.changelog.items:
            if item.field == "assignee" and item.to == bot_account_id:
                triggered = True
                break

    if not triggered:
        event.processed = True
        await db.commit()
        return Response(status_code=200, content="OK - not triggered")

    # Check usage limit
    allowed, reason, plan, usage = await billing_service.check_usage_limit(db, org.id)
    if not allowed:
        event.processed = True
        await db.commit()
        await billing_service.notify_usage_limit_reached(
            org=org,
            plan=plan,
            usage=usage,
            source="jira",
            issue_id=payload.issue.id,
            issue_key=payload.issue.key,
        )
        return Response(status_code=200, content="OK - usage limit reached")

    # Duplicate check with advisory lock
    from sqlalchemy import text

    issue_id = payload.issue.id
    lock_key = hash(issue_id) % (2**31)
    lock_result = await db.execute(
        text(f"SELECT pg_try_advisory_xact_lock({lock_key})")
    )
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
            AgentRun.jira_issue_id == issue_id,
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

    # Find repository
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

    # Build issue URL
    issue_url = f"{org.jira_base_url}/browse/{payload.issue.key}"

    # Create agent run
    run = AgentRun(
        org_id=org.id,
        repo_id=repo.id,
        jira_issue_id=issue_id,
        jira_issue_key=payload.issue.key,
        jira_issue_summary=(
            payload.issue.fields.summary if payload.issue.fields else None
        ),
        jira_issue_url=issue_url,
        status=RunStatus.queued,
        trigger_type="jira_assignment",
    )
    db.add(run)
    await db.flush()

    event.run_id = run.id
    event.processed = True
    await db.commit()

    # Increment usage
    await billing_service.increment_usage(db, org.id)

    # Enqueue to SQS
    try:
        sqs_service.send_run_message(str(run.id))
    except Exception:
        logger.exception(f"Failed to enqueue Jira run {run.id} to SQS")

    return Response(status_code=200, content="OK")
