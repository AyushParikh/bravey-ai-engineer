from pydantic import BaseModel, Field


class JiraUser(BaseModel):
    accountId: str
    displayName: str | None = None
    emailAddress: str | None = None


class JiraStatusCategory(BaseModel):
    id: int | None = None
    key: str | None = None
    name: str | None = None


class JiraStatus(BaseModel):
    id: str | None = None
    name: str | None = None
    statusCategory: JiraStatusCategory | None = None


class JiraIssueType(BaseModel):
    id: str | None = None
    name: str | None = None
    subtask: bool = False


class JiraPriority(BaseModel):
    id: str | None = None
    name: str | None = None


class JiraProject(BaseModel):
    id: str | None = None
    key: str | None = None
    name: str | None = None


class JiraComment(BaseModel):
    comments: list[dict] | None = None
    total: int | None = None


class JiraIssueFields(BaseModel):
    summary: str | None = None
    description: dict | None = None  # ADF format
    status: JiraStatus | None = None
    assignee: JiraUser | None = None
    creator: JiraUser | None = None
    reporter: JiraUser | None = None
    priority: JiraPriority | None = None
    labels: list[str] | None = None
    issuetype: JiraIssueType | None = None
    project: JiraProject | None = None
    comment: JiraComment | None = None
    parent: dict | None = None


class JiraIssue(BaseModel):
    id: str
    key: str
    self_url: str | None = Field(default=None, alias="self")
    fields: JiraIssueFields


class JiraChangelogItem(BaseModel):
    field: str
    fieldtype: str | None = None
    fromString: str | None = None
    toString: str | None = None
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None


class JiraChangelog(BaseModel):
    items: list[JiraChangelogItem] = []


class JiraWebhookPayload(BaseModel):
    timestamp: int | None = None
    webhookEvent: str
    issue: JiraIssue | None = None
    changelog: JiraChangelog | None = None
    user: JiraUser | None = None


class JiraInstallPayload(BaseModel):
    key: str
    clientKey: str
    sharedSecret: str
    baseUrl: str
    productType: str | None = None
    eventType: str | None = None
