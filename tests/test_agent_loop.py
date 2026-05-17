import asyncio
import json
import pathlib
from unittest.mock import AsyncMock, MagicMock, patch

from harness.shared.types import DeploymentResult


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


def test_loop_exits_on_submit_fix(tmp_path):
    """Model calls write_file then submit_fix — loop exits with submitted=True."""
    (tmp_path / "deployment").mkdir()

    from harness.agent.loop import run_agent_loop

    responses = [
        _make_litellm_response("tool_calls", tool_calls=[
            _make_tool_call("tc0", "write_file",
                            {"path": "deployment/handler.py", "content": "# fix"})
        ]),
        _make_litellm_response("tool_calls", tool_calls=[
            _make_tool_call("tc1", "submit_fix", {})
        ]),
    ]

    with patch("harness.agent.loop.litellm.completion", side_effect=responses), \
         patch("harness.agent.loop._start_mcp_session") as mock_sess_ctx:

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


def test_submit_fix_refused_without_write(tmp_path, monkeypatch):
    """submit_fix with no prior write_file is refused; loop continues, submitted stays False."""
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
            run_id="t002b",
            harness_api_key="hk",
            max_turns=3,
        ))

    assert submitted is False
    assert not pathlib.Path(signal_path).exists()


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


def test_submit_fix_does_not_write_signal_file(tmp_path):
    import pathlib
    from harness.agent.tools import dispatch_file_tool
    signal = pathlib.Path("/tmp/ace-bench-update.json")
    if signal.exists():
        signal.unlink()
    dispatch_file_tool("submit_fix", {}, str(tmp_path))
    assert not signal.exists(), "submit_fix must not write signal file when deploy_callback is used"


def test_file_tool_calls_not_logged(tmp_path):
    """File tool calls are NOT passed to result_logger."""
    from harness.agent.loop import run_agent_loop

    (tmp_path / "deployment").mkdir()

    responses = [
        _make_litellm_response("tool_calls", tool_calls=[_make_tool_call("tc1", "list_directory", {"path": "deployment"})]),
        _make_litellm_response("stop"),
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


# ── deploy_callback tests ─────────────────────────────────────────────────────

def test_deploy_callback_success(tmp_path):
    """deploy_callback returns success — loop exits with submitted=True."""
    (tmp_path / "deployment").mkdir()
    from harness.agent.loop import run_agent_loop
    from unittest.mock import MagicMock

    deploy_callback = MagicMock(return_value=DeploymentResult(outcome="deploy_success"))

    responses = [
        _make_litellm_response("tool_calls", tool_calls=[
            _make_tool_call("tc0", "write_file",
                            {"path": "deployment/handler.py", "content": "# fix"})
        ]),
        _make_litellm_response("tool_calls", tool_calls=[
            _make_tool_call("tc1", "submit_fix", {})
        ]),
    ]

    with patch("harness.agent.loop.litellm.completion", side_effect=responses), \
         patch("harness.agent.loop._start_mcp_session") as mock_sess_ctx:

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
            run_id="t_cb1",
            harness_api_key="hk",
            max_turns=10,
            deploy_callback=deploy_callback,
        ))

    assert submitted is True
    assert deploy_callback.call_count == 1


def test_deploy_callback_failure_then_success(tmp_path):
    """deploy_callback fails once, loop continues, second attempt succeeds."""
    (tmp_path / "deployment").mkdir()
    from harness.agent.loop import run_agent_loop
    from unittest.mock import MagicMock

    deploy_results = [
        DeploymentResult(outcome="no_changes", error="no changes detected"),
        DeploymentResult(outcome="deploy_success"),
    ]
    deploy_callback = MagicMock(side_effect=deploy_results)

    responses = [
        # turn 1: write_file
        _make_litellm_response("tool_calls", tool_calls=[
            _make_tool_call("tc0", "write_file",
                            {"path": "deployment/handler.py", "content": "# fix v1"})
        ]),
        # turn 2: submit_fix (fails)
        _make_litellm_response("tool_calls", tool_calls=[
            _make_tool_call("tc1", "submit_fix", {})
        ]),
        # turn 3: write_file again (after failure)
        _make_litellm_response("tool_calls", tool_calls=[
            _make_tool_call("tc2", "write_file",
                            {"path": "deployment/handler.py", "content": "# fix v2"})
        ]),
        # turn 4: submit_fix again (succeeds)
        _make_litellm_response("tool_calls", tool_calls=[
            _make_tool_call("tc3", "submit_fix", {})
        ]),
    ]

    with patch("harness.agent.loop.litellm.completion", side_effect=responses), \
         patch("harness.agent.loop._start_mcp_session") as mock_sess_ctx:

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
            run_id="t_cb2",
            harness_api_key="hk",
            max_turns=10,
            deploy_callback=deploy_callback,
        ))

    assert submitted is True
    assert deploy_callback.call_count == 2


