# Bravey — Detailed ERD & Integration Spec

---

## Overview

Bravey is a bot user that lives in Linear. When a Linear issue is assigned to Bravey, it:
1. Receives a webhook from Linear
2. Fetches the full issue context via Linear's GraphQL API
3. Notifies Slack that work is starting
4. Creates a branch on GitHub
5. Spins up a Claude Code instance in an ephemeral cloud container
6. Claude reads the repo and ticket, writes the code, commits, and pushes
7. Bravey opens a PR on GitHub
8. Updates the Linear issue state and posts a comment with the PR link
9. Updates the Slack message with the PR link

---

## Integration 1: Linear

### How Bravey connects

Bravey is a **dedicated Linear bot user** created per customer organization. The user has an email like `bravey+{org_slug}@yourdomain.com`. When onboarding a new org, Bravey programmatically creates this user via the Linear OAuth app flow and invites it to the workspace.

Bravey registers a **webhook** on the organization via the Linear API:

```
POST https://api.linear.app/graphql
Authorization: Bearer {linear_oauth_access_token}

mutation WebhookCreate {
  webhookCreate(input: {
    url: "https://api.bravey.dev/webhooks/linear"
    resourceTypes: ["Issue"]
    secret: "{randomly_generated_per_org_secret}"
  }) {
    success
    webhook { id secret }
  }
}
```

Store the returned `webhook.id` and `webhook.secret` in the `organizations` table. The secret is used to verify every incoming webhook.

---

### Incoming webhook payload (on assignment)

When a user assigns an issue to the Bravey bot user, Linear fires a POST to your webhook URL with these headers:

```
Linear-Event: Issue
Linear-Delivery: <uuid>
Linear-Signature: <hmac-sha256-hex>
Content-Type: application/json
```

The payload body looks like:

```json
{
  "action": "update",
  "actor": {
    "id": "user-uuid",
    "name": "Jane Smith",
    "type": "user"
  },
  "createdAt": "2025-02-19T10:00:00.000Z",
  "organizationId": "org-uuid",
  "webhookTimestamp": 1739962800000,
  "type": "Issue",
  "data": {
    "id": "issue-uuid",
    "identifier": "ENG-42",
    "title": "Fix null pointer in auth middleware",
    "description": "When a user logs in with an expired token...",
    "priority": 2,
    "priorityLabel": "High",
    "teamId": "team-uuid",
    "stateId": "state-uuid",
    "assigneeId": "bravey-bot-user-uuid",
    "creatorId": "user-uuid",
    "labelIds": ["label-uuid"],
    "url": "https://linear.app/yourorg/issue/ENG-42/fix-null-pointer",
    "state": {
      "id": "state-uuid",
      "name": "In Progress",
      "type": "started"
    },
    "team": {
      "id": "team-uuid",
      "name": "Engineering",
      "key": "ENG"
    }
  },
  "updatedFrom": {
    "assigneeId": null,
    "updatedAt": "2025-02-19T09:59:00.000Z"
  }
}
```

**Trigger condition**: `action === "update"` AND `data.assigneeId === bravey_bot_user_id` AND `updatedFrom.assigneeId !== bravey_bot_user_id` (i.e., assignee was just changed TO Bravey).

---

### Webhook signature verification

```
HMAC-SHA256(raw_request_body, webhook_secret) === Linear-Signature header
```

Also check that `webhookTimestamp` is within 60 seconds of current time to prevent replay attacks. Respond with `200 OK` immediately (within 5 seconds or Linear will retry). Do all real processing asynchronously via a job queue.

Linear retries failed deliveries 3 times: after 1 minute, 1 hour, and 6 hours. If your endpoint keeps failing, Linear disables the webhook. Persist the raw payload to `webhook_events` before touching the queue so you can replay manually if needed.

---

### Fetching full issue context (Linear GraphQL API)

The webhook payload gives you a summary but not comments or attachments. After enqueueing, fetch the full issue:

```
POST https://api.linear.app/graphql
Authorization: Bearer {linear_oauth_access_token}

query GetIssue($id: String!) {
  issue(id: $id) {
    id
    identifier
    title
    description
    priority
    priorityLabel
    url
    team {
      id
      name
      key
    }
    state {
      id
      name
      type
    }
    labels {
      nodes { id name color }
    }
    parent {
      id
      identifier
      title
      description
    }
    comments {
      nodes {
        id
        body
        createdAt
        user { name email }
      }
    }
    attachments {
      nodes {
        id
        title
        url
      }
    }
    project {
      id
      name
    }
  }
}
```

This full context object becomes the prompt for Claude Code.

---

### Updating the Linear issue after PR is opened

Move the issue to "In Review" state and post a comment with the PR link:

```
POST https://api.linear.app/graphql

mutation UpdateIssue($id: String!, $stateId: String!) {
  issueUpdate(id: $id, input: { stateId: $stateId }) {
    success
    issue { id state { name } }
  }
}
```

The `stateId` for "In Review" must be fetched from the team's workflow states during onboarding and stored in org config. Different teams have different state IDs.

Then post a comment:

```
mutation CommentCreate($issueId: String!, $body: String!) {
  commentCreate(input: { issueId: $issueId, body: $body }) {
    success
    comment { id }
  }
}
```

Comment body example:
```
🤖 **Bravey opened a PR**

**PR:** [ENG-42: Fix null pointer in auth middleware](https://github.com/org/repo/pull/123)
**Branch:** `bravey/ENG-42-fix-null-pointer-in-auth-middleware`

Changes made:
- Fixed null check in `src/middleware/auth.ts` line 47
- Added test case for expired token scenario

[View Bravey run log](https://app.bravey.dev/runs/run-uuid)
```

---

## Integration 2: GitHub

### How Bravey connects

Bravey uses a **GitHub App** (not OAuth). This is the right approach because:
- GitHub Apps use short-lived installation access tokens (expire after 1 hour) — better security
- Fine-grained permissions scoped per repo
- Can be installed by org admins without a personal OAuth grant

GitHub App permissions required:
- `contents: write` — to clone, create branches, push commits
- `pull_requests: write` — to open PRs
- `metadata: read` — required for any GitHub App

When a customer installs the Bravey GitHub App, GitHub fires an `installation` webhook event with an `installation_id`. Store this in `organizations.github_installation_id`.

---

### Getting an installation access token

Before every GitHub API call during an agent run, generate a short-lived token:

```
POST https://api.github.com/app/installations/{installation_id}/access_tokens
Authorization: Bearer {jwt_signed_with_github_app_private_key}
Accept: application/vnd.github+json
X-GitHub-Api-Version: 2022-11-28
```

Response:
```json
{
  "token": "ghs_abc123...",
  "expires_at": "2025-02-19T11:00:00Z",
  "permissions": { "contents": "write", "pull_requests": "write" }
}
```

Use this `token` as `Authorization: Bearer {token}` for all subsequent GitHub REST calls. Tokens expire in 1 hour; generate a fresh one at the start of each agent run.

---

### Creating the branch

First get the SHA of the default branch HEAD:

```
GET https://api.github.com/repos/{owner}/{repo}/git/ref/heads/{default_branch}
Authorization: Bearer {installation_token}
```

Response includes `object.sha`. Then create the branch:

```
POST https://api.github.com/repos/{owner}/{repo}/git/refs
Authorization: Bearer {installation_token}
Content-Type: application/json

{
  "ref": "refs/heads/bravey/ENG-42-fix-null-pointer-in-auth-middleware",
  "sha": "{default_branch_head_sha}"
}
```

Branch naming convention: `bravey/{issue-identifier}-{slugified-title}` (max 100 chars, lowercase, hyphens only).

---

### Cloning the repo in the container

The Claude Code container authenticates using the installation token via HTTPS:

```bash
git clone https://x-access-token:{installation_token}@github.com/{owner}/{repo}.git
git checkout bravey/ENG-42-fix-null-pointer-in-auth-middleware
```

Claude Code runs inside this clone. It commits and pushes to the branch using the same token in the remote URL.

---

### Opening the Pull Request

After Claude Code finishes and pushes its commits:

