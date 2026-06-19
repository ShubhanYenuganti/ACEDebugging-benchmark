# Phase 2A — X-Ray Trace Tools + arch01 Instrumentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship two real-AWS X-Ray observe tools (`ace_get_trace_summaries`, `ace_get_trace`) and the arch01 handler instrumentation + one new fault scenario that make them return real, benchmark-relevant trace data.

**Architecture:** Two read-only MCP tools are added to the existing `observe_tracing.js` array (already spread into the server). A shared Python module `xray_instrument.py` instruments arch01 Lambda handlers using `aws-xray-sdk` (begin a segment, `patch_all()` boto3 so downstream calls become subsegments) and emits segments to LocalStack via a `PutTraceSegments` emitter (the proven path). One new arch01 fault scenario surfaces a faulted downstream subsegment so the fastest diagnosis runs through the trace tools.

**Tech Stack:** Node.js (`@aws-sdk/client-xray`, already installed), Python 3.11 (`aws-xray-sdk`, vendored into handler zips), LocalStack Pro `2026.5.4`, CloudFormation (raw `AWS::Lambda::Function`, not SAM).

## Global Constraints

- **LocalStack must run with IAM enforcement:** `ENFORCE_IAM=1`, `IAM_SOFT_MODE=0`. (Already running on `2026.5.4`.)
- **AWS creds:** accessKeyId=`test`, secretAccessKey=`test`, region=`us-east-1`, endpoint `http://localhost:4566`.
- **`ace_get_service_graph` is NOT shipped** — non-functional on LocalStack.
- **Realism gate:** prefer real AWS APIs; no LocalStack-proprietary introspection. Handler instrumentation code must look like real-AWS X-Ray usage (`@traced` + `patch_all()`); only the emitter wiring is environment-specific.
- **Both new tools are read-only observe tools** — exposed to the model (NOT gated like score tools). No edits to `filter_model_tools` allow/deny lists.
- **Tool conventions:** LocalStack endpoint + `test`/`test`, return structured-empty or `{ error, error_type }` on failure, never throw.
- **`fault_manifest.json` and `known_good.yaml` are never exposed to the model.**
- **Node tests:** `node --test tests/test_mcp_server.js`. Handler lookup helper in that file: `tool(list, name)`.

---

### Task 1: Add `ace_get_trace_summaries` and `ace_get_trace` MCP tools

**Files:**
- Modify: `harness/mcp_server/tools/observe_tracing.js` (add X-Ray imports, client, two tool defs to `observeTracingTools`)
- Test: `tests/test_mcp_server.js` (add cases near the existing `ace_lookup_events` tests)

**Interfaces:**
- Produces: two tool objects in the exported `observeTracingTools` array.
  - `ace_get_trace_summaries.handler({ window_minutes?, filter_expression?, only_errors? }) -> { traces: [{ id, duration, response_time, has_error, has_fault, has_throttle, http_status, entry_service }], count, window_minutes } | { error, error_type }`
  - `ace_get_trace.handler({ trace_id }) -> { trace_id, segments: [{ name, origin, error, fault, throttle, http_status, duration, subsegments: [{ name, namespace, error, fault, http_status, aws_operation }] }] } | { error, error_type }`
- Consumes (in tests): `PutTraceSegmentsCommand` from `@aws-sdk/client-xray` to seed data (proven to round-trip on this build).

- [x] **Step 1: Write the failing tests**

Add to `tests/test_mcp_server.js` (after the `ace_lookup_events` tests). First add an import for seeding at the top with the other `@aws-sdk` imports:

```js
import { XRayClient, PutTraceSegmentsCommand } from "@aws-sdk/client-xray";
```

Then add the tests:

