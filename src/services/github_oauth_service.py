from urllib.parse import urlencode

import httpx

from src.config import settings

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_API_URL = "https://api.github.com"


def get_authorization_url(state: str) -> str:
    params = {
        "client_id": settings.github_oauth_client_id,
        "scope": "read:user user:email",
        "state": state,
    }
    return f"{GITHUB_AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code_for_token(code: str) -> str:
    resp = httpx.post(
        GITHUB_TOKEN_URL,
        json={
            "client_id": settings.github_oauth_client_id,
            "client_secret": settings.github_oauth_client_secret,
            "code": code,
        },
        headers={"Accept": "application/json"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise ValueError(f"GitHub OAuth error: {data['error_description']}")
    return data["access_token"]


def get_github_user(access_token: str) -> dict:
    resp = httpx.get(
        f"{GITHUB_API_URL}/user",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github+json",
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def get_github_emails(access_token: str) -> list[dict]:
    resp = httpx.get(
        f"{GITHUB_API_URL}/user/emails",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github+json",
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()
