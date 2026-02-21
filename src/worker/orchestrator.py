import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from src.database import SyncSessionLocal
from src.models.agent_log import AgentLog, LogLevel
from src.models.agent_run import AgentRun, RunStatus
from src.models.organization import Organization
from src.models.repository import Repository
from src.models.slack_notification import NotificationType, SlackNotification
from src.services import agent_service, github_service, slack_service
from src.services.linear_service import (
    create_comment,
    fetch_issue,
    update_issue_state,
)

logger = logging.getLogger(__name__)


def _log(db, run_id, level: LogLevel, message: str, metadata: dict | None = None):
    db.add(AgentLog(run_id=run_id, level=level, message=message, metadata_=metadata))
    db.flush()


def _format_duration(start: datetime, end: datetime) -> str:
    delta = end - start
    minutes = int(delta.total_seconds() // 60)
    seconds = int(delta.total_seconds() % 60)
    return f"{minutes}m {seconds}s"


def _build_issue_context(issue: dict) -> str:
    parts = [
        f"Title: {issue.get('title', '')}",
        f"Identifier: {issue.get('identifier', '')}",
        f"Description: {issue.get('description', 'No description')}",
        f"Priority: {issue.get('priorityLabel', 'None')}",
    ]

    labels = issue.get("labels", {})
    if labels and labels.get("nodes"):
        label_names = [l["name"] for l in labels["nodes"]]
        parts.append(f"Labels: {', '.join(label_names)}")

    parent = issue.get("parent")
    if parent:
        parts.append(f"Parent issue: {parent['identifier']} - {parent['title']}")
        if parent.get("description"):
            parts.append(f"Parent description: {parent['description']}")

    comments = issue.get("comments", {})
    if comments and comments.get("nodes"):
        parts.append("Comments:")
        for c in comments["nodes"]:
            user = c.get("user", {})
            name = user.get("name", "Unknown") if user else "Unknown"
            parts.append(f"  - {name}: {c['body']}")

    return "\n".join(parts)


def run_pipeline(run_id: str) -> None:
    db = SyncSessionLocal()
    try:
        _execute_pipeline(db, run_id)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _execute_pipeline(db, run_id: str) -> None:
    print(f"[BRAVEY] Pipeline executing for run {run_id}", flush=True)
    # Step 2: Load run and set running
    run = db.execute(select(AgentRun).where(AgentRun.id == run_id)).scalar_one()
    org = db.execute(
        select(Organization).where(Organization.id == run.org_id)
    ).scalar_one()
    repo = db.execute(
        select(Repository).where(Repository.id == run.repo_id)
    ).scalar_one()

    now = datetime.now(timezone.utc)
    run.status = RunStatus.running
    run.started_at = now
    run.timeout_at = now + timedelta(minutes=30)
    db.commit()

    _log(db, run.id, LogLevel.info, "Pipeline started")

    slack_message_ts = None
    slack_channel = org.slack_default_channel_id

    try:
        # Step 3: Fetch full Linear issue context
        _log(db, run.id, LogLevel.info, "Fetching Linear issue context")
        issue = fetch_issue(org.linear_access_token, run.linear_issue_id)
        run.linear_issue_title = issue.get("title", run.linear_issue_title)
        run.linear_issue_url = issue.get("url", run.linear_issue_url)
        db.commit()

        issue_context = _build_issue_context(issue)

        # Step 3b: Post "picked up" comment on Linear issue
        if org.linear_access_token:
            try:
                _log(db, run.id, LogLevel.info, "Posting picked-up comment on Linear")
                picked_up_body = (
                    f"**Bravey has picked up this ticket** and is working on it.\n\n"
                    f"A pull request will be opened shortly."
                )
                create_comment(
                    org.linear_access_token,
                    run.linear_issue_id,
                    picked_up_body,
                )
            except Exception:
                logger.exception("Failed to post picked-up comment on Linear")

        # Step 4: Post Slack "started" message
        if org.slack_bot_token and slack_channel:
            _log(db, run.id, LogLevel.info, "Posting Slack started message")
            try:
                slack_resp = slack_service.post_run_started(
                    bot_token=org.slack_bot_token,
                    channel_id=slack_channel,
                    issue_identifier=run.linear_issue_identifier,
                    issue_title=run.linear_issue_title or "",
                    issue_url=run.linear_issue_url or "",
                    assigned_by="User",
                )
                if slack_resp.get("ok"):
                    slack_message_ts = slack_resp["ts"]
                    db.add(
                        SlackNotification(
                            run_id=run.id,
                            channel_id=slack_channel,
                            message_ts=slack_message_ts,
                            type=NotificationType.started,
                        )
                    )
                    db.commit()
            except Exception:
                logger.exception("Failed to post Slack message")

        # Step 5: Generate GitHub installation token
        _log(db, run.id, LogLevel.info, "Generating GitHub installation token")
        from src.config import settings

        gh_token = github_service.get_installation_token(
            app_id=settings.github_app_id,
            private_key=settings.github_app_private_key,
            installation_id=org.github_installation_id,
        )

        # Step 6: Create GitHub branch
        branch_name = github_service.slugify_branch_name(
            run.linear_issue_identifier, run.linear_issue_title or ""
        )
        _log(db, run.id, LogLevel.info, f"Creating branch {branch_name}")

        head_sha = github_service.get_default_branch_sha(
            gh_token, repo.github_owner, repo.github_repo_name, repo.default_branch
        )
        github_service.create_branch(
            gh_token, repo.github_owner, repo.github_repo_name, branch_name, head_sha
        )
        run.branch_name = branch_name
        db.commit()

        # Step 7: Run Claude agent via Anthropic API
        _log(db, run.id, LogLevel.info, "Running Claude agent via Anthropic API")

        result = agent_service.provision_and_run(
            gh_token=gh_token,
            owner=repo.github_owner,
            repo_name=repo.github_repo_name,
            branch_name=branch_name,
            base_sha=head_sha,
            issue_context=issue_context,
        )
        run.claude_session_id = result.claude_session_id
        run.claude_summary = result.summary
        db.commit()

        if not result.success:
            raise RuntimeError(result.error or "Agent run failed")

        # Step 9: Open GitHub Pull Request
        _log(db, run.id, LogLevel.info, "Opening pull request")
        pr_title = f"{run.linear_issue_identifier}: {run.linear_issue_title}"
        pr_body = (
            f"## Summary\n\n"
            f"{result.summary or 'No summary available.'}\n\n"
            f"**Linear issue:** [{run.linear_issue_identifier}]({run.linear_issue_url})\n\n"
            f"---\n"
            f"*This PR was opened by [Bravey](https://bravey.co)*"
        )

        pr = github_service.create_pull_request(
            token=gh_token,
            owner=repo.github_owner,
            repo=repo.github_repo_name,
            title=pr_title,
            body=pr_body,
            head=branch_name,
            base=repo.default_branch,
        )
        run.pr_number = pr["number"]
        run.pr_url = pr["html_url"]
        run.pr_sha = pr.get("head", {}).get("sha")
        db.commit()

        # Add label
        try:
            github_service.add_labels(
                gh_token, repo.github_owner, repo.github_repo_name,
                pr["number"], ["bravey-generated"],
            )
        except Exception:
            logger.warning("Failed to add label to PR")

        # Step 10: Update Linear issue
        _log(db, run.id, LogLevel.info, "Updating Linear issue")
        if org.linear_access_token and org.linear_in_review_state_id:
            try:
                update_issue_state(
                    org.linear_access_token,
                    run.linear_issue_id,
                    org.linear_in_review_state_id,
                )

                comment_body = (
                    f"**Bravey opened a PR**\n\n"
                    f"**PR:** [{pr_title}]({run.pr_url})\n"
                    f"**Branch:** `{branch_name}`\n\n"
                    f"Changes made:\n{result.summary or 'See PR for details.'}"
                )
                create_comment(
                    org.linear_access_token,
                    run.linear_issue_id,
                    comment_body,
                )
            except Exception:
                logger.exception("Failed to update Linear issue")

        # Step 11: Update Slack message
        if org.slack_bot_token and slack_channel and slack_message_ts:
            _log(db, run.id, LogLevel.info, "Updating Slack message")
            try:
                duration = _format_duration(run.started_at, datetime.now(timezone.utc))
                slack_service.update_run_completed(
                    bot_token=org.slack_bot_token,
                    channel_id=slack_channel,
                    message_ts=slack_message_ts,
                    issue_identifier=run.linear_issue_identifier,
                    issue_url=run.linear_issue_url or "",
                    pr_url=run.pr_url or "",
                    pr_number=run.pr_number or 0,
                    pr_title=run.linear_issue_title or "",
                    branch_name=branch_name,
                    duration=duration,
                )
                db.add(
                    SlackNotification(
                        run_id=run.id,
                        channel_id=slack_channel,
                        message_ts=slack_message_ts,
                        type=NotificationType.pr_opened,
                    )
                )
                db.commit()
            except Exception:
                logger.exception("Failed to update Slack message")

        # Step 12: Mark success
        run.status = RunStatus.success
        run.completed_at = datetime.now(timezone.utc)
        db.commit()
        _log(db, run.id, LogLevel.info, "Pipeline completed successfully")

    except Exception as e:
        # Failure handling
        logger.exception(f"Pipeline failed for run {run_id}")
        run.status = RunStatus.failed
        run.completed_at = datetime.now(timezone.utc)
        run.error_message = str(e)
        db.commit()
        _log(db, run.id, LogLevel.error, f"Pipeline failed: {e}")

        # Update Slack with failure
        if org.slack_bot_token and slack_channel and slack_message_ts:
            try:
                slack_service.update_run_failed(
                    bot_token=org.slack_bot_token,
                    channel_id=slack_channel,
                    message_ts=slack_message_ts,
                    issue_identifier=run.linear_issue_identifier,
                    issue_url=run.linear_issue_url or "",
                    error_message=str(e),
                    run_id=str(run.id),
                )
                db.add(
                    SlackNotification(
                        run_id=run.id,
                        channel_id=slack_channel,
                        message_ts=slack_message_ts,
                        type=NotificationType.failed,
                    )
                )
                db.commit()
            except Exception:
                logger.exception("Failed to update Slack with failure")
