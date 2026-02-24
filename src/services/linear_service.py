import hashlib
import hmac
import logging
import time
from datetime import datetime, timedelta, timezone

import httpx

from src.config import settings

logger = logging.getLogger(__name__)

LINEAR_API_URL = "https://api.linear.app/graphql"
LINEAR_TOKEN_URL = "https://api.linear.app/oauth/token"
LINEAR_MIGRATE_URL = "https://api.linear.app/oauth/migrate_old_token"

# Refresh tokens when they expire within this window
TOKEN_REFRESH_BUFFER = timedelta(minutes=5)


def verify_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(
        secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_timestamp(webhook_timestamp_ms: int, max_age_ms: int = 60_000) -> bool:
    now_ms = int(time.time() * 1000)
    return abs(now_ms - webhook_timestamp_ms) <= max_age_ms


def graphql_request(
    access_token: str, query: str, variables: dict | None = None
) -> dict:
    resp = httpx.post(
        LINEAR_API_URL,
        json={"query": query, "variables": variables or {}},
        headers={
            "Authorization": access_token,
            "Content-Type": "application/json",
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


FETCH_ISSUE_QUERY = """
query GetIssue($id: String!) {
  issue(id: $id) {
    id
    identifier
    title
    description
    priority
    priorityLabel
    url
    creator { id name email }
    team { id name key }
    state { id name type }
    labels { nodes { id name color } }
    parent { id identifier title description }
    comments { nodes { id body createdAt user { name email } } }
    attachments { nodes { id title url } }
    project { id name }
  }
}
"""


def fetch_issue(access_token: str, issue_id: str) -> dict:
    result = graphql_request(
        access_token, FETCH_ISSUE_QUERY, {"id": issue_id}
    )
    return result.get("data", {}).get("issue", {})


UPDATE_ISSUE_STATE_MUTATION = """
mutation UpdateIssue($id: String!, $stateId: String!) {
  issueUpdate(id: $id, input: { stateId: $stateId }) {
    success
    issue { id state { name } }
  }
}
"""


def update_issue_state(
    access_token: str, issue_id: str, state_id: str
) -> dict:
    return graphql_request(
        access_token,
        UPDATE_ISSUE_STATE_MUTATION,
        {"id": issue_id, "stateId": state_id},
    )


UNASSIGN_ISSUE_MUTATION = """
mutation UnassignIssue($id: String!) {
  issueUpdate(id: $id, input: { assigneeId: null }) {
    success
    issue { id }
  }
}
"""


def unassign_issue(access_token: str, issue_id: str) -> dict:
    return graphql_request(
        access_token,
        UNASSIGN_ISSUE_MUTATION,
        {"id": issue_id},
    )


CREATE_COMMENT_MUTATION = """
mutation CommentCreate($issueId: String!, $body: String!) {
  commentCreate(input: { issueId: $issueId, body: $body }) {
    success
    comment { id }
  }
}
"""


def create_comment(
    access_token: str, issue_id: str, body: str
) -> dict:
    return graphql_request(
        access_token,
        CREATE_COMMENT_MUTATION,
        {"issueId": issue_id, "body": body},
    )


# --- Token refresh ---


def refresh_token(refresh_tok: str) -> dict:
    """Exchange a refresh token for a new access + refresh token pair."""
    resp = httpx.post(
        LINEAR_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "client_id": settings.linear_client_id,
            "client_secret": settings.linear_client_secret,
            "refresh_token": refresh_tok,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def migrate_old_token(access_token: str) -> dict:
    """Migrate a long-lived token to get a refresh token (one-time)."""
    resp = httpx.post(
        LINEAR_MIGRATE_URL,
        data={
            "client_id": settings.linear_client_id,
            "client_secret": settings.linear_client_secret,
            "access_token": access_token,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def _apply_token_refresh(org, token_data: dict, token_type: str) -> str:
    """Write refreshed token data onto org fields. Returns the new access token."""
    new_access = token_data["access_token"]
    new_refresh = token_data.get("refresh_token")
    expires_in = token_data.get("expires_in", 0)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in) if expires_in else None

    if token_type == "bot":
        org.linear_bot_token = new_access
        org.linear_bot_refresh_token = new_refresh
        org.linear_bot_token_expires_at = expires_at
    else:
        org.linear_access_token = new_access
        org.linear_refresh_token = new_refresh
        org.linear_token_expires_at = expires_at

    return new_access


def ensure_valid_token(db, org, token_type: str = "admin") -> str:
    """Ensure the Linear token is valid (sync — for orchestrator).

    Returns the current or refreshed access token.
    """
    if token_type == "bot":
        access_tok = org.linear_bot_token
        refresh_tok = org.linear_bot_refresh_token
        expires_at = org.linear_bot_token_expires_at
    else:
        access_tok = org.linear_access_token
        refresh_tok = org.linear_refresh_token
        expires_at = org.linear_token_expires_at

    if not access_tok:
        return access_tok

    now = datetime.now(timezone.utc)

    # Token still valid with buffer
    if expires_at is not None and expires_at - now > TOKEN_REFRESH_BUFFER:
        return access_tok

    # No expiry tracked and no refresh token → try migration
    if expires_at is None and refresh_tok is None:
        try:
            logger.info("Attempting Linear token migration for %s (%s)", org.slug, token_type)
            token_data = migrate_old_token(access_tok)
            new_token = _apply_token_refresh(org, token_data, token_type)
            db.commit()
            logger.info("Linear token migration succeeded for %s (%s)", org.slug, token_type)
            return new_token
        except Exception:
            logger.warning(
                "Linear token migration failed for %s (%s), using existing token",
                org.slug, token_type, exc_info=True,
            )
            return access_tok

    # Need to refresh (expired or about to expire)
    if refresh_tok:
        try:
            logger.info("Refreshing Linear token for %s (%s)", org.slug, token_type)
            token_data = refresh_token(refresh_tok)
            new_token = _apply_token_refresh(org, token_data, token_type)
            db.commit()
            logger.info("Linear token refresh succeeded for %s (%s)", org.slug, token_type)
            return new_token
        except Exception:
            logger.warning(
                "Linear token refresh failed for %s (%s), using existing token",
                org.slug, token_type, exc_info=True,
            )
            return access_tok

    # Have expiry but no refresh token — return as-is
    return access_tok


async def async_ensure_valid_token(db, org, token_type: str = "admin") -> str:
    """Ensure the Linear token is valid (async — for API routes).

    Returns the current or refreshed access token.
    """
    if token_type == "bot":
        access_tok = org.linear_bot_token
        refresh_tok = org.linear_bot_refresh_token
        expires_at = org.linear_bot_token_expires_at
    else:
        access_tok = org.linear_access_token
        refresh_tok = org.linear_refresh_token
        expires_at = org.linear_token_expires_at

    if not access_tok:
        return access_tok

    now = datetime.now(timezone.utc)

    # Token still valid with buffer
    if expires_at is not None and expires_at - now > TOKEN_REFRESH_BUFFER:
        return access_tok

    # No expiry tracked and no refresh token → try migration
    if expires_at is None and refresh_tok is None:
        try:
            logger.info("Attempting Linear token migration for %s (%s)", org.slug, token_type)
            token_data = migrate_old_token(access_tok)
            new_token = _apply_token_refresh(org, token_data, token_type)
            await db.commit()
            logger.info("Linear token migration succeeded for %s (%s)", org.slug, token_type)
            return new_token
        except Exception:
            logger.warning(
                "Linear token migration failed for %s (%s), using existing token",
                org.slug, token_type, exc_info=True,
            )
            return access_tok

    # Need to refresh
    if refresh_tok:
        try:
            logger.info("Refreshing Linear token for %s (%s)", org.slug, token_type)
            token_data = refresh_token(refresh_tok)
            new_token = _apply_token_refresh(org, token_data, token_type)
            await db.commit()
            logger.info("Linear token refresh succeeded for %s (%s)", org.slug, token_type)
            return new_token
        except Exception:
            logger.warning(
                "Linear token refresh failed for %s (%s), using existing token",
                org.slug, token_type, exc_info=True,
            )
            return access_tok

    return access_tok
