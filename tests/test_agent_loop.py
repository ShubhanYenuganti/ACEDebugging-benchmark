import asyncio
import json
import os
import pathlib
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


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


def test_write_file_blocks_path_traversal(tmp_path):
    from harness.agent.tools import dispatch_file_tool
    result = dispatch_file_tool("write_file", {"path": "deployment/../../etc/passwd", "content": "x"}, str(tmp_path))
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


def test_dispatch_unknown_tool(tmp_path):
    from harness.agent.tools import dispatch_file_tool
    result = dispatch_file_tool("nonexistent_tool", {}, str(tmp_path))
    assert "unknown" in result.lower()


def test_file_tool_definitions_are_openai_format():
    from harness.agent.tools import FILE_TOOL_DEFINITIONS
    for tool in FILE_TOOL_DEFINITIONS:
        assert tool["type"] == "function"
        assert "name" in tool["function"]
        assert "parameters" in tool["function"]
    names = [t["function"]["name"] for t in FILE_TOOL_DEFINITIONS]
    assert set(names) == {"read_file", "write_file", "list_directory", "submit_fix"}


# ── LiteLLM response helpers ──────────────────────────────────────────────────

def _make_tool_call(call_id, name, args_dict):
    tc = MagicMock()
    tc.id = call_id
    tc.function = MagicMock()
    tc.function.name = name
    tc.function.arguments = json.dumps(args_dict)
    return tc


def _make_litellm_response(finish_reason, tool_calls=None, content=""):
    msg = MagicMock()
    msg.tool_calls = tool_calls or []
    msg.content = content
    choice = MagicMock()
    choice.finish_reason = finish_reason
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _fake_mcp_tool(name):
    t = MagicMock()
    t.name = name
    t.description = f"{name} desc"
    t.inputSchema = {"type": "object", "properties": {}, "required": []}
    return t


def _mock_mcp_session(tool_names, call_return='{"ok": true}'):
    session = MagicMock()
    tools_result = MagicMock()
    tools_result.tools = [_fake_mcp_tool(n) for n in tool_names]
    session.list_tools = AsyncMock(return_value=tools_result)
    mcp_call_result = MagicMock()
    mcp_call_result.content = [MagicMock(text=call_return)]
    session.call_tool = AsyncMock(return_value=mcp_call_result)
    return session


# ── Loop tests ────────────────────────────────────────────────────────────────

def test_loop_exits_on_stop_reason(tmp_path):
    """finish_reason='stop' with no tool calls — loop exits, submitted=False."""
    from harness.agent.loop import run_agent_loop

    with patch("harness.agent.loop.litellm.completion") as mock_llm, \
         patch("harness.agent.loop._start_mcp_session") as mock_sess_ctx:

        mock_llm.return_value = _make_litellm_response("stop", content="I am done.")
        sess = _mock_mcp_session(["ace_invoke_lambda"])
        mock_sess_ctx.return_value.__aenter__ = AsyncMock(return_value=sess)
        mock_sess_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        submitted = asyncio.run(run_agent_loop(
            model="anthropic/claude-sonnet-4-6",
            api_key="sk-test",
            base_url=None,
            context={"scenario_brief": "brief", "instruction": "fix it",
                     "stack_outputs": {}, "template_path": "/t", "deployment_dir": "/d"},
            scenario_dir=str(tmp_path),
            run_id="t001",
            harness_api_key="hk",
            max_turns=5,
        ))

    assert submitted is False


def test_loop_exits_on_end_turn(tmp_path):
    """finish_reason='end_turn' (Anthropic) — loop exits, submitted=False."""
    from harness.agent.loop import run_agent_loop

    with patch("harness.agent.loop.litellm.completion") as mock_llm, \
         patch("harness.agent.loop._start_mcp_session") as mock_sess_ctx:

        mock_llm.return_value = _make_litellm_response("end_turn", content="Done.")
        sess = _mock_mcp_session(["ace_invoke_lambda"])
        mock_sess_ctx.return_value.__aenter__ = AsyncMock(return_value=sess)
        mock_sess_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        submitted = asyncio.run(run_agent_loop(
            model="anthropic/claude-sonnet-4-6",
            api_key="sk-test",
            base_url=None,
            context={"scenario_brief": "brief", "instruction": "fix it",
                     "stack_outputs": {}, "template_path": "/t", "deployment_dir": "/d"},
            scenario_dir=str(tmp_path),
            run_id="t006",
            harness_api_key="hk",
            max_turns=5,
        ))

    assert submitted is False


