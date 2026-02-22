from datetime import datetime, timezone

from src.worker.orchestrator import _build_issue_context, _format_duration


class TestBuildIssueContextBasic:
    def test_includes_title_identifier_description(self):
        issue = {
            "title": "Fix auth bug",
            "identifier": "ENG-42",
            "description": "Auth is broken",
            "priorityLabel": "High",
        }
        result = _build_issue_context(issue)
        assert "Title: Fix auth bug" in result
        assert "Identifier: ENG-42" in result
        assert "Description: Auth is broken" in result
        assert "Priority: High" in result

    def test_missing_description_shows_default(self):
        issue = {"title": "Test", "identifier": "ENG-1"}
        result = _build_issue_context(issue)
        assert "Description: No description" in result


class TestBuildIssueContextWithComments:
    def test_includes_comments(self):
        issue = {
            "title": "Test",
            "identifier": "ENG-1",
            "comments": {
                "nodes": [
                    {
                        "body": "Please prioritize this",
                        "user": {"name": "Alice"},
                    },
                    {
                        "body": "Working on it",
                        "user": {"name": "Bob"},
                    },
                ]
            },
        }
        result = _build_issue_context(issue)
        assert "Comments:" in result
        assert "Alice: Please prioritize this" in result
        assert "Bob: Working on it" in result


class TestBuildIssueContextWithParent:
    def test_includes_parent_issue(self):
        issue = {
            "title": "Sub-task",
            "identifier": "ENG-2",
            "parent": {
                "identifier": "ENG-1",
                "title": "Epic: Overhaul auth",
                "description": "We need to redo auth",
            },
        }
        result = _build_issue_context(issue)
        assert "Parent issue: ENG-1 - Epic: Overhaul auth" in result
        assert "Parent description: We need to redo auth" in result

    def test_parent_without_description(self):
        issue = {
            "title": "Sub-task",
            "identifier": "ENG-2",
            "parent": {
                "identifier": "ENG-1",
                "title": "Epic",
            },
        }
        result = _build_issue_context(issue)
        assert "Parent issue: ENG-1 - Epic" in result
        assert "Parent description" not in result


class TestBuildIssueContextWithLabels:
    def test_includes_labels(self):
        issue = {
            "title": "Test",
            "identifier": "ENG-1",
            "labels": {
                "nodes": [
                    {"name": "bug"},
                    {"name": "high-priority"},
                ]
            },
        }
        result = _build_issue_context(issue)
        assert "Labels: bug, high-priority" in result

    def test_empty_labels_not_shown(self):
        issue = {
            "title": "Test",
            "identifier": "ENG-1",
            "labels": {"nodes": []},
        }
        result = _build_issue_context(issue)
        assert "Labels" not in result


class TestFormatDuration:
    def test_minutes_and_seconds(self):
        start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 1, 1, 0, 3, 45, tzinfo=timezone.utc)
        assert _format_duration(start, end) == "3m 45s"

    def test_zero_duration(self):
        t = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        assert _format_duration(t, t) == "0m 0s"

    def test_seconds_only(self):
        start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 1, 1, 0, 0, 30, tzinfo=timezone.utc)
        assert _format_duration(start, end) == "0m 30s"
