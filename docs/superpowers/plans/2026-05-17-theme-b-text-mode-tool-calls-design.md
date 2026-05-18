# Design: Theme B — Text-Mode Tool-Call Extraction

**Date:** 2026-05-17
**Breakpoint addressed:** 4.1
**Status:** Approved

---

## Problem Summary

`run_agent_loop` in `harness/agent/loop.py` handles models that emit tool calls as JSON text
rather than structured `tool_calls` objects (common with some Ollama/local models). The current
implementation has two failure modes:

**Failure mode 1 — Silent parse drop.**
`_extract_text_tool_calls` uses brace-depth tracking on free text. It fails silently when a
model emits:
- Single-quoted JSON (`{'name': 'foo'}`)
- Trailing commas (`{"name": "foo",}`)
- JS-style comments
- Prose mixed with JSON (`"Here is my call: {...}"`)

The call is silently dropped and the model gets a generic "you did not emit a tool call" message
with no indication of what was wrong with its output.

**Failure mode 2 — Silent loop termination.**
After exactly two consecutive no-tool-call turns, the loop executes `break`. The run exits with
`submitted=False`, `outcome=no_submission`, and no structured record of why it stopped. There
is no log entry for the failure and no way to distinguish "model chose to stop" from "extraction
failed twice."

---

## Approved Design

### Principle

Two separable changes, each independently deliverable:

1. **Replace free-text brace-scan with fence-only extraction.** The fallback parser only
   looks for tool call JSON inside ` ```json ``` ` fenced blocks. This is more conservative
   (won't false-positive on prose) and easier to reason about. Models that use fenced blocks
   continue to work. Models that emit raw JSON without fences get a precise error message.

2. **Replace `break` with bounded retry + `max_turns` exhaustion.** The loop never silently
   breaks on parse failure. Instead it tracks `consecutive_no_tool_failures` (int), adds an
   informative error message per failure (including a truncated preview of what the model
   actually emitted), and only stops when `max_turns` is exhausted. Every failure is logged
   to `text_mode_failures.json` in the run directory.

---

## Specification

### 1. `_extract_text_tool_calls` — fenced-block-only extraction

Replace the current implementation with one that:

1. Finds all ` ```json ... ``` ` fenced blocks in `content` using a non-greedy regex.
2. For each block, attempts `json.loads`.
3. Applies the same transport-envelope rejection logic (reject `{"id":..., "type":..., "function": {...}}`).
4. Returns the list of synthesised `SimpleNamespace` tool calls, or `None` if no valid blocks found.
5. Does **not** scan free text for `{...}` patterns — if no fenced block is found, returns `None` immediately.

**Rationale:** The brace-depth scan was intended to rescue models that emit bare JSON, but it
fires on any `{}` in prose and is fragile. Requiring fenced blocks is a clear, teachable
contract: the system prompt already instructs models to emit `{"name": ..., "arguments": ...}`.
Models that fence their output (the common case for instruction-tuned models) still work.
Models that don't fence still get the retry prompt.

**Parse-error logging (new):** When a fenced block is found but `json.loads` fails, emit one log
entry per failure to `results/<run_id>/text_mode_failures.json` via `result_logger`:

```json
[
  {
    "turn": 7,
    "raw_preview": "```json\n{'name': 'ace_invoke_lambda',\n",
    "error": "Expecting property name enclosed in double quotes: line 1 column 2 (char 1)"
  }
]
```

### 2. `run_agent_loop` — no silent `break` on parse failure

Replace the `retried_no_tool: bool` flag with `consecutive_no_tool_failures: int = 0`.

**New no-tool-call branch:**

```
if not effective_tool_calls:
    consecutive_no_tool_failures += 1
    log_text_mode_failure(run_id, turn, msg.content, parse_error)
    if consecutive_no_tool_failures >= max_no_tool_failures:
        # Still do NOT break — let max_turns exhaust naturally.
        # Just give the model an escalated message.
        error_msg = (escalated no-tool-call message with content preview)
    else:
        error_msg = (standard retry message with content preview)
    messages.append({"role": "user", "content": error_msg})
    continue   # ← never break
```

`max_no_tool_failures` is a new parameter to `run_agent_loop` with default `3`.

**Content preview in error messages:** Both the standard and escalated messages include a
truncated preview of what the model actually emitted (first 300 chars), so the model can
identify and correct its own formatting error.

Standard retry message (first and second failure):
```
You did not emit a valid tool call. Your output was:
  <first 300 chars of msg.content>
Respond with ONLY a fenced JSON block, nothing else:
```json
{"name": "<tool_name>", "arguments": {<args>}}
```
Do NOT wrap in {"id":...,"type":...,"function":...}. Do NOT add prose.
```

Escalated message (≥ max_no_tool_failures consecutive failures):
```
Warning: you have failed to emit a valid tool call N times in a row.
Your last output was:
  <first 300 chars of msg.content>
This run will exhaust its turn budget if you do not emit a properly fenced
JSON tool call. Use EXACTLY this format:
```json
{"name": "<tool_name>", "arguments": {<args>}}
```
```

**Reset:** `consecutive_no_tool_failures` resets to 0 whenever a successful tool call is
extracted (either structured or text-mode).

### 3. `result_logger.py` — new `log_text_mode_failure`

New function:

```python
def log_text_mode_failure(run_id: str, turn: int, raw: str, error: str) -> None:
    """Append one text-mode parse failure record to results/<run_id>/text_mode_failures.json."""
```

Appends to a JSON array in `results/<run_id>/text_mode_failures.json`. Creates the file on
first call. Each record: `{"turn": int, "raw_preview": str (first 300 chars), "error": str}`.

---

## Affected Files

| File | Change |
|---|---|
| `harness/agent/loop.py` | Replace `_extract_text_tool_calls`; replace `retried_no_tool: bool` with `consecutive_no_tool_failures: int`; add `max_no_tool_failures` param; remove `break`; call `log_text_mode_failure` |
| `harness/shared/result_logger.py` | Add `log_text_mode_failure(run_id, turn, raw, error)` |
| `tests/test_agent_loop.py` | Add tests for: fenced-block extraction, free-text rejection with log, no-break behaviour, escalated message, consecutive failure reset |

---

## Out of Scope

- Supporting `json5`, trailing commas, or single-quoted JSON. Models that emit malformed JSON
  should be told to fix their output, not silently patched.
- Changing `tool_choice="required"`. That's a LiteLLM parameter that should already prevent
  well-behaved models from returning no tool call.
- Altering the structured-tool-call path (when `msg.tool_calls` is populated by LiteLLM).

---

## Success Criteria

1. A model that emits a fenced JSON block with a valid tool call has it executed correctly.
2. A model that emits a fenced JSON block with single-quoted JSON gets a retry message that
   includes a truncated preview of what it emitted and the parse error.
3. A parse failure is appended to `text_mode_failures.json`.
4. After 3 consecutive no-tool-call turns, the loop continues (does not `break`) and the
   model receives an escalated message.
5. After `max_turns` exhaustion, `run_agent_loop` returns `False` (not submitted) — identical
   behaviour to today but reached via turn exhaustion rather than `break`.
6. `consecutive_no_tool_failures` resets to 0 after any successful tool dispatch.
7. All existing `tests/test_agent_loop.py` tests pass without modification.