def test_loop_exits_on_submit_fix(tmp_path, monkeypatch):
    """Model calls submit_fix — loop exits with submitted=True, signal file written."""
    import harness.agent.tools as t_mod
    signal_path = str(tmp_path / "signal.json")
    monkeypatch.setattr(t_mod, "SIGNAL_FILE", signal_path)

    from harness.agent.loop import run_agent_loop

    with patch("harness.agent.loop.litellm.completion") as mock_llm, \
         patch("harness.agent.loop._start_mcp_session") as mock_sess_ctx:

        mock_llm.return_value = _make_litellm_response(
            "tool_calls", tool_calls=[_make_tool_call("tc1", "submit_fix", {})]
        )
        sess = _mock_mcp_session(["ace_invoke_lambda"])
        mock_sess_ctx.return_value.__aenter__ = AsyncMock(return_value=sess)
        mock_sess_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        submitted = asyncio.run(run_agent_loop(
            model="openai/gpt-4o",
            api_key="sk-test",
            base_url=None,
            context={"scenario_brief": "b", "instruction": "i",
                     "stack_outputs": {}, "template_path": "/t", "deployment_dir": "/d"},
            scenario_dir=str(tmp_path),
            run_id="t002",
            harness_api_key="hk",
            max_turns=5,
        ))

    assert submitted is True
    assert pathlib.Path(signal_path).exists()


def test_loop_respects_max_turns(tmp_path):
    """Loop stops after max_turns even if model keeps calling tools."""
    from harness.agent.loop import run_agent_loop

    with patch("harness.agent.loop.litellm.completion") as mock_llm, \
         patch("harness.agent.loop._start_mcp_session") as mock_sess_ctx, \
         patch("harness.agent.loop.result_logger.log_tool_call"):

        mock_llm.return_value = _make_litellm_response(
            "tool_calls",
            tool_calls=[_make_tool_call("tc_x", "ace_invoke_lambda", {"function_name": "fn", "payload": {}})],
        )
        sess = _mock_mcp_session(["ace_invoke_lambda"])
        mock_sess_ctx.return_value.__aenter__ = AsyncMock(return_value=sess)
        mock_sess_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        asyncio.run(run_agent_loop(
            model="ollama/qwen2.5",
            api_key=None,
            base_url="http://localhost:11434",
            context={"scenario_brief": "b", "instruction": "i",
                     "stack_outputs": {}, "template_path": "/t", "deployment_dir": "/d"},
            scenario_dir=str(tmp_path),
            run_id="t003",
            harness_api_key="hk",
            max_turns=3,
        ))

    assert mock_llm.call_count == 3


def test_mcp_tool_calls_are_logged(tmp_path):
    """MCP tool calls (not file tools) are passed to result_logger.log_tool_call."""
    from harness.agent.loop import run_agent_loop

    responses = [
        _make_litellm_response("tool_calls", tool_calls=[_make_tool_call("tc1", "ace_invoke_lambda", {"function_name": "fn", "payload": {}})]),
        _make_litellm_response("stop"),
    ]

    with patch("harness.agent.loop.litellm.completion", side_effect=responses), \
         patch("harness.agent.loop._start_mcp_session") as mock_sess_ctx, \
         patch("harness.agent.loop.result_logger.log_tool_call") as mock_log:

        sess = _mock_mcp_session(["ace_invoke_lambda"])
        mock_sess_ctx.return_value.__aenter__ = AsyncMock(return_value=sess)
        mock_sess_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        asyncio.run(run_agent_loop(
            model="gemini/gemini-1.5-pro",
            api_key="key",
            base_url=None,
            context={"scenario_brief": "b", "instruction": "i",
                     "stack_outputs": {}, "template_path": "/t", "deployment_dir": "/d"},
            scenario_dir=str(tmp_path),
            run_id="t004",
            harness_api_key="hk",
            max_turns=5,
        ))

    mock_log.assert_called_once()
    args, kwargs = mock_log.call_args
    tool_name = kwargs.get("tool") or args[2]
    assert tool_name == "ace_invoke_lambda"


def test_file_tool_calls_not_logged(tmp_path):
    """File tool calls are NOT passed to result_logger."""
    from harness.agent.loop import run_agent_loop

    (tmp_path / "deployment").mkdir()

    responses = [
        _make_litellm_response("tool_calls", tool_calls=[_make_tool_call("tc1", "list_directory", {"path": "deployment"})]),
        _make_litellm_response("stop"),
    ]

    with patch("harness.agent.loop.litellm.completion", side_effect=responses), \
         patch("harness.agent.loop._start_mcp_session") as mock_sess_ctx, \
         patch("harness.agent.loop.result_logger.log_tool_call") as mock_log:

        sess = _mock_mcp_session(["ace_invoke_lambda"])
        mock_sess_ctx.return_value.__aenter__ = AsyncMock(return_value=sess)
        mock_sess_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        asyncio.run(run_agent_loop(
            model="anthropic/claude-sonnet-4-6",
            api_key="key",
            base_url=None,
            context={"scenario_brief": "b", "instruction": "i",
                     "stack_outputs": {}, "template_path": "/t", "deployment_dir": "/d"},
            scenario_dir=str(tmp_path),
            run_id="t005",
            harness_api_key="hk",
            max_turns=5,
        ))

    mock_log.assert_not_called()
