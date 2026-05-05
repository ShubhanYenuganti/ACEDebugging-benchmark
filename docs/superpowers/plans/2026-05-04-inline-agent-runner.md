# Inline Agent Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `--model`, `--api-key`, and `--base-url` flags to `harness/run.py` so the harness can drive any LLM (Anthropic, OpenAI, Gemini, Ollama, Qwen, GLM, Kimi, etc.) through a scenario end-to-end without requiring Claude Code or an external model script.

**Architecture:** A new `harness/agent/` package contains all agent logic. **LiteLLM** is the universal adapter — it normalizes every provider's API to the OpenAI format and converts OpenAI-format tool definitions into each provider's native format, so tool dispatch is identical regardless of which model is running. Tools are defined once in OpenAI function-calling format: the 12 MCP probe/observe tools (converted from MCP schema), Python-native file tools (`read_file`, `write_file`, `list_directory` scoped to `deployment/` and `faulted.yaml`), and a `submit_fix` tool that writes the redeployment signal file. The agent loop spawns the existing Node.js MCP server as a stdio subprocess via the Python `mcp` package, discovers tools at runtime, and runs async. `run.py` starts the loop in a daemon thread after printing context; the existing signal-file polling, deployment handler, verify loop, and scorer are all unchanged.

**Tech Stack:** Python 3.11, `litellm>=1.0.0` (new), `mcp>=1.0.0` (new), `anthropic` (already installed), Node.js v22+ (existing MCP server)

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `harness/agent/__init__.py` | package marker |
| Create | `harness/agent/tools.py` | OpenAI-format tool definitions; MCP→OpenAI schema conversion; file tool + submit_fix dispatch |
| Create | `harness/agent/loop.py` | async LiteLLM agent loop; MCP server spawn; tool dispatch; tool-call logging |
| Modify | `pyproject.toml` | add `litellm>=1.0.0`, `mcp>=1.0.0` |
| Modify | `harness/run.py` | add `--model`, `--api-key`, `--base-url` args; start agent daemon thread before signal-file poll |
| Create | `tests/test_agent_loop.py` | unit tests for tool conversion, file dispatch, loop termination, max_turns, signal file |

---

### Task 1: Package scaffolding + dependencies

**Files:**
- Create: `harness/agent/__init__.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Create package directory and marker**

```bash
mkdir -p harness/agent
touch harness/agent/__init__.py
```

- [ ] **Step 2: Add `litellm` and `mcp` to `pyproject.toml`**

In `pyproject.toml`, extend `dependencies`:

```toml
dependencies = [
    "boto3>=1.34.0",
    "pytest>=8.0.0",
    "pytest-mock>=3.12.0",
    "cfn-lint>=0.87.0",
    "requests>=2.31.0",
    "python-dotenv>=1.0.0",
    "mcp>=1.0.0",
    "litellm>=1.0.0",
]
```

- [ ] **Step 3: Install**

```bash
pip install -e .
```

Expected: completes without error.

- [ ] **Step 4: Verify imports**

```bash
python -c "import litellm; from mcp import ClientSession; import harness.agent; print('ok')"
```

Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add harness/agent/__init__.py pyproject.toml
git commit -m "feat(agent): scaffold harness/agent package, add litellm + mcp dependencies"
```

---

### Task 2: `harness/agent/tools.py` — tool definitions and file dispatch

**Files:**
- Create: `harness/agent/tools.py`

This module owns:
1. Converting a MCP tool object to an OpenAI-format tool dict.
2. Filtering out score tools the model should never call.
3. OpenAI-format definitions for `read_file`, `write_file`, `list_directory`, `submit_fix`.
4. A synchronous dispatcher for all four file/submit tools with path-boundary enforcement.

**OpenAI tool format used throughout:**
```python
{
    "type": "function",
    "function": {
        "name": str,
        "description": str,
        "parameters": {   # JSON Schema object — same shape as MCP inputSchema
            "type": "object",
            "properties": {...},
            "required": [...]
        }
    }
}
```

LiteLLM converts this format automatically to Anthropic, Gemini, or any other provider's native format.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_agent_loop.py`:

