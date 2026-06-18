"""Emission gate: deploy arch01 known_good, run traffic, assert the
FrontHandlerFunction trace shows a DynamoDB call as a *nested subsegment*
(with aws_operation) via the X-Ray tools. Run against a live LocalStack.

Strict criterion: ace_get_trace on the front-handler trace must return the
FrontHandlerFunction segment containing a subsegment whose aws_operation is a
DynamoDB call (e.g. PutItem/UpdateItem). A flat sibling "dynamodb" segment is
NOT acceptable and triggers a loud WARNING + failure.
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

    # STRICT criterion: the FrontHandlerFunction segment must contain a
    # subsegment whose aws_operation is a DynamoDB call. This is the real-AWS
    # X-Ray shape and is what justifies the trace tools (operation-level signal).
    front_seg = next(s for s in front_handler_trace["segments"] if s["name"] == "FrontHandlerFunction")
    ddb_subsegments = [
        s for s in front_seg["subsegments"]
        if s.get("aws_operation") and s["name"].lower() in ("dynamodb", "amazon dynamodb")
    ]

    if ddb_subsegments:
        ops = [s["aws_operation"] for s in ddb_subsegments]
        print(f"DynamoDB captured as subsegment(s) of FrontHandlerFunction — aws_operation={ops}")
        print("EMISSION GATE PASSED")
        return

    # FALLBACK (documented, NOT a silent pass): some LocalStack builds flatten
    # subsegments into bare sibling segments named "dynamodb" with no
    # aws_operation. That loses the operation-level signal. We still detect it,
    # but loudly, because it means the strict criterion is NOT met.
    has_ddb_sibling = any(
        s["name"].lower() in ("dynamodb", "amazon dynamodb")
        for s in front_handler_trace["segments"]
        if s["name"] != "FrontHandlerFunction"
    )
    if has_ddb_sibling:
        print("WARNING: DynamoDB appeared only as a FLAT SIBLING segment, not a "
              "nested subsegment of FrontHandlerFunction. The strict gate criterion "
              "(subsegment with aws_operation) is NOT met — operation-level signal lost.")

    raise AssertionError(
        "STRICT GATE FAILED: FrontHandlerFunction has no DynamoDB subsegment with "
        f"aws_operation. Segments: {seg_names}, subsegments: {front_seg['subsegments']}"
    )


if __name__ == "__main__":
    sys.exit(main())
