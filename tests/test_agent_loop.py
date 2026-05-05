import json
import os
import pathlib
import pytest
from unittest.mock import MagicMock


# ── Helpers ──────────────────────────────────────────────────────────────────

def _mcp_tool(name, schema=None):
    t = MagicMock()
    t.name = name
    t.description = f"{name} description"
    t.inputSchema = schema or {"type": "object", "properties": {}, "required": []}
    return t


# ── MCP → OpenAI conversion ───────────────────────────────────────────────────

def test_mcp_to_openai_tool_shape():
    from harness.agent.tools import mcp_to_openai_tool
    schema = {"type": "object", "properties": {"function_name": {"type": "string"}}, "required": ["function_name"]}
    result = mcp_to_openai_tool(_mcp_tool("ace_invoke_lambda", schema))
    assert result["type"] == "function"
    assert result["function"]["name"] == "ace_invoke_lambda"
    assert result["function"]["description"] == "ace_invoke_lambda description"
    assert result["function"]["parameters"] == schema


def test_filter_blocks_score_tools():
    from harness.agent.tools import filter_model_tools, mcp_to_openai_tool
    tools = [
        mcp_to_openai_tool(_mcp_tool("ace_invoke_lambda")),
        mcp_to_openai_tool(_mcp_tool("ace_verify_fix")),
        mcp_to_openai_tool(_mcp_tool("ace_score_run")),
        mcp_to_openai_tool(_mcp_tool("ace_get_log_tail")),
    ]
    filtered = filter_model_tools(tools)
    names = [t["function"]["name"] for t in filtered]
    assert "ace_invoke_lambda" in names
    assert "ace_get_log_tail" in names
    assert "ace_verify_fix" not in names
    assert "ace_score_run" not in names


# ── File tool dispatch ────────────────────────────────────────────────────────

def test_read_file_returns_content(tmp_path):
    from harness.agent.tools import dispatch_file_tool
    f = tmp_path / "deployment" / "lambda" / "handler.py"
    f.parent.mkdir(parents=True)
    f.write_text("def handler(): pass")
    result = dispatch_file_tool("read_file", {"path": "deployment/lambda/handler.py"}, str(tmp_path))
    assert "def handler(): pass" in result


def test_read_file_blocks_fault_manifest(tmp_path):
    from harness.agent.tools import dispatch_file_tool
    (tmp_path / "fault_manifest.json").write_text('{"secret": true}')
    result = dispatch_file_tool("read_file", {"path": "fault_manifest.json"}, str(tmp_path))
    assert "not allowed" in result.lower()


def test_read_file_blocks_known_good(tmp_path):
    from harness.agent.tools import dispatch_file_tool
    (tmp_path / "known_good.yaml").write_text("Resources: {}")
    result = dispatch_file_tool("read_file", {"path": "known_good.yaml"}, str(tmp_path))
    assert "not allowed" in result.lower()


def test_read_file_blocks_path_traversal(tmp_path):
    from harness.agent.tools import dispatch_file_tool
    result = dispatch_file_tool("read_file", {"path": "../../etc/passwd"}, str(tmp_path))
    assert "not allowed" in result.lower()


def test_write_file_in_deployment(tmp_path):
    from harness.agent.tools import dispatch_file_tool
    (tmp_path / "deployment").mkdir()
    dispatch_file_tool("write_file", {"path": "deployment/lambda/handler.py", "content": "# fixed"}, str(tmp_path))
    assert (tmp_path / "deployment" / "lambda" / "handler.py").read_text() == "# fixed"


def test_write_file_for_template(tmp_path):
    from harness.agent.tools import dispatch_file_tool
    (tmp_path / "faulted.yaml").write_text("old: content")
    dispatch_file_tool("write_file", {"path": "faulted.yaml", "content": "new: content"}, str(tmp_path))
    assert (tmp_path / "faulted.yaml").read_text() == "new: content"


def test_write_file_blocked_outside_deployment(tmp_path):
    from harness.agent.tools import dispatch_file_tool
    result = dispatch_file_tool("write_file", {"path": "scenario.md", "content": "x"}, str(tmp_path))
    assert "not allowed" in result.lower()


def test_list_directory(tmp_path):
    from harness.agent.tools import dispatch_file_tool
    (tmp_path / "deployment" / "lambda").mkdir(parents=True)
    (tmp_path / "deployment" / "lambda" / "handler.py").write_text("")
    result = dispatch_file_tool("list_directory", {"path": "deployment"}, str(tmp_path))
    assert "lambda" in result


def test_submit_fix_writes_signal(tmp_path, monkeypatch):
    import harness.agent.tools as t_mod
    signal_path = str(tmp_path / "signal.json")
    monkeypatch.setattr(t_mod, "SIGNAL_FILE", signal_path)
    from harness.agent.tools import dispatch_file_tool
    result = dispatch_file_tool("submit_fix", {}, str(tmp_path))
    assert pathlib.Path(signal_path).exists()
    assert "submitted" in result.lower()
    data = json.loads(pathlib.Path(signal_path).read_text())
    assert data == {"trigger": "update-stack"}


def test_file_tool_definitions_are_openai_format():
    from harness.agent.tools import FILE_TOOL_DEFINITIONS
    for tool in FILE_TOOL_DEFINITIONS:
        assert tool["type"] == "function"
        assert "name" in tool["function"]
        assert "parameters" in tool["function"]
    names = [t["function"]["name"] for t in FILE_TOOL_DEFINITIONS]
    assert set(names) == {"read_file", "write_file", "list_directory", "submit_fix"}