```python
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


def test_file_tool_definitions_are_openai_format():
    from harness.agent.tools import FILE_TOOL_DEFINITIONS
    for tool in FILE_TOOL_DEFINITIONS:
        assert tool["type"] == "function"
        assert "name" in tool["function"]
        assert "parameters" in tool["function"]
    names = [t["function"]["name"] for t in FILE_TOOL_DEFINITIONS]
    assert set(names) == {"read_file", "write_file", "list_directory", "submit_fix"}
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_agent_loop.py -v --tb=line 2>&1 | head -15
```

Expected: `ModuleNotFoundError: No module named 'harness.agent.tools'`

- [ ] **Step 3: Write `harness/agent/tools.py`**

```python
import json
import os
import pathlib

SIGNAL_FILE = os.environ.get("ACE_BENCH_SIGNAL_FILE", "/tmp/ace-bench-update.json")

_BLOCKED_READS = {"fault_manifest.json", "known_good.yaml"}
_SUBMIT_TOOL = "submit_fix"
_FILE_TOOL_NAMES = {"read_file", "write_file", "list_directory", "submit_fix"}


def mcp_to_openai_tool(mcp_tool) -> dict:
    """Convert an MCP tool object to an OpenAI-format tool dict."""
    return {
        "type": "function",
        "function": {
            "name": mcp_tool.name,
            "description": mcp_tool.description or "",
            "parameters": mcp_tool.inputSchema,
        },
    }


def filter_model_tools(tools: list[dict]) -> list[dict]:
    """Remove score tools the model must not call."""
    blocked = {"ace_verify_fix", "ace_score_run"}
    return [t for t in tools if t["function"]["name"] not in blocked]


FILE_TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read a file within the scenario directory. "
                "Path is relative to the scenario root."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path from scenario root, e.g. deployment/lambda/handler.py",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Write content to a file. "
                "Only files inside deployment/ or faulted.yaml may be written."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path from scenario root",
                    },
                    "content": {
                        "type": "string",
                        "description": "Full file content to write",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List entries in a directory within the scenario.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path from scenario root",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_fix",
            "description": (
                "Submit your fix for redeployment. "
                "Call this once you have edited all necessary files and are ready to deploy. "
                "Your first call is your final scored submission."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


def dispatch_file_tool(name: str, inputs: dict, scenario_dir: str) -> str:
    scenario_root = pathlib.Path(scenario_dir).resolve()

    def _safe_resolve(rel: str) -> pathlib.Path | None:
        target = (scenario_root / rel).resolve()
        try:
            target.relative_to(scenario_root)
        except ValueError:
            return None
        return target

    if name == "read_file":
        rel = inputs.get("path", "")
        if pathlib.Path(rel).name in _BLOCKED_READS:
            return f"Error: access to {pathlib.Path(rel).name} is not allowed."
        target = _safe_resolve(rel)
        if target is None:
            return "Error: path traversal not allowed."
        if not target.exists():
            return f"Error: {rel} does not exist."
        return target.read_text(encoding="utf-8")

    if name == "write_file":
        rel = inputs.get("path", "")
        content = inputs.get("content", "")
        norm = rel.replace("\\", "/")
        if not (norm.startswith("deployment/") or norm == "faulted.yaml"):
            return f"Error: writing to {rel} is not allowed. Only deployment/ files and faulted.yaml may be modified."
        target = _safe_resolve(rel)
        if target is None:
            return "Error: path traversal not allowed."
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"Written {rel} ({len(content)} chars)."

    if name == "list_directory":
        rel = inputs.get("path", "")
        target = _safe_resolve(rel)
        if target is None:
            return "Error: path traversal not allowed."
        if not target.is_dir():
            return f"Error: {rel} is not a directory."
        entries = sorted(
            ("DIR  " if (target / e).is_dir() else "FILE ") + e
            for e in os.listdir(target)
        )
        return "\n".join(entries) if entries else "(empty)"

    if name == "submit_fix":
        pathlib.Path(SIGNAL_FILE).write_text(
            json.dumps({"trigger": "update-stack"}), encoding="utf-8"
        )
        return "Fix submitted. Redeployment triggered."

    return f"Error: unknown file tool '{name}'."
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_agent_loop.py -v --tb=short
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/agent/tools.py tests/test_agent_loop.py
git commit -m "feat(agent): tools.py — OpenAI-format tool defs, MCP conversion, file dispatch"
```

---

### Task 3: `harness/agent/loop.py` — universal LiteLLM agent loop