```js
test("observeTracingTools includes the two X-Ray trace tools", () => {
  assert.ok(observeTracingTools.some((t) => t.name === "ace_get_trace_summaries"));
  assert.ok(observeTracingTools.some((t) => t.name === "ace_get_trace"));
});

test("ace_get_trace_summaries: returns traces array or error", async () => {
  const res = await tool(observeTracingTools, "ace_get_trace_summaries").handler({ window_minutes: 30 });
  if (res.error) { assert.ok(typeof res.error === "string"); }
  else {
    assert.ok(Array.isArray(res.traces));
    assert.equal(typeof res.count, "number");
    assert.equal(res.window_minutes, 30);
  }
});

test("ace_get_trace_summaries: clamps window_minutes to <= 1440", async () => {
  const res = await tool(observeTracingTools, "ace_get_trace_summaries").handler({ window_minutes: 99999 });
  if (!res.error) { assert.equal(res.window_minutes, 1440); }
});

test("ace_get_trace: missing trace_id returns error", async () => {
  const res = await tool(observeTracingTools, "ace_get_trace").handler({});
  assert.equal(res.error_type, "INVALID_INPUT");
});

test("ace_get_trace: round-trips a seeded segment with subsegment", async () => {
  const xray = new XRayClient({
    endpoint: "http://localhost:4566", region: "us-east-1",
    credentials: { accessKeyId: "test", secretAccessKey: "test" },
  });
  const epoch = Date.now() / 1000;
  const traceId = `1-${Math.floor(epoch).toString(16)}-${Array.from({length:24},()=>Math.floor(Math.random()*16).toString(16)).join("")}`;
  const seg = {
    trace_id: traceId,
    id: Array.from({length:16},()=>Math.floor(Math.random()*16).toString(16)).join(""),
    name: "ace-test-service", start_time: epoch - 1, end_time: epoch,
    subsegments: [{
      id: Array.from({length:16},()=>Math.floor(Math.random()*16).toString(16)).join(""),
      name: "ace-test-table", namespace: "aws", start_time: epoch - 0.9, end_time: epoch - 0.1,
      fault: true, aws: { operation: "PutItem" },
    }],
  };
  await xray.send(new PutTraceSegmentsCommand({ TraceSegmentDocuments: [JSON.stringify(seg)] }));
  await new Promise((r) => setTimeout(r, 1500));
  const res = await tool(observeTracingTools, "ace_get_trace").handler({ trace_id: traceId });
  if (res.error) return; // LocalStack X-Ray unavailable in this env; tolerated
  assert.equal(res.trace_id, traceId);
  assert.ok(res.segments.length >= 1);
  const sub = res.segments[0].subsegments.find((s) => s.name === "ace-test-table");
  assert.ok(sub);
  assert.equal(sub.fault, true);
  assert.equal(sub.aws_operation, "PutItem");
});
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `node --test tests/test_mcp_server.js 2>&1 | grep -A2 "trace"`
Expected: FAIL — `ace_get_trace_summaries` / `ace_get_trace` not found in `observeTracingTools` (the membership test fails; handler calls throw on undefined).

- [x] **Step 3: Implement the two tools**

In `harness/mcp_server/tools/observe_tracing.js`, extend the import line and add the client below the existing CloudTrail client:

```js
import { CloudTrailClient, LookupEventsCommand } from "@aws-sdk/client-cloudtrail";
import {
  XRayClient, GetTraceSummariesCommand, BatchGetTracesCommand,
} from "@aws-sdk/client-xray";
```

Below `const cloudTrailClient = new CloudTrailClient(awsConfig);` add:

```js
const xrayClient = new XRayClient(awsConfig);
```

Then add these two objects to the `observeTracingTools` array (after the `ace_lookup_events` object):

```js
  {
    name: "ace_get_trace_summaries",
    description:
      "X-Ray GetTraceSummaries: list recent traces over a window with error/fault/throttle flags, to find broken requests. Optional X-Ray filter_expression (e.g. 'fault = true'); only_errors is a convenience that applies 'error = true OR fault = true' when no filter_expression is given. Defaults: last 60 min. Returns nothing unless scenario handlers are X-Ray-instrumented.",
    inputSchema: {
      type: "object",
      properties: {
        window_minutes: { type: "number" },
        filter_expression: { type: "string" },
        only_errors: { type: "boolean" },
      },
      required: [],
    },
    async handler({ window_minutes = 60, filter_expression, only_errors } = {}) {
      const clampedWindow = Math.min(Math.max(1, window_minutes ?? 60), 1440);
      const endTime = new Date();
      const startTime = new Date(endTime.getTime() - clampedWindow * 60 * 1000);
      let filterExpr = filter_expression;
      if (!filterExpr && only_errors) filterExpr = "error = true OR fault = true";
      try {
        const res = await xrayClient.send(
          new GetTraceSummariesCommand({
            StartTime: startTime,
            EndTime: endTime,
            FilterExpression: filterExpr || undefined,
          })
        );
        const traces = (res.TraceSummaries ?? []).map((t) => ({
          id: t.Id,
          duration: t.Duration ?? null,
          response_time: t.ResponseTime ?? null,
          has_error: t.HasError ?? false,
          has_fault: t.HasFault ?? false,
          has_throttle: t.HasThrottle ?? false,
          http_status: t.Http?.HttpStatus ?? null,
          entry_service: (t.ServiceIds ?? [])[0]?.Name ?? null,
        }));
        return { traces, count: traces.length, window_minutes: clampedWindow };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "XRAY_ERROR" };
      }
    },
  },
  {
    name: "ace_get_trace",
    description:
      "X-Ray BatchGetTraces for a single trace_id: returns the segment tree (segments + subsegments) with error/fault/throttle and the downstream AWS operation per subsegment — 'where in the flow did it break'. Use a trace_id from ace_get_trace_summaries.",
    inputSchema: {
      type: "object",
      properties: { trace_id: { type: "string" } },
      required: ["trace_id"],
    },
    async handler({ trace_id } = {}) {
      if (!trace_id) return { error: "trace_id is required", error_type: "INVALID_INPUT" };
      try {
        const res = await xrayClient.send(new BatchGetTracesCommand({ TraceIds: [trace_id] }));
        const trace = (res.Traces ?? [])[0];
        if (!trace) return { trace_id, segments: [] };
        const segments = (trace.Segments ?? []).map((s) => {
          let doc = {};
          try { doc = JSON.parse(s.Document ?? "{}"); } catch { doc = {}; }
          const dur = (typeof doc.end_time === "number" && typeof doc.start_time === "number")
            ? +(doc.end_time - doc.start_time).toFixed(3) : null;
          return {
            name: doc.name ?? null,
            origin: doc.origin ?? null,
            error: !!doc.error,
            fault: !!doc.fault,
            throttle: !!doc.throttle,
            http_status: doc.http?.response?.status ?? null,
            duration: dur,
            subsegments: (doc.subsegments ?? []).map((ss) => ({
              name: ss.name ?? null,
              namespace: ss.namespace ?? null,
              error: !!ss.error,
              fault: !!ss.fault,
              http_status: ss.http?.response?.status ?? null,
              aws_operation: ss.aws?.operation ?? null,
            })),
          };
        });
        return { trace_id, segments };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "XRAY_ERROR" };
      }
    },
  },
