import hashlib
import hmac
import time

import httpx

LINEAR_API_URL = "https://api.linear.app/graphql"


def verify_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(
        secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_timestamp(webhook_timestamp_ms: int, max_age_ms: int = 60_000) -> bool:
    now_ms = int(time.time() * 1000)
    return abs(now_ms - webhook_timestamp_ms) <= max_age_ms


async def graphql_request(
    access_token: str, query: str, variables: dict | None = None
) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
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


async def fetch_issue(access_token: str, issue_id: str) -> dict:
    result = await graphql_request(
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


async def update_issue_state(
    access_token: str, issue_id: str, state_id: str
) -> dict:
    return await graphql_request(
        access_token,
        UPDATE_ISSUE_STATE_MUTATION,
        {"id": issue_id, "stateId": state_id},
    )


CREATE_COMMENT_MUTATION = """
mutation CommentCreate($issueId: String!, $body: String!) {
  commentCreate(input: { issueId: $issueId, body: $body }) {
    success
    comment { id }
  }
}
"""


async def create_comment(
    access_token: str, issue_id: str, body: str
) -> dict:
    return await graphql_request(
        access_token,
        CREATE_COMMENT_MUTATION,
        {"issueId": issue_id, "body": body},
    )