**Files:**
- Create: `harness/agent/loop.py`

LiteLLM takes OpenAI-format tool definitions and converts them to each provider's format automatically. The loop response is always in OpenAI format: `response.choices[0].message.tool_calls` is a list of tool-call objects with `.id`, `.function.name`, and `.function.arguments` (JSON string). This is identical regardless of whether the underlying model is Claude, GPT, Gemini, or Qwen on Ollama.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_agent_loop.py`:

```python
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


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
         patch("harness.agent.loop._start_mcp_session") as mock_sess_ctx:

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
    # third positional arg (or kwarg 'tool') is the tool name
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
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_agent_loop.py -v --tb=line -k "loop" 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'harness.agent.loop'`

- [ ] **Step 3: Write `harness/agent/loop.py`**

```python
import asyncio
import datetime
import json
import os
from contextlib import asynccontextmanager

import litellm
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from harness.agent.tools import (
    FILE_TOOL_DEFINITIONS,
    _FILE_TOOL_NAMES,
    dispatch_file_tool,
    filter_model_tools,
    mcp_to_openai_tool,
)
from harness.shared import result_logger

_MCP_SERVER_SCRIPT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "mcp_server", "index.js")
)


@asynccontextmanager
async def _start_mcp_session(harness_api_key: str):
    params = StdioServerParameters(
        command="node",
        args=[_MCP_SERVER_SCRIPT],
        env={
            **os.environ,
            "HARNESS_API_KEY": harness_api_key,
            "LOCALSTACK_ENDPOINT": os.environ.get("LOCALSTACK_ENDPOINT", "http://localhost:4566"),
        },
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def _build_system(context: dict) -> str:
    outputs = ""
    if context.get("stack_outputs"):
        outputs = "\nStack outputs:\n" + "\n".join(
            f"  {k}: {v}" for k, v in context["stack_outputs"].items()
        )
    return (
        "You are evaluating a deployed AWS infrastructure system that has a fault injected.\n"
        "Use the diagnostic tools to probe the running system, identify the root cause, "
        "edit files with write_file, and call submit_fix when ready to redeploy.\n"
        f"Template: {context['template_path']}\n"
        f"Deployment dir: {context['deployment_dir']}"
        + outputs
    )


async def run_agent_loop(
    model: str,
    api_key: str | None,
    base_url: str | None,
    context: dict,
    scenario_dir: str,
    run_id: str,
    harness_api_key: str,
    max_turns: int = 50,
) -> bool:
    """Drive the model through the scenario. Returns True if submit_fix was called."""
    async with _start_mcp_session(harness_api_key) as session:
        mcp_list = await session.list_tools()
        tools = filter_model_tools(
            [mcp_to_openai_tool(t) for t in mcp_list.tools]
        ) + FILE_TOOL_DEFINITIONS

        messages = [
            {"role": "system", "content": _build_system(context)},
            {"role": "user", "content": f"{context['scenario_brief']}\n\n{context['instruction']}"},
        ]
        submitted = False

        for turn in range(max_turns):
            kwargs: dict = dict(model=model, messages=messages, tools=tools, tool_choice="auto")
            if api_key:
                kwargs["api_key"] = api_key
            if base_url:
                kwargs["api_base"] = base_url

            response = litellm.completion(**kwargs)
            msg = response.choices[0].message
            finish = response.choices[0].finish_reason

            # Append assistant turn (convert to plain dict for message history)
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in (msg.tool_calls or [])
                ] or None,
            })

            if finish in ("stop", "end_turn") or not msg.tool_calls:
                break

            tool_results = []
            for tc in msg.tool_calls:
                name = tc.function.name
                args = json.loads(tc.function.arguments or "{}")

                if name in _FILE_TOOL_NAMES:
                    content = dispatch_file_tool(name, args, scenario_dir)
                    if name == "submit_fix":
                        submitted = True
                else:
                    mcp_result = await session.call_tool(name, args)
                    raw = mcp_result.content[0].text if mcp_result.content else "{}"
                    content = raw
                    result_logger.log_tool_call(
                        run_id=run_id,
                        turn=turn,
                        tool=name,
                        input=args,
                        output=json.loads(raw) if raw.strip().startswith("{") else {"raw": raw},
                        timestamp=datetime.datetime.utcnow().isoformat(),
                    )

                tool_results.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": content,
                })

            messages.extend(tool_results)

            if submitted:
                break

    return submitted
```