```
POST https://api.github.com/repos/{owner}/{repo}/pulls
Authorization: Bearer {installation_token}
Content-Type: application/json

{
  "title": "ENG-42: Fix null pointer in auth middleware",
  "body": "## Summary\n\nFixes null pointer exception in auth middleware when token is expired.\n\n**Linear issue:** [ENG-42](https://linear.app/yourorg/issue/ENG-42)\n\n## Changes\n- Added null check before token validation in `src/middleware/auth.ts`\n- Added test case for expired token scenario\n\n---\n*This PR was opened by [Bravey](https://bravey.dev) 🤖*",
  "head": "bravey/ENG-42-fix-null-pointer-in-auth-middleware",
  "base": "main",
  "draft": false
}
```

Response includes `number` (PR number) and `html_url`. Store both in `agent_runs.pr_number` and `agent_runs.pr_url`.

After creating the PR, add the label `bravey-generated` via:

```
POST https://api.github.com/repos/{owner}/{repo}/issues/{pr_number}/labels
{
  "labels": ["bravey-generated"]
}
```

(Labels must be pre-created in the repo during onboarding.)

---

### Listening for GitHub webhooks (optional but recommended)

Register a webhook on the installation for PR events so Bravey can:
- Update Linear to "Done" when PR is merged
- Notify Slack when reviewer requests changes

Events to subscribe: `pull_request` (actions: `closed`, `review_requested`).

Verify GitHub webhook signatures via `X-Hub-Signature-256` header using `HMAC-SHA256(raw_body, webhook_secret)`.

---

## Integration 3: Slack

### How Bravey connects

Bravey uses a **Slack App** with Bot Token Scopes. Customers install Bravey to their workspace via OAuth. The OAuth flow returns a `bot_token` (starts with `xoxb-`). Store this encrypted in `organizations.slack_bot_token`.

Required OAuth scopes:
- `chat:write` — post and update messages
- `chat:write.public` — post in channels the bot hasn't joined (optional; otherwise must invite bot to channel)
- `channels:read` — list channels so user can pick one during onboarding

---

### Posting the "run started" message

When a run is enqueued, immediately post to Slack before the agent starts:

```
POST https://slack.com/api/chat.postMessage
Authorization: Bearer {slack_bot_token}
Content-Type: application/json

{
  "channel": "C0123456789",
  "text": "🤖 Bravey is working on ENG-42",
  "blocks": [
    {
      "type": "section",
      "text": {
        "type": "mrkdwn",
        "text": "🤖 *Bravey started working on <https://linear.app/yourorg/issue/ENG-42|ENG-42: Fix null pointer in auth middleware>*"
      }
    },
    {
      "type": "context",
      "elements": [
        { "type": "mrkdwn", "text": "⏳ Status: *Running*  |  Assigned by: Jane Smith" }
      ]
    }
  ]
}
```

The response includes:
```json
{
  "ok": true,
  "channel": "C0123456789",
  "ts": "1739962801.000100",
  "message": { ... }
}
```

**Save `ts` and `channel` to `slack_notifications`.** This is the key to updating the message later instead of posting a new one.

---

### Updating the message when PR is opened

```
POST https://slack.com/api/chat.update
Authorization: Bearer {slack_bot_token}
Content-Type: application/json

{
  "channel": "C0123456789",
  "ts": "1739962801.000100",
  "text": "✅ Bravey opened a PR for ENG-42",
  "blocks": [
    {
      "type": "section",
      "text": {
        "type": "mrkdwn",
        "text": "✅ *Bravey opened a PR for <https://linear.app/yourorg/issue/ENG-42|ENG-42>*\n\n<https://github.com/org/repo/pull/123|#123: Fix null pointer in auth middleware>"
      }
    },
    {
      "type": "context",
      "elements": [
        { "type": "mrkdwn", "text": "Branch: `bravey/ENG-42-fix-null-pointer`  |  Duration: 4m 12s" }
      ]
    }
  ]
}
```

On failure, update the message with:
```json
{
  "text": "❌ Bravey failed on ENG-42",
  "blocks": [
    {
      "type": "section",
      "text": {
        "type": "mrkdwn",
        "text": "❌ *Bravey failed on <https://linear.app/yourorg/issue/ENG-42|ENG-42>*\n\nError: `Claude Code timed out after 30 minutes`\n\n<https://app.bravey.dev/runs/run-uuid|View run log>"
      }
    }
  ]
}
```

