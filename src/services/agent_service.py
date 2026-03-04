"""Agent service — calls Claude API with GitHub-based tools."""

import base64
import json
import logging
import uuid

import httpx
from anthropic import Anthropic

from src.config import settings

logger = logging.getLogger(__name__)

GITHUB_API_URL = "https://api.github.com"
GITHUB_HEADERS_BASE = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

MAX_TOOL_TURNS = 50
MODEL = "claude-sonnet-4-20250514"
MODEL_FAST = "claude-haiku-4-5-20251001"



class AgentResult:
    def __init__(
        self,
        success: bool,
        claude_session_id: str | None = None,
        summary: str | None = None,
        error: str | None = None,
    ):
        self.success = success
        self.claude_session_id = claude_session_id
        self.summary = summary
        self.error = error


# ---------------------------------------------------------------------------
# GitHub helper functions (used as tool implementations)
# ---------------------------------------------------------------------------

def _gh_headers(token: str) -> dict:
    return {**GITHUB_HEADERS_BASE, "Authorization": f"Bearer {token}"}


def _gh_read_file(token: str, owner: str, repo: str, path: str, ref: str) -> str:
    """Read a single file's content from the repo."""
    resp = httpx.get(
        f"{GITHUB_API_URL}/repos/{owner}/{repo}/contents/{path}",
        params={"ref": ref},
        headers=_gh_headers(token),
        timeout=15,
    )
    if resp.status_code == 404:
        return f"Error: File not found: {path}"
    resp.raise_for_status()
    data = resp.json()
    if data.get("type") != "file":
        return f"Error: {path} is a {data.get('type')}, not a file"
    content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
    return content


def _gh_list_files(token: str, owner: str, repo: str, path: str, ref: str) -> str:
    """List files/directories at a given path."""
    resp = httpx.get(
        f"{GITHUB_API_URL}/repos/{owner}/{repo}/contents/{path}",
        params={"ref": ref},
        headers=_gh_headers(token),
        timeout=15,
    )
    if resp.status_code == 404:
        return f"Error: Path not found: {path}"
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict):
        return f"{data['name']} ({data['type']})"
    entries = []
    for item in data:
        suffix = "/" if item["type"] == "dir" else ""
        entries.append(f"{item['name']}{suffix}")
    return "\n".join(sorted(entries))


