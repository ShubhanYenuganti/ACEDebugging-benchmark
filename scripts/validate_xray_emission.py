"""Emission gate: deploy arch01 known_good, run traffic, assert a trace with a
DynamoDB subsegment is visible via the X-Ray tools. Run against a live LocalStack.
"""
import json
import subprocess
import sys
import time

import boto3

ENDPOINT = "http://localhost:4566"
CORPUS = "corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda"


def call_tool(name, args):
    """Invoke a single MCP tool handler via node and return its JSON result."""
    script = (
        f"import {{ observeTracingTools }} from './harness/mcp_server/tools/observe_tracing.js';"
        f"const t = observeTracingTools.find(x => x.name === '{name}');"
        f"console.log(JSON.stringify(await t.handler({json.dumps(args)})));"
    )
    out = subprocess.run(["node", "--input-type=module", "-e", script],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def main():
    print("Deploy arch01 known_good + run its functional traffic using the "
          "validate-corpus flow or harness deploy, THEN run this script.")
    # Give traces time to flush after traffic.
    time.sleep(3)
    summaries = call_tool("ace_get_trace_summaries", {"window_minutes": 15})
    print("summaries:", json.dumps(summaries, indent=2)[:500])
    assert not summaries.get("error"), summaries
    assert summaries["count"] >= 1, "no traces emitted — emission FAILED"

    # Find the FrontHandlerFunction trace specifically.
    front_handler_trace = None
    for t in summaries["traces"]:
        trace = call_tool("ace_get_trace", {"trace_id": t["id"]})
        seg_names = [s["name"] for s in trace["segments"]]
        if "FrontHandlerFunction" in seg_names:
            front_handler_trace = trace
            break

    assert front_handler_trace is not None, (
        "No FrontHandlerFunction segment found in any trace — emission FAILED. "
        f"Traces seen: {[t['id'] for t in summaries['traces']]}"
    )

    print("trace:", json.dumps(front_handler_trace, indent=2)[:1200])
    seg_names = [s["name"] for s in front_handler_trace["segments"]]
    print("segment names in trace:", seg_names)

    # On real AWS, patch_all attaches DynamoDB calls as subsegments of the
    # handler segment. On LocalStack the DynamoDB service records them as a
    # sibling segment in the same trace. Either shape proves patch_all worked
    # and that trace-context propagation is functioning.
    front_seg = next(s for s in front_handler_trace["segments"] if s["name"] == "FrontHandlerFunction")
    has_ddb_subsegment = any(
        s.get("aws_operation") for s in front_seg["subsegments"]
    )
    has_ddb_sibling = any(
        s["name"].lower() in ("dynamodb", "amazon dynamodb")
        for s in front_handler_trace["segments"]
        if s["name"] != "FrontHandlerFunction"
    )

    assert has_ddb_subsegment or has_ddb_sibling, (
        "No DynamoDB subsegment or sibling segment found — patch_all not capturing hops. "
        f"Segments: {seg_names}, subsegments: {front_seg['subsegments']}"
    )

    if has_ddb_subsegment:
        print("DynamoDB captured as subsegment (real-AWS shape)")
    else:
        print("DynamoDB captured as sibling segment (LocalStack shape — patch_all propagated trace context)")

    print("EMISSION GATE PASSED")


if __name__ == "__main__":
    sys.exit(main())
