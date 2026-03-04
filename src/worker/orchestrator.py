import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from src.database import SyncSessionLocal
from src.models.agent_log import AgentLog, LogLevel
from src.models.agent_run import AgentRun, RunStatus
from src.models.organization import Organization
from src.models.repository import Repository
from src.models.slack_notification import NotificationType, SlackNotification
from src.services import agent_service, github_service, jira_service, slack_service
from src.services.linear_service import (
    create_comment,
    ensure_valid_token,
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


def _build_jira_issue_context(issue: dict) -> str:
    """Build context string from a Jira issue (REST API v3 format)."""
    fields = issue.get("fields", {})
    parts = [
        f"Title: {fields.get('summary', '')}",
        f"Key: {issue.get('key', '')}",
        f"Description: {jira_service.adf_to_plaintext(fields.get('description')) or 'No description'}",
    ]

    priority = fields.get("priority")
    if priority:
        parts.append(f"Priority: {priority.get('name', 'None')}")

    labels = fields.get("labels")
    if labels:
        parts.append(f"Labels: {', '.join(labels)}")

    parent = fields.get("parent")
    if parent:
        parent_fields = parent.get("fields", {})
        parts.append(f"Parent issue: {parent.get('key', '')} - {parent_fields.get('summary', '')}")

    comment_data = fields.get("comment", {})
    comments = comment_data.get("comments", []) if comment_data else []
    if comments:
        parts.append("Comments:")
        for c in comments:
            author = c.get("author", {})
            name = author.get("displayName", "Unknown")
            body_text = jira_service.adf_to_plaintext(c.get("body"))
            parts.append(f"  - {name}: {body_text}")

    return "\n".join(parts)


def _execute_comment_pipeline(db, run: AgentRun) -> None:
    """Handle a PR review comment follow-up: make changes and reply on GitHub."""
    now = datetime.now(timezone.utc)
    run.status = RunStatus.running
    run.started_at = now
    run.timeout_at = now + timedelta(minutes=30)
    db.commit()

    _log(db, run.id, LogLevel.info, "PR comment pipeline started")

    org = db.execute(
        select(Organization).where(Organization.id == run.org_id)
    ).scalar_one()
    repo = db.execute(
        select(Repository).where(Repository.id == run.repo_id)
    ).scalar_one()

    try:
        # Load parent run for context
        parent_run = db.execute(
            select(AgentRun).where(AgentRun.id == run.parent_run_id)
        ).scalar_one()

        branch_name = parent_run.branch_name
        pr_number = parent_run.pr_number

        # Generate GitHub installation token
        _log(db, run.id, LogLevel.info, "Generating GitHub installation token")
        from src.config import settings

        gh_token = github_service.get_installation_token(
            app_id=settings.github_app_id,
            private_key=settings.github_app_private_key,
            installation_id=org.github_installation_id,
        )

        # Fetch PR diff for context
        _log(db, run.id, LogLevel.info, "Fetching PR diff")
        pr_diff = github_service.get_pr_diff(
            gh_token, repo.github_owner, repo.github_repo_name, pr_number
        )
        # Truncate if too large
        if len(pr_diff) > 50000:
            pr_diff = pr_diff[:50000] + "\n... (truncated)"

        # Fetch the triggering comment
        _log(db, run.id, LogLevel.info, "Fetching triggering comment")
        comment_body = ""
        comment_file = None
        comment_line = None
        is_review_comment = False

        try:
            review_comment = github_service.get_review_comment(
                gh_token, repo.github_owner, repo.github_repo_name,
                run.trigger_comment_id,
            )
            comment_body = review_comment.get("body", "")
            comment_file = review_comment.get("path")
            comment_line = review_comment.get("original_line") or review_comment.get("line")
            is_review_comment = True
            _log(db, run.id, LogLevel.info, f"Review comment on {comment_file}:{comment_line}")
        except Exception:
            # Might be a general issue_comment — fetch from issues API
            _log(db, run.id, LogLevel.info, "Not a review comment, treating as issue comment")
            import httpx
            resp = httpx.get(
                f"https://api.github.com/repos/{repo.github_owner}/{repo.github_repo_name}"
                f"/issues/comments/{run.trigger_comment_id}",
                headers={
                    "Authorization": f"Bearer {gh_token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                timeout=15,
            )
            resp.raise_for_status()
            comment_body = resp.json().get("body", "")

        # Build context for Claude
        file_context = ""
        if comment_file:
            file_context = f"\nFile: {comment_file}"
            if comment_line:
                file_context += f" (line {comment_line})"

        # Use whichever issue tracker is relevant
        issue_identifier = run.linear_issue_identifier or run.jira_issue_key or ""
        issue_title = run.linear_issue_title or run.jira_issue_summary or ""
        tracker_label = "Jira" if run.jira_issue_key else "Linear"

        issue_context = (
            f"You are working on a follow-up to a pull request.\n\n"
            f"PR #{pr_number} on branch `{branch_name}`\n"
            f"{tracker_label} issue: {issue_identifier} - {issue_title}\n\n"
            f"A reviewer left this comment requesting changes:{file_context}\n"
            f"---\n{comment_body}\n---\n\n"
            f"Current PR diff:\n```diff\n{pr_diff}\n```\n\n"
            f"Please make the requested changes on the existing branch. "
            f"Do NOT open a new PR — just push commits to the existing branch."
        )

        # Get the current branch HEAD sha
        head_sha = github_service.get_default_branch_sha(
            gh_token, repo.github_owner, repo.github_repo_name, branch_name
        )

        # Run Claude agent on the existing branch
        _log(db, run.id, LogLevel.info, "Running Claude agent for PR comment")
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

        # Reply to the comment on GitHub
        _log(db, run.id, LogLevel.info, "Replying to comment on GitHub")
        reply_body = (
            f"I've pushed changes to address this comment.\n\n"
            f"**Changes made:**\n{result.summary or 'See latest commits for details.'}"
        )

        try:
            if is_review_comment:
                github_service.reply_to_review_comment(
                    gh_token, repo.github_owner, repo.github_repo_name,
                    pr_number, run.trigger_comment_id, reply_body,
                )
            else:
                github_service.create_issue_comment(
                    gh_token, repo.github_owner, repo.github_repo_name,
                    pr_number, reply_body,
                )
        except Exception:
            logger.exception("Failed to reply to GitHub comment")

        # Mark success
        run.status = RunStatus.success
        run.completed_at = datetime.now(timezone.utc)
        db.commit()
        _log(db, run.id, LogLevel.info, "PR comment pipeline completed successfully")

    except Exception as e:
        logger.exception(f"PR comment pipeline failed for run {run.id}")
        run.status = RunStatus.failed
        run.completed_at = datetime.now(timezone.utc)
        run.error_message = str(e)
        db.commit()
        _log(db, run.id, LogLevel.error, f"PR comment pipeline failed: {e}")


def _execute_slack_mention_pipeline(db, run: AgentRun) -> None:
    """Handle a Slack @mention with full thread context and 3-way intent."""
    import re

    now = datetime.now(timezone.utc)
    run.status = RunStatus.running
    run.started_at = now
    run.timeout_at = now + timedelta(minutes=30)
    db.commit()

    _log(db, run.id, LogLevel.info, "Slack mention pipeline started")

    org = db.execute(
        select(Organization).where(Organization.id == run.org_id)
    ).scalar_one()

    channel_id = run.slack_channel_id
    thread_ts = run.slack_thread_ts
    message_text = run.slack_message_text or ""

    try:
        # Fetch thread context for conversation awareness
        thread_context = ""
        if org.slack_bot_token and channel_id and thread_ts:
            try:
                thread_msgs = slack_service.fetch_thread_messages(
                    org.slack_bot_token, channel_id, thread_ts
                )
                # Build context string (exclude the current message which is last)
                if len(thread_msgs) > 1:
                    context_parts = []
                    for msg in thread_msgs[:-1]:
                        role = "Bravey" if msg["is_bot"] else "User"
                        context_parts.append(f"{role}: {msg['text']}")
                    thread_context = "\n".join(context_parts)
            except Exception:
                logger.exception("Failed to fetch Slack thread context")

        # Classify intent with thread context
        _log(db, run.id, LogLevel.info, "Classifying Slack message intent")
        intent = agent_service.classify_slack_intent(message_text, thread_context)
        _log(db, run.id, LogLevel.info, f"Intent classified as: {intent}")

        # --- Conversation: reply directly, no codebase access needed ---
        if intent == "conversation":
            reply = agent_service.generate_conversation_reply(
                message_text, thread_context
            )
            if org.slack_bot_token and channel_id and thread_ts:
                slack_service.post_thread_message(
                    org.slack_bot_token, channel_id, thread_ts, reply,
                )
            run.claude_summary = reply
            run.status = RunStatus.success
            run.completed_at = datetime.now(timezone.utc)
            db.commit()
            _log(db, run.id, LogLevel.info, "Slack conversation reply sent")
            return

        # --- For question/action, we need a repo and GitHub token ---
        # Resolve repo from message text
        repos = db.execute(
            select(Repository).where(
                Repository.org_id == org.id,
                Repository.is_active.is_(True),
            )
        ).scalars().all()

        if not repos:
            if org.slack_bot_token and channel_id and thread_ts:
                slack_service.post_thread_message(
                    org.slack_bot_token, channel_id, thread_ts,
                    "No active repositories found. Please connect a repo first.",
                )
            run.status = RunStatus.failed
            run.completed_at = datetime.now(timezone.utc)
            run.error_message = "No active repositories"
            db.commit()
            return

        matched_repo = None
        message_lower = message_text.lower()
        for repo in repos:
            if repo.github_repo_name.lower() in message_lower:
                matched_repo = repo
                break

        if not matched_repo and len(repos) == 1:
            matched_repo = repos[0]

        if not matched_repo:
            repo_names = ", ".join(f"`{r.github_repo_name}`" for r in repos)
            if org.slack_bot_token and channel_id and thread_ts:
                slack_service.post_thread_message(
                    org.slack_bot_token, channel_id, thread_ts,
                    f"Which repo should I look at? You have: {repo_names}",
                )
            run.status = RunStatus.success
            run.completed_at = datetime.now(timezone.utc)
            run.claude_summary = "Asked user to specify repo"
            db.commit()
            return

        # Update run with resolved repo
        run.repo_id = matched_repo.id
        db.commit()

        # Post "thinking" message now that we know real work is needed
        if org.slack_bot_token and channel_id and thread_ts:
            slack_service.post_thread_message(
                org.slack_bot_token, channel_id, thread_ts,
                "On it! Give me a moment...",
            )

        from src.config import settings

        gh_token = github_service.get_installation_token(
            app_id=settings.github_app_id,
            private_key=settings.github_app_private_key,
            installation_id=org.github_installation_id,
        )

        # --- Question: read-only agent ---
        if intent == "question":
            _log(db, run.id, LogLevel.info, "Running Q&A agent")
            result = agent_service.answer_question(
                gh_token=gh_token,
                owner=matched_repo.github_owner,
                repo_name=matched_repo.github_repo_name,
                branch_name=matched_repo.default_branch,
                question=message_text,
            )
            run.claude_session_id = result.claude_session_id
            run.claude_summary = result.summary
            db.commit()

            if not result.success:
                raise RuntimeError(result.error or "Q&A agent failed")

            answer = result.summary or "I couldn't find an answer."
            if len(answer) > 2900:
                answer = answer[:2900] + "..."

            if org.slack_bot_token and channel_id and thread_ts:
                slack_service.post_thread_message(
                    org.slack_bot_token, channel_id, thread_ts, answer,
                )

            run.status = RunStatus.success
            run.completed_at = datetime.now(timezone.utc)
            db.commit()
            _log(db, run.id, LogLevel.info, "Slack Q&A pipeline completed")

        # --- Action: full agent + PR ---
        else:
            _log(db, run.id, LogLevel.info, "Running action agent")

            slug = re.sub(r"[^a-z0-9]+", "-", message_text[:60].lower()).strip("-")
            branch_name = f"slack/{slug}"

            head_sha = github_service.get_default_branch_sha(
                gh_token, matched_repo.github_owner,
                matched_repo.github_repo_name, matched_repo.default_branch,
            )
            github_service.create_branch(
                gh_token, matched_repo.github_owner,
                matched_repo.github_repo_name, branch_name, head_sha,
            )
            run.branch_name = branch_name
            db.commit()

            issue_context = (
                f"A user requested the following change via Slack:\n\n"
                f"{message_text}\n\n"
                f"Please implement the changes needed."
            )

            result = agent_service.provision_and_run(
                gh_token=gh_token,
                owner=matched_repo.github_owner,
                repo_name=matched_repo.github_repo_name,
                branch_name=branch_name,
                base_sha=head_sha,
                issue_context=issue_context,
            )
            run.claude_session_id = result.claude_session_id
            run.claude_summary = result.summary
            db.commit()

            if not result.success:
                raise RuntimeError(result.error or "Agent run failed")

            _log(db, run.id, LogLevel.info, "Opening pull request")
            pr_title = message_text[:60]
            if len(message_text) > 60:
                pr_title += "..."
            pr_body = (
                f"## Summary\n\n"
                f"{result.summary or 'No summary available.'}\n\n"
                f"**Requested via Slack**\n\n"
                f"---\n"
                f"*This PR was opened by [Bravey](https://bravey.co)*"
            )

            pr = github_service.create_pull_request(
                token=gh_token,
                owner=matched_repo.github_owner,
                repo=matched_repo.github_repo_name,
                title=pr_title,
                body=pr_body,
                head=branch_name,
                base=matched_repo.default_branch,
            )
            run.pr_number = pr["number"]
            run.pr_url = pr["html_url"]
            run.pr_sha = pr.get("head", {}).get("sha")
            db.commit()

            try:
                github_service.add_labels(
                    gh_token, matched_repo.github_owner,
                    matched_repo.github_repo_name,
                    pr["number"], ["bravey-generated"],
                )
            except Exception:
                logger.warning("Failed to add label to PR")

            if org.slack_bot_token and channel_id and thread_ts:
                slack_service.post_thread_message(
                    org.slack_bot_token, channel_id, thread_ts,
                    f"Done! I've opened a PR: <{run.pr_url}|#{run.pr_number}: {pr_title}>",
                )

            run.status = RunStatus.success
            run.completed_at = datetime.now(timezone.utc)
            db.commit()
            _log(db, run.id, LogLevel.info, "Slack action pipeline completed")

    except Exception as e:
        logger.exception(f"Slack mention pipeline failed for run {run.id}")
        run.status = RunStatus.failed
        run.completed_at = datetime.now(timezone.utc)
        run.error_message = str(e)
        db.commit()
        _log(db, run.id, LogLevel.error, f"Slack mention pipeline failed: {e}")

        if org.slack_bot_token and channel_id and thread_ts:
            try:
                slack_service.post_thread_message(
                    org.slack_bot_token, channel_id, thread_ts,
                    f"Sorry, something went wrong: {str(e)[:200]}",
                )
            except Exception:
                logger.exception("Failed to post error to Slack thread")


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

    # Route PR comment follow-ups to their own pipeline
    if run.trigger_type == "pr_comment":
        _execute_comment_pipeline(db, run)
        return

    # Route Slack mention to its own pipeline
    if run.trigger_type == "slack_mention":
        _execute_slack_mention_pipeline(db, run)
        return

    is_jira = run.trigger_type == "jira_assignment"

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

    # Pre-initialize issue identifiers (may be updated after fetching full issue)
    issue_identifier = run.jira_issue_key if is_jira else run.linear_issue_identifier
    issue_title = run.jira_issue_summary if is_jira else run.linear_issue_title
    issue_url = run.jira_issue_url if is_jira else run.linear_issue_url

    try:
        # Refresh Linear tokens if needed
        if not is_jira:
            org.linear_access_token = ensure_valid_token(db, org, "admin")
            if org.linear_bot_token:
                org.linear_bot_token = ensure_valid_token(db, org, "bot")

        # Step 3: Fetch full issue context (Linear or Jira)
        if is_jira:
            _log(db, run.id, LogLevel.info, "Fetching Jira issue context")
            issue = jira_service.fetch_issue(
                org.jira_client_key, org.jira_shared_secret,
                org.jira_base_url, run.jira_issue_key,
            )
            issue_fields = issue.get("fields", {})
            run.jira_issue_summary = issue_fields.get("summary", run.jira_issue_summary)
            db.commit()
            issue_context = _build_jira_issue_context(issue)
        else:
            _log(db, run.id, LogLevel.info, "Fetching Linear issue context")
            issue = fetch_issue(org.linear_access_token, run.linear_issue_id)
            run.linear_issue_title = issue.get("title", run.linear_issue_title)
            run.linear_issue_url = issue.get("url", run.linear_issue_url)
            db.commit()
            issue_context = _build_issue_context(issue)

        # Step 3b: Post "picked up" comment
        picked_up_body = (
            "Bravey has picked up this ticket and is working on it.\n\n"
            "A pull request will be opened shortly."
        )
        if is_jira:
            try:
                _log(db, run.id, LogLevel.info, "Posting picked-up comment on Jira")
                jira_service.add_comment(
                    org.jira_client_key, org.jira_shared_secret,
                    org.jira_base_url, run.jira_issue_id,
                    picked_up_body,
                )
            except Exception:
                logger.exception("Failed to post picked-up comment on Jira")
        else:
            comment_token = org.linear_bot_token or org.linear_access_token
            if comment_token:
                try:
                    _log(db, run.id, LogLevel.info, "Posting picked-up comment on Linear")
                    create_comment(
                        comment_token,
                        run.linear_issue_id,
                        f"**{picked_up_body}**",
                    )
                except Exception:
                    logger.exception("Failed to post picked-up comment on Linear")

        # Step 3c: Set issue to "In Progress"
        if is_jira:
            if org.jira_in_progress_status_id:
                try:
                    _log(db, run.id, LogLevel.info, "Setting Jira issue to In Progress")
                    jira_service.transition_issue_to_status(
                        org.jira_client_key, org.jira_shared_secret,
                        org.jira_base_url, run.jira_issue_id,
                        org.jira_in_progress_status_id,
                    )
                except Exception:
                    logger.exception("Failed to set Jira issue to In Progress")
        else:
            if org.linear_access_token and org.linear_in_progress_state_id:
                try:
                    _log(db, run.id, LogLevel.info, "Setting Linear issue to In Progress")
                    update_issue_state(
                        org.linear_access_token,
                        run.linear_issue_id,
                        org.linear_in_progress_state_id,
                    )
                except Exception:
                    logger.exception("Failed to set Linear issue to In Progress")

        # Abstracted issue identifiers for downstream use
        issue_identifier = run.jira_issue_key if is_jira else run.linear_issue_identifier
        issue_title = run.jira_issue_summary if is_jira else run.linear_issue_title
        issue_url = run.jira_issue_url if is_jira else run.linear_issue_url

        # Step 4: Post Slack "started" message
        if org.slack_bot_token and slack_channel:
            _log(db, run.id, LogLevel.info, "Posting Slack started message")
            try:
                slack_service.join_channel(org.slack_bot_token, slack_channel)
                slack_resp = slack_service.post_run_started(
                    bot_token=org.slack_bot_token,
                    channel_id=slack_channel,
                    issue_identifier=issue_identifier,
                    issue_title=issue_title or "",
                    issue_url=issue_url or "",
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
                else:
                    logger.error("Slack post_run_started failed: %s", slack_resp.get("error"))
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
            issue_identifier, issue_title or ""
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
        pr_title = f"{issue_identifier}: {issue_title}"
        tracker_label = "Jira issue" if is_jira else "Linear issue"
        pr_body = (
            f"## Summary\n\n"
            f"{result.summary or 'No summary available.'}\n\n"
            f"**{tracker_label}:** [{issue_identifier}]({issue_url})\n\n"
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

        # Step 10: Update issue tracker — set to "In Review" and post PR comment
        if is_jira:
            _log(db, run.id, LogLevel.info, "Updating Jira issue")
            if org.jira_in_review_status_id:
                try:
                    jira_service.transition_issue_to_status(
                        org.jira_client_key, org.jira_shared_secret,
                        org.jira_base_url, run.jira_issue_id,
                        org.jira_in_review_status_id,
                    )
                except Exception:
                    logger.exception("Failed to transition Jira issue to In Review")

            try:
                comment_text = (
                    f"Bravey opened a PR\n\n"
                    f"PR: {pr_title} — {run.pr_url}\n"
                    f"Branch: {branch_name}\n\n"
                    f"Changes made:\n{result.summary or 'See PR for details.'}"
                )
                jira_service.add_comment(
                    org.jira_client_key, org.jira_shared_secret,
                    org.jira_base_url, run.jira_issue_id,
                    comment_text,
                )
            except Exception:
                logger.exception("Failed to comment on Jira issue")
        else:
            # Wait for Linear's GitHub integration to process the PR first,
            # then override the status to "In Review"
            _log(db, run.id, LogLevel.info, "Waiting for Linear-GitHub sync before setting In Review")
            time.sleep(5)
            _log(db, run.id, LogLevel.info, "Updating Linear issue")
            if org.linear_access_token and org.linear_in_review_state_id:
                try:
                    update_issue_state(
                        org.linear_access_token,
                        run.linear_issue_id,
                        org.linear_in_review_state_id,
                    )
                except Exception:
                    logger.exception("Failed to update Linear issue state")

            comment_token = org.linear_bot_token or org.linear_access_token
            if comment_token and run.linear_issue_id:
                try:
                    comment_body = (
                        f"**Bravey opened a PR**\n\n"
                        f"**PR:** [{pr_title}]({run.pr_url})\n"
                        f"**Branch:** `{branch_name}`\n\n"
                        f"Changes made:\n{result.summary or 'See PR for details.'}"
                    )
                    create_comment(
                        comment_token,
                        run.linear_issue_id,
                        comment_body,
                    )
                except Exception:
                    logger.exception("Failed to comment on Linear issue")

        # Step 10b: DM the issue creator via Slack
        if org.slack_bot_token:
            if is_jira:
                reporter = issue.get("fields", {}).get("reporter") or issue.get("fields", {}).get("creator") or {}
                creator_email = reporter.get("emailAddress")
            else:
                creator = issue.get("creator")
                creator_email = creator.get("email") if creator else None
            _log(db, run.id, LogLevel.info, f"Issue creator email: {creator_email}")
            if creator_email:
                try:
                    _log(db, run.id, LogLevel.info, f"Looking up Slack user for {creator_email}")
                    slack_user_id = slack_service.lookup_user_by_email(
                        org.slack_bot_token, creator_email
                    )
                    _log(db, run.id, LogLevel.info, f"Slack user lookup result: {slack_user_id}")
                    if slack_user_id:
                        dm_text = (
                            f"Bravey opened a PR for your ticket "
                            f"*{issue_identifier}: {issue_title}*"
                        )
                        dm_blocks = [
                            {
                                "type": "section",
                                "text": {
                                    "type": "mrkdwn",
                                    "text": (
                                        f"*Bravey opened a PR for your ticket "
                                        f"<{issue_url}|{issue_identifier}: "
                                        f"{issue_title}>*\n\n"
                                        f"<{run.pr_url}|#{run.pr_number}: {pr_title}>"
                                    ),
                                },
                            },
                            {
                                "type": "context",
                                "elements": [
                                    {
                                        "type": "mrkdwn",
                                        "text": f"Branch: `{branch_name}`",
                                    }
                                ],
                            },
                        ]
                        dm_resp = slack_service.send_dm(
                            org.slack_bot_token, slack_user_id, dm_text, dm_blocks
                        )
                        _log(db, run.id, LogLevel.info, f"Slack DM response: {dm_resp}")
                    else:
                        _log(db, run.id, LogLevel.warn, f"No Slack user found for {creator_email}")
                except Exception:
                    logger.exception("Failed to send Slack DM to issue creator")
            else:
                _log(db, run.id, LogLevel.warn, "No creator email on issue, skipping DM")

        # Step 11: Update Slack message
        if org.slack_bot_token and slack_channel and slack_message_ts:
            _log(db, run.id, LogLevel.info, "Updating Slack message")
            try:
                duration = _format_duration(run.started_at, datetime.now(timezone.utc))
                slack_service.update_run_completed(
                    bot_token=org.slack_bot_token,
                    channel_id=slack_channel,
                    message_ts=slack_message_ts,
                    issue_identifier=issue_identifier,
                    issue_url=issue_url or "",
                    pr_url=run.pr_url or "",
                    pr_number=run.pr_number or 0,
                    pr_title=issue_title or "",
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
                    issue_identifier=issue_identifier,
                    issue_url=issue_url or "",
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
