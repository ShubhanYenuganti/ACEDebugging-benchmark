# Ultimate-Tier Depth: Real-AWS Observability MCP Tooling — Design

**Date:** 2026-06-14
**Status:** Approved (brainstorming complete; pending implementation plan)
**Author:** Shubhan + Claude

---

## Context

The LocalStack instance was upgraded from the **Hobby trial** to the **Ultimate**
license. The existing ACE-Bench diagnostic MCP server (53 diagnostic + 2 score
tools across 25 AWS SDK clients) was built entirely under the Hobby ceiling, so
every current tool covers a service that was already in the free/community tier.

Ultimate unlocks two kinds of leverage:

- **Depth** — capabilities that make the *existing* corpus more diagnostically
  realistic (X-Ray, CloudTrail, real IAM policy enforcement, App Inspector).
- **Breadth** — services that enable *new* architecture types (RDS/Aurora,
  ECS/EKS, Glue/Athena/EMR, MSK, EventBridge Pipes, Cognito, AppSync, etc.).

**Decision: depth first, breadth later.** The goal of this phase is to give each
agent the maximum *real-AWS-transferable* diagnostic coverage possible against
the existing four architectures. Breadth (new architectures) is a separate,
later phase.

After this tooling lands, the corpus will be **reimplemented**: tool-call traces
for verification re-baselined against the new tooling, active tracing enabled on
scenario resources, and fault scenarios refined to exploit the new signals. That
corpus work is **out of scope for this design** — this design delivers only the
tooling and the enforcement contract that the corpus rebuild will consume.

---

## Scope decisions

### Realism gate (key decision)

The benchmark measures debugging skill that should **transfer to real AWS**.
Capabilities were split on a realism line:

- **Included — real AWS APIs:** X-Ray, CloudTrail, IAM enforcement (diagnosed via
  real IAM APIs + AccessDenied signals).
- **Dropped — LocalStack-proprietary introspection:** App Inspector and IAM
  Policy Streams. These expose captured request/response payloads and internal
  allow/deny decision logs that have no real-AWS equivalent — too much of a
  crutch for a transfer-focused benchmark.

### In scope

1. **X-Ray** diagnostic tools (3) — `@aws-sdk/client-xray`.
2. **CloudTrail** diagnostic tool (1) — `@aws-sdk/client-cloudtrail`.
3. **IAM enforcement ON** — environment contract + fail-fast startup check.
4. A **per-architecture validation spike** (throwaway) to confirm LocalStack
   Ultimate actually populates the backing data before tool code is written.

### Out of scope (deferred to corpus-rebuild phase)

- Enabling active tracing on each scenario's resources.
- Re-baselining `optimal_tool_calls` / efficiency scoring.
- Refining fault scenarios to exploit new signals.
- App Inspector, IAM Policy Streams (dropped, see above).
- Any breadth / new-architecture tooling.

---

## Shared risk

We do not yet know how faithfully LocalStack Ultimate **populates** X-Ray traces
and CloudTrail events for our resource types — X-Ray requires active tracing on
resources, and CloudTrail management-event capture in LocalStack is historically
partial. The tools are worthless if the backend is empty. Therefore the spike
(below) is non-negotiable and runs *before* tool code is written.

---

## Section 1 — Per-architecture validation spike (Step 0, throwaway)

A short, deletable script deploys each existing architecture's `known_good.yaml`
(temporarily patched with active tracing), exercises its real `traffic_flow`, and
records go/no-go for each capability **per hop type** — fidelity varies by hop,
not by service.

| Arch | Flow shape | Hop types stress-tested |
|------|-----------|--------------------------|
| **arch01** serverless microservices | API GW REST → Lambda → DynamoDB, + SQS async | sync HTTP, SQS async hop, DynamoDB write |
| **arch02** fuzzy movie search | Lambda Function URL → Lambda → Kinesis → Firehose → Elasticsearch + S3 | Function URL, Kinesis/Firehose streaming, ES HTTP, S3 put |
| **arch08** event-driven SNS FIFO | SNS FIFO → Lambda → DynamoDB + S3 | SNS FIFO async fan-out, S3 |
| **arch12** event-driven SQS | S3 event → SQS → Lambda → DynamoDB | S3 event notification → SQS → Lambda chain |

For each arch, record per capability:

- **X-Ray** — does `GetServiceGraph` show the edges? Do traces survive async hops
  (SQS / SNS / Kinesis / S3-event), or break at the boundary?
