import time

import httpx
import pytest

from src.services.github_service import (
    add_labels,
    create_branch,
    create_issue_comment,
    create_pull_request,
    generate_jwt,
    get_default_branch_sha,
    get_installation_token,
    get_pr_diff,
    get_review_comment,
    reply_to_review_comment,
)


# Generate a valid RSA key at import time for testing JWT generation
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

_test_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
TEST_RSA_PRIVATE_KEY = _test_key.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.TraditionalOpenSSL,
    serialization.NoEncryption(),
).decode()


class TestGenerateJWT:
    def test_valid_jwt_claims(self):
        token = generate_jwt("12345", TEST_RSA_PRIVATE_KEY)
        import jwt as pyjwt

        # Decode with the public key extracted from the private key
        public_key = _test_key.public_key()
        decoded = pyjwt.decode(
            token, public_key, algorithms=["RS256"], options={"verify_exp": False}
        )
        assert decoded["iss"] == "12345"
        assert "iat" in decoded
        assert "exp" in decoded
        assert decoded["exp"] - decoded["iat"] > 0


class TestGetInstallationToken:
    def test_returns_token(self, httpx_mock):
        httpx_mock.add_response(
            url="https://api.github.com/app/installations/999/access_tokens",
            method="POST",
            json={"token": "ghs_test_token_123", "expires_at": "2026-01-01T00:00:00Z"},
        )
        token = get_installation_token("12345", TEST_RSA_PRIVATE_KEY, 999)
        assert token == "ghs_test_token_123"


class TestGetDefaultBranchSha:
    def test_returns_sha(self, httpx_mock):
        httpx_mock.add_response(
            url="https://api.github.com/repos/acme/webapp/git/ref/heads/main",
            json={"object": {"sha": "abc123def456"}},
        )
        sha = get_default_branch_sha("tok", "acme", "webapp", "main")
        assert sha == "abc123def456"


class TestCreateBranch:
    def test_success(self, httpx_mock):
        httpx_mock.add_response(
            url="https://api.github.com/repos/acme/webapp/git/refs",
            method="POST",
            json={"ref": "refs/heads/bravey/eng-1-test", "object": {"sha": "abc123"}},
            status_code=201,
        )
        result = create_branch("tok", "acme", "webapp", "bravey/eng-1-test", "abc123")
        assert result["ref"] == "refs/heads/bravey/eng-1-test"

    def test_already_exists_deletes_and_recreates(self, httpx_mock):
        # First POST returns 422 (branch exists)
        httpx_mock.add_response(
            url="https://api.github.com/repos/acme/webapp/git/refs",
            method="POST",
            status_code=422,
            json={"message": "Reference already exists"},
        )
        # DELETE the old branch
        httpx_mock.add_response(
            url="https://api.github.com/repos/acme/webapp/git/refs/heads/bravey/eng-1-test",
            method="DELETE",
            status_code=204,
        )
        # Second POST succeeds
        httpx_mock.add_response(
            url="https://api.github.com/repos/acme/webapp/git/refs",
            method="POST",
            json={"ref": "refs/heads/bravey/eng-1-test", "object": {"sha": "abc123"}},
            status_code=201,
        )
        result = create_branch("tok", "acme", "webapp", "bravey/eng-1-test", "abc123")
        assert result["ref"] == "refs/heads/bravey/eng-1-test"


class TestCreatePullRequest:
    def test_success(self, httpx_mock):
        httpx_mock.add_response(
            url="https://api.github.com/repos/acme/webapp/pulls",
            method="POST",
            json={"number": 7, "html_url": "https://github.com/acme/webapp/pull/7"},
            status_code=201,
        )
        result = create_pull_request(
            "tok", "acme", "webapp", "Fix auth", "body", "bravey/eng-1", "main"
        )
        assert result["number"] == 7

    def test_already_exists_finds_existing(self, httpx_mock):
        # POST returns 422
        httpx_mock.add_response(
            url="https://api.github.com/repos/acme/webapp/pulls",
            method="POST",
            status_code=422,
            json={"message": "A pull request already exists"},
        )
        # GET existing PRs
        httpx_mock.add_response(
            url=httpx.URL(
                "https://api.github.com/repos/acme/webapp/pulls",
                params={"head": "acme:bravey/eng-1", "state": "open"},
            ),
            method="GET",
            json=[{"number": 7, "html_url": "https://github.com/acme/webapp/pull/7"}],
        )
        result = create_pull_request(
            "tok", "acme", "webapp", "Fix auth", "body", "bravey/eng-1", "main"
        )
        assert result["number"] == 7


class TestGetPrDiff:
    def test_returns_diff_with_correct_accept_header(self, httpx_mock):
        diff_text = "diff --git a/file.py b/file.py\n+new line"
        httpx_mock.add_response(
            url="https://api.github.com/repos/acme/webapp/pulls/7",
            text=diff_text,
        )
        result = get_pr_diff("tok", "acme", "webapp", 7)
        assert result == diff_text

        request = httpx_mock.get_request()
        assert request.headers["Accept"] == "application/vnd.github.diff"


class TestGetReviewComment:
    def test_returns_comment(self, httpx_mock):
        httpx_mock.add_response(
            url="https://api.github.com/repos/acme/webapp/pulls/comments/555",
            json={
                "id": 555,
                "body": "Fix this",
                "path": "src/main.py",
                "line": 10,
            },
        )
        result = get_review_comment("tok", "acme", "webapp", 555)
        assert result["path"] == "src/main.py"
        assert result["line"] == 10


class TestReplyToReviewComment:
    def test_correct_url(self, httpx_mock):
        httpx_mock.add_response(
            url="https://api.github.com/repos/acme/webapp/pulls/7/comments/555/replies",
            method="POST",
            json={"id": 556, "body": "Done!"},
            status_code=201,
        )
        result = reply_to_review_comment("tok", "acme", "webapp", 7, 555, "Done!")
        assert result["body"] == "Done!"

        request = httpx_mock.get_request()
        assert "/pulls/7/comments/555/replies" in str(request.url)


class TestCreateIssueComment:
    def test_posts_to_issues_endpoint(self, httpx_mock):
        httpx_mock.add_response(
            url="https://api.github.com/repos/acme/webapp/issues/42/comments",
            method="POST",
            json={"id": 900, "body": "Hello"},
            status_code=201,
        )
        result = create_issue_comment("tok", "acme", "webapp", 42, "Hello")
        assert result["id"] == 900

        request = httpx_mock.get_request()
        assert "/issues/42/comments" in str(request.url)


class TestAddLabels:
    def test_posts_labels(self, httpx_mock):
        httpx_mock.add_response(
            url="https://api.github.com/repos/acme/webapp/issues/7/labels",
            method="POST",
            json=[{"name": "bravey-generated"}],
        )
        result = add_labels("tok", "acme", "webapp", 7, ["bravey-generated"])
        assert result[0]["name"] == "bravey-generated"

        request = httpx_mock.get_request()
        import json
        body = json.loads(request.content)
        assert body["labels"] == ["bravey-generated"]
