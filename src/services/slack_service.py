import httpx

SLACK_API_URL = "https://slack.com/api"


def _headers(bot_token: str) -> dict:
    return {
        "Authorization": f"Bearer {bot_token}",
        "Content-Type": "application/json",
    }


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