- **CloudTrail** — are relevant API calls (PutItem, PutRecord, Publish, PutObject,
  …) captured by `LookupEvents`?
- **IAM enforcement** — with `ENFORCE_IAM=1`, does removing a required permission
  from the matrix actually produce AccessDenied, and is it visible in
  logs/CloudTrail?

**Output:** a capability × hop matrix marking ✅ / ⚠️ / ❌, captured in this design
doc as findings. Anything ❌ (e.g., "X-Ray drops the trace across Kinesis") is
documented as a known limitation and the corresponding tool is scoped to where it
works, or dropped. **Nothing in the spike ships** — it only decides what
Section 2 builds and tells us which architectures each tool actually serves.

### Spike findings (2026-06-14)

Environment: LocalStack `2026.5.0.dev29` edition pro, license active (trial),
`ENFORCE_IAM=1`, `IAM_SOFT_MODE=0`. Validated end-to-end against **arch01**
(deployed via `validate_deploy.py`, traffic via its `functional_test.py`, all 7
assertions passing). X-Ray behavior is LocalStack-wide (not arch-specific), so
arch02/08/12 were not separately deployed for X-Ray — they would show the same
empty X-Ray; CloudTrail is API-call-based and captures activity for any arch.

| Capability | Result | Evidence |
|------------|--------|----------|
| CloudTrail `LookupEvents` | ✅ **Works** | After arch01 traffic, 10–30 events captured with real names (`Query`, `GetItem`, `UpdateItem`, `DescribeTable`, `ListEventSourceMappings`). |
| CloudTrail — captures IAM-denied calls | ❌ **No** | A real `AccessDenied` S3 call was made; CloudTrail recorded 0 events with an `errorCode`. Enforcement rejects before the service layer, so denied calls are not logged. |
| X-Ray `GetTraceSummaries` / `BatchGetTraces` | ⚠️ **Backend works, no auto-instrumentation** | Manual `PutTraceSegments` → `GetTraceSummaries` round-trips (1 trace returned). But with `TracingConfig: Active` on all 6 Lambdas + real traffic, 0 traces appear — LocalStack does not auto-emit segments for Lambda invocations. Traces only exist if handler code uses the X-Ray SDK. |
| X-Ray `GetServiceGraph` | ❌ **Non-functional** | Returns 0 services even after a manually emitted segment and after live traffic. The service-map aggregation does not populate on this build. |
| IAM enforcement ON | ✅ **Confirmed** | A no-policy IAM user (fresh access key) is denied (`AccessDenied`) on `s3:ListBuckets`. This is the validated detection recipe for Task 6. |

**Consequences for the tool list (Section 2):**

- **Keep `ace_lookup_events` (CloudTrail).** Real signal out of the box, no corpus
  changes needed. Note: it surfaces *what the system did*, not IAM denials.
- **Drop `ace_get_service_graph`.** Non-functional on this build; would ship a dead
  tool.
- **Defer `ace_get_trace_summaries` and `ace_get_trace`.** Supported APIs, but they
  return nothing on the current corpus because handlers are not X-Ray-instrumented.
  Shipping them now would give the evaluated model tools that always return empty.
  Reconsider during the corpus-rebuild phase *if* we choose to instrument handlers
  with the X-Ray SDK (and accept that the service graph remains unavailable).
- **IAM enforcement** stays in scope (Task 6). Permission-fault diagnosis relies on
  Lambda CloudWatch logs (existing `ace_get_log_tail` / `ace_filter_log_events`),
  not CloudTrail.

**Revised depth surface:** +1 tool now (`ace_lookup_events`) + the IAM-enforcement
contract. X-Ray (2 trace tools) revisited in the corpus-rebuild phase.

---

## Section 2 — New tool surface

A single new file `harness/mcp_server/tools/observe_tracing.js`, following the
existing `observe_extended.js` pattern (array of tool defs spread into
`index.js`). All four are **read-only observe tools** — they inspect, never
mutate — so they are exposed to the evaluated model (not gated like score tools).
Each follows existing conventions: LocalStack endpoint + `test`/`test` creds,
JSON output, defensive empty-result handling (return structured empty, never
throw).

### X-Ray (`@aws-sdk/client-xray`)

| Tool | Maps to | Purpose |
|------|---------|---------|
| `ace_get_service_graph` | `GetServiceGraph` | Service map with nodes/edges + error/fault/throttle rates between services — "where in the flow did it break". |
| `ace_get_trace_summaries` | `GetTraceSummaries` | List recent traces over a window, filterable, with fault/error flags — find broken requests. |
| `ace_get_trace` | `BatchGetTraces` | Full segment tree for one trace ID — detailed per-hop drill-down. |