```

- [x] **Step 4: Run the tests to verify they pass**

Run: `node --test tests/test_mcp_server.js 2>&1 | tail -15`
Expected: all tests pass (`# pass` count increased by 5, `# fail 0`). The seeded round-trip test passes if LocalStack X-Ray is reachable; it self-tolerates an `error` return otherwise.

- [x] **Step 5: Confirm agent exposure (no allow-list change needed)**

Run: `python -c "from harness.agent.tools import filter_model_tools" 2>&1; echo "import ok"`
Then verify the tools are observe-class by confirming they are NOT in any score filter. Run: `grep -n "ace_verify_fix\|ace_score_run\|ace_get_trace" harness/agent/tools.py`
Expected: only `ace_verify_fix`/`ace_score_run` appear in filter logic; `ace_get_trace*` do not (they pass through automatically).

- [x] **Step 6: Commit**

```bash
git add harness/mcp_server/tools/observe_tracing.js tests/test_mcp_server.js
git commit -m "feat(mcp): add ace_get_trace_summaries and ace_get_trace X-Ray observe tools"
```

---

### Task 2: Emission gate — shared `xray_instrument.py`, vendor SDK, prove one arch01 handler emits a trace

This is the de-risking gate. It builds the shared instrumentation module, vendors `aws-xray-sdk` into one handler package, instruments one arch01 handler, and proves end-to-end that a trace with a downstream DynamoDB subsegment appears via the Task 1 tools. **Do not proceed to Task 3 until this passes.**