def _gh_search_files(token: str, owner: str, repo: str, query: str) -> str:
    """Search for code in the repo using GitHub code search."""
    resp = httpx.get(
        f"{GITHUB_API_URL}/search/code",
        params={"q": f"{query} repo:{owner}/{repo}"},
        headers=_gh_headers(token),
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    items = data.get("items", [])
    if not items:
        return "No results found."
    results = []
    for item in items[:20]:
        results.append(item["path"])
    return "\n".join(results)


def _gh_create_or_update_file(
    token: str, owner: str, repo: str, path: str, content: str, branch: str,
    message: str,
) -> str:
    """Create or update a file on the branch via the GitHub Contents API."""
    # Check if file exists to get its SHA (needed for updates)
    existing_sha = None
    check_resp = httpx.get(
        f"{GITHUB_API_URL}/repos/{owner}/{repo}/contents/{path}",
        params={"ref": branch},
        headers=_gh_headers(token),
        timeout=15,
    )
    if check_resp.status_code == 200:
        existing_sha = check_resp.json().get("sha")

    body = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if existing_sha:
        body["sha"] = existing_sha

    resp = httpx.put(
        f"{GITHUB_API_URL}/repos/{owner}/{repo}/contents/{path}",
        json=body,
        headers=_gh_headers(token),
        timeout=15,
    )
    resp.raise_for_status()
    commit_sha = resp.json().get("commit", {}).get("sha", "unknown")
    action = "Updated" if existing_sha else "Created"
    return f"{action} {path} (commit: {commit_sha[:8]})"


def _gh_delete_file(
    token: str, owner: str, repo: str, path: str, branch: str, message: str,
) -> str:
    """Delete a file on the branch via the GitHub Contents API."""
    # Get the file SHA
    check_resp = httpx.get(
        f"{GITHUB_API_URL}/repos/{owner}/{repo}/contents/{path}",
        params={"ref": branch},
        headers=_gh_headers(token),
        timeout=15,
    )
    if check_resp.status_code == 404:
        return f"Error: File not found: {path}"
    check_resp.raise_for_status()
    file_sha = check_resp.json()["sha"]

    resp = httpx.delete(
        f"{GITHUB_API_URL}/repos/{owner}/{repo}/contents/{path}",
        json={
            "message": message,
            "sha": file_sha,
            "branch": branch,
        },
        headers=_gh_headers(token),
        timeout=15,
    )
    resp.raise_for_status()
    return f"Deleted {path}"


# ---------------------------------------------------------------------------
# Tool definitions for the Claude API
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "read_file",
        "description": (
            "Read the contents of a file from the repository. "
            "Returns the full file content as text."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to repo root (e.g. 'src/main.py')",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "list_files",
        "description": (
            "List files and directories at a given path in the repository. "
            "Use empty string or '.' for the root directory."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path relative to repo root",
                    "default": "",
                },
            },
            "required": [],
        },
    },
    {
        "name": "search_files",
        "description": (
            "Search for code in the repository using GitHub code search. "
            "Returns a list of file paths matching the query."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (code, function names, etc.)",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "write_file",
        "description": (
            "Create or update a file in the repository. Each call creates a commit. "
            "Provide the COMPLETE file content — this replaces the entire file."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to repo root",
                },
                "content": {
                    "type": "string",
                    "description": "The complete file content to write",
                },
                "commit_message": {
                    "type": "string",
                    "description": "Short commit message describing the change",
                },
            },
            "required": ["path", "content", "commit_message"],
        },
    },
    {
        "name": "delete_file",
        "description": "Delete a file from the repository. Creates a commit.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to repo root",
                },
                "commit_message": {
                    "type": "string",
                    "description": "Short commit message describing the deletion",
                },
            },
            "required": ["path", "commit_message"],
        },
    },
]

READ_ONLY_TOOLS = TOOLS[:3]  # read_file, list_files, search_files


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------

