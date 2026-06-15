# Ultimate-Tier Depth MCP Tooling — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add real-AWS-equivalent depth diagnostics (X-Ray + CloudTrail observe tools) and a fail-fast IAM-enforcement contract to the ACE-Bench harness, gated by a per-architecture validation spike.

**Architecture:** Four new read-only MCP observe tools live in a new file `harness/mcp_server/tools/observe_tracing.js`, registered into `index.js` exactly like the existing five tool arrays. IAM enforcement is a LocalStack container setting validated by a new Python module (`harness/shared/iam_enforcement.py`) that `run.py` calls right after `health_check()`, failing fast if enforcement is off. A throwaway spike (Task 2) confirms LocalStack Ultimate actually populates the backing data and fixes the exact enforcement-probe recipe before any tool code is written.

**Tech Stack:** Node.js v22+ (MCP server, `@aws-sdk/client-xray`, `@aws-sdk/client-cloudtrail`, `node:test`), Python 3.11 (`boto3`, `pytest`), LocalStack Ultimate.

**Spec:** `docs/superpowers/specs/2026-06-14-ultimate-tier-depth-mcp-tooling-design.md`

**Branch:** `feat/ultimate-depth-mcp-tooling` (already created; the spec commit is on it).

**Prerequisites for running tests in this plan:**
- LocalStack Ultimate running at `http://localhost:4566` with `LOCALSTACK_AUTH_TOKEN`, `ENFORCE_IAM=1`, `IAM_SOFT_MODE=0`.
- `cd harness/mcp_server && npm install` has been run (Task 1 adds the deps).

---

## File Structure

| File | Responsibility | Task |
|------|----------------|------|
| `harness/mcp_server/package.json` | Add `@aws-sdk/client-xray`, `@aws-sdk/client-cloudtrail` deps | 1 |
| `scratch/spike_observability.mjs` (throwaway, not committed) | Validate X-Ray/CloudTrail/enforcement per architecture | 2 |
| `docs/superpowers/specs/2026-06-14-...-design.md` (modify: "Spike findings") | Record the capability×hop matrix + enforcement recipe | 2 |
| `harness/mcp_server/tools/observe_tracing.js` (create) | The 4 new observe tools (X-Ray ×3, CloudTrail ×1) | 3, 4 |
| `harness/mcp_server/index.js` (modify) | Import + spread `observeTracingTools` | 5 |
| `tests/test_mcp_server.js` (modify) | Node tests for the 4 tools + export check | 3, 4, 5 |
| `harness/shared/iam_enforcement.py` (create) | `iam_enforcement_active()` + `assert_iam_enforcement()` | 6 |
| `tests/test_iam_enforcement.py` (create) | Unit tests for the enforcement contract | 6 |
| `harness/run.py` (modify ~line 321) | Call `assert_iam_enforcement()` after `health_check()` | 6 |
| `README.md`, `CLAUDE.md`, `RUN.md`, `docs/localstack-freetier.md` (modify) | Counts, prerequisites, Ultimate addendum | 7 |

---

## Task 1: Add X-Ray and CloudTrail npm dependencies

**Files:**
- Modify: `harness/mcp_server/package.json` (dependencies block)

- [ ] **Step 1: Add the two dependencies**

In `harness/mcp_server/package.json`, add these two lines to the `dependencies` object (keep alphabetical-ish grouping near the other `@aws-sdk/client-*` entries):

```json
    "@aws-sdk/client-cloudtrail": "^3.0.0",
    "@aws-sdk/client-xray": "^3.0.0",
```

- [ ] **Step 2: Install**

Run: `cd harness/mcp_server && npm install`
Expected: `package-lock.json` updates, `node_modules/@aws-sdk/client-xray` and `node_modules/@aws-sdk/client-cloudtrail` exist, no errors.

- [ ] **Step 3: Verify the clients import**

