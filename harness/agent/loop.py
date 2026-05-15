import asyncio
import datetime
import json
import os
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

def _iter_json_objects(text: str):
    """Yield top-level JSON objects from text using brace-depth tracking."""
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                yield text[start:i + 1]
                start = -1


def _extract_text_tool_calls(content: str) -> list | None:
    """Parse tool calls from model text for models that emit JSON instead of structured calls."""
    if not content:
        return None
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.DOTALL).strip()
    candidates = []
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            candidates.append(obj)
    except (json.JSONDecodeError, ValueError):
        for raw in _iter_json_objects(text):
            try:
                obj = json.loads(raw)
                if isinstance(obj, dict):
                    candidates.append(obj)
            except (json.JSONDecodeError, ValueError):
                pass
    result = []
    for obj in candidates:
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
    return result or None


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
        retried_no_tool = False
        writes_made = 0
        retry_count = 0
        writes_since_last_submit = 0

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
                effective_tool_calls = _extract_text_tool_calls(msg.content)
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
                if not retried_no_tool:
                    retried_no_tool = True
                    if verbose:
                        print(f"[turn {turn}] no tool call extracted — retrying with stronger prompt", flush=True)
                    messages.append({
                        "role": "user",
                        "content": (
                            "You did NOT emit a valid tool call. Do not echo prior outputs. "
                            'Respond with ONLY a single JSON object: {"name": "<tool>", "arguments": {...}}. '
                            "Pick ANY diagnostic tool you have not yet used and call it now, "
                            "or call submit_fix if you are ready."
                        ),
                    })
                    continue
                if verbose:
                    print(f"[turn {turn}] model stopped (finish={finish})", flush=True)
                break
            retried_no_tool = False
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
                            elif retry_count > 0 and writes_since_last_submit == 0:
                                content = (
                                    "Error: no new file changes since last failed attempt. "
                                    "Revise your fix with write_file before calling submit_fix again."
                                )
                            else:
                                deploy_result = await asyncio.get_running_loop().run_in_executor(None, deploy_callback)
                                if deploy_result["success"]:
                                    submitted = True
                                    content = "Fix deployed successfully."
                                elif retry_count >= max_deploy_retries:
                                    # exit gracefully — model cannot fix within budget
                                    submitted = True
                                    content = (
                                        f"Maximum retries ({max_deploy_retries}) reached. "
                                        f"Last error: {deploy_result.get('error', 'unknown')}. Exiting."
                                    )
                                else:
                                    retry_count += 1
                                    writes_since_last_submit = 0
                                    content = (
                                        f"Deployment failed (attempt {retry_count}/{max_deploy_retries}): "
                                        f"{deploy_result.get('error', 'unknown')}. "
                                        "Read the error carefully, revise your fix with write_file, "
                                        "then call submit_fix again."
                                    )
                        else:
                            content = dispatch_file_tool(name, args, scenario_dir)
                            if name == "write_file" and content.startswith("Written "):
                                writes_made += 1
                                writes_since_last_submit += 1
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

                tool_results.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": content,
                })

            messages.extend(tool_results)

            if submitted:
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