def _execute_tool(
    tool_name: str,
    tool_input: dict,
    gh_token: str,
    owner: str,
    repo_name: str,
    branch_name: str,
) -> str:
    """Execute a tool call and return the result as a string."""
    try:
        if tool_name == "read_file":
            return _gh_read_file(
                gh_token, owner, repo_name, tool_input["path"], branch_name
            )
        elif tool_name == "list_files":
            return _gh_list_files(
                gh_token, owner, repo_name, tool_input.get("path", ""), branch_name
            )
        elif tool_name == "search_files":
            return _gh_search_files(
                gh_token, owner, repo_name, tool_input["query"]
            )
        elif tool_name == "write_file":
            return _gh_create_or_update_file(
                gh_token, owner, repo_name,
                tool_input["path"], tool_input["content"], branch_name,
                tool_input["commit_message"],
            )
        elif tool_name == "delete_file":
            return _gh_delete_file(
                gh_token, owner, repo_name,
                tool_input["path"], branch_name,
                tool_input["commit_message"],
            )
        else:
            return f"Error: Unknown tool '{tool_name}'"
    except httpx.HTTPStatusError as e:
        return f"Error ({e.response.status_code}): {e.response.text[:500]}"
    except Exception as e:
        return f"Error: {e}"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def provision_and_run(
    gh_token: str,
    owner: str,
    repo_name: str,
    branch_name: str,
    base_sha: str,
    issue_context: str,
    max_turns: int = MAX_TOOL_TURNS,
) -> AgentResult:
    """Run Claude agent via Anthropic API with GitHub-based tools."""
    session_id = uuid.uuid4().hex[:12]

    system_prompt = (
        "You are Bravey, an AI coding agent. You have access to a GitHub repository "
        f"({owner}/{repo_name}) on branch `{branch_name}`.\n\n"
        "You can read files, list directories, search code, and write/delete files. "
        "Each write_file or delete_file call creates a git commit automatically.\n\n"
        "Your workflow:\n"
        "1. Start by listing the repo root to understand the project structure\n"
        "2. Read relevant files to understand the codebase\n"
        "3. Implement the changes needed to address the issue\n"
        "4. Write tests if appropriate\n"
        "5. When finished, provide a brief summary of what you changed (3-5 bullet points)\n\n"
        "IMPORTANT:\n"
        "- Always read a file before modifying it\n"
        "- When writing a file, provide the COMPLETE file content\n"
        "- Use descriptive commit messages\n"
        "- Do NOT open a PR — that will be handled for you\n"
    )

    user_message = (
        f"You have been assigned the following Linear issue:\n\n"
        f"{issue_context}\n\n"
        f"Please implement the changes needed to address this issue."
    )

    client = Anthropic(api_key=settings.anthropic_api_key)

    messages = [{"role": "user", "content": user_message}]

    try:
        for turn in range(max_turns):
            logger.info(f"[BRAVEY] Agent turn {turn + 1}/{max_turns}")

            response = client.messages.create(
                model=MODEL,
                max_tokens=16384,
                system=system_prompt,
                tools=TOOLS,
                messages=messages,
            )

            # Check if we're done (no tool use)
            if response.stop_reason == "end_turn":
                # Extract text summary from the final response
                summary = ""
                for block in response.content:
                    if block.type == "text":
                        summary += block.text
                return AgentResult(
                    success=True,
                    claude_session_id=f"session-{session_id}",
                    summary=summary or "Agent completed but produced no summary.",
                )

            # Process tool calls
            if response.stop_reason == "tool_use":
                # Build assistant message with all content blocks
                assistant_content = []
                tool_results = []

                for block in response.content:
                    if block.type == "text":
                        assistant_content.append({
                            "type": "text",
                            "text": block.text,
                        })
                    elif block.type == "tool_use":
                        assistant_content.append({
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        })

                        logger.info(
                            f"[BRAVEY] Tool call: {block.name}("
                            f"{json.dumps(block.input)[:200]})"
                        )

                        result_text = _execute_tool(
                            block.name, block.input,
                            gh_token, owner, repo_name, branch_name,
                        )

                        # Truncate very large results
                        if len(result_text) > 50000:
                            result_text = result_text[:50000] + "\n... (truncated)"

                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result_text,
                        })

                messages.append({"role": "assistant", "content": assistant_content})
                messages.append({"role": "user", "content": tool_results})
            else:
                # Unexpected stop reason
                summary = ""
                for block in response.content:
                    if block.type == "text":
                        summary += block.text
                return AgentResult(
                    success=True,
                    claude_session_id=f"session-{session_id}",
                    summary=summary or "Agent stopped unexpectedly.",
                )

        # Exhausted max turns
        return AgentResult(
            success=False,
            claude_session_id=f"session-{session_id}",
            error=f"Agent exhausted maximum {max_turns} tool turns without completing.",
            summary="Agent ran out of turns before completing the task.",
        )

    except Exception as e:
        logger.exception(f"Agent run failed: {e}")
        return AgentResult(
            success=False,
            claude_session_id=f"session-{session_id}",
            error=str(e),
        )


# ---------------------------------------------------------------------------
# Slack intent classification
# ---------------------------------------------------------------------------

def classify_slack_intent(message_text: str, thread_context: str = "") -> str:
    """Classify a Slack message as 'conversation', 'question', or 'action'."""
    client = Anthropic(api_key=settings.anthropic_api_key)

    prompt = message_text
    if thread_context:
        prompt = f"Thread context:\n{thread_context}\n\nLatest message: {message_text}"

    response = client.messages.create(
        model=MODEL_FAST,
        max_tokens=16,
        system=(
            "You are a classifier for a coding assistant bot in Slack. "
            "Given a user message (and optional thread context), respond with "
            "exactly one word:\n"
            "- 'conversation' — greetings, casual chat, follow-up questions, "
            "clarifications, thank-yous, or anything that doesn't need codebase access\n"
            "- 'question' — the user is asking a specific question about the codebase "
            "that requires reading code to answer\n"
            "- 'action' — the user is explicitly requesting a code change, new feature, "
            "bug fix, or anything that requires writing/modifying code\n\n"
            "Respond with only that single word, nothing else."
        ),
        messages=[{"role": "user", "content": prompt}],
    )
    result = response.content[0].text.strip().lower()
    if result not in ("conversation", "question", "action"):
        return "conversation"
    return result