- [ ] **Step 4: Run all agent loop tests**

```bash
pytest tests/test_agent_loop.py -v --tb=short
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add harness/agent/loop.py tests/test_agent_loop.py
git commit -m "feat(agent): loop.py — universal LiteLLM agent loop with MCP + file tools"
```

---

### Task 4: Wire into `harness/run.py`

**Files:**
- Modify: `harness/run.py`

Add `--model`, `--api-key`, `--base-url` arguments. If `--model` is set, start the async agent loop in a daemon thread immediately after context is printed. The existing signal-file polling loop, deployment handler, verify loop, and scorer are untouched.

- [ ] **Step 1: Add import at the top of `harness/run.py`**

After the existing imports, add:

```python
from harness.agent.loop import run_agent_loop
```

- [ ] **Step 2: Add three CLI arguments in the `argparse` block**

After the existing `--run-id` argument:

```python
    parser.add_argument(
        "--model",
        default=None,
        metavar="PROVIDER/MODEL",
        help=(
            "LiteLLM model string to use as the evaluated agent "
            "(e.g. anthropic/claude-sonnet-4-6, openai/gpt-4o, "
            "gemini/gemini-1.5-pro, ollama/qwen2.5). "
            "If omitted, the harness waits for an external agent to write the signal file."
        ),
    )
    parser.add_argument(
        "--api-key",
        default=None,
        metavar="KEY",
        help=(
            "API key for the model provider. "
            "Falls back to the provider-specific env var "
            "(ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY, etc.). "
            "Not required for local Ollama."
        ),
    )
    parser.add_argument(
        "--base-url",
        default=None,
        metavar="URL",
        help=(
            "Custom API base URL (e.g. http://localhost:11434 for Ollama, "
            "or a self-hosted OpenAI-compatible endpoint)."
        ),
    )
```

- [ ] **Step 3: Start agent thread after `_print_context(ctx)`**

Locate the `_print_context(ctx)` call in `main()`. Immediately after it, add:

```python
    # If --model is provided, start the inline agent runner as a daemon thread.
    # The agent calls submit_fix → writes the signal file → main poll loop below detects it.
    if args.model:
        _harness_key = os.environ.get("HARNESS_API_KEY", "")
        _api_key = args.api_key  # None is fine for Ollama; LiteLLM falls back to env vars
        _base_url = args.base_url

        def _run_agent():
            asyncio.run(
                run_agent_loop(
                    model=args.model,
                    api_key=_api_key,
                    base_url=_base_url,
                    context=ctx,
                    scenario_dir=scenario_dir,
                    run_id=run_id,
                    harness_api_key=_harness_key,
                )
            )

        threading.Thread(target=_run_agent, daemon=True, name="agent-runner").start()
```

- [ ] **Step 4: Verify import and help output**

```bash
python -c "from harness.run import main; print('import ok')"
python harness/run.py --help | grep -A2 "\-\-model"
```

Expected:
```
import ok
  --model PROVIDER/MODEL
                        LiteLLM model string to use as the evaluated agent ...
```

- [ ] **Step 5: Commit**

```bash
git add harness/run.py
git commit -m "feat(agent): wire --model/--api-key/--base-url into run.py"
```

---

### Task 5: Full suite verification + update RUN.md

- [ ] **Step 1: Run all agent tests**

```bash
pytest tests/test_agent_loop.py -v --tb=short
```

Expected: all PASS.

- [ ] **Step 2: Run existing test suite — check for regressions**

```bash
pytest tests/test_scoring.py tests/test_shared.py tests/test_runner.py tests/test_verify.py -v --tb=short 2>&1 | tail -15
```

Expected: same pass/fail as before this branch.

- [ ] **Step 3: Lint**

```bash
ruff check harness/agent/ tests/test_agent_loop.py
```

Fix any issues, then commit:

```bash
git add -u && git commit -m "fix(agent): ruff lint fixes"
```

- [ ] **Step 4: Update RUN.md — model runner section**

In `RUN.md`, replace the existing **Step 3 — Add an LLM Model to Evaluate** section with:

````markdown
## Step 3 — Add an LLM Model to Evaluate