def test_deploy_callback_max_retries_exits(tmp_path):
    """Loop exits with submitted=True when max_deploy_retries exhausted."""
    (tmp_path / "deployment").mkdir()
    from harness.agent.loop import run_agent_loop
    from unittest.mock import MagicMock

    deploy_callback = MagicMock(return_value=DeploymentResult(outcome="deploy_fail", error="persistent error"))

    # Need: write_file + submit_fix repeated (1 initial + 5 retries = 6 calls)
    # Each retry needs a write_file + submit_fix pair (to pass writes_since_last_submit guard)
    responses = []
    for i in range(6):
        responses.append(_make_litellm_response("tool_calls", tool_calls=[
            _make_tool_call(f"w{i}", "write_file",
                            {"path": "deployment/handler.py", "content": f"# fix v{i}"})
        ]))
        responses.append(_make_litellm_response("tool_calls", tool_calls=[
            _make_tool_call(f"s{i}", "submit_fix", {})
        ]))

    with patch("harness.agent.loop.litellm.completion", side_effect=responses), \
         patch("harness.agent.loop._start_mcp_session") as mock_sess_ctx:

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
            run_id="t_cb3",
            harness_api_key="hk",
            max_turns=30,
            deploy_callback=deploy_callback,
            max_deploy_retries=5,
        ))

    assert submitted is True
    assert deploy_callback.call_count == 6  # 1 initial + 5 retries


def test_deploy_callback_refuses_submit_without_new_write(tmp_path):
    """submit_fix refused if no write_file since last failed attempt."""
    (tmp_path / "deployment").mkdir()
    from harness.agent.loop import run_agent_loop
    from unittest.mock import MagicMock

    deploy_callback = MagicMock(return_value=DeploymentResult(outcome="no_changes", error="no changes"))

    responses = [
        # turn 1: write_file
        _make_litellm_response("tool_calls", tool_calls=[
            _make_tool_call("tc0", "write_file",
                            {"path": "deployment/handler.py", "content": "# fix"})
        ]),
        # turn 2: submit_fix (deploy_callback returns failure)
        _make_litellm_response("tool_calls", tool_calls=[
            _make_tool_call("tc1", "submit_fix", {})
        ]),
        # turn 3: submit_fix again without any write_file in between — must be refused
        _make_litellm_response("tool_calls", tool_calls=[
            _make_tool_call("tc2", "submit_fix", {})
        ]),
        # turn 4: write_file, then exit via stop
        _make_litellm_response("tool_calls", tool_calls=[
            _make_tool_call("tc3", "write_file",
                            {"path": "deployment/handler.py", "content": "# fix v2"})
        ]),
        _make_litellm_response("stop"),
        _make_litellm_response("stop"),
    ]

    with patch("harness.agent.loop.litellm.completion", side_effect=responses), \
         patch("harness.agent.loop._start_mcp_session") as mock_sess_ctx:

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
            run_id="t_cb4",
            harness_api_key="hk",
            max_turns=10,
            deploy_callback=deploy_callback,
        ))
    assert submitted is False

    # deploy_callback only called once (second submit_fix without write was refused)
    assert deploy_callback.call_count == 1


# ── Verbose / thinking block tests ───────────────────────────────────────────