def generate_conversation_reply(
    message_text: str, thread_context: str = ""
) -> str:
    """Generate a conversational reply for casual Slack messages."""
    client = Anthropic(api_key=settings.anthropic_api_key)

    prompt = message_text
    if thread_context:
        prompt = f"Thread context:\n{thread_context}\n\nLatest message: {message_text}"

    response = client.messages.create(
        model=MODEL_FAST,
        max_tokens=512,
        system=(
            "You are Bravey, a friendly AI coding assistant in Slack. "
            "Respond naturally and concisely to the user's message. "
            "If they greet you, greet them back and briefly mention what you can do "
            "(answer questions about their codebase, or make code changes and open PRs). "
            "If they ask a follow-up or clarification, respond helpfully. "
            "Keep replies short (1-3 sentences). Be warm but professional."
        ),
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


# ---------------------------------------------------------------------------
# Read-only Q&A agent
# ---------------------------------------------------------------------------

def answer_question(
    gh_token: str,
    owner: str,
    repo_name: str,
    branch_name: str,
    question: str,
    max_turns: int = 20,
) -> AgentResult:
    """Run a read-only Claude agent to answer a question about the codebase."""
    session_id = uuid.uuid4().hex[:12]

    system_prompt = (
        "You are Bravey, an AI coding assistant. You have read-only access to a "
        f"GitHub repository ({owner}/{repo_name}) on branch `{branch_name}`.\n\n"
        "You can read files, list directories, and search code. "
        "Use these tools to explore the codebase and answer the user's question.\n\n"
        "Provide a clear, concise answer. Include relevant code snippets or file "
        "paths when helpful. Keep your answer under 2500 characters."
    )

    client = Anthropic(api_key=settings.anthropic_api_key)
    messages = [{"role": "user", "content": question}]

    try:
        for turn in range(max_turns):
            logger.info(f"[BRAVEY-QA] Agent turn {turn + 1}/{max_turns}")

            response = client.messages.create(
                model=MODEL,
                max_tokens=4096,
                system=system_prompt,
                tools=READ_ONLY_TOOLS,
                messages=messages,
            )

            if response.stop_reason == "end_turn":
                summary = ""
                for block in response.content:
                    if block.type == "text":
                        summary += block.text
                return AgentResult(
                    success=True,
                    claude_session_id=f"qa-{session_id}",
                    summary=summary or "I couldn't find an answer.",
                )

            if response.stop_reason == "tool_use":
                assistant_content = []
                tool_results = []

                for block in response.content:
                    if block.type == "text":
                        assistant_content.append({
                            "type": "text",
                            "text": block.text,
                        })
                    elif block.type == "tool_use":
                        assistant_content.append({
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        })

                        result_text = _execute_tool(
                            block.name, block.input,
                            gh_token, owner, repo_name, branch_name,
                        )

                        if len(result_text) > 50000:
                            result_text = result_text[:50000] + "\n... (truncated)"

                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result_text,
                        })

                messages.append({"role": "assistant", "content": assistant_content})
                messages.append({"role": "user", "content": tool_results})
            else:
                summary = ""
                for block in response.content:
                    if block.type == "text":
                        summary += block.text
                return AgentResult(
                    success=True,
                    claude_session_id=f"qa-{session_id}",
                    summary=summary or "Agent stopped unexpectedly.",
                )

        return AgentResult(
            success=False,
            claude_session_id=f"qa-{session_id}",
            error=f"Q&A agent exhausted maximum {max_turns} turns.",
            summary="I ran out of turns while searching for an answer.",
        )

    except Exception as e:
        logger.exception(f"Q&A agent failed: {e}")
        return AgentResult(
            success=False,
            claude_session_id=f"qa-{session_id}",
            error=str(e),
        )
