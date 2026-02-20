import re
import time

import httpx
import jwt

GITHUB_API_URL = "https://api.github.com"


def generate_jwt(app_id: str, private_key: str) -> str:
    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + (10 * 60),  # 10-minute expiry
        "iss": app_id,
    }
    return jwt.encode(payload, private_key, algorithm="RS256")


def get_installation_token(
    app_id: str, private_key: str, installation_id: int
) -> str:
    token = generate_jwt(app_id, private_key)
    resp = httpx.post(
        f"{GITHUB_API_URL}/app/installations/{installation_id}/access_tokens",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["token"]


def slugify_branch_name(identifier: str, title: str, max_length: int = 100) -> str:
    slug = f"{identifier}-{title}".lower()
    slug = re.sub(r"[^a-z0-9-]", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    branch = f"bravey/{slug}"
    return branch[:max_length]


def get_default_branch_sha(
    token: str, owner: str, repo: str, branch: str
) -> str:
    resp = httpx.get(
        f"{GITHUB_API_URL}/repos/{owner}/{repo}/git/ref/heads/{branch}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["object"]["sha"]


def create_branch(
    token: str, owner: str, repo: str, branch_name: str, sha: str
) -> dict:
    resp = httpx.post(
        f"{GITHUB_API_URL}/repos/{owner}/{repo}/git/refs",
        json={"ref": f"refs/heads/{branch_name}", "sha": sha},
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=15,
    )
    # If branch already exists (422), delete and recreate it
    if resp.status_code == 422:
        # Delete the old branch
        del_resp = httpx.delete(
            f"{GITHUB_API_URL}/repos/{owner}/{repo}/git/refs/heads/{branch_name}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=15,
        )
        # Recreate
        retry_resp = httpx.post(
            f"{GITHUB_API_URL}/repos/{owner}/{repo}/git/refs",
            json={"ref": f"refs/heads/{branch_name}", "sha": sha},
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=15,
        )
        retry_resp.raise_for_status()
        return retry_resp.json()
    resp.raise_for_status()
    return resp.json()


def create_pull_request(
    token: str,
    owner: str,
    repo: str,
    title: str,
    body: str,
    head: str,
    base: str,
) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    resp = httpx.post(
        f"{GITHUB_API_URL}/repos/{owner}/{repo}/pulls",
        json={
            "title": title,
            "body": body,
            "head": head,
            "base": base,
            "draft": False,
        },
        headers=headers,
        timeout=15,
    )
    # If a PR already exists for this head branch, find and return it
    if resp.status_code == 422:
        existing = httpx.get(
            f"{GITHUB_API_URL}/repos/{owner}/{repo}/pulls",
            params={"head": f"{owner}:{head}", "state": "open"},
            headers=headers,
            timeout=15,
        )
        existing.raise_for_status()
        prs = existing.json()
        if prs:
            return prs[0]
    resp.raise_for_status()
    return resp.json()


def create_stub_commit(
    token: str, owner: str, repo: str, branch_name: str, base_sha: str,
    message: str = "stub: bravey agent placeholder commit",
) -> str:
    """Create a dummy commit on a branch via the GitHub API (no clone needed).
    Returns the new commit SHA.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    import base64

    # 1. Create a blob
    blob_resp = httpx.post(
        f"{GITHUB_API_URL}/repos/{owner}/{repo}/git/blobs",
        json={
            "content": base64.b64encode(
                b"# This file was created by Bravey (stub mode)\n"
            ).decode(),
            "encoding": "base64",
        },
        headers=headers,
        timeout=15,
    )
    blob_resp.raise_for_status()
    blob_sha = blob_resp.json()["sha"]

    # 2. Get the base tree
    tree_resp = httpx.get(
        f"{GITHUB_API_URL}/repos/{owner}/{repo}/git/commits/{base_sha}",
        headers=headers,
        timeout=15,
    )
    tree_resp.raise_for_status()
    base_tree_sha = tree_resp.json()["tree"]["sha"]

    # 3. Create a new tree with the stub file
    new_tree_resp = httpx.post(
        f"{GITHUB_API_URL}/repos/{owner}/{repo}/git/trees",
        json={
            "base_tree": base_tree_sha,
            "tree": [
                {
                    "path": ".bravey-stub",
                    "mode": "100644",
                    "type": "blob",
                    "sha": blob_sha,
                }
            ],
        },
        headers=headers,
        timeout=15,
    )
    new_tree_resp.raise_for_status()
    new_tree_sha = new_tree_resp.json()["sha"]

    # 4. Create the commit
    commit_resp = httpx.post(
        f"{GITHUB_API_URL}/repos/{owner}/{repo}/git/commits",
        json={
            "message": message,
            "tree": new_tree_sha,
            "parents": [base_sha],
        },
        headers=headers,
        timeout=15,
    )
    commit_resp.raise_for_status()
    new_commit_sha = commit_resp.json()["sha"]

    # 5. Update the branch ref (force to handle reused branches)
    update_resp = httpx.patch(
        f"{GITHUB_API_URL}/repos/{owner}/{repo}/git/refs/heads/{branch_name}",
        json={"sha": new_commit_sha, "force": True},
        headers=headers,
        timeout=15,
    )
    update_resp.raise_for_status()

    return new_commit_sha


def add_labels(
    token: str, owner: str, repo: str, issue_number: int, labels: list[str]
) -> dict:
    resp = httpx.post(
        f"{GITHUB_API_URL}/repos/{owner}/{repo}/issues/{issue_number}/labels",
        json={"labels": labels},
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()
