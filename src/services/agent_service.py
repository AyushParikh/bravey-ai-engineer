"""Agent service — clones the repo and runs Claude Code CLI."""

import os
import shutil
import subprocess
import tempfile
import uuid


class AgentResult:
    def __init__(
        self,
        success: bool,
        container_id: str,
        claude_session_id: str | None = None,
        summary: str | None = None,
        error: str | None = None,
    ):
        self.success = success
        self.container_id = container_id
        self.claude_session_id = claude_session_id
        self.summary = summary
        self.error = error


def provision_and_run(
    repo_clone_url: str,
    branch_name: str,
    issue_context: str,
    timeout_seconds: int = 1800,
) -> AgentResult:
    """Clone repo, run Claude Code CLI, and return results."""
    run_id = uuid.uuid4().hex[:12]
    work_dir = tempfile.mkdtemp(prefix=f"bravey-{run_id}-")

    try:
        print(f"[BRAVEY] Cloning repo to {work_dir}", flush=True)

        # Clone the repo
        clone_result = subprocess.run(
            ["git", "clone", "--depth", "1", "-b", branch_name, repo_clone_url, work_dir + "/repo"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if clone_result.returncode != 0:
            return AgentResult(
                success=False,
                container_id=f"local-{run_id}",
                error=f"Git clone failed: {clone_result.stderr}",
            )

        repo_dir = os.path.join(work_dir, "repo")

        # Build the prompt for Claude
        prompt = (
            f"You are Bravey, an AI coding agent. You have been assigned the following Linear issue:\n\n"
            f"{issue_context}\n\n"
            f"Your job:\n"
            f"1. Read the codebase to understand the relevant code\n"
            f"2. Implement a fix/feature that addresses the issue description\n"
            f"3. Write or update tests as needed\n"
            f"4. Commit your changes with descriptive commit messages\n"
            f"5. When done, output a brief summary of what you changed (3-5 bullet points)\n\n"
            f"Do not open a PR — that will be handled for you.\n"
            f"Branch `{branch_name}` is already checked out.\n"
            f"IMPORTANT: You MUST commit and push your changes before finishing."
        )

        print(f"[BRAVEY] Running Claude Code in {repo_dir}", flush=True)

        # Run Claude Code CLI (unset CLAUDECODE to avoid nested session detection)
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        claude_result = subprocess.run(
            ["claude", "-p", prompt, "--allowedTools", "Bash,Read,Write,Edit,Glob,Grep"],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            cwd=repo_dir,
            env=env,
        )

        print(f"[BRAVEY] Claude exit code: {claude_result.returncode}", flush=True)
        if claude_result.stdout:
            print(f"[BRAVEY] Claude stdout (first 500): {claude_result.stdout[:500]}", flush=True)
        if claude_result.stderr:
            print(f"[BRAVEY] Claude stderr (first 500): {claude_result.stderr[:500]}", flush=True)

        summary = claude_result.stdout.strip() if claude_result.stdout else None

        # Push any commits
        push_result = subprocess.run(
            ["git", "push", "origin", branch_name],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=repo_dir,
        )

        if push_result.returncode != 0:
            print(f"[BRAVEY] Push output: {push_result.stderr}", flush=True)

        if claude_result.returncode != 0:
            return AgentResult(
                success=False,
                container_id=f"local-{run_id}",
                error=f"Claude Code failed (exit {claude_result.returncode}): {claude_result.stderr[:500] if claude_result.stderr else 'No error output'}",
                summary=summary,
            )

        # Check if there are actual commits to push
        log_result = subprocess.run(
            ["git", "log", f"origin/{branch_name}..HEAD", "--oneline"],
            capture_output=True, text=True, timeout=10, cwd=repo_dir,
        )
        has_commits = bool(log_result.stdout.strip()) if log_result.returncode == 0 else False

        if not has_commits and push_result.returncode != 0:
            # Claude didn't make any commits — use stub commit as fallback
            print("[BRAVEY] No commits from Claude, will use stub commit", flush=True)
            return AgentResult(
                success=True,
                container_id=f"stub-{run_id}",
                claude_session_id=f"session-{run_id}",
                summary=summary or "Claude ran but made no changes.",
            )

        return AgentResult(
            success=True,
            container_id=f"local-{run_id}",
            claude_session_id=f"session-{run_id}",
            summary=summary,
        )

    except subprocess.TimeoutExpired:
        return AgentResult(
            success=False,
            container_id=f"local-{run_id}",
            error=f"Claude Code timed out after {timeout_seconds} seconds",
        )
    except Exception as e:
        return AgentResult(
            success=False,
            container_id=f"local-{run_id}",
            error=str(e),
        )
    finally:
        # Clean up work directory
        try:
            shutil.rmtree(work_dir, ignore_errors=True)
        except Exception:
            pass