def test_verbose_prints_reasoning_text(tmp_path, capsys):
    """[thinking] block printed when model returns text before tool call in verbose mode."""
    (tmp_path / "deployment").mkdir()
    from harness.agent.loop import run_agent_loop

    tool_call = _make_tool_call("tc0", "write_file",
        {"path": "deployment/handler.py", "content": "# fix"})
    submit_call = _make_tool_call("tc1", "submit_fix", {})

    response_with_text = _make_litellm_response(
        "tool_calls", tool_calls=[tool_call], content="I will fix the StreamViewType."
    )
    response_submit = _make_litellm_response("tool_calls", tool_calls=[submit_call])

    # Build a fake streaming chunk that carries the text content
    def _make_stream_chunk(content_text):
        delta = MagicMock()
        delta.content = content_text
        choice = MagicMock()
        choice.delta = delta
        chunk = MagicMock()
        chunk.choices = [choice]
        return chunk

    text_chunk = _make_stream_chunk("I will fix the StreamViewType.")
    empty_chunk = _make_stream_chunk(None)

    with patch("harness.agent.loop.litellm.completion") as mock_llm, \
         patch("harness.agent.loop.litellm.stream_chunk_builder") as mock_builder, \
         patch("harness.agent.loop._start_mcp_session") as mock_sess_ctx:

        # Turn 0 (verbose): returns chunks with content; stream_chunk_builder assembles.
        # Turn 1 (verbose): no content chunks; stream_chunk_builder assembles submit.
        mock_llm.side_effect = [[text_chunk], [empty_chunk]]
        mock_builder.side_effect = [response_with_text, response_submit]

        sess = _mock_mcp_session(["ace_invoke_lambda"])
        mock_sess_ctx.return_value.__aenter__ = AsyncMock(return_value=sess)
        mock_sess_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        asyncio.run(run_agent_loop(
            model="openai/gpt-4o",
            api_key="sk-test",
            base_url=None,
            context={"scenario_brief": "b", "instruction": "i",
                     "stack_outputs": {}, "template_path": "/t", "deployment_dir": "/d"},
            scenario_dir=str(tmp_path),
            run_id="t_verbose1",
            harness_api_key="hk",
            max_turns=10,
            verbose=True,
        ))

    captured = capsys.readouterr()
    assert "[thinking]" in captured.out
    assert "StreamViewType" in captured.out


def test_non_verbose_no_reasoning_printed(tmp_path, capsys):
    """No [thinking] block printed when verbose=False."""
    (tmp_path / "deployment").mkdir()
    from harness.agent.loop import run_agent_loop

    tool_call = _make_tool_call("tc0", "write_file",
        {"path": "deployment/handler.py", "content": "# fix"})
    submit_call = _make_tool_call("tc1", "submit_fix", {})

    responses = [
        _make_litellm_response("tool_calls", tool_calls=[tool_call],
                               content="I will fix the StreamViewType."),
        _make_litellm_response("tool_calls", tool_calls=[submit_call]),
    ]

    with patch("harness.agent.loop.litellm.completion", side_effect=responses), \
         patch("harness.agent.loop._start_mcp_session") as mock_sess_ctx:

        sess = _mock_mcp_session(["ace_invoke_lambda"])
        mock_sess_ctx.return_value.__aenter__ = AsyncMock(return_value=sess)
        mock_sess_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        asyncio.run(run_agent_loop(
            model="openai/gpt-4o",
            api_key="sk-test",
            base_url=None,
            context={"scenario_brief": "b", "instruction": "i",
                     "stack_outputs": {}, "template_path": "/t", "deployment_dir": "/d"},
            scenario_dir=str(tmp_path),
            run_id="t_verbose2",
            harness_api_key="hk",
            max_turns=10,
            verbose=False,
        ))

    captured = capsys.readouterr()
    assert "[thinking]" not in captured.out


def test_verbose_prints_write_file_preview(tmp_path, capsys):
    """First 30 lines of write_file content printed in verbose mode."""
    (tmp_path / "deployment").mkdir()
    from harness.agent.loop import run_agent_loop

    content_lines = [f"line {i}" for i in range(1, 41)]  # 40 lines
    file_content = "\n".join(content_lines)

    tool_call = _make_tool_call("tc0", "write_file",
        {"path": "deployment/handler.py", "content": file_content})
    submit_call = _make_tool_call("tc1", "submit_fix", {})

    response1 = _make_litellm_response("tool_calls", tool_calls=[tool_call])
    response2 = _make_litellm_response("tool_calls", tool_calls=[submit_call])

    with patch("harness.agent.loop.litellm.completion") as mock_llm, \
         patch("harness.agent.loop.litellm.stream_chunk_builder") as mock_builder, \
         patch("harness.agent.loop._start_mcp_session") as mock_sess_ctx:

        mock_llm.return_value = []
        mock_builder.side_effect = [response1, response2]

        sess = _mock_mcp_session(["ace_invoke_lambda"])
        mock_sess_ctx.return_value.__aenter__ = AsyncMock(return_value=sess)
        mock_sess_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        asyncio.run(run_agent_loop(
            model="openai/gpt-4o",
            api_key="sk-test",
            base_url=None,
            context={"scenario_brief": "b", "instruction": "i",
                     "stack_outputs": {}, "template_path": "/t", "deployment_dir": "/d"},
            scenario_dir=str(tmp_path),
            run_id="t_preview1",
            harness_api_key="hk",
            max_turns=10,
            verbose=True,
        ))

    captured = capsys.readouterr()
    assert "[edit →" in captured.out
    assert "deployment/handler.py" in captured.out
    assert "line 1" in captured.out
    assert "line 30" in captured.out
    assert "line 31" not in captured.out
    assert "10 more changes" in captured.out