Run: `cd harness/mcp_server && node -e "import('@aws-sdk/client-xray').then(m=>console.log(!!m.XRayClient)); import('@aws-sdk/client-cloudtrail').then(m=>console.log(!!m.CloudTrailClient))"`
Expected: prints `true` twice.

- [ ] **Step 4: Commit**

```bash
git add harness/mcp_server/package.json harness/mcp_server/package-lock.json
git commit -m "build(mcp): add @aws-sdk/client-xray and @aws-sdk/client-cloudtrail deps"
```

---

## Task 2: Validation spike (Step 0 — throwaway, gates the tool list)

This task is **investigative, not TDD.** It deploys each existing architecture, exercises its real flow, and records whether the four candidate capabilities actually return data on LocalStack Ultimate. Its output is the "Spike findings" section of the spec and the validated enforcement-probe recipe used in Task 6. **Nothing here is committed except the spec edits.**

**Files:**
- Create (throwaway): `scratch/spike_observability.mjs`
- Modify: `docs/superpowers/specs/2026-06-14-ultimate-tier-depth-mcp-tooling-design.md` ("Spike findings (to be filled after Step 0)")

- [ ] **Step 1: Create the scratch directory (gitignored)**

Run: `mkdir -p scratch && grep -qxF 'scratch/' .gitignore || echo 'scratch/' >> .gitignore`
Expected: `scratch/` exists and is ignored.

- [ ] **Step 2: Write the spike script**

Create `scratch/spike_observability.mjs`. It probes each capability against whatever is currently deployed. Deploy each architecture first using the existing corpus deploy path (`python corpus/validate_deploy.py` or the `validate-corpus` skill) with active tracing enabled, run its `functional_test.py` to generate traffic, then run this script.

```js
import { XRayClient, GetServiceGraphCommand, GetTraceSummariesCommand } from "@aws-sdk/client-xray";
import { CloudTrailClient, LookupEventsCommand } from "@aws-sdk/client-cloudtrail";
import { IAMClient, CreateUserCommand, CreateAccessKeyCommand } from "@aws-sdk/client-iam";
import { S3Client, ListBucketsCommand } from "@aws-sdk/client-s3";

const cfg = { endpoint: "http://localhost:4566", region: "us-east-1",
  credentials: { accessKeyId: "test", secretAccessKey: "test" } };
const xray = new XRayClient(cfg);
const ct = new CloudTrailClient(cfg);

const now = new Date();
const start = new Date(now.getTime() - 30 * 60 * 1000);

// --- X-Ray ---
try {
  const g = await xray.send(new GetServiceGraphCommand({ StartTime: start, EndTime: now }));
  console.log("XRAY service_graph services:", (g.Services ?? []).length);
  console.log("XRAY service names:", (g.Services ?? []).map(s => s.Name));
} catch (e) { console.log("XRAY service_graph ERROR:", e.name, e.message); }

try {
  const s = await xray.send(new GetTraceSummariesCommand({ StartTime: start, EndTime: now }));
  console.log("XRAY trace_summaries:", (s.TraceSummaries ?? []).length);
} catch (e) { console.log("XRAY trace_summaries ERROR:", e.name, e.message); }

// --- CloudTrail ---
try {
  const ev = await ct.send(new LookupEventsCommand({ StartTime: start, EndTime: now, MaxResults: 10 }));
  console.log("CLOUDTRAIL events:", (ev.Events ?? []).length);
  console.log("CLOUDTRAIL event names:", (ev.Events ?? []).map(e => e.EventName));
} catch (e) { console.log("CLOUDTRAIL ERROR:", e.name, e.message); }

// --- IAM enforcement probe recipe (no-policy user => implicit deny when enforced) ---
try {
  const iam = new IAMClient(cfg);
  const uname = "ace-enforcement-probe";
  try { await iam.send(new CreateUserCommand({ UserName: uname })); } catch {}
  const ak = await iam.send(new CreateAccessKeyCommand({ UserName: uname }));
  const scoped = new S3Client({ ...cfg, credentials: {
    accessKeyId: ak.AccessKey.AccessKeyId, secretAccessKey: ak.AccessKey.SecretAccessKey } });
  try {
    await scoped.send(new ListBucketsCommand({}));
    console.log("ENFORCEMENT: OFF (no-policy user was ALLOWED)");
  } catch (e) {
    console.log("ENFORCEMENT: ON (no-policy user denied):", e.name);
  }
} catch (e) { console.log("ENFORCEMENT probe ERROR:", e.name, e.message); }
```