---

## ERD

```
┌─────────────────────────────────────────────────┐
│                   organizations                  │
├─────────────────────────────────────────────────┤
│ id                        UUID           PK      │
│ name                      VARCHAR(255)           │
│ slug                      VARCHAR(100)   UNIQUE  │
│ linear_org_id             VARCHAR(100)   UNIQUE  │
│ linear_webhook_id         VARCHAR(100)           │
│ linear_webhook_secret     TEXT           🔒      │
│ linear_access_token       TEXT           🔒      │
│ linear_bravey_user_id     VARCHAR(100)           │
│ linear_in_review_state_id VARCHAR(100)           │ ← fetched during onboarding
│ github_installation_id    BIGINT                 │
│ github_app_private_key    TEXT           🔒      │
│ slack_team_id             VARCHAR(100)           │
│ slack_bot_token           TEXT           🔒      │
│ slack_default_channel_id  VARCHAR(100)           │
│ created_at                TIMESTAMPTZ            │
│ updated_at                TIMESTAMPTZ            │
└───────────────┬─────────────────────────────────┘
                │ 1
                │
    ┌───────────┼─────────────────────────────────┐
    │ N         │ N                               │ N
    ▼           ▼                                 ▼
┌──────────┐ ┌─────────────────────────────┐ ┌──────────────────────┐
│  users   │ │        repositories          │ │    webhook_events     │
├──────────┤ ├─────────────────────────────┤ ├──────────────────────┤
│ id    PK │ │ id                    PK    │ │ id                PK │
│ org_id FK│ │ org_id                FK    │ │ org_id            FK │
│linear_uid│ │ github_repo_id  BIGINT UNIQ │ │ source  (linear/     │
│github_un │ │ github_owner    VARCHAR      │ │          github)     │
│slack_uid │ │ github_repo_name VARCHAR     │ │ event_type VARCHAR   │
│ email    │ │ full_name       VARCHAR      │ │ delivery_id VARCHAR  │
│ role     │ │ default_branch  VARCHAR      │ │ payload       JSONB  │
│created_at│ │ is_active       BOOLEAN      │ │ signature_ok  BOOL   │
└──────────┘ │ created_at      TIMESTAMPTZ  │ │ processed     BOOL   │
             └──────────────┬──────────────┘ │ run_id        FK     │
                            │ 1              │ received_at          │
                            │               └──────────────────────┘
                            │ N
                            ▼
┌───────────────────────────────────────────────────────────────────┐
│                          agent_runs                                │
├───────────────────────────────────────────────────────────────────┤
│ id                    UUID                                 PK      │
│ org_id                UUID                                FK      │
│ repo_id               UUID                                FK      │
│ triggered_by_user_id  UUID                                FK      │
│ linear_issue_id       VARCHAR(100)                                │ ← UUID from Linear
│ linear_issue_identifier VARCHAR(20)                               │ ← e.g. "ENG-42"
│ linear_issue_title    VARCHAR(500)                                │
│ linear_issue_url      TEXT                                        │
│ status                ENUM(queued,running,success,failed,         │
│                            cancelled,timed_out)                   │
│ branch_name           VARCHAR(255)                                │
│ pr_number             INT                                         │
│ pr_url                TEXT                                        │
│ pr_sha                VARCHAR(40)                                  │ ← head commit SHA
│ claude_session_id     VARCHAR(255)                                │
│ container_id          VARCHAR(255)                                │
│ started_at            TIMESTAMPTZ                                 │
│ completed_at          TIMESTAMPTZ                                 │
│ timeout_at            TIMESTAMPTZ                                 │ ← started_at + 30m
│ error_message         TEXT                                        │
│ claude_summary        TEXT                                        │ ← what Claude did
│ created_at            TIMESTAMPTZ                                 │
└──────────────────────────────┬────────────────────────────────────┘
                               │ 1
                   ┌───────────┴───────────────┐
                   │ N                         │ N
                   ▼                           ▼
┌──────────────────────────┐   ┌───────────────────────────────────┐
│       agent_logs          │   │        slack_notifications         │
├──────────────────────────┤   ├───────────────────────────────────┤
│ id          UUID    PK   │   │ id           UUID           PK    │
│ run_id      UUID    FK   │   │ run_id       UUID           FK    │
│ level       ENUM(info,   │   │ channel_id   VARCHAR(100)         │
│             warn,error,  │   │ message_ts   VARCHAR(50)          │ ← used for chat.update
│             debug)       │   │ type         ENUM(started,        │
│ message     TEXT         │   │               completed,failed,   │
│ metadata    JSONB        │   │               pr_opened)          │
│ created_at  TIMESTAMPTZ  │   │ sent_at      TIMESTAMPTZ          │
└──────────────────────────┘   └───────────────────────────────────┘
```

