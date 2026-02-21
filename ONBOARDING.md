# Bravey API Onboarding Guide

Technical step-by-step guide for integrating with the Bravey backend API. All endpoints are relative to the API base URL. Authenticated endpoints require a `Bearer` JWT token in the `Authorization` header.

```
Authorization: Bearer <jwt_token>
```

---

## Prerequisites

- GitHub account (for OAuth login)
- A GitHub organization with at least one repository
- A Linear workspace with admin access
- (Optional) A Slack workspace

---

Base URL FOR ALL ENDPOINTS: https://d2eefuzpqj.execute-api.us-east-1.amazonaws.com

## Step 1: Authenticate via GitHub OAuth

### 1.1 Get the GitHub authorization URL

```
GET /auth/github/login
```

**Response:**
```json
{
  "authorization_url": "https://github.com/login/oauth/authorize?client_id=...&scope=read:user+user:email&state=...",
  "state": "uuid"
}
```

### 1.2 Redirect the user to `authorization_url`

The user authorizes Bravey on GitHub. GitHub redirects back to:

```
GET /auth/github/callback?code=<github_code>&state=<state>
```

This endpoint exchanges the code for a GitHub token, creates or updates the user in the database, issues a JWT, and redirects to:

```
{FRONTEND_URL}/auth/callback?token=<jwt_token>
```

Store this JWT — all subsequent authenticated requests require it.

### 1.3 Verify authentication

```
GET /auth/me
Authorization: Bearer <jwt_token>
```

**Response:**
```json
{
  "id": "uuid",
  "github_login": "username",
  "avatar_url": "https://...",
  "name": "User Name",
  "email": "user@example.com",
  "role": "member"
}
```

---

## Step 2: Create an Organization

```
POST /organizations
Authorization: Bearer <jwt_token>
Content-Type: application/json
```

**Request body:**
```json
{
  "name": "My Company",
  "slug": "my-company"
}
```

**Response (201):**
```json
{
  "id": "uuid",
  "name": "My Company",
  "slug": "my-company",
  "github_installation_id": null,
  "linear_org_id": null,
  "created_at": "2025-01-01T00:00:00Z"
}
```

The creating user is automatically set as `admin` of the organization.

### Invite team members (optional)

```
POST /organizations/me/invites
Authorization: Bearer <jwt_token>
```

**Request body:**
```json
{
  "email": "teammate@example.com",
  "role": "member"
}
```

**Response (201):**
```json
{
  "id": "uuid",
  "email": "teammate@example.com",
  "role": "member",
  "token": "invite_token_string",
  "expires_at": "2025-01-08T00:00:00Z",
  "accepted_at": null,
  "created_at": "2025-01-01T00:00:00Z"
}
```

Invited users accept via:
```
POST /organizations/invites/{token}/accept
Authorization: Bearer <jwt_token>
```

---

## Step 3: Connect Linear (Admin OAuth)

This connects your Linear workspace so Bravey can read issues, post comments, and update issue states. It also creates a webhook automatically.

### 3.1 Get the Linear authorization URL

```
GET /integrations/linear/connect
Authorization: Bearer <jwt_token>
```

**Response:**
```json
{
  "authorization_url": "https://linear.app/oauth/authorize?client_id=...&scope=read,write,issues:create,comments:create,admin&state=...",
  "state": "<org_id>:<nonce>"
}
```

**OAuth scopes requested:** `read`, `write`, `issues:create`, `comments:create`, `admin`

### 3.2 Redirect the user to `authorization_url`

After the user authorizes, Linear redirects to:

```
GET /integrations/linear/callback?code=<code>&state=<org_id>:<nonce>
```

This endpoint:
1. Exchanges the OAuth code for a Linear access token.
2. Fetches the Linear organization ID via GraphQL.
3. Stores the access token (AES-256 encrypted at rest) and org ID.
4. Deletes any existing Bravey webhooks for the same URL.
5. Creates a new Linear webhook listening for `Issue` resource events on all public teams.
6. Stores the webhook ID and webhook secret (used for HMAC-SHA256 signature verification).

**Response:**
```json
{
  "status": "connected",
  "linear_org_id": "linear-org-uuid",
  "webhook_configured": true,
  "bot_installed": false,
  "next_step": "Install the bot: GET /integrations/linear/install-bot"
}
```

### 3.3 Check Linear connection status

```
GET /integrations/linear/status
Authorization: Bearer <jwt_token>
```

**Response:**
```json
{
  "connected": true,
  "linear_org_id": "linear-org-uuid",
  "webhook_configured": true,
  "bot_user_configured": false
}
```

