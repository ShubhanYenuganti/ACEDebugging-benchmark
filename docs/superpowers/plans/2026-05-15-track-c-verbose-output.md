# Track C — Verbose Reasoning + Write File Content Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When `--verbose` is passed, print the model's reasoning text before each tool call and print the first 30 lines of any `write_file` content so operators can see what the model is thinking and what it wrote.

**Architecture:** All changes are in `harness/agent/loop.py`. During streaming, text delta chunks are collected separately from tool-call chunks and printed as a `[thinking]` block if non-empty. After a successful `write_file` dispatch, the written content is printed with indentation and a line count. For non-streaming fallback (Anthropic extended thinking), the `thinking_blocks` field is extracted and printed similarly.

**Tech Stack:** Python 3.11, LiteLLM (OpenAI-compatible streaming API), asyncio

---

## File Structure

| File | Change |
|------|--------|
| `harness/agent/loop.py` | C1: collect and print reasoning chunks; C2: print write_file content preview |
| `tests/test_agent_loop.py` | Add tests: reasoning printed in verbose mode; write_file preview printed; non-verbose mode silent |

---

### Task 1: Print model reasoning text in verbose mode (C1)

**Files:**
- Modify: `harness/agent/loop.py`

- [ ] **Step 1: Understand current streaming structure**

In `loop.py`, find the streaming response handling. Currently it iterates chunks and collects tool call deltas. The text delta path (`delta.content`) is either ignored or printed inline. The goal is to collect all text deltas, join them, and print once as a labeled `[thinking]` block.

Look for a pattern like:
```python
async for chunk in response:
    delta = chunk.choices[0].delta
    if delta.tool_calls:
        ...
```

- [ ] **Step 2: Write failing tests**

In `tests/test_agent_loop.py`, add:

```python
import io
import sys

@pytest.mark.asyncio
async def test_verbose_prints_reasoning_text(mock_client, tmp_path, capsys):
    """[thinking] block printed in verbose mode when model returns text before tool call."""
    tool_call = MagicMock()
    tool_call.id = "call-1"
    tool_call.function.name = "write_file"
    tool_call.function.arguments = json.dumps({
        "path": "faulted.yaml",
        "content": "AWSTemplateFormatVersion: '2010-09-09'\nResources: {}\n",
    })

    response_with_text = MagicMock()
    response_with_text.choices = [MagicMock()]
    response_with_text.choices[0].message.content = "I will fix the StreamViewType."
    response_with_text.choices[0].message.tool_calls = [tool_call]

    # second response: submit_fix after write
    submit_call = MagicMock()
    submit_call.id = "call-2"
    submit_call.function.name = "submit_fix"
    submit_call.function.arguments = "{}"
    response_submit = MagicMock()
    response_submit.choices = [MagicMock()]
    response_submit.choices[0].message.content = None
    response_submit.choices[0].message.tool_calls = [submit_call]

    mock_client.chat.completions.create = AsyncMock(
        side_effect=[response_with_text, response_submit]
    )

    (tmp_path / "faulted.yaml").write_text("AWSTemplateFormatVersion: '2010-09-09'\nResources: {}\n")
    (tmp_path / "deployment").mkdir()

    await run_agent_loop(
        client=mock_client,
        model="test",
        context={"scenario_brief": "test", "instruction": "fix it", "stack_outputs": {}},
        mcp_tools=[],
        scenario_dir=str(tmp_path),
        verbose=True,
    )

    captured = capsys.readouterr()
    assert "[thinking]" in captured.out
    assert "StreamViewType" in captured.out


@pytest.mark.asyncio
async def test_non_verbose_no_reasoning_printed(mock_client, tmp_path, capsys):
    """No [thinking] block printed when verbose=False."""
    # same setup as above, verbose=False
    ...
    captured = capsys.readouterr()
    assert "[thinking]" not in captured.out
```

- [ ] **Step 3: Run to confirm tests fail**

```bash
pytest tests/test_agent_loop.py -k "reasoning" -v
```

Expected: FAIL — `[thinking]` not in captured output.

- [ ] **Step 4: Implement reasoning text collection and printing**

In `loop.py`, find the completion call. LiteLLM returns either a streaming async generator or a completed `ChatCompletion`. Handle both cases:

**Non-streaming path** (when `stream=False` or streaming not configured):

```python
message = response.choices[0].message
if verbose and message.content:
    reasoning = message.content.strip()
    if reasoning:
        truncated = reasoning[:500] + ("..." if len(reasoning) > 500 else "")
        print(f"  [thinking] {truncated}", flush=True)
tool_calls = message.tool_calls or []
```

**Streaming path** (when iterating chunks):

Before the chunk loop, initialize:

```python
reasoning_chunks = []
```

Inside the chunk loop, when `delta.content` is non-None:

```python
if delta.content:
    reasoning_chunks.append(delta.content)
```

After the loop ends, print the collected reasoning:

```python
if verbose and reasoning_chunks:
    reasoning = "".join(reasoning_chunks).strip()
    if reasoning:
        truncated = reasoning[:500] + ("..." if len(reasoning) > 500 else "")
        print(f"  [thinking] {truncated}", flush=True)
```

**Anthropic extended thinking** (when response has `thinking_blocks`):

```python
if verbose:
    for block in getattr(message, "thinking_blocks", []) or []:
        text = getattr(block, "thinking", None)
        if text:
            truncated = text.strip()[:500]
            print(f"  [thinking] {truncated}", flush=True)
            break  # print first block only
```

- [ ] **Step 5: Run reasoning tests**

```bash
pytest tests/test_agent_loop.py -k "reasoning" -v
```

Expected: PASS.

- [ ] **Step 6: Run full test suite**

```bash
pytest tests/
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add harness/agent/loop.py tests/test_agent_loop.py
git commit -m "feat(agent): print model reasoning text in verbose mode"
```

---

### Task 2: Print `write_file` content preview in verbose mode (C2)

**Files:**
- Modify: `harness/agent/loop.py`

- [ ] **Step 1: Write failing tests**

In `tests/test_agent_loop.py`, add:

```python
@pytest.mark.asyncio
async def test_verbose_prints_write_file_preview(mock_client, tmp_path, capsys):
    """First 30 lines of write_file content printed in verbose mode."""
    content_lines = [f"line {i}" for i in range(1, 41)]  # 40 lines
    file_content = "\n".join(content_lines)

    tool_call = MagicMock()
    tool_call.id = "call-1"
    tool_call.function.name = "write_file"
    tool_call.function.arguments = json.dumps({
        "path": "faulted.yaml",
        "content": file_content,
    })

    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = None
    response.choices[0].message.tool_calls = [tool_call]

    submit_call = MagicMock()
    submit_call.id = "call-2"
    submit_call.function.name = "submit_fix"
    submit_call.function.arguments = "{}"
    response_submit = MagicMock()
    response_submit.choices = [MagicMock()]
    response_submit.choices[0].message.content = None
    response_submit.choices[0].message.tool_calls = [submit_call]

    mock_client.chat.completions.create = AsyncMock(
        side_effect=[response, response_submit]
    )

    (tmp_path / "faulted.yaml").write_text(file_content)
    (tmp_path / "deployment").mkdir()

    await run_agent_loop(
        client=mock_client,
        model="test",
        context={"scenario_brief": "test", "instruction": "fix it", "stack_outputs": {}},
        mcp_tools=[],
        scenario_dir=str(tmp_path),
        verbose=True,
    )

    captured = capsys.readouterr()
    assert "[edit →" in captured.out
    assert "faulted.yaml" in captured.out
    assert "line 1" in captured.out
    assert "line 30" in captured.out
    # line 31+ should be summarized, not printed verbatim
    assert "line 31" not in captured.out
    assert "10 more lines" in captured.out


@pytest.mark.asyncio
async def test_verbose_write_file_preview_short_file(mock_client, tmp_path, capsys):
    """All lines printed when content has fewer than 30 lines."""
    content = "line 1\nline 2\nline 3\n"

    tool_call = MagicMock()
    tool_call.id = "call-1"
    tool_call.function.name = "write_file"
    tool_call.function.arguments = json.dumps({"path": "faulted.yaml", "content": content})

    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = None
    response.choices[0].message.tool_calls = [tool_call]

    submit_call = MagicMock()
    submit_call.id = "call-2"
    submit_call.function.name = "submit_fix"
    submit_call.function.arguments = "{}"
    response_submit = MagicMock()
    response_submit.choices = [MagicMock()]
    response_submit.choices[0].message.content = None
    response_submit.choices[0].message.tool_calls = [submit_call]

    mock_client.chat.completions.create = AsyncMock(side_effect=[response, response_submit])
    (tmp_path / "faulted.yaml").write_text(content)
    (tmp_path / "deployment").mkdir()

    await run_agent_loop(
        client=mock_client, model="test",
        context={"scenario_brief": "t", "instruction": "i", "stack_outputs": {}},
        mcp_tools=[], scenario_dir=str(tmp_path), verbose=True,
    )

    captured = capsys.readouterr()
    assert "line 1" in captured.out
    assert "line 3" in captured.out
    assert "more lines" not in captured.out


@pytest.mark.asyncio
async def test_non_verbose_no_write_preview(mock_client, tmp_path, capsys):
    """No [edit →] block printed when verbose=False."""
    content = "\n".join(f"line {i}" for i in range(1, 10))

    tool_call = MagicMock()
    tool_call.id = "call-1"
    tool_call.function.name = "write_file"
    tool_call.function.arguments = json.dumps({"path": "faulted.yaml", "content": content})

    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = None
    response.choices[0].message.tool_calls = [tool_call]

    submit_call = MagicMock()
    submit_call.id = "call-2"
    submit_call.function.name = "submit_fix"
    submit_call.function.arguments = "{}"
    response_submit = MagicMock()
    response_submit.choices = [MagicMock()]
    response_submit.choices[0].message.content = None
    response_submit.choices[0].message.tool_calls = [submit_call]

    mock_client.chat.completions.create = AsyncMock(side_effect=[response, response_submit])
    (tmp_path / "faulted.yaml").write_text(content)
    (tmp_path / "deployment").mkdir()

    await run_agent_loop(
        client=mock_client, model="test",
        context={"scenario_brief": "t", "instruction": "i", "stack_outputs": {}},
        mcp_tools=[], scenario_dir=str(tmp_path), verbose=False,
    )

    captured = capsys.readouterr()
    assert "[edit →" not in captured.out
```

