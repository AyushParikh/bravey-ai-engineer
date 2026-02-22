#!/usr/bin/env python3
"""
Interactive CLI to onboard a new organization.

Usage:
    python scripts/onboard.py --api-url https://your-api.com
"""

import argparse
import socket
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import httpx

# ANSI color helpers
BOLD = "\033[1m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"
DIM = "\033[2m"
RESET = "\033[0m"

POLL_INTERVAL = 2  # seconds
POLL_TIMEOUT = 300  # 5 minutes


def info(msg: str) -> None:
    print(f"{CYAN}=> {msg}{RESET}")


def success(msg: str) -> None:
    print(f"{GREEN}[OK] {msg}{RESET}")


def warn(msg: str) -> None:
    print(f"{YELLOW}[!] {msg}{RESET}")


def error(msg: str) -> None:
    print(f"{RED}[ERROR] {msg}{RESET}")


def header(msg: str) -> None:
    print(f"\n{BOLD}{'=' * 60}")
    print(f"  {msg}")
    print(f"{'=' * 60}{RESET}\n")


def prompt(msg: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{BOLD}{msg}{suffix}: {RESET}").strip()
    return value or default


def confirm(msg: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    value = input(f"{BOLD}{msg} ({hint}): {RESET}").strip().lower()
    if not value:
        return default
    return value in ("y", "yes")


class OnboardCLI:
    def __init__(self, api_url: str):
        self.api_url = api_url.rstrip("/")
        self.client = httpx.Client(timeout=30)
        self.token: str | None = None

    @property
    def headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def get(self, path: str, **kwargs) -> httpx.Response:
        return self.client.get(f"{self.api_url}{path}", headers=self.headers, **kwargs)

    def post(self, path: str, **kwargs) -> httpx.Response:
        return self.client.post(f"{self.api_url}{path}", headers=self.headers, **kwargs)

    def poll_status(self, path: str, check_fn, label: str = "Waiting") -> dict:
        """Poll an endpoint until check_fn(response_json) returns True."""
        start = time.time()
        spinner = ["|", "/", "-", "\\"]
        i = 0
        while time.time() - start < POLL_TIMEOUT:
            try:
                resp = self.get(path)
                if resp.status_code == 200:
                    data = resp.json()
                    if check_fn(data):
                        print()
                        return data
            except httpx.RequestError:
                pass
            frame = spinner[i % len(spinner)]
            print(f"\r{DIM}{frame} {label}...{RESET}", end="", flush=True)
            i += 1
            time.sleep(POLL_INTERVAL)
        print()
        error(f"Timed out after {POLL_TIMEOUT}s")
        sys.exit(1)

    # --- Steps ---

    @staticmethod
    def _find_free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def _wait_for_token_via_local_server(self, port: int) -> str:
        """Start a local HTTP server that captures the JWT token from the redirect."""
        captured: dict[str, str | None] = {"token": None}

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                qs = parse_qs(urlparse(self.path).query)
                token = qs.get("token", [None])[0]
                captured["token"] = token
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(
                    b"<html><body><h2>Login successful!</h2>"
                    b"<p>You can close this tab and return to the terminal.</p>"
                    b"</body></html>"
                )

            def log_message(self, format, *args):
                pass  # suppress request logs

        server = HTTPServer(("127.0.0.1", port), CallbackHandler)
        server.timeout = POLL_TIMEOUT

        # Handle one request (the redirect)
        while captured["token"] is None:
            server.handle_request()

        server.server_close()
        return captured["token"] or ""

    def step_github_login(self) -> None:
        header("Step 1: GitHub Login")

        port = self._find_free_port()
        cli_redirect = f"http://127.0.0.1:{port}/callback"

        resp = self.get("/auth/github/login", params={"cli_redirect": cli_redirect})
        if resp.status_code != 200:
            error(f"Failed to get login URL: {resp.text}")
            sys.exit(1)

        data = resp.json()
        auth_url = data["authorization_url"]

        info("Opening GitHub login in your browser...")
        webbrowser.open(auth_url)
        info("Waiting for login to complete...")

        self.token = self._wait_for_token_via_local_server(port)

        if not self.token:
            error("No token received")
            sys.exit(1)

        # Verify token
        resp = self.get("/auth/me")
        if resp.status_code != 200:
            error(f"Invalid token: {resp.text}")
            sys.exit(1)

        user = resp.json()
        success(f"Logged in as {user['github_login']} ({user.get('email', 'no email')})")

        if user.get("org_id"):
            warn("You already belong to an organization.")
            if not confirm("Continue anyway? (will skip org creation)"):
                sys.exit(0)
            return

    def step_create_org(self) -> None:
        header("Step 2: Create Organization")

        # Check if user already has an org
        resp = self.get("/auth/me")
        user = resp.json()
        if user.get("org_id"):
            info("You already belong to an organization, skipping creation.")
            return

        name = prompt("Organization name")
        slug = prompt("Organization slug (URL-friendly)", name.lower().replace(" ", "-"))

        resp = self.post("/organizations", json={"name": name, "slug": slug})
        if resp.status_code not in (200, 201):
            error(f"Failed to create organization: {resp.text}")
            sys.exit(1)

        org = resp.json()
        success(f"Organization created: {org['name']} (slug: {org['slug']})")

    def step_install_github_app(self) -> None:
        header("Step 3: Install GitHub App")

        # Check if already connected
        resp = self.get("/integrations/github/status")
        if resp.status_code == 200 and resp.json().get("connected"):
            success("GitHub App already installed.")
            return

        resp = self.get("/integrations/github/install")
        if resp.status_code != 200:
            error(f"Failed to get install URL: {resp.text}")
            sys.exit(1)

        install_url = resp.json()["install_url"]
        info("Opening GitHub App installation page...")
        webbrowser.open(install_url)
        info("Waiting for installation to be detected...")

        def _check_auto_claim(_: dict) -> bool:
            r = self.post("/integrations/github/auto-claim")
            if r.status_code == 200:
                data = r.json()
                return data.get("status") in ("claimed", "already_claimed")
            return False

        self.poll_status(
            "/integrations/github/status",
            _check_auto_claim,
            "Waiting for GitHub App installation",
        )

        resp = self.get("/integrations/github/status")
        installation_id = resp.json().get("installation_id")
        success(f"GitHub App installed (installation_id: {installation_id})")

    def step_add_repository(self) -> None:
        header("Step 4: Add Repository")

        info("Fetching repositories accessible to the GitHub App...")
        resp = self.get("/integrations/github/repos")
        if resp.status_code != 200:
            error(f"Failed to list repos: {resp.text}")
            sys.exit(1)

        repos = resp.json()["repositories"]
        if not repos:
            warn("No repositories found. Make sure the GitHub App has access to at least one repo.")
            return

        print(f"\n{BOLD}Available repositories:{RESET}")
        for i, r in enumerate(repos, 1):
            print(f"  {i}. {r['full_name']} (default branch: {r['default_branch']})")

        print()
        choice = prompt(f"Select a repository (1-{len(repos)})")
        try:
            idx = int(choice) - 1
            if idx < 0 or idx >= len(repos):
                raise ValueError
        except ValueError:
            error("Invalid selection")
            sys.exit(1)

        selected = repos[idx]
        resp = self.post(
            "/integrations/github/repos",
            json={
                "github_repo_id": selected["id"],
                "full_name": selected["full_name"],
                "default_branch": selected["default_branch"],
            },
        )
        if resp.status_code == 409:
            warn(f"Repository {selected['full_name']} is already registered.")
            return
        if resp.status_code not in (200, 201):
            error(f"Failed to add repository: {resp.text}")
            sys.exit(1)

        success(f"Repository added: {selected['full_name']}")

    def step_project_tracker(self) -> None:
        header("Step 5: Project Tracker (Linear / Jira)")

        print(f"  1. Linear")
        print(f"  2. Jira")
        print(f"  3. Skip")
        print()
        choice = prompt("Select your project tracker (1-3)", "1")

        if choice == "2":
            warn("Jira is not supported yet. Skipping.")
            return
        if choice == "3":
            info("Skipping project tracker setup.")
            return

        # Linear connect
        info("Starting Linear OAuth...")
        resp = self.get("/integrations/linear/connect")
        if resp.status_code != 200:
            error(f"Failed to start Linear OAuth: {resp.text}")
            sys.exit(1)

        auth_url = resp.json()["authorization_url"]
        info("Opening Linear authorization in your browser...")
        webbrowser.open(auth_url)

        data = self.poll_status(
            "/integrations/linear/status",
            lambda d: d.get("connected"),
            "Waiting for Linear authorization",
        )
        success("Linear connected!")

        # Install bot
        info("Now let's install the Bravey bot in Linear...")
        resp = self.get("/integrations/linear/install-bot")
        if resp.status_code != 200:
            error(f"Failed to get bot install URL: {resp.text}")
            return

        auth_url = resp.json()["authorization_url"]
        info("Opening Linear bot installation in your browser...")
        webbrowser.open(auth_url)

        data = self.poll_status(
            "/integrations/linear/status",
            lambda d: d.get("bot_user_configured"),
            "Waiting for bot installation",
        )
        success("Linear bot installed!")

        # Auto-configure workflow states
        info("Configuring Linear workflow states...")
        resp = self.post("/integrations/linear/configure-states")
        if resp.status_code == 200:
            data = resp.json()
            if data.get("in_progress_state_id") and data.get("in_review_state_id"):
                success("Workflow states configured (In Progress + In Review)")
            elif data.get("in_progress_state_id"):
                success("In Progress state configured")
                warn("'In Review' state not found — tickets won't be moved to In Review after PR.")
            elif data.get("in_review_state_id"):
                success("In Review state configured")
                warn("'In Progress' state not found — tickets won't be moved to In Progress.")
            else:
                warn("Could not find 'In Progress' or 'In Review' workflow states.")
                warn("You may need to configure these manually in Linear.")
        else:
            warn(f"Failed to configure workflow states: {resp.text}")

    def step_connect_slack(self) -> None:
        header("Step 6: Connect Slack")

        if not confirm("Connect Slack?"):
            info("Skipping Slack setup.")
            return

        resp = self.get("/integrations/slack/connect")
        if resp.status_code != 200:
            error(f"Failed to start Slack OAuth: {resp.text}")
            return

        auth_url = resp.json()["authorization_url"]
        info("Opening Slack authorization in your browser...")
        webbrowser.open(auth_url)

        data = self.poll_status(
            "/integrations/slack/status",
            lambda d: d.get("connected"),
            "Waiting for Slack authorization",
        )
        success("Slack connected!")

        info("Fetching Slack channels...")
        resp = self.get("/integrations/slack/channels")
        if resp.status_code != 200:
            warn(f"Could not list channels: {resp.text}")
            channel_id = prompt("Enter the Slack channel ID manually (e.g. C0123456789)")
        else:
            channels = resp.json()["channels"]
            if not channels:
                warn("No channels found.")
                return

            print(f"\n{BOLD}Available channels:{RESET}")
            for i, ch in enumerate(channels, 1):
                print(f"  {i}. #{ch['name']} ({ch['id']})")

            print()
            choice = prompt(f"Select a channel (1-{len(channels)})")
            try:
                idx = int(choice) - 1
                if idx < 0 or idx >= len(channels):
                    raise ValueError
            except ValueError:
                error("Invalid selection")
                return
            channel_id = channels[idx]["id"]

        if channel_id:
            resp = self.post(
                "/integrations/slack/channel",
                json={"channel_id": channel_id},
            )
            if resp.status_code == 200:
                success(f"Default Slack channel set: {channel_id}")
            else:
                warn(f"Failed to set channel: {resp.text}")

    def print_summary(self) -> None:
        header("Setup Summary")

        resp = self.get("/auth/me")
        if resp.status_code == 200:
            user = resp.json()
            print(f"  User:         {user['github_login']}")
            print(f"  Role:         {user['role']}")

        resp = self.get("/integrations/github/status")
        if resp.status_code == 200:
            data = resp.json()
            status = f"{GREEN}Connected{RESET}" if data["connected"] else f"{RED}Not connected{RESET}"
            print(f"  GitHub App:   {status}")

        resp = self.get("/integrations/linear/status")
        if resp.status_code == 200:
            data = resp.json()
            status = f"{GREEN}Connected{RESET}" if data["connected"] else f"{RED}Not connected{RESET}"
            bot = f"{GREEN}Yes{RESET}" if data.get("bot_user_configured") else f"{YELLOW}No{RESET}"
            print(f"  Linear:       {status}")
            print(f"  Linear Bot:   {bot}")

        resp = self.get("/integrations/slack/status")
        if resp.status_code == 200:
            data = resp.json()
            status = f"{GREEN}Connected{RESET}" if data["connected"] else f"{RED}Not connected{RESET}"
            print(f"  Slack:        {status}")
            if data.get("default_channel_id"):
                print(f"  Slack Channel:{data['default_channel_id']}")

        print()
        success("Onboarding complete!")

    def run(self) -> None:
        header("Bravey Onboarding CLI")
        info(f"API: {self.api_url}")
        print()

        self.step_github_login()
        self.step_create_org()
        self.step_install_github_app()
        self.step_add_repository()
        self.step_project_tracker()
        self.step_connect_slack()
        self.print_summary()


def main() -> None:
    parser = argparse.ArgumentParser(description="Onboard a new organization to Bravey")
    parser.add_argument(
        "--api-url",
        default=None,
        help="Base URL of the Bravey API (e.g. https://api.bravey.dev)",
    )
    args = parser.parse_args()

    api_url = args.api_url
    if not api_url:
        api_url = prompt("Enter the API base URL", "http://localhost:8000")

    cli = OnboardCLI(api_url)
    try:
        cli.run()
    except KeyboardInterrupt:
        print(f"\n{DIM}Aborted.{RESET}")
        sys.exit(1)


if __name__ == "__main__":
    main()