### CloudTrail (`@aws-sdk/client-cloudtrail`)

| Tool | Maps to | Purpose |
|------|---------|---------|
| `ace_lookup_events` | `LookupEvents` | Recent API-call history filterable by EventName / ResourceName / etc., surfacing AccessDenied and what the config actually did. Default window: last 1h; default max: 50 events; both overridable. |

### IAM enforcement — no new tool

Diagnosed via existing real-AWS tools (`ace_simulate_policy`, `ace_get_iam_role`,
`ace_get_caller_identity`) plus the AccessDenied signal now visible in
`ace_lookup_events`, `ace_get_log_tail`, and `ace_filter_log_events`.

**Net:** +4 tools → ~57 diagnostic + 2 score.

---

## Section 3 — IAM enforcement contract + startup check

IAM enforcement is a LocalStack *container* setting, not a tool. Because the
harness model is "you start LocalStack externally," enforcement is a **validated
contract**, not a silent assumption.

- **Required container env:** `ENFORCE_IAM=1` and `IAM_SOFT_MODE=0` (denials
  hard-fail rather than warn). Documented in RUN.md and CLAUDE.md alongside the
  auth token.
- **Startup check in `run.py`:** before a run begins, probe enforcement state via
  a **known-denied real AWS call** and assert it is denied (option (a) —
  real-AWS-pure, self-contained; avoids LocalStack-internal config endpoints).
  - If enforcement is **off** → **fail fast** with a clear message: "IAM
    enforcement required for valid scoring; restart LocalStack with
    `ENFORCE_IAM=1`." Fail-fast (not warn) because security/permission scenarios
    silently pass without enforcement — a warn-only path would let invalid runs
    score.
- **No retroactive scenario auditing** — the corpus is rebuilt against
  enforcement anyway; this guarantees the environment matches the rebuilt corpus.

---

## Section 4 — Integration, testing, docs

### MCP registration

`index.js` imports the new array and spreads it alongside the existing five
(`probe`, `probe_extended`, `observe`, `observe_extended`, `score`). No change to
the spread pattern.

### Agent exposure (Phase G)

The four new tools are plain read-only observe tools, so they flow through
`mcp_to_openai_tool` / `filter_model_tools` unchanged and become visible to the
evaluated model automatically. No allow/deny-list edits needed (unlike score
tools, which stay filtered out).

### Testing

- **Node** (`tests/test_mcp_server.js`): per-tool cases — schema validity,
  happy-path against seeded X-Ray/CloudTrail data, and graceful empty-result
  handling (structured empty, not a throw). Mirrors existing observe-tool tests.
- **Python startup check** (`tests/test_runner.py` or `test_e2e.py`): `run.py`
  fails fast when enforcement is off, proceeds when on (mock the probe).
- The throwaway spike is **not** committed as a test; its output (the
  capability × hop matrix) lands in Section 1 findings.

### Docs

- README.md + CLAUDE.md: bump tool counts (~57 diagnostic + 2 score); add the
  four tools to the layout/tool descriptions.
- RUN.md: add `ENFORCE_IAM=1` / `IAM_SOFT_MODE=0` prerequisites and the
  enforcement startup-check behavior.
- `docs/localstack-freetier.md`: now stale (we are on Ultimate) — add an
  Ultimate-tier addendum or rename.

---

## Sequencing

1. **Step 0 — Spike.** Deploy each arch with tracing, run traffic, fill the
   capability × hop matrix. Decide final tool list.
2. **Build `observe_tracing.js`** with the validated tools.
3. **Register** in `index.js`; confirm agent exposure.
4. **IAM enforcement contract** — startup check in `run.py` + docs.
5. **Tests** — Node tool tests + Python startup-check test.
6. **Docs** — counts, RUN.md prerequisites, Ultimate addendum.

Corpus rebuild (tracing enablement, trace re-baselining, fault refinement) is the
**next phase**, consuming this tooling.

---

## Success criteria

- Spike matrix exists and each shipped tool is justified by ✅/⚠️ findings on at
  least one architecture.
- Four new real-AWS observe tools registered and visible to the evaluated model.
- `run.py` fails fast when `ENFORCE_IAM` is off; proceeds when on.
- Node + Python test suites green.
- Docs reflect Ultimate tier, new tools, and the enforcement prerequisite.
- No LocalStack-proprietary introspection surface added.