- [ ] **Step 2: Run to confirm tests fail**

```bash
pytest tests/test_agent_loop.py -k "write_file_preview or write_preview" -v
```

Expected: FAIL — `[edit →` not in captured output.

- [ ] **Step 3: Implement write_file preview printing in `loop.py`**

Find where `dispatch_file_tool(name, args, scenario_dir)` is called (the `write_file` dispatch). After a successful dispatch (non-error result), add:

```python
if verbose and name == "write_file":
    written_content = args.get("content", "")
    file_path = args.get("path", "?")
    lines = written_content.splitlines()
    preview_lines = lines[:30]
    preview = "\n".join(f"    {line}" for line in preview_lines)
    suffix = f"\n    ... ({len(lines) - 30} more lines)" if len(lines) > 30 else ""
    print(f"  [edit → {file_path}]\n{preview}{suffix}", flush=True)
```

Place this block immediately after `content = dispatch_file_tool(name, args, scenario_dir)` (but only if `content` is not an error string — check that `dispatch_file_tool` returned a success string, i.e., `content.startswith("Written ")`).

Full pattern:

```python
content = dispatch_file_tool(name, args, scenario_dir)
if verbose and name == "write_file" and content.startswith("Written "):
    written_content = args.get("content", "")
    file_path = args.get("path", "?")
    lines = written_content.splitlines()
    preview = "\n".join(f"    {line}" for line in lines[:30])
    suffix = f"\n    ... ({len(lines) - 30} more lines)" if len(lines) > 30 else ""
    print(f"  [edit → {file_path}]\n{preview}{suffix}", flush=True)
```

- [ ] **Step 4: Run write_file preview tests**

```bash
pytest tests/test_agent_loop.py -k "write_file_preview or write_preview" -v
```

Expected: all three tests pass.

- [ ] **Step 5: Run full test suite**

```bash
pytest tests/
```

Expected: all tests pass.

- [ ] **Step 6: Manual smoke check (optional)**

Run a scenario with `--verbose` and confirm output shows `[thinking]` blocks and `[edit → faulted.yaml]` sections with indented YAML content.

- [ ] **Step 7: Commit**

```bash
git add harness/agent/loop.py tests/test_agent_loop.py
git commit -m "feat(agent): print write_file content preview in verbose mode"
```

---

## Self-Review

**Spec coverage:**
- Print model reasoning text labeled `[thinking]` in verbose mode: Task 1 ✓
- Truncate reasoning at 500 chars: Task 1 Step 4 ✓
- Handle Anthropic extended thinking blocks: Task 1 Step 4 ✓
- Print write_file content preview (first 30 lines, indented): Task 2 Step 3 ✓
- Print line count suffix when content > 30 lines: Task 2 Step 3 ✓
- No output when verbose=False: Task 1 Step 2 (non_verbose test) + Task 2 Step 1 (non_verbose test) ✓
- Only print preview on successful write (not error paths): Task 2 Step 3 (`content.startswith("Written ")` guard) ✓

**Placeholder scan:**
- Task 1 Step 2 non-verbose test body ends with `...` — implementer must copy the setup from the verbose test and change `verbose=True` to `verbose=False`. Pattern is clear; no guessing needed.
- All assertions are concrete strings (`"[thinking]"`, `"[edit →"`, `"more lines"`, specific line content).

**Type consistency:**
- `dispatch_file_tool` returns a string (either `"Written {rel} ({n} chars)."` or an error message) — the `startswith("Written ")` check is consistent with `tools.py` return format ✓
- `args.get("content", "")` — `args` is a dict parsed from `function.arguments` JSON; `content` key matches `write_file`'s parameter name in `FILE_TOOL_DEFINITIONS` ✓
- `args.get("path", "?")` — matches `write_file`'s `path` parameter ✓
