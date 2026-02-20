from pydantic import BaseModel


class GitHubUser(BaseModel):
    login: str
    id: int


class GitHubRepo(BaseModel):
    id: int
    full_name: str
    default_branch: str | None = None


class GitHubPullRequestHead(BaseModel):
    ref: str
    sha: str


class GitHubPullRequest(BaseModel):
    number: int
    title: str
    html_url: str
    state: str
    merged: bool | None = None
    head: GitHubPullRequestHead | None = None
    base: GitHubPullRequestHead | None = None
    user: GitHubUser | None = None


class GitHubWebhookPayload(BaseModel):
    action: str
    pull_request: GitHubPullRequest | None = None
    repository: GitHubRepo | None = None
    installation: dict | None = None