- [ ] **Step 3: Run the spike against each architecture**

For each of arch01, arch02, arch08, arch12: deploy `known_good.yaml` with active tracing, run its `functional_test.py` to generate traffic, then run:

Run: `cd harness/mcp_server && node ../../scratch/spike_observability.mjs`
Expected: prints non-error lines. Record counts per architecture.

- [ ] **Step 4: Record findings in the spec**

Replace the "Spike findings (to be filled after Step 0)" block in the spec with a capability×hop matrix marking ✅/⚠️/❌ for X-Ray (per async hop type), CloudTrail (per service), and IAM enforcement. Note the confirmed enforcement-probe recipe (the no-policy-user approach above, or a corrected one if it didn't behave as expected). Note any ❌ that will scope-limit or drop a tool.

- [ ] **Step 5: Commit the spec findings only**

```bash
git add docs/superpowers/specs/2026-06-14-ultimate-tier-depth-mcp-tooling-design.md
git commit -m "docs(spec): record observability validation spike findings"
```

> **Gate:** If a capability comes back ❌ across all architectures, drop its tool from Tasks 3–4 (or scope its description to where it works). Do not build tools against an empty backend.

---

## Task 3: Implement the three X-Ray observe tools

**Files:**
- Create: `harness/mcp_server/tools/observe_tracing.js`
- Test: `tests/test_mcp_server.js`

- [ ] **Step 1: Write the failing tests**

Add to the bottom of `tests/test_mcp_server.js` (after the existing imports, add the import near the other tool-array imports at the top):

At the top, with the other `import { ... } from "../harness/mcp_server/tools/..."` lines:
```js
import { observeTracingTools } from "../harness/mcp_server/tools/observe_tracing.js";
```

At the bottom of the file:
```js
test("observe_tracing exports an array", () => {
  assert.ok(Array.isArray(observeTracingTools));
  assert.equal(observeTracingTools.length, 4);
});

test("ace_get_service_graph: returns services array or error", async () => {
  const res = await tool(observeTracingTools, "ace_get_service_graph").handler({ window_minutes: 10 });
  if (res.error) { assert.ok(typeof res.error === "string"); }
  else { assert.ok(Array.isArray(res.services)); assert.equal(res.window_minutes, 10); }
});

test("ace_get_trace_summaries: returns traces array or error", async () => {
  const res = await tool(observeTracingTools, "ace_get_trace_summaries").handler({});
  if (res.error) { assert.ok(typeof res.error === "string"); }
  else { assert.ok(Array.isArray(res.traces)); assert.equal(typeof res.count, "number"); }
});

test("ace_get_trace: missing trace_id returns error", async () => {
  const res = await tool(observeTracingTools, "ace_get_trace").handler({});
  assert.equal(res.error, "trace_id is required");
});

test("ace_get_trace: unknown trace_id returns traces array or error", async () => {
  const res = await tool(observeTracingTools, "ace_get_trace").handler({ trace_id: "1-00000000-000000000000000000000000" });
  if (res.error) { assert.ok(typeof res.error === "string"); }
  else { assert.ok(Array.isArray(res.traces)); }
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test tests/test_mcp_server.js 2>&1 | grep -A2 observe_tracing`
Expected: FAIL — `Cannot find module '.../observe_tracing.js'`.

- [ ] **Step 3: Create the file with the X-Ray tools**

Create `harness/mcp_server/tools/observe_tracing.js`:

```js
import {
  XRayClient,
  GetServiceGraphCommand,
  GetTraceSummariesCommand,
  BatchGetTracesCommand,
} from "@aws-sdk/client-xray";

const awsConfig = {
  endpoint: process.env.LOCALSTACK_ENDPOINT ?? "http://localhost:4566",
  region: "us-east-1",
  credentials: { accessKeyId: "test", secretAccessKey: "test" },
};

const xrayClient = new XRayClient(awsConfig);

function clampWindow(minutes, max) {
  return Math.min(Math.max(1, minutes ?? 15), max);
}

export const observeTracingTools = [
  {
    name: "ace_get_service_graph",
    description:
      "X-Ray service map over a time window: services as nodes with edges and per-service total/ok/error/fault/throttle counts. Shows where in the request flow failures concentrate.",
    inputSchema: {
      type: "object",
      properties: { window_minutes: { type: "number" } },
      required: [],
    },
    async handler({ window_minutes = 15 } = {}) {
      const clamped = clampWindow(window_minutes, 60);
      const endTime = new Date();
      const startTime = new Date(endTime.getTime() - clamped * 60 * 1000);
      try {
        const res = await xrayClient.send(
          new GetServiceGraphCommand({ StartTime: startTime, EndTime: endTime })
        );
        const services = (res.Services ?? []).map((s) => ({
          name: s.Name,
          type: s.Type,
          edges: (s.Edges ?? []).map((e) => ({ reference_id: e.ReferenceId })),
          summary: {
            total: s.SummaryStatistics?.TotalCount ?? 0,
            ok: s.SummaryStatistics?.OkCount ?? 0,
            errors: s.SummaryStatistics?.ErrorStatistics?.TotalCount ?? 0,
            faults: s.SummaryStatistics?.FaultStatistics?.TotalCount ?? 0,
            throttles: s.SummaryStatistics?.ErrorStatistics?.ThrottleCount ?? 0,
          },
        }));
        return { services, window_minutes: clamped };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "XRAY_ERROR" };
      }
    },
  },
  {
    name: "ace_get_trace_summaries",
    description:
      "List X-Ray trace summaries over a time window with error/fault/throttle flags and HTTP status. Optional X-Ray filter_expression narrows results. Use to find the broken requests, then drill in with ace_get_trace.",
    inputSchema: {
      type: "object",
      properties: {
        window_minutes: { type: "number" },
        filter_expression: { type: "string" },
      },
      required: [],
    },
    async handler({ window_minutes = 15, filter_expression } = {}) {
      const clamped = clampWindow(window_minutes, 60);
      const endTime = new Date();
      const startTime = new Date(endTime.getTime() - clamped * 60 * 1000);
      try {
        const res = await xrayClient.send(
          new GetTraceSummariesCommand({
            StartTime: startTime,
            EndTime: endTime,
            FilterExpression: filter_expression || undefined,
          })
        );
        const traces = (res.TraceSummaries ?? []).map((t) => ({
          id: t.Id,
          duration: t.Duration,
          response_time: t.ResponseTime,
          has_error: t.HasError ?? false,
          has_fault: t.HasFault ?? false,
          has_throttle: t.HasThrottle ?? false,
          http_status: t.Http?.HttpStatus,
        }));
        return { traces, count: traces.length, window_minutes: clamped };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "XRAY_ERROR" };
      }
    },
  },
  {
    name: "ace_get_trace",
    description:
      "Fetch one X-Ray trace by id and return its segment tree (name, origin, error/fault/throttle flags, http, cause per segment). The detailed per-hop view of a single request's path.",
    inputSchema: {
      type: "object",
      properties: { trace_id: { type: "string" } },
      required: ["trace_id"],
    },
    async handler({ trace_id } = {}) {
      if (!trace_id) return { error: "trace_id is required" };
      try {
        const res = await xrayClient.send(
          new BatchGetTracesCommand({ TraceIds: [trace_id] })
        );
        const traces = (res.Traces ?? []).map((t) => ({
          id: t.Id,
          duration: t.Duration,
          segments: (t.Segments ?? []).map((seg) => {
            let doc = {};
            try {
              doc = JSON.parse(seg.Document ?? "{}");
            } catch {
              doc = {};
            }
            return {
              id: doc.id,
              name: doc.name,
              origin: doc.origin,
              error: doc.error ?? false,
              fault: doc.fault ?? false,
              throttle: doc.throttle ?? false,
              http: doc.http,
              cause: doc.cause,
            };
          }),
        }));
        return { traces, unprocessed: res.UnprocessedTraceIds ?? [] };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "XRAY_ERROR" };
      }
    },
  },
];
```

> Note: the array has 3 entries now; the export-length test asserts 4 and will stay red until Task 4 adds the CloudTrail tool. That is expected — proceed to Step 4 to confirm the three X-Ray tests pass, then Task 4 turns the length test green.

- [ ] **Step 4: Run the X-Ray tests to verify they pass**

Run: `node --test tests/test_mcp_server.js 2>&1 | grep -E "ace_get_(service_graph|trace)"`
Expected: the three `ace_get_service_graph` / `ace_get_trace_summaries` / `ace_get_trace` tests PASS. (The `exports an array` length test is still failing — fixed in Task 4.)

- [ ] **Step 5: Commit**

```bash
git add harness/mcp_server/tools/observe_tracing.js tests/test_mcp_server.js
git commit -m "feat(mcp): add X-Ray observe tools (service graph, trace summaries, trace)"
```

---

## Task 4: Add the CloudTrail lookup tool

**Files:**
- Modify: `harness/mcp_server/tools/observe_tracing.js`
- Test: `tests/test_mcp_server.js`

- [ ] **Step 1: Write the failing tests**

Add to the bottom of `tests/test_mcp_server.js`:

```js
test("ace_lookup_events: returns events array or error", async () => {
  const res = await tool(observeTracingTools, "ace_lookup_events").handler({ window_minutes: 30 });
  if (res.error) { assert.ok(typeof res.error === "string"); }
  else {
    assert.ok(Array.isArray(res.events));
    assert.equal(typeof res.count, "number");
    assert.equal(res.window_minutes, 30);
  }
});

test("ace_lookup_events: clamps max_results to <= 100", async () => {
  const res = await tool(observeTracingTools, "ace_lookup_events").handler({ max_results: 9999 });
  if (!res.error) { assert.ok(res.events.length <= 100); }
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test tests/test_mcp_server.js 2>&1 | grep ace_lookup_events`
Expected: FAIL — `Cannot read properties of undefined (reading 'handler')` (tool not found yet).

- [ ] **Step 3: Add the CloudTrail import and tool**

In `harness/mcp_server/tools/observe_tracing.js`, add the import below the X-Ray import:

```js
import { CloudTrailClient, LookupEventsCommand } from "@aws-sdk/client-cloudtrail";
```

Add the client below `const xrayClient = ...`:

```js
const cloudTrailClient = new CloudTrailClient(awsConfig);
```

Add this object as the **last element** of the `observeTracingTools` array (after the `ace_get_trace` object, before the closing `];`):

```js
  {
    name: "ace_lookup_events",
    description:
      "CloudTrail LookupEvents: recent API-call history over a window, surfacing error_code/error_message (e.g. AccessDenied) and the resources touched. Optional single filter: event_name OR resource_name (CloudTrail allows one attribute per lookup; event_name takes precedence). Defaults: last 60 min, 50 events.",
    inputSchema: {
      type: "object",
      properties: {
        window_minutes: { type: "number" },
        max_results: { type: "number" },
        event_name: { type: "string" },
        resource_name: { type: "string" },
      },
      required: [],
    },
    async handler({ window_minutes = 60, max_results = 50, event_name, resource_name } = {}) {
      const clampedWindow = Math.min(Math.max(1, window_minutes ?? 60), 1440);
      const clampedMax = Math.min(Math.max(1, max_results ?? 50), 100);
      const endTime = new Date();
      const startTime = new Date(endTime.getTime() - clampedWindow * 60 * 1000);
      const lookupAttributes = [];
      if (event_name) {
        lookupAttributes.push({ AttributeKey: "EventName", AttributeValue: event_name });
      } else if (resource_name) {
        lookupAttributes.push({ AttributeKey: "ResourceName", AttributeValue: resource_name });
      }
      try {
        const res = await cloudTrailClient.send(
          new LookupEventsCommand({
            StartTime: startTime,
            EndTime: endTime,
            MaxResults: clampedMax,
            LookupAttributes: lookupAttributes.length ? lookupAttributes : undefined,
          })
        );
        const events = (res.Events ?? []).map((e) => {
          let detail = {};
          try {
            detail = JSON.parse(e.CloudTrailEvent ?? "{}");
          } catch {
            detail = {};
          }
          return {
            event_name: e.EventName,
            event_time: e.EventTime,
            event_source: e.EventSource,
            username: e.Username,
            error_code: detail.errorCode ?? null,
            error_message: detail.errorMessage ?? null,
            resources: (e.Resources ?? []).map((r) => ({
              type: r.ResourceType,
              name: r.ResourceName,
            })),
          };
        });
        return { events, count: events.length, window_minutes: clampedWindow };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "CLOUDTRAIL_ERROR" };
      }
    },
  },
```

- [ ] **Step 4: Run all observe_tracing tests to verify they pass**

Run: `node --test tests/test_mcp_server.js 2>&1 | grep -E "ace_lookup_events|observe_tracing exports"`
Expected: both `ace_lookup_events` tests PASS and `observe_tracing exports an array` (length === 4) now PASSES.

- [ ] **Step 5: Commit**

```bash
git add harness/mcp_server/tools/observe_tracing.js tests/test_mcp_server.js
git commit -m "feat(mcp): add ace_lookup_events CloudTrail observe tool"
```

---

## Task 5: Register the new tools in the MCP server

**Files:**
- Modify: `harness/mcp_server/index.js`
- Test: `tests/test_mcp_server.js`

- [ ] **Step 1: Write the failing test**

Add to the bottom of `tests/test_mcp_server.js`:

```js
test("observe_tracing tool names are unique and registered", () => {
  const names = observeTracingTools.map((t) => t.name);
  assert.deepEqual(
    new Set(names).size, names.length, "tool names must be unique");
  for (const n of ["ace_get_service_graph", "ace_get_trace_summaries", "ace_get_trace", "ace_lookup_events"]) {
    assert.ok(names.includes(n), `missing ${n}`);
  }
});
```

- [ ] **Step 2: Run test to verify it passes already (names) — confirms expectations**

Run: `node --test tests/test_mcp_server.js 2>&1 | grep "unique and registered"`
Expected: PASS (this test validates the array contents independent of index.js wiring).

- [ ] **Step 3: Wire into index.js**

In `harness/mcp_server/index.js`, add the import after the `scoreTools` import:

```js
import { observeTracingTools } from "./tools/observe_tracing.js";
```

Then update the registration loop's spread to include the new array:

```js
for (const tool of [...probeTools, ...probeExtendedTools, ...observeTools, ...observeExtendedTools, ...observeTracingTools, ...scoreTools]) {
```

- [ ] **Step 4: Verify the server boots with the new tools registered**

Run: `cd harness/mcp_server && node -e "import('./index.js').catch(e=>{console.error(e);process.exit(1)}); setTimeout(()=>{console.log('booted ok');process.exit(0)}, 1500)"`
Expected: prints `booted ok` with no import/registration errors. (It will try to connect a stdio transport; the timeout exit is fine.)

- [ ] **Step 5: Run the full Node suite**

Run: `node --test tests/test_mcp_server.js`
Expected: all tests pass (existing + new observe_tracing tests).

- [ ] **Step 6: Commit**

```bash
git add harness/mcp_server/index.js tests/test_mcp_server.js
git commit -m "feat(mcp): register observe_tracing tools in MCP server"
```

---

## Task 6: IAM enforcement contract + fail-fast startup check

**Files:**
- Create: `harness/shared/iam_enforcement.py`
- Test: `tests/test_iam_enforcement.py`
- Modify: `harness/run.py` (after `health_check()` at ~line 321)

> Use the enforcement-probe recipe confirmed by the spike (Task 2). The code below uses the default no-policy-user recipe; if the spike found a different reliable recipe, substitute the denied call accordingly while keeping the same function names and return contract.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_iam_enforcement.py`:

```python
import pytest

from harness.shared import iam_enforcement


def test_assert_raises_when_enforcement_off(monkeypatch):
    monkeypatch.setattr(iam_enforcement, "iam_enforcement_active", lambda: False)
    with pytest.raises(RuntimeError, match="IAM enforcement"):
        iam_enforcement.assert_iam_enforcement()


def test_assert_passes_when_enforcement_on(monkeypatch):
    monkeypatch.setattr(iam_enforcement, "iam_enforcement_active", lambda: True)
    # Should not raise
    iam_enforcement.assert_iam_enforcement()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_iam_enforcement.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness.shared.iam_enforcement'`.

- [ ] **Step 3: Implement the module**

Create `harness/shared/iam_enforcement.py`:

```python
"""IAM enforcement contract for ACE-Bench.

LocalStack only checks IAM policies when started with ENFORCE_IAM=1. Without it,
security/permission fault scenarios silently pass. We detect enforcement with a
real-AWS call: a freshly created IAM user with no attached policies is granted an
access key, and a benign API call is attempted with those credentials. Under
enforcement the no-policy principal is implicitly denied (AccessDenied); without
enforcement the call succeeds.
"""

import boto3
from botocore.exceptions import ClientError

_ENDPOINT = "http://localhost:4566"
_CREDS = {
    "aws_access_key_id": "test",
    "aws_secret_access_key": "test",
    "region_name": "us-east-1",
}
_PROBE_USER = "ace-enforcement-probe"
_DENY_CODES = {"AccessDenied", "AccessDeniedException", "UnauthorizedOperation"}


def _client(service, **creds):
    return boto3.client(service, endpoint_url=_ENDPOINT, **(creds or _CREDS))


def iam_enforcement_active() -> bool:
    """Return True if LocalStack is enforcing IAM (no-policy principal is denied)."""
    iam = _client("iam")
    try:
        iam.create_user(UserName=_PROBE_USER)
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "EntityAlreadyExists":
            raise
    key = iam.create_access_key(UserName=_PROBE_USER)["AccessKey"]
    scoped = _client(
        "s3",
        aws_access_key_id=key["AccessKeyId"],
        aws_secret_access_key=key["SecretAccessKey"],
        region_name="us-east-1",
    )
    try:
        scoped.list_buckets()
        return False  # no-policy principal was allowed -> enforcement OFF
    except ClientError as exc:
        return exc.response["Error"]["Code"] in _DENY_CODES


def assert_iam_enforcement() -> None:
    """Raise RuntimeError if IAM enforcement is not active."""
    if not iam_enforcement_active():
        raise RuntimeError(
            "IAM enforcement is OFF. Security/permission scenarios cannot be "
            "scored validly. Restart LocalStack with ENFORCE_IAM=1 and "
            "IAM_SOFT_MODE=0, then re-run."
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_iam_enforcement.py -v`
Expected: both tests PASS.

- [ ] **Step 5: Wire the check into run.py**

In `harness/run.py`, the existing block (~line 320) reads:

```python
    # Step 2 — health check
    try:
        health_check()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
```

Change it to also assert enforcement. First add the import near the other `harness.shared` imports at the top of the file:

```python
from harness.shared.iam_enforcement import assert_iam_enforcement
```

Then extend the try block:

```python
    # Step 2 — health check + IAM enforcement contract
    try:
        health_check()
        assert_iam_enforcement()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
```

- [ ] **Step 6: Verify run.py still imports cleanly**

Run: `python -c "import harness.run"`
Expected: no output, no ImportError.

- [ ] **Step 7: Commit**

```bash
git add harness/shared/iam_enforcement.py tests/test_iam_enforcement.py harness/run.py
git commit -m "feat(harness): fail fast when LocalStack IAM enforcement is off"
```

---

## Task 7: Documentation updates

**Files:**
- Modify: `README.md`, `CLAUDE.md`, `RUN.md`, `docs/localstack-freetier.md`

- [ ] **Step 1: Update README.md tool counts and tooling table**

In `README.md`, update the Phase B row and any tool-count mentions: the MCP server now has **~57 diagnostic tools** (was 53) — add a note: "+4 real-AWS depth observe tools: X-Ray (`ace_get_service_graph`, `ace_get_trace_summaries`, `ace_get_trace`) and CloudTrail (`ace_lookup_events`)."

- [ ] **Step 2: Update CLAUDE.md layout and tool description**

In `CLAUDE.md`, in the Project Layout `mcp_server/tools/` block, add:
```
│       ├── observe_tracing.js  # 4 real-AWS depth tools (X-Ray x3, CloudTrail x1)
```
Update the `mcp_server/` header comment count from "50 tools" / "27 services" to the new totals (~57 diagnostic + 2 score). Add an invariant line under "Key Invariants":
```
- **IAM enforcement is required.** `run.py` calls `assert_iam_enforcement()` after the health check and exits if LocalStack was not started with `ENFORCE_IAM=1`.
```

- [ ] **Step 3: Update RUN.md prerequisites**

In `RUN.md`, in Step 1 (Start LocalStack), document that the container must be started with the Ultimate auth token and enforcement:
```bash
LOCALSTACK_AUTH_TOKEN=ls-... ENFORCE_IAM=1 IAM_SOFT_MODE=0 localstack start -d
```
Add a sentence: "The harness fails fast at startup if `ENFORCE_IAM` is not active."

- [ ] **Step 4: Add Ultimate-tier addendum**

In `docs/localstack-freetier.md`, add a top note: "**Update 2026-06-14:** the project now runs on the LocalStack **Ultimate** license, not the free/Hobby tier. The table below reflects the old free-tier service set and is retained for historical reference. Depth tooling (X-Ray, CloudTrail) and real IAM enforcement now assume Ultimate."

- [ ] **Step 5: Commit**

```bash
git add README.md CLAUDE.md RUN.md docs/localstack-freetier.md
git commit -m "docs: reflect Ultimate tier, depth tools, and IAM enforcement prerequisite"
```

---

## Final verification

- [ ] **Step 1: Run the full Node MCP suite**

Run: `node --test tests/test_mcp_server.js`
Expected: all pass, including the 7 new observe_tracing tests.

- [ ] **Step 2: Run the Python suites touched**

Run: `pytest tests/test_iam_enforcement.py tests/test_runner.py -v`
Expected: all pass.

- [ ] **Step 3: Confirm the model-facing tool list includes the new tools and excludes score tools**

Run: `pytest tests/test_agent_loop.py -v`
Expected: all pass (the new observe tools flow through `filter_model_tools` automatically; no test should break).

---

## Notes for the corpus-rebuild phase (NOT part of this plan)

Deferred per the design: enable active tracing on each scenario's resources, re-baseline `optimal_tool_calls` against the expanded tool surface, and refine fault scenarios to exploit X-Ray/CloudTrail/AccessDenied signals. This plan only delivers the tooling and the enforcement contract.
