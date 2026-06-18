# Phase 2A — X-Ray Trace Tools + arch01 Instrumentation — Design

**Date:** 2026-06-17
**Status:** Approved (brainstorming complete; pending implementation plan)
**Author:** Shubhan + Claude
**Predecessor:** `2026-06-14-next-phase-features-roadmap.md` (Phase 2A item) and
`2026-06-14-ultimate-tier-depth-mcp-tooling-design.md` (deferred the two X-Ray
trace tools pending corpus instrumentation).

---

## Purpose

Phase 1 (depth) shipped CloudTrail `ace_lookup_events` and the IAM-enforcement
contract, and **deferred** the two X-Ray trace tools because LocalStack did not
auto-instrument Lambda (the tools would have returned empty). This phase builds
those two tools and pays the corpus cost that makes them return real data:
manual X-Ray SDK instrumentation of arch01 handlers, plus one new fault scenario
that rewards using the tools.

This is a depth phase against the existing corpus. Breadth (new architecture
families) remains a separate later phase.

---

## Re-validation findings (Step 0, completed 2026-06-17)

The roadmap required re-validating X-Ray against the current LocalStack build
before building. A fresh `localstack/localstack-pro:latest` image was pulled
(22h old) and started with `ENFORCE_IAM=1 IAM_SOFT_MODE=0`, moving from the prior
spike's `2026.5.0.dev29` to **`2026.5.4:6e0208279`**.

Throwaway spikes (`scratch/spike_xray_*.mjs`, gitignored) produced:

| Capability | dev29 | **2026.5.4** | Verdict |
|------------|-------|--------------|---------|
| Manual `PutTraceSegments` → `GetTraceSummaries` / `BatchGetTraces` | ✅ | ✅ | trace store round-trips emitted segments |
| **Lambda auto-instrumentation** (`TracingConfig=Active`, no SDK in handler) | ❌ 0 | ❌ **0** | still broken — 5 invocations produced 0 fresh traces (verified by excluding a leftover manually emitted trace that initially read as a false positive) |
| `GetServiceGraph` | ❌ 0 | ❌ **0** | still dead even after an emitted segment |
| CloudTrail `LookupEvents` | ✅ | ✅ | unchanged |

**Conclusion: X-Ray is unchanged across builds.** A newer LocalStack did not fix
auto-instrumentation or the service graph. Therefore:
- **Manual X-Ray SDK instrumentation of handlers is a hard dependency** — the
  tools return nothing without it.
- **`ace_get_service_graph` stays dropped** (non-functional on LocalStack).
- The trace store works for emitted segments, so the two trace tools are viable
  once handlers emit.

---

## Decisions (from brainstorming)

1. **Build both trace tools** (`ace_get_trace_summaries`, `ace_get_trace`).
2. **Instrument via `aws-xray-sdk-python`** — idiomatic and real-AWS-transferable,
   honoring Phase 1's realism gate (prefer real AWS APIs; no LocalStack-proprietary
   introspection). Hand-rolled `PutTraceSegments` in handlers was rejected as
   non-transferable.
3. **arch01 first**, fan out to arch02/08/12 in a later phase (async-boundary
   trace fidelity is the riskiest part and is deferred with the fan-out).
4. **Prove value, not just capability:** add one arch01 fault whose fastest
   diagnosis runs through a trace, and re-baseline that scenario.
5. **`ace_get_service_graph` remains out of scope.**

---

## Component 1 — Two MCP trace tools

Added to the existing `harness/mcp_server/tools/observe_tracing.js`
(`observeTracingTools` array — already imported and spread in `index.js`; no
change to the spread in `index.js:54`). Both are read-only **observe** tools, so
they flow through `mcp_to_openai_tool` / `filter_model_tools` unchanged and are
auto-exposed to the evaluated model (unlike score tools). Each follows existing
conventions: LocalStack endpoint + `test`/`test` creds, JSON output, defensive
structured-empty handling (return `{ error, error_type }` or empty arrays, never
throw). Uses `@aws-sdk/client-xray` (already present in `node_modules`).

### `ace_get_trace_summaries` → `GetTraceSummaries`

List recent traces over a window, flag broken requests.

- **Inputs:**
  - `window_minutes` — default 60, clamped to [1, 1440].
  - `filter_expression` — optional X-Ray filter expression string (e.g.
    `fault = true`, `service("name")`).
  - `only_errors` — optional bool; convenience that applies
    `error = true OR fault = true` when no explicit `filter_expression` is given.
- **Output:**
  ```
  {
    traces: [{ id, duration, response_time, has_error, has_fault,
               has_throttle, http_status, entry_service }],
    count, window_minutes
  }
  ```

### `ace_get_trace` → `BatchGetTraces`

Full segment tree for one trace — "where in the flow did it break".

- **Input:** `trace_id` (required, single — single-trace drill-down).
- **Output:** normalized segment tree:
  ```
  {
    trace_id,
    segments: [{
      name, origin, error, fault, throttle, http_status, duration,
      subsegments: [{ name, namespace, error, fault, http_status, aws_operation }]
    }]
  }
  ```
  Returns structured-empty (`{ trace_id, segments: [] }`) if the trace is absent.

---