def test_verbose_write_file_preview_short_file(tmp_path, capsys):
    """All lines printed when content has fewer than 30 lines."""
    (tmp_path / "deployment").mkdir()
    from harness.agent.loop import run_agent_loop

    file_content = "line 1\nline 2\nline 3"

    tool_call = _make_tool_call("tc0", "write_file",
        {"path": "deployment/handler.py", "content": file_content})
    submit_call = _make_tool_call("tc1", "submit_fix", {})

    response1 = _make_litellm_response("tool_calls", tool_calls=[tool_call])
    response2 = _make_litellm_response("tool_calls", tool_calls=[submit_call])

    with patch("harness.agent.loop.litellm.completion") as mock_llm, \
         patch("harness.agent.loop.litellm.stream_chunk_builder") as mock_builder, \
         patch("harness.agent.loop._start_mcp_session") as mock_sess_ctx:

        mock_llm.return_value = []
        mock_builder.side_effect = [response1, response2]

        sess = _mock_mcp_session(["ace_invoke_lambda"])
        mock_sess_ctx.return_value.__aenter__ = AsyncMock(return_value=sess)
        mock_sess_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        asyncio.run(run_agent_loop(
            model="openai/gpt-4o",
            api_key="sk-test",
            base_url=None,
            context={"scenario_brief": "b", "instruction": "i",
                     "stack_outputs": {}, "template_path": "/t", "deployment_dir": "/d"},
            scenario_dir=str(tmp_path),
            run_id="t_preview2",
            harness_api_key="hk",
            max_turns=10,
            verbose=True,
        ))

    captured = capsys.readouterr()
    assert "line 1" in captured.out
    assert "line 3" in captured.out
    assert "more lines" not in captured.out


def test_non_verbose_no_write_preview(tmp_path, capsys):
    """No [edit →] block printed when verbose=False."""
    (tmp_path / "deployment").mkdir()
    from harness.agent.loop import run_agent_loop

    content = "\n".join(f"line {i}" for i in range(1, 10))

    tool_call = _make_tool_call("tc0", "write_file",
        {"path": "deployment/handler.py", "content": content})
    submit_call = _make_tool_call("tc1", "submit_fix", {})

    responses = [
        _make_litellm_response("tool_calls", tool_calls=[tool_call]),
        _make_litellm_response("tool_calls", tool_calls=[submit_call]),
    ]

    with patch("harness.agent.loop.litellm.completion", side_effect=responses), \
         patch("harness.agent.loop._start_mcp_session") as mock_sess_ctx:

        sess = _mock_mcp_session(["ace_invoke_lambda"])
        mock_sess_ctx.return_value.__aenter__ = AsyncMock(return_value=sess)
        mock_sess_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        asyncio.run(run_agent_loop(
            model="openai/gpt-4o",
            api_key="sk-test",
            base_url=None,
            context={"scenario_brief": "b", "instruction": "i",
                     "stack_outputs": {}, "template_path": "/t", "deployment_dir": "/d"},
            scenario_dir=str(tmp_path),
            run_id="t_preview3",
            harness_api_key="hk",
            max_turns=10,
            verbose=False,
        ))

    captured = capsys.readouterr()
    assert "[edit →" not in captured.out


# ── verify_callback / test-retry tests ──────────────────────────────────────

