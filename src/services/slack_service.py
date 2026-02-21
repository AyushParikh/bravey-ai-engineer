import logging

import httpx

logger = logging.getLogger(__name__)

SLACK_API_URL = "https://slack.com/api"


def _headers(bot_token: str) -> dict:
    return {
        "Authorization": f"Bearer {bot_token}",
        "Content-Type": "application/json",
    }


def lookup_user_by_email(bot_token: str, email: str) -> str | None:
    """Look up a Slack user ID by email. Returns None if not found."""
    resp = httpx.get(
        f"{SLACK_API_URL}/users.lookupByEmail",
        params={"email": email},
        headers={"Authorization": f"Bearer {bot_token}"},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("ok"):
        return data["user"]["id"]
    logger.warning("Slack user lookup failed for %s: %s", email, data.get("error"))
    return None


def send_dm(bot_token: str, user_id: str, text: str, blocks: list | None = None) -> dict | None:
    """Open a DM channel with a user and send a message."""
    # Open (or retrieve) the DM channel
    open_resp = httpx.post(
        f"{SLACK_API_URL}/conversations.open",
        json={"users": user_id},
        headers=_headers(bot_token),
        timeout=10,
    )
    open_resp.raise_for_status()
    open_data = open_resp.json()
    if not open_data.get("ok"):
        logger.warning("Failed to open DM channel with %s: %s", user_id, open_data.get("error"))
        return None

    channel_id = open_data["channel"]["id"]

    # Send the message
    msg_payload: dict = {"channel": channel_id, "text": text}
    if blocks:
        msg_payload["blocks"] = blocks

    msg_resp = httpx.post(
        f"{SLACK_API_URL}/chat.postMessage",
        json=msg_payload,
        headers=_headers(bot_token),
        timeout=10,
    )
    msg_resp.raise_for_status()
    return msg_resp.json()


def post_run_started(
    bot_token: str,
    channel_id: str,
    issue_identifier: str,
    issue_title: str,
    issue_url: str,
    assigned_by: str,
) -> dict:
    resp = httpx.post(
        f"{SLACK_API_URL}/chat.postMessage",
        json={
            "channel": channel_id,
            "text": f"Bravey is working on {issue_identifier}",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"*Bravey started working on "
                            f"<{issue_url}|{issue_identifier}: {issue_title}>*"
                        ),
                    },
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": f"Status: *Running*  |  Assigned by: {assigned_by}",
                        }
                    ],
                },
            ],
        },
        headers=_headers(bot_token),
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def update_run_completed(
    bot_token: str,
    channel_id: str,
    message_ts: str,
    issue_identifier: str,
    issue_url: str,
    pr_url: str,
    pr_number: int,
    pr_title: str,
    branch_name: str,
    duration: str,
) -> dict:
    resp = httpx.post(
        f"{SLACK_API_URL}/chat.update",
        json={
            "channel": channel_id,
            "ts": message_ts,
            "text": f"Bravey opened a PR for {issue_identifier}",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"*Bravey opened a PR for "
                            f"<{issue_url}|{issue_identifier}>*\n\n"
                            f"<{pr_url}|#{pr_number}: {pr_title}>"
                        ),
                    },
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": f"Branch: `{branch_name}`  |  Duration: {duration}",
                        }
                    ],
                },
            ],
        },
        headers=_headers(bot_token),
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def update_run_failed(
    bot_token: str,
    channel_id: str,
    message_ts: str,
    issue_identifier: str,
    issue_url: str,
    error_message: str,
    run_id: str,
) -> dict:
    resp = httpx.post(
        f"{SLACK_API_URL}/chat.update",
        json={
            "channel": channel_id,
            "ts": message_ts,
            "text": f"Bravey failed on {issue_identifier}",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"*Bravey failed on "
                            f"<{issue_url}|{issue_identifier}>*\n\n"
                            f"Error: `{error_message}`"
                        ),
                    },
                },
            ],
        },
        headers=_headers(bot_token),
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()