## Component 2 — Shared X-Ray instrumentation module

A small `xray_instrument.py` bundled alongside the corpus arch01 Lambda handlers
(and their scenario copies). Responsibilities:

- Configure the recorder with `AWS_XRAY_CONTEXT_MISSING=LOG_ERROR` (do not crash
  handlers when no context exists).
- `patch_all()` so boto3 downstream calls (DynamoDB / SQS / S3) auto-emit
  **subsegments** — this is what gives per-hop diagnostic value.
- A `@traced(name)` decorator that **begins a segment explicitly** (LocalStack
  provides no Lambda facade segment, so relying on the Lambda integration alone
  emits nothing), runs the handler, records raised exceptions as a segment
  `fault`, and ends/flushes the segment.

### Emitter wiring — the one open implementation risk

`aws-xray-sdk` emits via UDP to the X-Ray daemon (`AWS_XRAY_DAEMON_ADDRESS`,
default `127.0.0.1:2000`). Whether that reaches LocalStack's X-Ray backend from
*inside the Lambda container* is unproven. The implementation plan's **first
step** resolves this (see Gate below): either point the daemon address at
LocalStack, or configure the SDK emitter to call `PutTraceSegments` directly
(which is proven to round-trip). This choice does not change the handler-facing
`@traced` API — only the module's internal emitter setup.

---

## Component 3 — arch01 corpus value work

- **Instrument the corpus arch01 handlers** (`corpus/arch_01_.../deployment/lambda/*.py`)
  — the source of truth — with `@traced` + `patch_all()` via the shared module.
- **Instrument the existing arch01 scenario handler copies** so the trace tools
  are not empty during those scenario runs. (Existing scenarios' `optimal_*`
  baselines are NOT re-cut here unless adding the trace path changes the optimal;
  broad re-baselining is deferred.)
- **Add one new arch01 fault scenario** whose fastest diagnosis runs through a
  trace — e.g. a downstream hop that errors mid-flow, surfacing as a faulted
  subsegment in `ace_get_trace`. It gets a full `fault_manifest.json` whose
  `optimal_diagnostic_path` runs `ace_get_trace_summaries` →
  `ace_get_trace`, and a re-baselined `optimal_tool_calls` /
  `optimal_files_changed` / `optimal_lines_changed` against the expanded surface.

---

## Data flow

1. Scenario deploys; its `traffic_flow` runs against instrumented handlers, which
   emit segments (parent + boto3 subsegments) to LocalStack's X-Ray store.
2. Model calls `ace_get_trace_summaries` (optionally `only_errors`) to find the
   faulted trace.
3. Model picks a trace `id` and calls `ace_get_trace` to read the segment tree
   and see which hop carries `error`/`fault`.
4. Model forms and applies a fix hypothesis.

---

## Error handling

Both tools follow the `ace_lookup_events` pattern: wrap the SDK call, return
structured-empty on missing data, and return `{ error, error_type }` (never
throw) on SDK failure. The `@traced` decorator must never break a handler:
context-missing is logged, not raised, and emitter failures are swallowed.

---

## Testing

- **Node** (`tests/test_mcp_server.js`): for each new tool — schema validity,
  happy-path parse against X-Ray data seeded via `PutTraceSegments` (proven to
  round-trip), and graceful structured-empty handling. Mirrors existing
  observe-tool tests.
- **Python / corpus:** the new arch01 fault scenario validates end-to-end
  (`validate-corpus` flow + functional test) with instrumentation present; the
  arch01 known-good functional test still passes all assertions (no regression
  from adding instrumentation).

---

## Sequencing (for the implementation plan)

1. **Emission gate (Step 1).** Instrument one arch01 handler with the shared
   module, deploy, run real traffic, and confirm a trace with downstream
   subsegments appears via the new tools. Lock the emitter wiring here.
2. **Build the two tools** in `observe_tracing.js`; confirm agent exposure.
3. **Instrument** corpus arch01 handlers + existing arch01 scenario copies.
4. **New X-Ray fault scenario** + `fault_manifest.json` + re-baseline.
5. **Tests** — Node tool tests + corpus validation.
6. **Docs** — bump tool counts, add a "Tracing observe tools" X-Ray entry,
   note the instrumentation dependency.

---

## Out of scope (later phases)

- arch02/08/12 instrumentation and async-hop fidelity (SQS/SNS FIFO/Kinesis/
  Firehose/S3-event trace propagation).
- `ace_get_service_graph` (non-functional on LocalStack).
- Broad efficiency re-baseline and multi-fault refinement beyond the one new
  scenario.
- Any breadth / new-architecture (Phase 2B) work.

---

## Success criteria

- Re-validation findings recorded (done — see above).
- `ace_get_trace_summaries` and `ace_get_trace` registered, real-AWS-mapped, and
  visible to the evaluated model.
- arch01 handlers emit traces with downstream subsegments; the new tools return
  non-empty trace data during an arch01 run.
- One new arch01 fault scenario whose optimal diagnostic path runs through the
  trace tools, with re-baselined optimal counts.
- Node + corpus tests green; arch01 known-good functional test still passes.
- No LocalStack-proprietary introspection surface added; `ace_get_service_graph`
  not shipped.