---

## Step 4: Install the Bravey Bot in Linear

This creates a bot user identity in your Linear workspace. Issues must be assigned to (or delegated to) this bot user to trigger Bravey.

### 4.1 Get the bot install authorization URL

```
GET /integrations/linear/install-bot
Authorization: Bearer <jwt_token>
```

**Response:**
```json
{
  "authorization_url": "https://linear.app/oauth/authorize?client_id=...&actor=app&scope=read,write,issues:create,comments:create,app:assignable&state=bot:<org_id>:<nonce>",
  "state": "bot:<org_id>:<nonce>"
}
```

Note the `actor=app` parameter — this tells Linear to create a bot identity rather than authenticating as a human user.

**OAuth scopes requested:** `read`, `write`, `issues:create`, `comments:create`, `app:assignable`

### 4.2 Redirect the user to `authorization_url`

After authorization, Linear redirects to the same callback:

```
GET /integrations/linear/callback?code=<code>&state=bot:<org_id>:<nonce>
```

The callback detects the `bot:` prefix in state, exchanges the code for a bot token, queries `{ viewer { id name displayName } }` to get the bot's user ID, and stores it on the organization as `linear_bravey_user_id`.

**Response:**
```json
{
  "status": "bot_installed",
  "bot_user_id": "linear-bot-user-uuid",
  "bot_name": "Bravey"
}
```

### 4.3 Alternative: Manually set bot user ID

If the OAuth bot flow doesn't work, you can manually set the bot user ID.

First, list all Linear workspace members to find the bot:

```
GET /integrations/linear/members
Authorization: Bearer <jwt_token>
```

**Response:**
```json
{
  "members": [
    {
      "id": "user-uuid-1",
      "name": "Alice",
      "displayName": "Alice",
      "email": "alice@example.com",
      "active": true,
      "admin": true
    },
    {
      "id": "bot-user-uuid",
      "name": "Bravey",
      "displayName": "Bravey",
      "email": null,
      "active": true,
      "admin": false
    }
  ]
}
```

Then set the bot user ID:

```
POST /integrations/linear/bot-user
Authorization: Bearer <jwt_token>
Content-Type: application/json
```

**Request body:**
```json
{
  "linear_user_id": "bot-user-uuid"
}
```

**Response:**
```json
{
  "status": "ok",
  "linear_bravey_user_id": "bot-user-uuid"
}
```

---

## Step 5: Install the GitHub App

This gives Bravey permission to create branches, push commits, and open PRs in your repositories.

### 5.1 Get the GitHub App install URL

```
GET /integrations/github/install
Authorization: Bearer <jwt_token>
```

**Response:**
```json
{
  "install_url": "https://github.com/apps/<app-name>/installations/new"
}
```

### 5.2 Redirect the user to `install_url`

The user selects the GitHub org/account and the repositories to grant access to, then clicks **Install**.

After installation, GitHub fires an `installation.created` webhook to:

```
POST /webhooks/github/app
```

This webhook handler automatically matches the GitHub user to a Bravey user (by `github_login`) and stores the `installation_id` on the organization.

### 5.3 Alternative: Manually claim the installation