Pass `--model` (a LiteLLM provider/model string) and, where required, `--api-key` to
`run.py`. The harness runs the model in-process with full access to the registered MCP
diagnostic tools and the scenario's `deployment/` directory.

### Supported providers (examples)

| Provider | `--model` | Auth |
|----------|-----------|------|
| Anthropic | `anthropic/claude-sonnet-4-6` | `--api-key` or `ANTHROPIC_API_KEY` |
| OpenAI | `openai/gpt-4o` | `--api-key` or `OPENAI_API_KEY` |
| Google Gemini | `gemini/gemini-1.5-pro` | `--api-key` or `GEMINI_API_KEY` |
| Ollama (local) | `ollama/qwen2.5` | none — use `--base-url http://localhost:11434` |
| Ollama (local) | `ollama/glm4` | none — use `--base-url http://localhost:11434` |
| Any OpenAI-compatible | `openai/your-model` | `--api-key` + `--base-url` |

### Example invocations

```bash
# Anthropic
python harness/run.py scenarios/arch01_fault01_security/ \
  --model anthropic/claude-sonnet-4-6 \
  --api-key sk-ant-...

# OpenAI
python harness/run.py scenarios/arch01_fault01_security/ \
  --model openai/gpt-4o \
  --api-key sk-...

# Gemini
python harness/run.py scenarios/arch01_fault01_security/ \
  --model gemini/gemini-1.5-pro \
  --api-key AIza...

# Ollama (Qwen, GLM, Gemma — no key needed)
python harness/run.py scenarios/arch01_fault01_security/ \
  --model ollama/qwen2.5 \
  --base-url http://localhost:11434
```

Without `--model`, the harness prints context to stdout and waits up to 30 minutes for
an external agent to write `/tmp/ace-bench-update.json`.
````

```bash
git add RUN.md && git commit -m "docs: update RUN.md with universal model runner usage"
```

---

## Self-Review Against Spec

### Spec coverage check

| Requirement | Task |
|---|---|
| Supports Anthropic models | Tasks 3, 4 — LiteLLM `anthropic/` prefix |
| Supports OpenAI models | Tasks 3, 4 — LiteLLM `openai/` prefix |
| Supports Gemini | Tasks 3, 4 — LiteLLM `gemini/` prefix |
| Supports Ollama (Qwen, GLM, Gemma, etc.) | Tasks 3, 4 — LiteLLM `ollama/` + `--base-url` |
| Single tool definition format regardless of model | Task 2 — OpenAI format; LiteLLM converts |
| `--model` CLI arg | Task 4 |
| `--api-key` CLI arg (falls back to env var per provider) | Task 4 |
| `--base-url` for self-hosted / Ollama | Task 4 |
| MCP probe + observe tools available to model | Task 3 (`_start_mcp_session` + `list_tools`) |
| Score tools (`ace_verify_fix`, `ace_score_run`) blocked | Task 2 (`filter_model_tools`) |
| `fault_manifest.json` and `known_good.yaml` read-blocked | Task 2 (`_BLOCKED_READS`) |
| `write_file` restricted to `deployment/` and `faulted.yaml` | Task 2 |
| `submit_fix` writes signal file | Task 2 |
| MCP tool calls logged; file tool calls not logged | Task 3 |
| Loop exits on `stop`/`end_turn` | Task 3, tested |
| Loop exits after `max_turns` (default 50) | Task 3, tested |
| Existing signal-file polling + downstream steps unchanged | Task 4 (agent runs in daemon thread) |
| `litellm` and `mcp` added as project dependencies | Task 1 |
| RUN.md updated with all provider examples | Task 5 |

### Signature consistency

- `run_agent_loop(model, api_key, base_url, context, scenario_dir, run_id, harness_api_key, max_turns)` — matches call in Task 4 exactly.
- `dispatch_file_tool(name, inputs, scenario_dir)` — matches usage in `loop.py` and all tests.
- `filter_model_tools(tools: list[dict])` — takes OpenAI-format dicts, matches usage in `loop.py`.
- `mcp_to_openai_tool(mcp_tool)` — takes MCP tool object, returns OpenAI-format dict, matches `loop.py`.
- `FILE_TOOL_DEFINITIONS` — list of OpenAI-format dicts, appended to filtered MCP tools in `loop.py`.
- `_FILE_TOOL_NAMES` — set of strings imported in `loop.py` for dispatch branching.