🔒 = AES-256 encrypted at rest

---

## Detailed Step-by-Step Flow

### Step 1 — Linear fires webhook

Linear POSTs to `POST https://api.bravey.dev/webhooks/linear`.

Your handler:
1. Read raw request body as bytes (do not parse JSON first — needed for HMAC verification)
2. Compute `HMAC-SHA256(raw_body, org_webhook_secret)` and compare to `Linear-Signature` header. Return `401` if mismatch.
3. Check `webhookTimestamp` is within 60,000ms of `Date.now()`. Return `400` if stale.
4. Parse JSON body.
5. Insert row into `webhook_events`: `{ org_id, source: "linear", event_type: data.type, delivery_id: Linear-Delivery header, payload: full_body, signature_ok: true, processed: false }`.
6. Check trigger condition: `action === "update"` AND `data.assigneeId === org.linear_bravey_user_id` AND `updatedFrom.assigneeId !== org.linear_bravey_user_id`.
7. If triggered: insert row into `agent_runs` with `status: "queued"`, enqueue job to the worker queue with `run_id`.
8. Update `webhook_events.run_id` and `processed: true`.
9. Return `200 OK` immediately. Total handler time must be < 5 seconds.

---

### Step 2 — Worker picks up the job

Worker reads `agent_runs` row, sets `status: "running"`, `started_at: now()`, `timeout_at: now() + 30min`.

---

### Step 3 — Fetch full Linear issue context

Worker calls Linear GraphQL API with the `issue(id: ...)` query shown above. Stores `linear_issue_title` and `linear_issue_url` on the run. Combines issue title, description, labels, comments, and parent issue into a context string for Claude's prompt.

---

### Step 4 — Post Slack "started" message

Call `chat.postMessage` with the run-started block kit message. Save the response `ts` and `channel` to `slack_notifications` with `type: "started"`.

---

### Step 5 — Generate GitHub installation token

Call `POST /app/installations/{installation_id}/access_tokens` using a JWT signed with the GitHub App's private key (RS256, 10-minute expiry). Store the token in memory for the duration of the run — don't persist it.

---

### Step 6 — Create GitHub branch

Call `GET /repos/{owner}/{repo}/git/ref/heads/{default_branch}` to get the HEAD SHA. Call `POST /repos/{owner}/{repo}/git/refs` to create `refs/heads/bravey/{ENG-42-slug}`. Store `branch_name` on `agent_runs`.

---

### Step 7 — Provision ephemeral container

Start a container (Docker or Firecracker microVM) with:
- The cloned repo (authenticated via `x-access-token:{token}@github.com`)
- Claude Code CLI installed
- No internet access except `github.com` and `api.anthropic.com`
- Env vars: `ANTHROPIC_API_KEY`, `GITHUB_TOKEN`, `LINEAR_ISSUE_ID`
- 30-minute wall-clock timeout

Store the container ID in `agent_runs.container_id`. Stream stdout/stderr to `agent_logs` in real time.

---

### Step 8 — Claude Code runs

Claude Code is invoked with a system prompt:

```
You are Bravey, an AI coding agent. You have been assigned the following Linear issue:

Title: Fix null pointer in auth middleware
Identifier: ENG-42
Description: When a user logs in with an expired token...
Priority: High
Labels: bug, auth
Comments:
  - Jane (2h ago): "This is happening in production, urgent"

Your job:
1. Read the codebase to understand the relevant code
2. Implement a fix that addresses the issue description
3. Write or update tests as needed
4. Commit your changes with descriptive commit messages
5. When done, output a brief summary of what you changed (3-5 bullet points)

Do not open a PR — that will be handled for you.
Branch `bravey/ENG-42-fix-null-pointer-in-auth-middleware` is already checked out.
```

Claude Code has full tool access: bash execution, file read/write, grep/search. It commits incrementally. When done, it outputs a summary of changes. Bravey captures this as `agent_runs.claude_summary`.

---

### Step 9 — Open GitHub Pull Request

After Claude Code exits successfully:

Call `POST /repos/{owner}/{repo}/pulls` with the branch, title (`{identifier}: {title}`), and body (issue description + Claude summary + Linear link + Bravey attribution). Store `pr_number` and `pr_url` on `agent_runs`.

---

### Step 10 — Update Linear issue

Call `issueUpdate` mutation to set `stateId` to `org.linear_in_review_state_id`. Then call `commentCreate` mutation with the PR link and Claude's summary.

---

### Step 11 — Update Slack message

Call `chat.update` using the saved `message_ts` and `channel_id` from `slack_notifications`. Post the PR URL and run duration. Insert a second row in `slack_notifications` with `type: "pr_opened"`.

---

### Step 12 — Cleanup

Set `agent_runs.status = "success"`, `completed_at = now()`. Destroy the container. Revoke the installation token (optional — they expire in 1h anyway).

On any failure at steps 6–11: set `status = "failed"`, `error_message`, update Slack with the failure message, and destroy the container.

---

## Onboarding Flow (what happens when a new customer signs up)

1. Customer clicks "Connect Linear" → OAuth flow → Bravey gets `linear_access_token` with scopes `read write`
2. Bravey fetches customer's Linear org ID via `{ viewer { organization { id name } } }`
3. Bravey creates a bot user in their Linear workspace (via invite or pre-existing Bravey user they add manually)
4. Bravey fetches their team's workflow states to find the "In Review" state ID and stores it
5. Bravey registers the Linear webhook (resourceTypes: `["Issue"]`) and stores the secret
6. Customer clicks "Connect GitHub" → GitHub App installation flow → Bravey stores `installation_id`
7. Customer clicks "Connect Slack" → Slack OAuth → Bravey stores `bot_token` and `team_id`
8. Customer selects which Slack channel to post to → stored as `slack_default_channel_id`
9. Customer maps Linear team to GitHub repo (stored as a repo record)

---

## Database Indexes

```sql
-- Fast lookup when webhook arrives
CREATE INDEX idx_organizations_linear_org_id ON organizations(linear_org_id);

-- Fast lookup of runs by status for the worker queue
CREATE INDEX idx_agent_runs_status ON agent_runs(status) WHERE status IN ('queued', 'running');

-- Fast lookup of runs by org
CREATE INDEX idx_agent_runs_org_id ON agent_runs(org_id);

-- Fast lookup of unprocessed webhook events for replay
CREATE INDEX idx_webhook_events_unprocessed ON webhook_events(org_id, processed) WHERE processed = false;

-- Fast log streaming by run
CREATE INDEX idx_agent_logs_run_id_created ON agent_logs(run_id, created_at);

-- Slack notification lookup for updates
CREATE INDEX idx_slack_notifications_run_id ON slack_notifications(run_id);
```

---

## Environment Variables Required

```
# Linear
LINEAR_CLIENT_ID=
LINEAR_CLIENT_SECRET=

# GitHub App
GITHUB_APP_ID=
GITHUB_APP_PRIVATE_KEY=          # PEM file contents
GITHUB_WEBHOOK_SECRET=           # for verifying GitHub webhook events

# Slack
SLACK_CLIENT_ID=
SLACK_CLIENT_SECRET=

# Anthropic
ANTHROPIC_API_KEY=

# Encryption
ENCRYPTION_KEY=                  # AES-256 key for encrypting stored tokens

# DB
DATABASE_URL=

# Queue
QUEUE_URL=                       # e.g. Redis URL for BullMQ or SQS endpoint
```