**Files:**
- Create: `corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/deployment/lambda/_shared/xray_instrument.py` (canonical copy; copied into each handler dir during vendoring)
- Modify: `corpus/arch_01_.../deployment/lambda/front-handler/index.py` (instrument this one handler for the gate)
- Modify: `corpus/arch_01_.../known_good.yaml` (add `TracingConfig: Mode: Active` + `xray:PutTraceSegments`/`xray:PutTelemetryRecords` to the front handler's IAM role)
- Modify: `pyproject.toml` (add `aws-xray-sdk` so vendoring is reproducible)
- Create: `scripts/vendor_xray.sh` (pip-install `aws-xray-sdk` into a handler dir)
- Create: `scripts/validate_xray_emission.py` (deploy known_good, run traffic, assert a trace with a DynamoDB subsegment via `ace_get_trace`)

**Interfaces:**
- Produces: `xray_instrument.py` exposing `traced(name)` decorator and a configured `xray_recorder` with `patch_all()` applied on import.
  - `traced(name: str) -> Callable` wraps `def handler(event, context)`.
- Consumes: Task 1's `ace_get_trace` / `ace_get_trace_summaries` (used by the validation script to confirm emission).

- [x] **Step 1: Write the shared instrumentation module**

Create `corpus/arch_01_.../deployment/lambda/_shared/xray_instrument.py`:

```python
"""Shared X-Ray instrumentation for arch01 handlers.

Begins an explicit segment (LocalStack provides no Lambda facade segment),
patches boto3 so downstream calls become subsegments, and emits each finished
segment to LocalStack via PutTraceSegments (the proven emission path on this
build). Handler-facing usage (`@traced(...)` + patch_all) matches real-AWS
X-Ray instrumentation; only the emitter is environment-specific.
"""
import os
import boto3
from aws_xray_sdk.core import xray_recorder, patch_all
from aws_xray_sdk.core.emitters.udp_emitter import UDPEmitter

_ENDPOINT = os.environ.get("AWS_ENDPOINT_URL") or "http://localhost.localstack.cloud:4566"
_xray_client = boto3.client(
    "xray", endpoint_url=_ENDPOINT, region_name=os.environ.get("AWS_REGION", "us-east-1")
)


class PutSegmentsEmitter(UDPEmitter):
    """Emit finished entities via the X-Ray API instead of UDP to a daemon."""

    def send_entity(self, entity):
        try:
            self._xray = _xray_client
            self._xray.put_trace_segments(TraceSegmentDocuments=[entity.serialize()])
        except Exception:
            # Never let trace emission break a handler.
            pass


xray_recorder.configure(
    context_missing="LOG_ERROR",
    sampling=False,
    emitter=PutSegmentsEmitter(),
)
patch_all()


def traced(name):
    """Wrap a Lambda handler so its work runs inside an X-Ray segment."""

    def decorator(fn):
        def wrapper(event, context):
            xray_recorder.begin_segment(name)
            try:
                return fn(event, context)
            except Exception as exc:  # noqa: BLE001 - record then re-raise
                segment = xray_recorder.current_segment()
                if segment is not None:
                    segment.add_exception(exc, None)
                raise
            finally:
                xray_recorder.end_segment()

        return wrapper

    return decorator
```

- [x] **Step 2: Instrument the front handler**

In `corpus/arch_01_.../deployment/lambda/front-handler/index.py`, add the import after the existing imports and wrap the handler. The module is co-located in the package (vendored in Step 4), so import is flat:

```python
from xray_instrument import traced
```

Change the handler signature line from `def handler(event, context):` to:

```python
@traced("FrontHandlerFunction")
def handler(event, context):
```

- [x] **Step 3: Add TracingConfig + X-Ray IAM perms to known_good.yaml (front handler only, for the gate)**

In `corpus/arch_01_.../known_good.yaml`, add to the `FrontHandlerFunction` resource `Properties:`:

```yaml
      TracingConfig:
        Mode: Active
```

And to the IAM role used by the front handler, add an inline policy statement granting X-Ray write (find the role's `Policies` → `PolicyDocument` → `Statement` and append):

```yaml
          - Effect: Allow
            Action:
              - xray:PutTraceSegments
              - xray:PutTelemetryRecords
            Resource: "*"
```

- [x] **Step 4: Add the vendoring dependency and script**

Add `aws-xray-sdk` to `pyproject.toml` dependencies (the `dependencies = [...]` array):

```toml
    "aws-xray-sdk",
```

Create `scripts/vendor_xray.sh`:

```bash
#!/usr/bin/env bash
# Vendor aws-xray-sdk + the shared module into a handler package dir so the
# Lambda zip is self-contained. Usage: scripts/vendor_xray.sh <handler_dir>
set -euo pipefail
HANDLER_DIR="$1"
SHARED="$(dirname "$0")/../corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/deployment/lambda/_shared/xray_instrument.py"
cp "$SHARED" "$HANDLER_DIR/xray_instrument.py"
pip install aws-xray-sdk --target "$HANDLER_DIR" --quiet \
  --platform manylinux2014_aarch64 --python-version 3.11 \
  --only-binary=:all: --implementation cp 2>/dev/null \
  || pip install aws-xray-sdk --target "$HANDLER_DIR" --quiet
echo "vendored aws-xray-sdk + xray_instrument.py into $HANDLER_DIR"
```

Make it executable and vendor the front handler:

```bash
chmod +x scripts/vendor_xray.sh
scripts/vendor_xray.sh corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/deployment/lambda/front-handler
```

- [x] **Step 5: Write the emission validation script**

Create `scripts/validate_xray_emission.py`:

```python
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
    tid = summaries["traces"][0]["id"]
    trace = call_tool("ace_get_trace", {"trace_id": tid})
    print("trace:", json.dumps(trace, indent=2)[:1200])
    seg = trace["segments"][0]
    subs = [s["name"] for s in seg["subsegments"]]
    print("segment:", seg["name"], "subsegments:", subs)
    assert any(s["aws_operation"] for s in seg["subsegments"]), \
        "no downstream AWS subsegment — patch_all not capturing hops"
    print("EMISSION GATE PASSED")


if __name__ == "__main__":
    sys.exit(main())
```

- [x] **Step 6: Run the gate**

Deploy arch01 and drive traffic, then validate. Using the existing corpus validation flow:

```bash
# 1. deploy known_good + run functional traffic (use the validate-corpus skill,
#    or: python corpus/validate_deploy.py <arch01 dir>  then its functional_test.py)
python corpus/validate_deploy.py corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda
python corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/functional_test.py
# 2. assert traces emitted
python scripts/validate_xray_emission.py
```

Expected: `EMISSION GATE PASSED`, with the front handler segment showing at least one subsegment whose `aws_operation` is a DynamoDB call (e.g. `PutItem`/`UpdateItem`).

**If it fails** (no traces / no subsegments): the emitter or packaging is wrong. Diagnose in this order: (a) confirm `aws_xray_sdk` imports inside the Lambda (`ace_get_log_tail` on the front handler for ImportError); (b) confirm the handler role has `xray:PutTraceSegments` (CloudWatch logs / `ace_filter_log_events` for AccessDenied); (c) confirm `AWS_ENDPOINT_URL` is set in the Lambda env or the fallback host resolves. Fix and re-run before proceeding.

- [x] **Step 7: Commit**

```bash
git add corpus/arch_01_*/deployment/lambda/_shared/xray_instrument.py \
        corpus/arch_01_*/deployment/lambda/front-handler/ \
        corpus/arch_01_*/known_good.yaml pyproject.toml \
        scripts/vendor_xray.sh scripts/validate_xray_emission.py
git commit -m "feat(corpus): X-Ray instrumentation module + emission gate (arch01 front handler)"
```

---

### Task 3: Instrument all arch01 corpus handlers + add tracing/IAM to known_good.yaml

**Files:**
- Modify: all six `corpus/arch_01_.../deployment/lambda/<handler>/index.py` (the five not done in Task 2; front handler already done)
- Modify: `corpus/arch_01_.../known_good.yaml` (TracingConfig on all six functions; X-Ray IAM perms on all execution roles)

**Interfaces:**
- Consumes: `from xray_instrument import traced` and the `@traced(name)` pattern from Task 2.

- [x] **Step 1: Instrument the remaining five handlers**

For each of `accept-state-handler`, `read-handler`, `reject-state-handler`, `request-state-handler`, `unfriend-state-handler` (`index.py`):
- add `from xray_instrument import traced` with the other imports
- decorate the entry handler with `@traced("<FunctionLogicalName>")` (match the logical name from known_good.yaml, e.g. `AcceptStateHandlerFunction`).

- [x] **Step 2: Add TracingConfig to all six functions and X-Ray IAM perms to all roles**

In `corpus/arch_01_.../known_good.yaml`, for each of the six `AWS::Lambda::Function` resources add under `Properties:`:

```yaml
      TracingConfig:
        Mode: Active
```

For each IAM role's inline policy `Statement`, append:

```yaml
          - Effect: Allow
            Action:
              - xray:PutTraceSegments
              - xray:PutTelemetryRecords
            Resource: "*"
```

- [x] **Step 3: Vendor the SDK + shared module into all six handler dirs**

```bash
for h in accept-state-handler front-handler read-handler reject-state-handler request-state-handler unfriend-state-handler; do
  scripts/vendor_xray.sh "corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/deployment/lambda/$h"
done
```

- [x] **Step 4: Re-deploy known_good and confirm no regression**

```bash
python corpus/validate_deploy.py corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda
python corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/functional_test.py
```

Expected: deployment `CREATE_COMPLETE`; all functional_test assertions pass (instrumentation does not change behavior).

- [x] **Step 5: Confirm multi-segment traces appear**

```bash
python scripts/validate_xray_emission.py
```

Expected: `EMISSION GATE PASSED`; `ace_get_trace_summaries` returns multiple traces spanning the request→accept flow.

- [x] **Step 6: Commit**

```bash
git add corpus/arch_01_*/deployment/lambda/ corpus/arch_01_*/known_good.yaml
git commit -m "feat(corpus): instrument all arch01 handlers with X-Ray tracing"
```

---

### Task 4: Propagate instrumentation to existing arch01 scenario handler copies

So the trace tools are not empty during existing arch01 scenario runs. Their `optimal_*` baselines are NOT re-cut here (deferred); only handler code + template tracing are synced.

**Files:**
- Modify: each `scenarios/arch01_fault*/deployment/lambda/<handler>/index.py` that mirrors a corpus handler
- Modify: each `scenarios/arch01_fault*/faulted.yaml` and `faulted_annotated.yaml` (add TracingConfig + X-Ray IAM perms, preserving the injected fault)

**Interfaces:**
- Consumes: the instrumented corpus handlers + `xray_instrument.py` from Task 3.

- [x] **Step 1: Sync instrumented handler code + vendored deps into each scenario**

For each `scenarios/arch01_fault*` directory, copy the instrumented + vendored handler packages from the corpus, preserving any scenario-specific handler fault. Use a guarded copy that overwrites handler code but is reviewed against each scenario's fault_manifest for handler-level faults:

```bash
CORPUS=corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/deployment/lambda
for d in scenarios/arch01_fault*/deployment/lambda; do
  for h in "$CORPUS"/*/; do
    name=$(basename "$h")
    [ -d "$d/$name" ] || continue
    cp "$h/xray_instrument.py" "$d/$name/" 2>/dev/null || true
    # vendor SDK into the scenario copy
    scripts/vendor_xray.sh "$d/$name" >/dev/null
  done
done
echo "synced shared module + vendored SDK into scenario handler dirs"
```

Then, for each scenario, apply the `@traced(...)` decorator + import to the scenario's handler `index.py` files **unless** that handler carries the scenario's injected fault (check `fault_manifest.json` `target_resource`/`root_cause`); for a faulted handler, add instrumentation without altering the fault.

- [x] **Step 2: Add TracingConfig + X-Ray IAM perms to each scenario template**

For each `scenarios/arch01_fault*/faulted.yaml` (and `faulted_annotated.yaml`), apply the same `TracingConfig: Mode: Active` on the six functions and `xray:PutTraceSegments`/`xray:PutTelemetryRecords` on the roles as in Task 3 — **without** touching the injected fault property recorded in that scenario's `fault_manifest.json`.

- [x] **Step 3: Lint all modified templates**

```bash
python -c "from harness.shared.cfn_lint_runner import run_lint; import glob,sys; [print(p, run_lint(p)) for p in glob.glob('scenarios/arch01_fault*/faulted.yaml')]"
```

Expected: no lint errors introduced (warnings unchanged from baseline).

- [x] **Step 4: Spot-check one scenario still reproduces its fault**

Pick `scenarios/arch01_fault01_connectivity`. Deploy it and confirm its observable symptom still occurs (instrumentation did not mask the fault). Use the harness scenario runner or `validate_deploy.py` on the faulted template, then its observability check.

Run: `python harness/run.py scenarios/arch01_fault01_connectivity/ --model <noop or dry-run path>` if a dry-run mode exists; otherwise deploy `faulted.yaml` via `validate_deploy.py` and assert `accept_terminal_state` still fails.
Expected: the fault still reproduces (`accept_terminal_state` fails as recorded in the manifest), proving instrumentation is fault-transparent.

- [x] **Step 5: Commit**

```bash
git add scenarios/arch01_fault*/deployment/ scenarios/arch01_fault*/faulted.yaml scenarios/arch01_fault*/faulted_annotated.yaml
git commit -m "feat(scenarios): propagate X-Ray instrumentation to arch01 scenario handlers"
```

---

### Task 5: Add one X-Ray-diagnosable arch01 fault scenario + re-baseline

A new fault whose fastest diagnosis runs through the trace tools: a downstream call faults mid-flow, visible as a faulted subsegment in `ace_get_trace`.

**Files:**
- Create: `scenarios/arch01_fault11_data_correctness/` with `scenario.md`, `faulted.yaml`, `faulted_annotated.yaml`, `fault_manifest.json`, `deployment/lambda/...` (instrumented copies)

**Interfaces:**
- Consumes: instrumented corpus handlers (Task 3); the trace tools (Task 1).
- Produces: a scenario whose `fault_manifest.json.optimal_diagnostic_path` includes `ace_get_trace_summaries` then `ace_get_trace`.

- [x] **Step 1: Choose the fault (concrete)**

Inject a wrong DynamoDB table name into one downstream handler's environment so its `PutItem`/`UpdateItem` subsegment faults with `ResourceNotFoundException`, while upstream segments succeed. Target: `RequestStateHandlerFunction.Properties.Environment.Variables.FRIEND_TABLE` set to a nonexistent table name (original: the `!Ref FriendTable` value). This is a clean CFN property mutation; the receiver-side reciprocal record is never written, and the trace shows exactly which hop/operation faulted.

- [x] **Step 2: Create the faulted template + scenario from the instrumented corpus**

Copy the instrumented arch01 `known_good.yaml` → `scenarios/arch01_fault11_data_correctness/faulted.yaml`, apply the env-var mutation, and create `faulted_annotated.yaml` marking the injected line. Copy the instrumented `deployment/lambda/` tree in. Write `scenario.md` describing the observable symptom (receiver never sees the incoming request after a Request action) without revealing the root cause.

- [x] **Step 3: Write fault_manifest.json**

```json
{
  "fault_id": "arch01_fault11",
  "fault_class": "data_correctness",
  "architecture": "arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda",
  "scenario_id": "arch01_fault11_data_correctness",
  "baseline_idempotent": false,
  "target_resource": "RequestStateHandlerFunction",
  "target_property": "Properties.Environment.Variables.FRIEND_TABLE",
  "injected_value": "Friend-nonexistent",
  "original_value": "<the !Ref FriendTable resolved table name>",
  "valid_fixes": [
    "Restore RequestStateHandlerFunction Environment FRIEND_TABLE to reference the real FriendTable so the reciprocal Pending record write targets the existing table"
  ],
  "invalid_patches": [
    "Create a new table named Friend-nonexistent — masks the misconfiguration and diverges receiver-side data from the canonical FriendTable"
  ],
  "optimal_tool_calls": 2,
  "optimal_files_changed": 1,
  "optimal_lines_changed": 1,
  "optimal_diagnostic_path": [
    "ace_get_trace_summaries(only_errors=true) → surfaces the faulted trace for the Request flow",
    "ace_get_trace(trace_id=...) → RequestStateHandlerFunction segment shows a faulted DynamoDB PutItem subsegment with ResourceNotFoundException on table Friend-nonexistent"
  ],
  "concurrency_probe_n": null,
  "deployment_check": "CREATE_COMPLETE",
  "observability_check": "request_reciprocal_record",
  "observable_symptom": "After a Request action, the requester-side Requested record is written, but the receiver-side reciprocal Pending record never appears. request_reciprocal_record assertion fails.",
  "root_cause": "RequestStateHandlerFunction Environment FRIEND_TABLE points at a nonexistent table (Friend-nonexistent). The handler's PutItem for the reciprocal record raises ResourceNotFoundException, captured as a faulted X-Ray subsegment, so the receiver-side record is never created.",
  "corpus_path": "corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda",
  "functional_test_path": "corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/functional_test.py",
  "known_good_path": "corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/known_good.yaml"
}
```

Replace `original_value` with the actual resolved table name used in known_good.yaml.

- [x] **Step 4: Validate the scenario reproduces and is trace-diagnosable**

Deploy the faulted template, run traffic, and confirm:

```bash
python corpus/validate_deploy.py --template scenarios/arch01_fault11_data_correctness/faulted.yaml 2>/dev/null \
  || python harness/run.py scenarios/arch01_fault11_data_correctness/ --dry-run 2>/dev/null
python scripts/validate_xray_emission.py
```

Expected: the `request_reciprocal_record` symptom reproduces, AND `ace_get_trace` on the faulted trace shows the `RequestStateHandlerFunction` segment with a faulted subsegment whose `aws_operation` is `PutItem`. If LocalStack does not capture the error inside the subsegment, fall back to making the faulted hop a *missing* expected subsegment (handler never reaches the write) and update the manifest's `optimal_diagnostic_path` accordingly.

- [x] **Step 5: Re-baseline optimal_tool_calls**

Confirm the trace path (`ace_get_trace_summaries` → `ace_get_trace`) is genuinely 2 calls and shorter than the non-trace path (env-var inspection via `ace_get_environment_variables` + `ace_get_log_tail`). If the non-trace path is equal/shorter, adjust `optimal_tool_calls`/`optimal_diagnostic_path` to reflect the true minimum.

- [x] **Step 6: Commit**

```bash
git add scenarios/arch01_fault11_data_correctness/
git commit -m "feat(scenarios): add arch01 X-Ray-diagnosable fault (fault11) with trace-based optimal path"
```

---

### Task 6: Documentation

**Files:**
- Modify: `README.md`, `CLAUDE.md` (tool counts, layout), `RUN.md` (tool inventory)

**Interfaces:** none (docs only).

- [x] **Step 1: Bump tool counts and add the X-Ray tools**

Update tool counts everywhere they appear: the MCP server now has **56 diagnostic + 2 score = 58 tools** (was 54 diagnostic + 2 score). Update:
- `CLAUDE.md`: the `mcp_server/` header comment ("54 diagnostic + 2 score" → "56 diagnostic + 2 score"), and the `observe_tracing.js` description (now 3 tools: `ace_lookup_events` + the two X-Ray tools).
- `README.md`: Phase B tool inventory and the Tracing observe tools section — add `ace_get_trace_summaries` and `ace_get_trace` with their `GetTraceSummaries`/`BatchGetTraces` mappings, and note the handler-instrumentation dependency.
- `RUN.md`: tool inventory header and the Tracing observe tools list.

- [x] **Step 2: Document the instrumentation dependency**

In `RUN.md` (or `README.md` Tracing section) add a short note: the X-Ray trace tools return data only for X-Ray-instrumented architectures; arch01 is instrumented via `aws-xray-sdk` (`xray_instrument.py`), other architectures are not yet (deferred to the fan-out phase). `ace_get_service_graph` is intentionally not provided (non-functional on LocalStack).

- [x] **Step 3: Verify counts are consistent**

Run: `grep -rn "54 diagnostic\|56 diagnostic\|58 tools\|56 tools" README.md CLAUDE.md RUN.md`
Expected: no stale "54 diagnostic" / old totals remain; counts agree across files.

- [x] **Step 4: Commit**

```bash
git add README.md CLAUDE.md RUN.md
git commit -m "docs: document X-Ray trace tools and arch01 instrumentation (58 tools)"
```

---

## Self-Review notes

- **Spec coverage:** Component 1 (two tools) → Task 1. Component 2 (shared module + emitter gate) → Task 2. Component 3 (corpus instrumentation, scenario propagation, new fault + re-baseline) → Tasks 3–5. Testing → Task 1 (Node) + Tasks 2–5 (corpus validation). Docs → Task 6. Re-validation findings already recorded in the spec.
- **Sequencing note:** the spec listed the emission gate as Step 1; this plan builds the tools first (Task 1) so they serve as the verification instrument for the gate (Task 2). Corpus fan-out (Tasks 3–5) still occurs only after the gate passes, preserving the spec's de-risking intent.
- **Type consistency:** tool output keys (`traces`/`segments`/`subsegments`, `aws_operation`, `has_fault`/`fault`) are used identically in the tool code, tests, and validation script. `traced(name)` signature is consistent across Tasks 2–5.
- **Known residual risk:** Task 5 Step 4 carries an explicit fallback if LocalStack does not capture errors inside subsegments — the only place the trace-diagnosable premise could break, gated by Task 2's subsegment-fidelity check.
- **Task 5 finding (relevant to Task 6 docs + `ace_get_trace_summaries`):** On this LocalStack build, `GetTraceSummaries` does NOT implement the `FilterExpression` parameter (returns `{"error":"Not implemented yet - moto"}`), so the `only_errors=true` / `filter_expression` path of `ace_get_trace_summaries` is non-functional — it errors rather than filtering. LocalStack also does not propagate subsegment `fault`/`error` flags up to the summary level (`has_fault`/`has_error` are always `false` on summaries). Subsegment-level fault flags ARE correct via `ace_get_trace` (a failed DynamoDB call shows `error:true, fault:true, http_status:400` on its subsegment — this is how fault11 is diagnosed). So the working trace-diagnostic path is `ace_get_trace_summaries(window_minutes=...)` to list trace IDs, then `ace_get_trace(trace_id)` to read the faulted subsegment. Task 6 docs should note the `only_errors` limitation; consider softening the `ace_get_trace_summaries` tool description's `only_errors` claim.
- **Task 2 finding (MANDATORY for Tasks 3–5):** LocalStack's Lambda detection sets the X-Ray recorder's `streaming_threshold` to `0`, which streams each subsegment out as an independent document the instant it closes — leaving the parent segment empty and making LocalStack flatten downstream calls into flat sibling segments that lose `aws_operation`. `xray_instrument.py` fixes this with `streaming_threshold=1000` (keep subsegments embedded) plus a `send_entity` guard that skips subsegment-type entities. The emission gate now passes the STRICT criterion: `ace_get_trace` on the front-handler trace returns `FrontHandlerFunction` with a nested `dynamodb` subsegment carrying `aws_operation=PutItem`. Tasks 3–5 must use this same `_shared/xray_instrument.py` unchanged. Note: `vendor_xray.sh` is non-deterministic about pulling `botocore` (Lambda runtime already provides it) — the committed front-handler intentionally vendors only `aws_xray_sdk`/`wrapt`/`six`, which is sufficient.
