# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Bravey Engineer Backend — a serverless AI coding assistant that integrates with Linear, Jira, and GitHub. When an issue is assigned to the Bravey bot, a webhook triggers an AWS Lambda worker that uses Claude to implement the task and open a PR.

## Commands

```bash
# Local dev server
uvicorn src.api.app:app --reload

# Database migrations
alembic upgrade head                    # apply all
alembic revision --autogenerate -m "description"  # create new

# Tests
pytest                                  # all tests
pytest tests/test_webhook_handlers.py   # single file
pytest -k "test_name"                   # single test

# Deploy (happens automatically on push to main via GitHub Actions)
sam build && sam deploy
```

## Architecture

**Two Lambda functions:**
- **API** (`src/api/app.py`): FastAPI + Mangum, 29s timeout — handles OAuth flows, webhooks, REST endpoints
- **Worker** (`src/worker/handler.py`): SQS consumer, 15min timeout — runs the Claude agent pipeline

**Core flow:** Webhook (Linear/Jira/GitHub) → create `AgentRun` → enqueue to SQS → worker runs `orchestrator.run_pipeline()` → Claude implements task → PR opened

### Key directories

- `src/api/routes/` — FastAPI routers (auth, integrations, webhooks, billing, organizations)
- `src/services/` — External API clients (linear, jira, github, slack, billing, agent, sqs)
- `src/models/` — SQLAlchemy ORM models
- `src/schemas/` — Pydantic request/response schemas
- `src/worker/orchestrator.py` — Main pipeline: fetch issue → run Claude → open PR → notify

### Multi-tenancy

`Organization` is the root tenant. All resources (repos, runs, users, subscriptions) are scoped to an org via `org_id`. OAuth tokens for all integrations are stored encrypted (AES-256-GCM via `EncryptedString` column type) on the org record.

### Async vs Sync

- API uses `asyncpg` (async SQLAlchemy sessions) — all route handlers are `async def`
- Worker uses `psycopg2` (sync sessions) — orchestrator and service calls are synchronous
- `config.py` derives sync DB URL from async URL automatically via `sync_database_url` property

### Webhook trigger logic

- **Linear**: Fires on `assigneeId` or `delegateId` change to the Bravey bot user. Delegates are Linear's agent concept.
- **Jira**: Fires on changelog `assignee` field change to the Bravey account.
- **GitHub PR comments**: Fires on `issue_comment` or `pull_request_review_comment` on a Bravey-opened PR, creating a follow-up `AgentRun` with `parent_run_id`.

All webhooks verify signatures (HMAC-SHA256 for Linear/GitHub, JWT for Jira Connect).

### Linear token management

Linear OAuth tokens expire. Both admin and bot tokens auto-refresh when within a 5-min expiry buffer. Two helper functions exist: `ensure_valid_token` (sync, for worker) and `async_ensure_valid_token` (async, for API routes).

### Billing

Plans table defines `agent_runs_limit` per month (-1 = unlimited). Usage is checked by counting `AgentRun` rows in the current period. When limits are hit, `notify_usage_limit_reached()` posts a comment on the issue, sends a Slack message, and removes Bravey from the issue.

## Testing

Tests use `pytest-asyncio` (auto mode), `pytest-httpx` for HTTP mocking, and `moto` for AWS SQS mocking. `conftest.py` sets up env var defaults and prevents `.env` from loading during tests.
