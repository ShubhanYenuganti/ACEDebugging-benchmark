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
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except (json.JSONDecodeError, ValueError):
                    args = {}

                if name in _FILE_TOOL_NAMES:
                    content = dispatch_file_tool(name, args, scenario_dir)
                    if name == "submit_fix":
                        submitted = True
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

    return submitted
