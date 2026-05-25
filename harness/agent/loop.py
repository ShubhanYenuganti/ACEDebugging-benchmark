import asyncio
import datetime
import difflib
import json
import os
import pathlib
import re
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

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
from harness.shared.types import AssertionRunResult

_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)```", re.DOTALL)


def _extract_text_tool_calls(
    content: str, turn: int = 0
) -> tuple[list | None, list[dict]]:
    """Parse tool calls from fenced ```json``` blocks only.

    Returns (tool_calls | None, parse_failures).  Free-text brace scanning is
    intentionally omitted: models that emit valid JSON inside fenced blocks work
    correctly; models that don't still get the retry prompt.
    """
    failures: list[dict] = []
    if not content:
        return None, failures

    blocks = _FENCE_RE.findall(content)
    if not blocks:
        return None, failures

    result = []
    for raw_block in blocks:
        raw_block = raw_block.strip()
        try:
            obj = json.loads(raw_block)
        except (json.JSONDecodeError, ValueError) as exc:
            failures.append({
                "turn": turn,
                "raw_preview": raw_block[:120],
                "error": str(exc),
            })
            continue
        if not isinstance(obj, dict):
            continue
        # Reject echoed transport envelopes {"id":..., "type":..., "function": {...}}
        if "function" in obj and isinstance(obj["function"], dict) and "name" in obj["function"]:
            inner = obj["function"]
            name = inner.get("name")
            args = inner.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except (json.JSONDecodeError, ValueError):
                    args = {}
        elif "name" in obj:
            name = obj["name"]
            args = obj.get("arguments", obj.get("parameters", obj.get("input", {})))
        else:
            continue
        if not isinstance(name, str) or not name:
            continue
        if not isinstance(args, dict):
            args = {}
        result.append(SimpleNamespace(
            id=f"synth_{uuid.uuid4().hex[:8]}",
            function=SimpleNamespace(
                name=name,
                arguments=json.dumps(args),
            ),
        ))
    return result or None, failures


def _append_text_mode_failures(run_id: str, failures: list[dict]) -> None:
    """Forward parse-failure records to result_logger.log_text_mode_failure."""
    for entry in failures or []:
        try:
            result_logger.log_text_mode_failure(
                run_id,
                turn=entry.get("turn", 0),
                raw=entry.get("raw_preview", ""),
                error=entry.get("error", ""),
            )
        except OSError:
            pass


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
        "Workflow (strict): (1) use the diagnostic tools to probe the running system "
        "and identify the root cause; (2) edit at least one broken source file with "
        "write_file (under deployment/ or faulted.yaml); (3) call submit_fix to "
        "redeploy. submit_fix will refuse with an error if no write_file has been "
        "called this run — you must edit before submitting.\n\n"
        "TOOL CALL FORMAT (critical):\n"
        "If your runtime supports structured tool calls, use them. Otherwise emit a "
        "single JSON object per turn with EXACTLY this shape and nothing else:\n"
        '  {"name": "<tool_name>", "arguments": {<args>}}\n'
        "Do NOT wrap it in {\"id\":...,\"type\":...,\"function\":...} — that is the "
        "transport envelope, not what you emit. Do NOT echo prior tool calls. Every "
        "turn must issue a NEW tool call with fresh arguments, or call submit_fix.\n"
        "Keep diagnosing until you have enough evidence to fix the fault, then edit "
        "files and call submit_fix.\n\n"
        f"Template: {context['template_path']}\n"
        f"Deployment dir: {context['deployment_dir']}"
        + outputs
    )


def _format_test_summary(result: AssertionRunResult, attempt: int, max_retries: int) -> str:
    n_passed = len(result.passed)
    n_failed = len(result.failed)
    lines = [f"Tests: {n_passed} passed, {n_failed} failed."]
    if result.crash_reason:
        lines.append(f"(test runner error: {result.crash_reason})")
    for a in result.passed:
        lines.append(f"[{a.name}]: PASS")
    for a in result.failed:
        err = f" — {a.message}" if a.message else ""
        lines.append(f"[{a.name}]: FAIL{err}")
    lines.append(
        f"Revise your fix with write_file and call submit_fix again. "
        f"(Attempt {attempt} of {max_retries}.)"
    )
    return "\n".join(lines)


async def run_agent_loop(
    model: str,
    api_key: str | None,
    base_url: str | None,
    context: dict,
    scenario_dir: str,
    run_id: str,
    harness_api_key: str,
    extra_headers: dict | None = None,
    max_turns: int = 50,
    verbose: bool = False,
    deploy_callback=None,
    max_deploy_retries: int = 5,
    redeploy_callback=None,
    verify_callback=None,
    max_test_retries: int = 5,
    max_no_tool_failures: int = 3,
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
        consecutive_no_tool_failures = 0
        writes_made = 0
        retry_count = 0
        writes_since_last_submit = 0
        all_tests_passed = verify_callback is None
        test_retry_count = 0

        for turn in range(max_turns):
            kwargs: dict = dict(model=model, messages=messages, tools=tools, tool_choice="required")
            if api_key:
                kwargs["api_key"] = api_key
            if base_url:
                kwargs["api_base"] = base_url
            if extra_headers:
                kwargs["extra_headers"] = extra_headers

            if verbose:
                kwargs["stream"] = True
                chunks = []
                reasoning_chunks = []
                print(f"\n[turn {turn}]", flush=True)
                for chunk in litellm.completion(**kwargs):
                    chunks.append(chunk)
                    delta = chunk.choices[0].delta
                    if getattr(delta, "content", None):
                        reasoning_chunks.append(delta.content)
                if reasoning_chunks:
                    text = "".join(reasoning_chunks).strip()
                    if text:
                        truncated = text[:500] + ("..." if len(text) > 500 else "")
                        print(f"  [thinking] {truncated}", flush=True)
                print(flush=True)
                response = litellm.stream_chunk_builder(chunks, messages=messages)
            else:
                response = litellm.completion(**kwargs)
            msg = response.choices[0].message
            if verbose:
                for block in getattr(msg, "thinking_blocks", []) or []:
                    text = getattr(block, "thinking", None)
                    if text:
                        text_stripped = text.strip()
                        truncated = text_stripped[:500] + ("..." if len(text_stripped) > 500 else "")
                        print(f"  [thinking] {truncated}", flush=True)
                        break  # print first block only
            finish = response.choices[0].finish_reason

            synthesized = False
            effective_tool_calls = msg.tool_calls
            if not effective_tool_calls:
                effective_tool_calls, _text_failures = _extract_text_tool_calls(msg.content, turn=turn)
                if _text_failures:
                    _append_text_mode_failures(run_id, _text_failures)
                synthesized = effective_tool_calls is not None

            messages.append({
                "role": "assistant",
                "content": "" if synthesized else (msg.content or ""),
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in effective_tool_calls
                ] if effective_tool_calls else None,
            })

            if not effective_tool_calls:
                consecutive_no_tool_failures += 1
                content_preview = (msg.content or "")[:300]
                try:
                    result_logger.log_text_mode_failure(
                        run_id,
                        turn=turn,
                        raw=content_preview,
                        error="no_tool_call_extracted",
                    )
                except OSError:
                    pass
                if verbose:
                    print(
                        f"[turn {turn}] no tool call extracted "
                        f"(consecutive={consecutive_no_tool_failures}, "
                        f"max={max_no_tool_failures})",
                        flush=True,
                    )
                if consecutive_no_tool_failures >= max_no_tool_failures:
                    retry_msg = (
                        f"Warning: you have failed to emit a valid tool call "
                        f"{consecutive_no_tool_failures} times in a row.\n"
                        f"Your last output was:\n  {content_preview}\n"
                        "This run will exhaust its turn budget if you do not "
                        "emit a properly fenced JSON tool call. Use EXACTLY this format:\n"
                        '```json\n{"name": "<tool_name>", "arguments": {<args>}}\n```'
                    )
                else:
                    retry_msg = (
                        "You did not emit a valid tool call. Your output was:\n"
                        f"  {content_preview}\n"
                        "Respond with ONLY a fenced JSON block, nothing else:\n"
                        '```json\n{"name": "<tool_name>", "arguments": {<args>}}\n```\n'
                        'Do NOT wrap in {"id":...,"type":...,"function":...}. '
                        "Do NOT add prose."
                    )
                messages.append({"role": "user", "content": retry_msg})
                continue
            consecutive_no_tool_failures = 0
            if finish in ("stop", "end_turn") and not synthesized:
                if verbose:
                    print(f"[turn {turn}] model stopped (finish={finish})", flush=True)
                break

            tool_results = []
            for tc in effective_tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except (json.JSONDecodeError, ValueError):
                    args = {}

                if verbose:
                    _args_preview = ", ".join(f"{k}={repr(v)[:60]}" for k, v in args.items())
                    print(f"  → {name}({_args_preview})", flush=True)

                if name in _FILE_TOOL_NAMES:
                    if name == "submit_fix" and writes_made == 0:
                        content = (
                            "Error: submit_fix refused — no write_file calls have been "
                            "made this run. You must edit at least one file under "
                            "deployment/ or faulted.yaml before submitting. Continue "
                            "diagnosing and apply a fix with write_file first."
                        )
                    else:
                        if name == "submit_fix":
                            if deploy_callback is None:
                                dispatch_file_tool(name, args, scenario_dir)
                                submitted = True
                                content = "Fix submitted."
                            elif (retry_count > 0 or test_retry_count > 0) and writes_since_last_submit == 0:
                                content = (
                                    "Error: no new file changes since last failed attempt. "
                                    "Revise your fix with write_file before calling submit_fix again."
                                )
                            else:
                                active_deploy_cb = (
                                    redeploy_callback
                                    if (test_retry_count > 0 and redeploy_callback is not None)
                                    else deploy_callback
                                )
                                deploy_result = await asyncio.get_running_loop().run_in_executor(None, active_deploy_cb)
                                if deploy_result.success:
                                    submitted = True
                                    # Surface Lambda files that landed on disk but
                                    # had no matching S3Key in the template — they
                                    # were NOT deployed. Without this warning the
                                    # agent has no signal that its edit is orphaned.
                                    _skipped = deploy_result.skipped_lambda_files
                                    _skipped_msg = ""
                                    if _skipped:
                                        _skipped_msg = (
                                            "WARNING: the following Lambda file(s) you edited "
                                            "had no matching S3Key in the template and were "
                                            f"NOT deployed: {_skipped}. Their content is on "
                                            "disk but the live Lambda still runs the old code. "
                                            "Either rename your edit to match a template S3Key "
                                            "stem, or edit the template's S3Key to match your "
                                            "filename, then submit_fix again.\n\n"
                                        )
                                    if verify_callback is not None:
                                        verify_result = await asyncio.get_running_loop().run_in_executor(
                                            None, verify_callback
                                        )
                                        if verify_result.all_passed:
                                            all_tests_passed = True
                                            result_lines = ["Fix deployed and all tests passed."]
                                            for a in verify_result.passed:
                                                result_lines.append(f"[{a.name}]: PASS")
                                            for a in verify_result.failed:
                                                err = f" — {a.message}" if a.message else ""
                                                result_lines.append(f"[{a.name}]: FAIL{err}")
                                            content = _skipped_msg + "\n".join(result_lines)
                                        elif test_retry_count >= max_test_retries:
                                            all_tests_passed = False
                                            content = _skipped_msg + (
                                                f"Maximum test retries ({max_test_retries}) reached. "
                                                + _format_test_summary(verify_result, test_retry_count, max_test_retries)
                                            )
                                        elif verify_result.crash_reason == "timeout":
                                            submitted = False
                                            test_retry_count += 1
                                            # Don't reset writes_since_last_submit — the fix
                                            # may be correct; the test just ran out of time.
                                            content = _skipped_msg + (
                                                f"Tests timed out after 600s "
                                                f"(attempt {test_retry_count} of {max_test_retries}). "
                                                "The infrastructure may need more time to settle. "
                                                "Call submit_fix again to retry, or revise your fix "
                                                "with write_file if you believe a code change is needed."
                                            )
                                        else:
                                            submitted = False
                                            test_retry_count += 1
                                            writes_since_last_submit = 0
                                            content = _skipped_msg + _format_test_summary(
                                                verify_result, test_retry_count, max_test_retries
                                            )
                                    else:
                                        all_tests_passed = True
                                        content = _skipped_msg + "Fix deployed successfully."
                                elif retry_count >= max_deploy_retries:
                                    # exit gracefully — model cannot fix within budget
                                    submitted = True
                                    content = (
                                        f"Maximum retries ({max_deploy_retries}) reached. "
                                        f"Last error: {deploy_result.error or 'unknown'}. Exiting."
                                    )
                                else:
                                    retry_count += 1
                                    writes_since_last_submit = 0
                                    if deploy_result.outcome == "lint_fail" and deploy_result.lint_errors:
                                        lint_detail = "\n".join(
                                            f"  - [{e.get('rule', '?')}] {e.get('message', '')} "
                                            f"({e.get('location', 'unknown location')})"
                                            for e in deploy_result.lint_errors
                                        )
                                        failure_detail = (
                                            f"lint_fail in faulted.yaml:\n{lint_detail}"
                                        )
                                    else:
                                        failure_detail = deploy_result.error or deploy_result.outcome
                                    content = (
                                        f"Deployment failed (attempt {retry_count}/{max_deploy_retries}): "
                                        f"{failure_detail}. "
                                        "Read the error carefully, revise your fix with write_file, "
                                        "then call submit_fix again."
                                    )
                        else:
                            _old_content = ""
                            if name == "write_file":
                                _old_path = (pathlib.Path(scenario_dir) / args.get("path", "")).resolve()
                                if _old_path.exists():
                                    try:
                                        _old_content = _old_path.read_text(encoding="utf-8")
                                    except Exception:
                                        _old_content = ""
                            content = dispatch_file_tool(name, args, scenario_dir)
                            if name == "read_file":
                                print(f"  [read_file → {args.get('path', '?')}]", flush=True)
                            if name == "write_file" and content.startswith("Written "):
                                writes_made += 1
                                writes_since_last_submit += 1
                                new_content = args.get("content", "")
                                file_path = args.get("path", "?")
                                before_lines = _old_content.splitlines() if _old_content else []
                                after_lines = new_content.splitlines()
                                diff_lines = list(difflib.unified_diff(
                                    before_lines, after_lines,
                                    fromfile=f"a/{file_path}", tofile=f"b/{file_path}",
                                    lineterm="",
                                ))
                                changed = [
                                    l for l in diff_lines
                                    if (l.startswith("+") or l.startswith("-"))
                                    and not l.startswith(("+++", "---"))
                                ]
                                print(f"  [write_file → {file_path}]", flush=True)
                                for dl in changed[:10]:
                                    print(f"    {dl}", flush=True)
                                if len(changed) > 10:
                                    print(f"    ... ({len(changed) - 10} more changes)", flush=True)
                else:
                    try:
                        mcp_result = await session.call_tool(name, args)
                        raw = mcp_result.content[0].text if mcp_result.content else "{}"
                    except Exception as exc:
                        raw = f"Error calling {name}: {exc}"
                    content = raw
                    try:
                        output = json.loads(raw)
                    except (json.JSONDecodeError, ValueError):
                        output = {"raw": raw}
                    result_logger.log_tool_call(
                        run_id=run_id,
                        turn=turn,
                        tool=name,
                        input=args,
                        output=output,
                        timestamp=datetime.datetime.now(datetime.UTC).isoformat(),
                    )

                if name == "submit_fix":
                    print(f"\n[submit_fix]\n{content}\n", flush=True)

                tool_results.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": content,
                })

            messages.extend(tool_results)

            if submitted and (all_tests_passed or test_retry_count >= max_test_retries):
                break

            messages.append({
                "role": "user",
                "content": (
                    "Based on the tool output above, issue your NEXT tool call as a "
                    'single JSON object: {"name": "<tool_name>", "arguments": {...}}. '
                    "Do not repeat the previous call."
                ),
            })

    return submitted