def test_loop_calls_verify_after_successful_deploy(tmp_path):
    """verify_callback fires once when deploy succeeds and tests pass."""
    (tmp_path / "deployment").mkdir()
    from harness.agent.loop import run_agent_loop
    from unittest.mock import MagicMock

    deploy_cb = MagicMock(return_value=DeploymentResult(outcome="deploy_success"))
    verify_cb = MagicMock(return_value={"all_passed": True, "passed": [], "failed": []})

    responses = [
        _make_litellm_response("tool_calls", tool_calls=[
            _make_tool_call("tc0", "write_file", {"path": "deployment/handler.py", "content": "# fix"})
        ]),
        _make_litellm_response("tool_calls", tool_calls=[
            _make_tool_call("tc1", "submit_fix", {})
        ]),
    ]

    with patch("harness.agent.loop.litellm.completion", side_effect=responses), \
         patch("harness.agent.loop._start_mcp_session") as mock_sess_ctx:
        sess = _mock_mcp_session(["ace_invoke_lambda"])
        mock_sess_ctx.return_value.__aenter__ = AsyncMock(return_value=sess)
        mock_sess_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        asyncio.run(run_agent_loop(
            model="openai/gpt-4o", api_key="sk-test", base_url=None,
            context={"scenario_brief": "b", "instruction": "i",
                     "stack_outputs": {}, "template_path": "/t", "deployment_dir": "/d"},
            scenario_dir=str(tmp_path), run_id="tv1", harness_api_key="hk", max_turns=10,
            deploy_callback=deploy_cb, verify_callback=verify_cb,
        ))

    assert verify_cb.call_count == 1
    assert deploy_cb.call_count == 1


def test_loop_continues_on_test_failure_then_exits_on_pass(tmp_path):
    """Loop injects test summary on failure and exits when tests pass on retry."""
    (tmp_path / "deployment").mkdir()
    from harness.agent.loop import run_agent_loop
    from unittest.mock import MagicMock

    deploy_cb = MagicMock(return_value=DeploymentResult(outcome="deploy_success"))
    redeploy_cb = MagicMock(return_value=DeploymentResult(outcome="deploy_success"))
    verify_results = [
        {"all_passed": False, "passed": [], "failed": [{"name": "test_x", "description": "", "short_error": "AssertionError"}]},
        {"all_passed": True, "passed": [{"name": "test_x", "description": ""}], "failed": []},
    ]
    verify_cb = MagicMock(side_effect=verify_results)

    responses = [
        _make_litellm_response("tool_calls", tool_calls=[
            _make_tool_call("tc0", "write_file", {"path": "deployment/handler.py", "content": "# v1"})
        ]),
        _make_litellm_response("tool_calls", tool_calls=[
            _make_tool_call("tc1", "submit_fix", {})
        ]),
        _make_litellm_response("tool_calls", tool_calls=[
            _make_tool_call("tc2", "write_file", {"path": "deployment/handler.py", "content": "# v2"})
        ]),
        _make_litellm_response("tool_calls", tool_calls=[
            _make_tool_call("tc3", "submit_fix", {})
        ]),
    ]

    with patch("harness.agent.loop.litellm.completion", side_effect=responses), \
         patch("harness.agent.loop._start_mcp_session") as mock_sess_ctx:
        sess = _mock_mcp_session(["ace_invoke_lambda"])
        mock_sess_ctx.return_value.__aenter__ = AsyncMock(return_value=sess)
        mock_sess_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        submitted = asyncio.run(run_agent_loop(
            model="openai/gpt-4o", api_key="sk-test", base_url=None,
            context={"scenario_brief": "b", "instruction": "i",
                     "stack_outputs": {}, "template_path": "/t", "deployment_dir": "/d"},
            scenario_dir=str(tmp_path), run_id="tv2", harness_api_key="hk", max_turns=20,
            deploy_callback=deploy_cb, redeploy_callback=redeploy_cb,
            verify_callback=verify_cb, max_test_retries=5,
        ))

    assert submitted is True
    assert verify_cb.call_count == 2
    assert redeploy_cb.call_count == 1


