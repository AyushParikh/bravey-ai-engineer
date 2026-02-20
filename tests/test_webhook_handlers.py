import hashlib
import hmac
import json
import time

from src.services.linear_service import verify_signature, verify_timestamp


class TestLinearSignatureVerification:
    def test_valid_signature(self):
        secret = "my-secret"
        body = b'{"test": "data"}'
        signature = hmac.new(
            secret.encode("utf-8"), body, hashlib.sha256
        ).hexdigest()
        assert verify_signature(body, signature, secret) is True

    def test_invalid_signature(self):
        secret = "my-secret"
        body = b'{"test": "data"}'
        assert verify_signature(body, "invalid-hex", secret) is False

    def test_tampered_body(self):
        secret = "my-secret"
        body = b'{"test": "data"}'
        signature = hmac.new(
            secret.encode("utf-8"), body, hashlib.sha256
        ).hexdigest()
        tampered = b'{"test": "tampered"}'
        assert verify_signature(tampered, signature, secret) is False


class TestTimestampVerification:
    def test_fresh_timestamp(self):
        now_ms = int(time.time() * 1000)
        assert verify_timestamp(now_ms) is True

    def test_stale_timestamp(self):
        old_ms = int(time.time() * 1000) - 120_000  # 2 minutes ago
        assert verify_timestamp(old_ms) is False

    def test_future_timestamp_within_window(self):
        future_ms = int(time.time() * 1000) + 30_000  # 30s in future
        assert verify_timestamp(future_ms) is True


class TestTriggerCondition:
    def _is_triggered(self, payload: dict, bravey_user_id: str) -> bool:
        return (
            payload.get("action") == "update"
            and payload.get("data", {}).get("assigneeId") == bravey_user_id
            and payload.get("updatedFrom") is not None
            and payload["updatedFrom"].get("assigneeId") != bravey_user_id
        )

    def test_assigned_to_bravey(self):
        payload = {
            "action": "update",
            "data": {"assigneeId": "bravey-bot"},
            "updatedFrom": {"assigneeId": None},
        }
        assert self._is_triggered(payload, "bravey-bot") is True

    def test_reassigned_from_bravey(self):
        payload = {
            "action": "update",
            "data": {"assigneeId": "other-user"},
            "updatedFrom": {"assigneeId": "bravey-bot"},
        }
        assert self._is_triggered(payload, "bravey-bot") is False

    def test_already_assigned_to_bravey(self):
        payload = {
            "action": "update",
            "data": {"assigneeId": "bravey-bot"},
            "updatedFrom": {"assigneeId": "bravey-bot"},
        }
        assert self._is_triggered(payload, "bravey-bot") is False

    def test_create_action_not_triggered(self):
        payload = {
            "action": "create",
            "data": {"assigneeId": "bravey-bot"},
            "updatedFrom": {"assigneeId": None},
        }
        assert self._is_triggered(payload, "bravey-bot") is False

    def test_no_updated_from(self):
        payload = {
            "action": "update",
            "data": {"assigneeId": "bravey-bot"},
        }
        assert self._is_triggered(payload, "bravey-bot") is False


class TestGitHubBranchSlugification:
    def test_basic_slug(self):
        from src.services.github_service import slugify_branch_name

        result = slugify_branch_name("ENG-42", "Fix null pointer in auth middleware")
        assert result == "bravey/eng-42-fix-null-pointer-in-auth-middleware"

    def test_special_characters(self):
        from src.services.github_service import slugify_branch_name

        result = slugify_branch_name("ENG-1", "Fix: user's (email) @login!")
        assert "bravey/eng-1-fix-user-s-email-login" == result

    def test_max_length(self):
        from src.services.github_service import slugify_branch_name

        result = slugify_branch_name("ENG-1", "A" * 200)
        assert len(result) <= 100
