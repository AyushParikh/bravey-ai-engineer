from pydantic import BaseModel


class LinearActor(BaseModel):
    id: str
    name: str | None = None
    type: str | None = None


class LinearState(BaseModel):
    id: str
    name: str
    type: str | None = None


class LinearTeam(BaseModel):
    id: str
    name: str
    key: str


class LinearIssueData(BaseModel):
    id: str
    identifier: str
    title: str
    description: str | None = None
    priority: int | None = None
    priorityLabel: str | None = None
    teamId: str | None = None
    stateId: str | None = None
    assigneeId: str | None = None
    delegateId: str | None = None
    creatorId: str | None = None
    labelIds: list[str] | None = None
    url: str | None = None
    state: LinearState | None = None
    team: LinearTeam | None = None


class LinearUpdatedFrom(BaseModel):
    assigneeId: str | None = None
    delegateId: str | None = None
    updatedAt: str | None = None


class LinearWebhookPayload(BaseModel):
    action: str
    actor: LinearActor | None = None
    createdAt: str | None = None
    organizationId: str
    webhookTimestamp: int
    type: str
    data: LinearIssueData
    updatedFrom: LinearUpdatedFrom | None = None


class LinearLabel(BaseModel):
    id: str
    name: str
    color: str | None = None


class LinearComment(BaseModel):
    id: str
    body: str
    createdAt: str | None = None
    user: dict | None = None


class LinearAttachment(BaseModel):
    id: str
    title: str | None = None
    url: str | None = None


class LinearParentIssue(BaseModel):
    id: str
    identifier: str
    title: str
    description: str | None = None


class LinearProject(BaseModel):
    id: str
    name: str


class LinearFullIssue(BaseModel):
    id: str
    identifier: str
    title: str
    description: str | None = None
    priority: int | None = None
    priorityLabel: str | None = None
    url: str | None = None
    team: LinearTeam | None = None
    state: LinearState | None = None
    labels: dict | None = None  # {"nodes": [...]}
    parent: LinearParentIssue | None = None
    comments: dict | None = None  # {"nodes": [...]}
    attachments: dict | None = None  # {"nodes": [...]}
    project: LinearProject | None = None