def test_loop_exits_after_max_test_retries(tmp_path):
    """Loop exits after max_test_retries even if tests keep failing."""
    (tmp_path / "deployment").mkdir()
    from harness.agent.loop import run_agent_loop
    from unittest.mock import MagicMock

    deploy_cb = MagicMock(return_value=DeploymentResult(outcome="deploy_success"))
    redeploy_cb = MagicMock(return_value=DeploymentResult(outcome="deploy_success"))
    verify_cb = MagicMock(return_value={
        "all_passed": False, "passed": [],
        "failed": [{"name": "test_x", "description": "", "short_error": "AssertionError"}],
    })

    responses = []
    for i in range(6):  # 1 initial + 5 retries
        responses.append(_make_litellm_response("tool_calls", tool_calls=[
            _make_tool_call(f"w{i}", "write_file", {"path": "deployment/handler.py", "content": f"# v{i}"})
        ]))
        responses.append(_make_litellm_response("tool_calls", tool_calls=[
            _make_tool_call(f"s{i}", "submit_fix", {})
        ]))

    with patch("harness.agent.loop.litellm.completion", side_effect=responses), \
         patch("harness.agent.loop._start_mcp_session") as mock_sess_ctx:
        sess = _mock_mcp_session(["ace_invoke_lambda"])
        mock_sess_ctx.return_value.__aenter__ = AsyncMock(return_value=sess)
        mock_sess_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        asyncio.run(run_agent_loop(
            model="openai/gpt-4o", api_key="sk-test", base_url=None,
            context={"scenario_brief": "b", "instruction": "i",
                     "stack_outputs": {}, "template_path": "/t", "deployment_dir": "/d"},
            scenario_dir=str(tmp_path), run_id="tv3", harness_api_key="hk", max_turns=30,
            deploy_callback=deploy_cb, redeploy_callback=redeploy_cb,
            verify_callback=verify_cb, max_test_retries=5,
        ))

    assert verify_cb.call_count == 6  # 1 initial + 5 retries


def test_loop_routes_retry_submit_to_redeploy_callback(tmp_path):
    """First submit_fix uses deploy_callback; subsequent retries use redeploy_callback."""
    (tmp_path / "deployment").mkdir()
    from harness.agent.loop import run_agent_loop
    from unittest.mock import MagicMock

    deploy_cb = MagicMock(return_value=DeploymentResult(outcome="deploy_success"))
    redeploy_cb = MagicMock(return_value=DeploymentResult(outcome="deploy_success"))
    verify_results = [
        {"all_passed": False, "passed": [], "failed": [{"name": "t", "description": "", "short_error": "err"}]},
        {"all_passed": True, "passed": [], "failed": []},
    ]
    verify_cb = MagicMock(side_effect=verify_results)

    responses = [
        _make_litellm_response("tool_calls", tool_calls=[
            _make_tool_call("tc0", "write_file", {"path": "deployment/handler.py", "content": "# v1"})
        ]),
        _make_litellm_response("tool_calls", tool_calls=[
            _make_tool_call("tc1", "submit_fix", {})
        ]),
        _make_litellm_response("tool_calls", tool_calls=[
            _make_tool_call("tc2", "write_file", {"path": "deployment/handler.py", "content": "# v2"})
        ]),
        _make_litellm_response("tool_calls", tool_calls=[
            _make_tool_call("tc3", "submit_fix", {})
        ]),
    ]

    with patch("harness.agent.loop.litellm.completion", side_effect=responses), \
         patch("harness.agent.loop._start_mcp_session") as mock_sess_ctx:
        sess = _mock_mcp_session(["ace_invoke_lambda"])
        mock_sess_ctx.return_value.__aenter__ = AsyncMock(return_value=sess)
        mock_sess_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        asyncio.run(run_agent_loop(
            model="openai/gpt-4o", api_key="sk-test", base_url=None,
            context={"scenario_brief": "b", "instruction": "i",
                     "stack_outputs": {}, "template_path": "/t", "deployment_dir": "/d"},
            scenario_dir=str(tmp_path), run_id="tv4", harness_api_key="hk", max_turns=20,
            deploy_callback=deploy_cb, redeploy_callback=redeploy_cb,
            verify_callback=verify_cb, max_test_retries=5,
        ))

    assert deploy_cb.call_count == 1
    assert redeploy_cb.call_count == 1


def test_loop_relays_skipped_lambda_files_in_success_message():
    """Smoke test: verify the success branch reads skipped_lambda_files
    from deploy_result['result'] and includes the WARNING block in the
    message handed back to the model. Guards against accidental deletion
    of the relay wiring during refactors.
    """
    import harness.agent.loop as loop
    src = open(loop.__file__).read()
    assert "skipped_lambda_files" in src
    assert "had no matching S3Key" in src
