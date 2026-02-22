"""Tests for the _handle_pr_comment webhook handler."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.routes.webhooks import _handle_pr_comment
from src.models.agent_run import AgentRun, RunStatus


def _make_parent_run(**overrides):
    """Create a mock parent AgentRun."""
    run = MagicMock(spec=AgentRun)
    run.id = overrides.get("id", uuid.uuid4())
    run.org_id = overrides.get("org_id", uuid.uuid4())
    run.repo_id = overrides.get("repo_id", uuid.uuid4())
    run.linear_issue_id = overrides.get("linear_issue_id", "iss-1")
    run.linear_issue_identifier = overrides.get("linear_issue_identifier", "ENG-42")
    run.linear_issue_title = overrides.get("linear_issue_title", "Fix bug")
    run.linear_issue_url = overrides.get("linear_issue_url", "https://linear.app/issue/ENG-42")
    run.branch_name = overrides.get("branch_name", "bravey/eng-42-fix-bug")
    run.pr_number = overrides.get("pr_number", 42)
    run.pr_url = overrides.get("pr_url", "https://github.com/acme/webapp/pull/42")
    run.trigger_type = overrides.get("trigger_type", "linear_assignment")
    run.status = overrides.get("status", RunStatus.success)
    return run


def _mock_db(parent_run=None):
    """Create a mock async DB session. If parent_run is given, DB query returns it."""
    db = AsyncMock()

    # db.execute() returns a result whose .scalar_one_or_none() returns parent_run
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = parent_run
    db.execute.return_value = result_mock

    return db


class TestIgnoresBotComments:
    async def test_bot_sender_skips(self, github_bot_comment_payload):
        db = _mock_db()
        await _handle_pr_comment(
            db, "issue_comment", "delivery-1", github_bot_comment_payload
        )
        # Should not query DB at all (returns early)
        db.execute.assert_not_called()


class TestIgnoresNonPrIssueComments:
    async def test_issue_without_pull_request_key(self, github_issue_comment_not_pr_payload):
        db = _mock_db()
        await _handle_pr_comment(
            db, "issue_comment", "delivery-2", github_issue_comment_not_pr_payload
        )
        # Should return early without querying for parent run
        db.execute.assert_not_called()


class TestCreatesRunForReviewComment:
    @patch("src.api.routes.webhooks.sqs_service")
    async def test_creates_agent_run(
        self, mock_sqs, github_review_comment_payload
    ):
        parent = _make_parent_run(pr_number=42)
        db = _mock_db(parent_run=parent)

        await _handle_pr_comment(
            db, "pull_request_review_comment", "delivery-3",
            github_review_comment_payload,
        )

        # Should have called db.add at least twice (WebhookEvent + AgentRun)
        assert db.add.call_count >= 2

        # Verify one of the added objects is an AgentRun with correct fields
        added_objects = [call.args[0] for call in db.add.call_args_list]
        agent_runs = [o for o in added_objects if isinstance(o, AgentRun)]
        assert len(agent_runs) == 1
        run = agent_runs[0]
        assert run.trigger_type == "pr_comment"
        assert run.trigger_comment_id == 12345678
        assert run.parent_run_id == parent.id
        assert run.status == RunStatus.queued

        # Should have committed
        db.commit.assert_called()

        # Should have enqueued to SQS
        mock_sqs.send_run_message.assert_called_once()


class TestCreatesRunForIssueComment:
    @patch("src.api.routes.webhooks.sqs_service")
    async def test_creates_agent_run(
        self, mock_sqs, github_issue_comment_payload
    ):
        parent = _make_parent_run(pr_number=42)
        db = _mock_db(parent_run=parent)

        await _handle_pr_comment(
            db, "issue_comment", "delivery-4",
            github_issue_comment_payload,
        )

        added_objects = [call.args[0] for call in db.add.call_args_list]
        agent_runs = [o for o in added_objects if isinstance(o, AgentRun)]
        assert len(agent_runs) == 1
        run = agent_runs[0]
        assert run.trigger_type == "pr_comment"
        assert run.trigger_comment_id == 87654321
        assert run.parent_run_id == parent.id

        mock_sqs.send_run_message.assert_called_once()


class TestNoParentRunFound:
    async def test_skips_when_no_parent_run(self, github_review_comment_payload):
        db = _mock_db(parent_run=None)

        await _handle_pr_comment(
            db, "pull_request_review_comment", "delivery-5",
            github_review_comment_payload,
        )

        # Should have queried DB but not added anything
        db.execute.assert_called_once()
        db.add.assert_not_called()
