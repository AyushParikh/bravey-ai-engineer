import hashlib
import hmac
import json
import os
import time
import uuid

import pytest

# Set test env vars before importing app modules
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://localhost/bravey_test")
os.environ.setdefault("ENCRYPTION_KEY", os.urandom(32).hex())
os.environ.setdefault("SQS_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/123456789/test-queue")


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
