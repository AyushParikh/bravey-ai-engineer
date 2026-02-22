import hashlib
import hmac
import json
import os
import sys
import time
import uuid

import pytest

# ---------------------------------------------------------------------------
# Environment setup — must happen before any src.* imports
# ---------------------------------------------------------------------------
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://localhost/bravey_test")
os.environ.setdefault("ENCRYPTION_KEY", os.urandom(32).hex())
os.environ.setdefault("SQS_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/123456789/test-queue")

# The .env file contains extra keys not declared on Settings (gh_app_id, etc.)
# which cause pydantic "extra_forbidden" errors. We must prevent Settings from
# reading .env before src.config is imported (since it runs Settings() at
# module level). We do this by temporarily renaming .env.
import pathlib as _pathlib

_env_file = _pathlib.Path(__file__).resolve().parent.parent / ".env"
_env_bak = _env_file.with_suffix(".env.test_bak")
_had_env = _env_file.exists()
if _had_env:
    _env_file.rename(_env_bak)

try:
    # Now import src.config — Settings() will run without .env
    import src.config  # noqa: F401
finally:
    # Restore .env immediately
    if _had_env and _env_bak.exists():
        _env_bak.rename(_env_file)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def linear_webhook_secret():
    return "test-webhook-secret"


@pytest.fixture
def linear_payload(linear_webhook_secret):
    return {
        "action": "update",
        "actor": {"id": "user-1", "name": "Jane", "type": "user"},
        "createdAt": "2026-02-19T10:00:00.000Z",
        "organizationId": "org-linear-123",
        "webhookTimestamp": int(time.time() * 1000),
        "type": "Issue",
        "data": {
            "id": "issue-uuid-1",
            "identifier": "ENG-42",
            "title": "Fix null pointer in auth middleware",
            "description": "When a user logs in with an expired token...",
            "assigneeId": "bravey-bot-user-id",
            "url": "https://linear.app/org/issue/ENG-42",
        },
        "updatedFrom": {
            "assigneeId": None,
            "updatedAt": "2026-02-19T09:59:00.000Z",
        },
    }


@pytest.fixture
def make_signature(linear_webhook_secret):
    def _sign(body: bytes) -> str:
        return hmac.new(
            linear_webhook_secret.encode("utf-8"), body, hashlib.sha256
        ).hexdigest()
    return _sign


# ---------------------------------------------------------------------------
# GitHub webhook payload fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def github_review_comment_payload():
    """Payload for a pull_request_review_comment.created event."""
    return {
        "action": "created",
        "comment": {
            "id": 12345678,
            "body": "Please fix the typo on line 10",
            "path": "src/main.py",
            "line": 10,
            "user": {"login": "reviewer", "type": "User"},
        },
        "pull_request": {
            "number": 42,
            "head": {"ref": "bravey/eng-42-fix-auth"},
        },
        "repository": {
            "full_name": "acme/webapp",
            "owner": {"login": "acme"},
            "name": "webapp",
        },
        "sender": {"login": "reviewer", "type": "User"},
    }


@pytest.fixture
def github_issue_comment_payload():
    """Payload for an issue_comment.created event on a PR."""
    return {
        "action": "created",
        "issue": {
            "number": 42,
            "pull_request": {"url": "https://api.github.com/repos/acme/webapp/pulls/42"},
        },
        "comment": {
            "id": 87654321,
            "body": "Can you also add tests for this?",
            "user": {"login": "reviewer", "type": "User"},
        },
        "repository": {
            "full_name": "acme/webapp",
            "owner": {"login": "acme"},
            "name": "webapp",
        },
        "sender": {"login": "reviewer", "type": "User"},
    }


@pytest.fixture
def github_bot_comment_payload():
    """Payload for an issue_comment from a bot."""
    return {
        "action": "created",
        "issue": {
            "number": 42,
            "pull_request": {"url": "https://api.github.com/repos/acme/webapp/pulls/42"},
        },
        "comment": {
            "id": 99999999,
            "body": "CI passed",
            "user": {"login": "github-actions[bot]", "type": "Bot"},
        },
        "repository": {
            "full_name": "acme/webapp",
            "owner": {"login": "acme"},
            "name": "webapp",
        },
        "sender": {"login": "github-actions[bot]", "type": "Bot"},
    }


@pytest.fixture
def github_issue_comment_not_pr_payload():
    """Payload for an issue_comment on a regular issue (not a PR)."""
    return {
        "action": "created",
        "issue": {
            "number": 100,
            # No pull_request key
        },
        "comment": {
            "id": 11111111,
            "body": "Some comment on an issue",
            "user": {"login": "reviewer", "type": "User"},
        },
        "repository": {
            "full_name": "acme/webapp",
            "owner": {"login": "acme"},
            "name": "webapp",
        },
        "sender": {"login": "reviewer", "type": "User"},
    }
