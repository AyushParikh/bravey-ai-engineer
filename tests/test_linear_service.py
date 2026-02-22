import pytest

from src.services.linear_service import (
    create_comment,
    fetch_issue,
    graphql_request,
    update_issue_state,
)

LINEAR_API_URL = "https://api.linear.app/graphql"


class TestGraphqlRequest:
    def test_sends_query_and_returns_data(self, httpx_mock):
        httpx_mock.add_response(
            url=LINEAR_API_URL,
            method="POST",
            json={"data": {"viewer": {"id": "user-1"}}},
        )
        result = graphql_request("lin_api_token", "{ viewer { id } }")
        assert result["data"]["viewer"]["id"] == "user-1"

        request = httpx_mock.get_request()
        assert request.headers["Authorization"] == "lin_api_token"
        import json
        body = json.loads(request.content)
        assert body["query"] == "{ viewer { id } }"
        assert body["variables"] == {}

    def test_sends_variables(self, httpx_mock):
        httpx_mock.add_response(
            url=LINEAR_API_URL,
            method="POST",
            json={"data": {"issue": {"id": "iss-1"}}},
        )
        result = graphql_request(
            "tok", "query($id: String!) { issue(id: $id) { id } }", {"id": "iss-1"}
        )
        assert result["data"]["issue"]["id"] == "iss-1"

        request = httpx_mock.get_request()
        import json
        body = json.loads(request.content)
        assert body["variables"] == {"id": "iss-1"}


class TestFetchIssue:
    def test_returns_issue_data(self, httpx_mock):
        httpx_mock.add_response(
            url=LINEAR_API_URL,
            method="POST",
            json={
                "data": {
                    "issue": {
                        "id": "iss-1",
                        "identifier": "ENG-42",
                        "title": "Fix bug",
                        "description": "It's broken",
                    }
                }
            },
        )
        issue = fetch_issue("tok", "iss-1")
        assert issue["identifier"] == "ENG-42"
        assert issue["title"] == "Fix bug"

    def test_returns_empty_dict_on_missing_data(self, httpx_mock):
        httpx_mock.add_response(
            url=LINEAR_API_URL,
            method="POST",
            json={"data": {}},
        )
        issue = fetch_issue("tok", "nonexistent")
        assert issue == {}


class TestUpdateIssueState:
    def test_sends_mutation(self, httpx_mock):
        httpx_mock.add_response(
            url=LINEAR_API_URL,
            method="POST",
            json={
                "data": {
                    "issueUpdate": {
                        "success": True,
                        "issue": {"id": "iss-1", "state": {"name": "In Review"}},
                    }
                }
            },
        )
        result = update_issue_state("tok", "iss-1", "state-review-id")
        assert result["data"]["issueUpdate"]["success"] is True

        request = httpx_mock.get_request()
        import json
        body = json.loads(request.content)
        assert body["variables"]["id"] == "iss-1"
        assert body["variables"]["stateId"] == "state-review-id"


class TestCreateComment:
    def test_sends_mutation(self, httpx_mock):
        httpx_mock.add_response(
            url=LINEAR_API_URL,
            method="POST",
            json={
                "data": {
                    "commentCreate": {
                        "success": True,
                        "comment": {"id": "comment-1"},
                    }
                }
            },
        )
        result = create_comment("tok", "iss-1", "Hello from Bravey")
        assert result["data"]["commentCreate"]["success"] is True

        request = httpx_mock.get_request()
        import json
        body = json.loads(request.content)
        assert body["variables"]["issueId"] == "iss-1"
        assert body["variables"]["body"] == "Hello from Bravey"
