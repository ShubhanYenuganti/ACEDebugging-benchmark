# Next-Phase Features Roadmap — Depth (X-Ray) + Breadth

**Date:** 2026-06-14
**Status:** Draft roadmap — requires brainstorming/refinement per item before implementation
**Predecessor:** `2026-06-14-ultimate-tier-depth-mcp-tooling-design.md` (depth phase 1 — shipped CloudTrail `ace_lookup_events` + IAM-enforcement contract; deferred X-Ray)

---

## Purpose

Phase 1 (depth) shipped the real-AWS diagnostics that work on LocalStack Ultimate
today: CloudTrail `ace_lookup_events` and a fail-fast IAM-enforcement contract.
The validation spike deferred X-Ray and we have not yet started breadth (new
Ultimate-only architectures). This document captures the deferred and follow-on
work so it isn't lost, and frames each item with enough detail to brainstorm into
its own spec. **It is a roadmap, not an approved implementation spec** — each
phase below should go through `brainstorming` → `writing-plans` before build.

---

## Phase 2A — X-Ray trace tools (the headline deferred item)

### Why deferred (spike findings, 2026-06-14)

On LocalStack `2026.5.0.dev29`:
- `PutTraceSegments` → `GetTraceSummaries`/`BatchGetTraces` **round-trips** — the
  X-Ray trace store works for explicitly emitted segments.
- LocalStack does **not auto-instrument Lambda**: with `TracingConfig: Active` on
  all Lambdas and real arch01 traffic, **0 traces** appeared. Traces only exist if
  application/handler code emits segments via the AWS X-Ray SDK.
- `GetServiceGraph` returned **0 services even for a manually emitted segment** —
  the service-map aggregation is non-functional on this build.

### Proposed scope

1. **Re-validate first.** Before building, re-run the spike's X-Ray checks against
   the then-current LocalStack version. If a newer build auto-instruments Lambda
   and/or populates the service graph, scope expands accordingly. The spike script
   `scratch/spike_observability.mjs` (gitignored) is the starting point.
2. **Build the two trace tools** (code already drafted in the predecessor spec,
   Section 2):
   - `ace_get_trace_summaries` → `GetTraceSummaries` (list traces, error/fault/throttle flags)
   - `ace_get_trace` → `BatchGetTraces` (segment tree for one trace)
   Add them to the existing `harness/mcp_server/tools/observe_tracing.js`
   (`observeTracingTools`) — the file was named and structured to receive them.
3. **`ace_get_service_graph` stays dropped** unless re-validation shows it works.
4. **Handler instrumentation is a hard dependency** (see Corpus Rebuild below):
   the trace tools return nothing until scenario Lambda handlers emit X-Ray
   segments. Building the tools without instrumenting handlers ships empty tools —
   so Phase 2A and the corpus instrumentation must land together.

### Open questions for brainstorming
- Do we instrument handlers manually (X-Ray SDK calls in handler code) or rely on
  a LocalStack/runtime auto-instrument flag if a newer version adds one?
- Is per-hop trace fidelity across async boundaries (SQS/SNS/Kinesis/S3-event)
  good enough on the target version to be worth the corpus cost? Re-test the four
  architectures' async hops specifically.
- If the service graph stays dead, is trace-summary + single-trace drill-down
  enough diagnostic value to justify instrumenting every scenario?

---

## Phase 2B — Breadth: new Ultimate-only architectures + tooling

Ultimate unlocks services that enable entirely new corpus architecture types
(out of reach on Hobby). Each new architecture family needs (a) a corpus
architecture with `known_good.yaml` + `functional_test.py`, and (b) any missing
MCP diagnostic tools for its services. Candidate families, roughly by
benchmark value and emulation maturity:

| Family | New services | New MCP tools likely needed |
|--------|-------------|------------------------------|
| Relational data | RDS / Aurora, RDS Data API | describe DB instance/cluster, parameter groups, connectivity probe |
| Containers | ECS, EKS, ECR | describe service/task/deployment, task health, image presence |
| Streaming/analytics | MSK (Kafka), Glue, Athena, EMR | topic/consumer-group lag, Glue job/crawler state, Athena query status |
| Event routing | EventBridge Pipes | pipe state, source/target wiring |
| Auth | Cognito (User/Identity Pools) | pool config, app client, token/authorizer probe |
| GraphQL | AppSync | API/resolver/data-source config |

This list is a menu, not a commitment. Breadth should be sequenced one family at
a time, each as its own spec → plan → corpus build, picking families by (1)
emulation fidelity on Ultimate (validate with a spike, as we did for X-Ray) and
(2) the realism of debuggable fault classes they enable.

### Hard rule carried forward
Keep the real-AWS-transferable bar from Phase 1: prefer real AWS APIs; avoid
LocalStack-proprietary introspection (App Inspector, IAM Policy Streams) so
diagnostic skill transfers to real AWS.

---

## Corpus rebuild (dependency for 2A; ongoing for 2B)

Deferred from Phase 1 and required to realize X-Ray value:

- **Enable active tracing + X-Ray SDK instrumentation** in scenario Lambda
  handlers so the trace tools have data. (Scenario templates already deploy fine
  under IAM enforcement — arch01 verified — so only tracing/instrumentation is
  net-new.)
- **Re-baseline efficiency scoring**: `optimal_tool_calls` / `optimal_files_changed`
  / `optimal_lines_changed` in each `fault_manifest.json` against the expanded
  diagnostic surface (CloudTrail now; X-Ray later).
- **Refine fault scenarios** to exploit the new signals (e.g., faults whose
  fastest diagnosis path runs through CloudTrail call history or an X-Ray trace),
  and add at least one scenario exercising the event-driven Pass-4 concurrency
  probe (still unused).
- **Test-fixture note:** under IAM enforcement, any fixture/scenario that creates
  Lambdas must define a real assumable role (the Node suite's `before()` hook was
  fixed for this; watch for the same pattern elsewhere).

---

## Suggested sequencing

1. **Phase 2A** — re-validate X-Ray on current LocalStack; if viable, instrument
   handlers + ship the two trace tools together. (Centerpiece of "next phase.")
2. **Corpus rebuild pass** — tracing/instrumentation + efficiency re-baseline +
   fault refinement (couples with 2A; continues into 2B).
3. **Phase 2B** — breadth, one architecture family at a time, each spike-gated.

Each phase: `brainstorming` → `writing-plans` → `subagent-driven-development`,
matching the workflow that produced Phase 1.
