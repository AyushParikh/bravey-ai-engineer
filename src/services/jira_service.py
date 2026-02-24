import hashlib
import logging
import time
from urllib.parse import urlparse

import httpx
import jwt as pyjwt

logger = logging.getLogger(__name__)


def create_jwt(method: str, url: str, client_key: str, shared_secret: str) -> str:
    """Create a JWT for outgoing Atlassian Connect API calls."""
    now = int(time.time())
    parsed = urlparse(url)
    # Canonical URI is path + query
    canonical_url = parsed.path
    if parsed.query:
        canonical_url += "?" + parsed.query

    # QSH (query string hash) per Atlassian Connect spec
    qsh_input = f"{method.upper()}&{canonical_url}&"
    qsh = hashlib.sha256(qsh_input.encode("utf-8")).hexdigest()

    payload = {
        "iss": client_key,
        "iat": now,
        "exp": now + 180,  # 3 minute expiry
        "qsh": qsh,
    }
    return pyjwt.encode(payload, shared_secret, algorithm="HS256")


def _headers(method: str, url: str, client_key: str, shared_secret: str) -> dict:
    """Build auth headers with JWT for outgoing API calls."""
    token = create_jwt(method, url, client_key, shared_secret)
    return {
        "Authorization": f"JWT {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def fetch_issue(
    client_key: str, shared_secret: str, base_url: str, issue_key: str
) -> dict:
    """Fetch a Jira issue by key."""
    url = f"{base_url}/rest/api/3/issue/{issue_key}"
    params = {"fields": "summary,description,status,assignee,creator,reporter,priority,labels,issuetype,project,comment,parent"}
    headers = _headers("GET", f"/rest/api/3/issue/{issue_key}", client_key, shared_secret)
    resp = httpx.get(url, headers=headers, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_transitions(
    client_key: str, shared_secret: str, base_url: str, issue_id: str
) -> list[dict]:
    """Get available transitions for an issue."""
    url = f"{base_url}/rest/api/3/issue/{issue_id}/transitions"
    headers = _headers("GET", f"/rest/api/3/issue/{issue_id}/transitions", client_key, shared_secret)
    resp = httpx.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.json().get("transitions", [])


def find_transition_to_status(transitions: list[dict], target_status_id: str) -> dict | None:
    """Find a transition that leads to the target status."""
    for t in transitions:
        to_status = t.get("to", {})
        if to_status.get("id") == target_status_id:
            return t
    return None


def transition_issue(
    client_key: str,
    shared_secret: str,
    base_url: str,
    issue_id: str,
    transition_id: str,
) -> dict:
    """Execute a transition on an issue (Jira requires transitions, not direct status sets)."""
    url = f"{base_url}/rest/api/3/issue/{issue_id}/transitions"
    headers = _headers("POST", f"/rest/api/3/issue/{issue_id}/transitions", client_key, shared_secret)
    body = {"transition": {"id": transition_id}}
    resp = httpx.post(url, headers=headers, json=body, timeout=15)
    resp.raise_for_status()
    return {}


def transition_issue_to_status(
    client_key: str,
    shared_secret: str,
    base_url: str,
    issue_id: str,
    target_status_id: str,
) -> bool:
    """Convenience: find and execute a transition to the target status. Returns True if successful."""
    transitions = get_transitions(client_key, shared_secret, base_url, issue_id)
    transition = find_transition_to_status(transitions, target_status_id)
    if not transition:
        logger.warning(
            "No transition found to status %s for issue %s", target_status_id, issue_id
        )
        return False
    transition_issue(client_key, shared_secret, base_url, issue_id, transition["id"])
    return True


def add_comment(
    client_key: str, shared_secret: str, base_url: str, issue_id: str, body_text: str
) -> dict:
    """Post a comment on a Jira issue in Atlassian Document Format (ADF)."""
    url = f"{base_url}/rest/api/3/issue/{issue_id}/comment"
    headers = _headers("POST", f"/rest/api/3/issue/{issue_id}/comment", client_key, shared_secret)
    adf_body = {
        "body": {
            "version": 1,
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": body_text}],
                }
            ],
        }
    }
    resp = httpx.post(url, headers=headers, json=adf_body, timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_statuses(
    client_key: str, shared_secret: str, base_url: str
) -> list[dict]:
    """List all workflow statuses in the Jira site."""
    url = f"{base_url}/rest/api/3/status"
    headers = _headers("GET", "/rest/api/3/status", client_key, shared_secret)
    resp = httpx.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.json()


def search_users(
    client_key: str, shared_secret: str, base_url: str
) -> list[dict]:
    """List users in the Jira site."""
    url = f"{base_url}/rest/api/3/users/search"
    headers = _headers("GET", "/rest/api/3/users/search", client_key, shared_secret)
    resp = httpx.get(url, headers=headers, params={"maxResults": 200}, timeout=15)
    resp.raise_for_status()
    return resp.json()


def find_user_by_email(
    client_key: str, shared_secret: str, base_url: str, email: str
) -> dict | None:
    """Look up a user by email address."""
    users = search_users(client_key, shared_secret, base_url)
    for u in users:
        if u.get("emailAddress", "").lower() == email.lower():
            return u
    return None


def adf_to_plaintext(adf: dict | None) -> str:
    """Convert Atlassian Document Format to plain text."""
    if not adf:
        return ""

    parts: list[str] = []

    def _walk(node: dict) -> None:
        if node.get("type") == "text":
            parts.append(node.get("text", ""))
        elif node.get("type") == "hardBreak":
            parts.append("\n")
        for child in node.get("content", []):
            _walk(child)
        # Add newline after block-level elements
        if node.get("type") in ("paragraph", "heading", "bulletList", "orderedList", "listItem", "blockquote", "codeBlock"):
            parts.append("\n")

    _walk(adf)
    return "".join(parts).strip()


def verify_jwt(authorization_header: str, shared_secret: str) -> dict | None:
    """Verify an incoming webhook JWT. Returns decoded claims or None."""
    if not authorization_header:
        return None

    # Format: "JWT <token>" or just "<token>"
    token = authorization_header
    if token.upper().startswith("JWT "):
        token = token[4:]

    try:
        claims = pyjwt.decode(
            token,
            shared_secret,
            algorithms=["HS256"],
            options={"verify_aud": False},
        )
        return claims
    except pyjwt.PyJWTError:
        logger.warning("JWT verification failed")
        return None