If automatic linking fails (e.g., the GitHub login doesn't match), manually claim it:

```
POST /integrations/github/claim?installation_id=12345678
Authorization: Bearer <jwt_token>
```

**Response:**
```json
{
  "status": "claimed",
  "installation_id": 12345678
}
```

### 5.4 Check GitHub connection status

```
GET /integrations/github/status
Authorization: Bearer <jwt_token>
```

**Response:**
```json
{
  "connected": true,
  "installation_id": 12345678
}
```

---

## Step 6: Configure the "In Review" Linear State

After Bravey opens a PR, it moves the Linear issue to an "In Review" workflow state. You need to set the `linear_in_review_state_id` on the organization.

To find the state ID, query the Linear GraphQL API directly (using the access token stored during Step 3):

```graphql
query {
  workflowStates {
    nodes {
      id
      name
      type
    }
  }
}
```

Look for a state like `"In Review"` with type `"started"` or `"unstarted"`, and note the `id`.

Set it on your organization (this may need to be done via a direct DB update or an admin endpoint depending on your setup).

---

## Step 7: Connect Slack (Optional)

Slack integration enables real-time notifications when Bravey picks up a ticket, opens a PR, or fails.

The Slack OAuth flow follows a similar pattern — the organization stores `slack_bot_token` and `slack_default_channel_id`. Once connected, Bravey posts:

- **Started message** — when the pipeline begins processing a ticket.
- **Completed message** — updated in-place with the PR link, branch name, and duration.
- **Failed message** — updated in-place if the pipeline errors out.

---

## Step 8: Verify All Integrations

```
GET /integrations/linear/status
GET /integrations/github/status
Authorization: Bearer <jwt_token>
```

Confirm:

| Integration | Field | Expected |
|---|---|---|
| Linear | `connected` | `true` |
| Linear | `webhook_configured` | `true` |
| Linear | `bot_user_configured` | `true` |
| GitHub | `connected` | `true` |
| GitHub | `installation_id` | non-null integer |

---

## Step 9: Disconnect Integrations

### Disconnect Linear

```
POST /integrations/linear/disconnect
Authorization: Bearer <jwt_token>
```

Clears: `linear_access_token`, `linear_org_id`, `linear_webhook_id`, `linear_webhook_secret`, `linear_bravey_user_id`.

**Response:**
```json
{
  "status": "disconnected"
}
```

---

## How It Works (End-to-End Flow)

Once onboarding is complete:

1. User creates a Linear issue and assigns it to the Bravey bot user (or delegates via `delegateId`).
2. Linear fires an `Issue` webhook to `POST /webhooks/linear`.
3. Bravey verifies the HMAC-SHA256 signature and timestamp (must be within 60 seconds).
4. Bravey creates an `AgentRun` record with status `queued` and enqueues it to SQS.
5. The worker Lambda picks up the SQS message and runs the pipeline:
   - Fetches full issue context (title, description, comments, labels, parent) from Linear.
   - **Posts a comment on the Linear ticket: "Bravey has picked up this ticket".**
   - Posts a "started" message to Slack (if connected).
   - Generates a GitHub installation token (JWT → short-lived token).
   - Creates a branch: `bravey/<issue-id>-<slugified-title>`.
   - Runs the Claude AI agent which reads the codebase and implements the changes.
   - Opens a pull request with the Claude summary and a link back to the Linear issue.
   - Adds a `bravey-generated` label to the PR.
   - Moves the Linear issue to the "In Review" state.
   - Posts a comment on the Linear issue with the PR link, branch name, and changes summary.
   - Updates the Slack message with PR details and duration.
   - Marks the run as `success`.

---

## Webhook Endpoints Reference

### Linear Webhook

```
POST /webhooks/linear
```

- **Signature header:** `Linear-Signature` — HMAC-SHA256 of the raw body using the stored webhook secret.
- **Timestamp field:** `webhookTimestamp` in the payload (milliseconds). Must be within 60s of current time.
- **Triggers on:** Issue `create` or `update` events where the `assigneeId` or `delegateId` matches `linear_bravey_user_id`.

### GitHub App Webhook

```
POST /webhooks/github/app
```

- Handles `installation.created` events to auto-link GitHub App installations.
- Handles `pull_request.closed` (merged) events for tracking.

---

## Environment Variables

The backend requires these environment variables to be configured:

| Variable | Description |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string (async/asyncpg) |
| `ENCRYPTION_KEY` | 32-byte hex-encoded AES-256 key for token encryption |
| `JWT_SECRET_KEY` | Secret for signing JWT tokens |
| `GITHUB_OAUTH_CLIENT_ID` | GitHub OAuth app client ID (user login) |
| `GITHUB_OAUTH_CLIENT_SECRET` | GitHub OAuth app client secret |
| `LINEAR_CLIENT_ID` | Linear OAuth app client ID |
| `LINEAR_CLIENT_SECRET` | Linear OAuth app client secret |
| `LINEAR_REDIRECT_URI` | Linear OAuth redirect URI (e.g. `https://api.bravey.co/integrations/linear/callback`) |
| `GITHUB_APP_ID` | GitHub App ID |
| `GITHUB_APP_PRIVATE_KEY` | GitHub App private key (PEM or base64-encoded) |
| `GITHUB_WEBHOOK_SECRET` | GitHub App webhook secret |
| `SLACK_CLIENT_ID` | Slack OAuth app client ID |
| `SLACK_CLIENT_SECRET` | Slack OAuth app client secret |
| `ANTHROPIC_API_KEY` | Anthropic API key for Claude |
| `FRONTEND_URL` | Frontend URL for redirects (e.g. `https://app.bravey.co`) |
| `SQS_QUEUE_URL` | AWS SQS queue URL for async job processing |
| `AWS_REGION` | AWS region (default: `us-east-1`) |
