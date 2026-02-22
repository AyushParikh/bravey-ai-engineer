import httpx
import pytest

from src.services.agent_service import AgentResult, _execute_tool


class TestAgentResultFields:
    def test_success_result(self):
        r = AgentResult(success=True, claude_session_id="sess-1", summary="Done")
        assert r.success is True
        assert r.claude_session_id == "sess-1"
        assert r.summary == "Done"
        assert r.error is None

    def test_failure_result(self):
        r = AgentResult(success=False, error="Something broke")
        assert r.success is False
        assert r.error == "Something broke"
        assert r.summary is None


class TestExecuteToolReadFile:
    def test_dispatches_to_read(self, httpx_mock):
        httpx_mock.add_response(
            url=httpx.URL(
                "https://api.github.com/repos/acme/webapp/contents/src/main.py",
                params={"ref": "bravey/eng-1"},
            ),
            json={
                "type": "file",
                "content": "cHJpbnQoImhlbGxvIik=",  # base64 of print("hello")
                "sha": "abc",
            },
        )
        result = _execute_tool(
            "read_file",
            {"path": "src/main.py"},
            "tok", "acme", "webapp", "bravey/eng-1",
        )
        assert 'print("hello")' in result


class TestExecuteToolWriteFile:
    def test_dispatches_to_write(self, httpx_mock):
        # Check if file exists (404 = new file)
        httpx_mock.add_response(
            url=httpx.URL(
                "https://api.github.com/repos/acme/webapp/contents/new.py",
                params={"ref": "bravey/eng-1"},
            ),
            method="GET",
            status_code=404,
        )
        # Create file
        httpx_mock.add_response(
            url="https://api.github.com/repos/acme/webapp/contents/new.py",
            method="PUT",
            json={"commit": {"sha": "deadbeef1234"}},
            status_code=201,
        )
        result = _execute_tool(
            "write_file",
            {"path": "new.py", "content": "print('hi')", "commit_message": "add new.py"},
            "tok", "acme", "webapp", "bravey/eng-1",
        )
        assert "Created" in result
        assert "new.py" in result


class TestExecuteToolUnknown:
    def test_returns_error(self):
        result = _execute_tool(
            "nonexistent_tool",
            {},
            "tok", "acme", "webapp", "main",
        )
        assert "Error: Unknown tool" in result
        assert "nonexistent_tool" in result


class TestExecuteToolHttpError:
    def test_returns_error_message(self, httpx_mock):
        httpx_mock.add_response(
            url=httpx.URL(
                "https://api.github.com/repos/acme/webapp/contents/missing.py",
                params={"ref": "main"},
            ),
            status_code=500,
            json={"message": "Internal Server Error"},
        )
        result = _execute_tool(
            "read_file",
            {"path": "missing.py"},
            "tok", "acme", "webapp", "main",
        )
        assert "Error" in result
        assert "500" in result
