# Breadth Expansion Framework — Ultimate-Tier New Architectures + Corpus Migration — Design

**Date:** 2026-06-20
**Status:** Approved (brainstorming complete; spec review waived by user; transitioning to writing-plans)
**Author:** Shubhan + Claude
**Predecessors:**
- `2026-06-14-ultimate-tier-depth-mcp-tooling-design.md` (Phase 1 depth — CloudTrail + IAM enforcement)
- `2026-06-14-next-phase-features-roadmap.md` (depth + breadth roadmap menu)
- `2026-06-17-phase-2a-xray-trace-tools-design.md` (X-Ray trace tools + arch01 instrumentation)
- `2026-06-19-phase-2b1-rds-architecture-design.md` (Phase 2B-1 — RDS/arch03, the first breadth family)

---

## Context

The depth phase is complete: the MCP server now carries 63 tools (61 diagnostic + 2 score),
including CloudTrail `ace_lookup_events`, two X-Ray trace tools, three RDS tools, and a
fail-fast IAM-enforcement contract. The first breadth family — Relational data (RDS/arch03) —
shipped via the Phase 2B-1 plan, establishing the **spike-gated breadth pattern**: prove
LocalStack Ultimate emulation fidelity *before* building tools or faults, because emulation
is uneven and posture-only faults are forbidden.

This design covers the **rest of the breadth roadmap** plus the **migration of the three
pre-Ultimate corpora** (arch02, arch08, arch12) onto the new tooling. It is deliberately a
**framework**: it defines one repeatable lifecycle and three cross-cutting gates that every
downstream plan instantiates, so the six implementation plans do not each re-derive the
spike methodology, the realism rules, or the discoverability QA gate.

**Hard environmental constraint:** the author of these plans cannot run LocalStack. Therefore
every plan is written so that **LocalStack startup, spikes, and verification are explicit,
self-contained executor steps** — the plans request the environment, they do not assume it.
Each spike is a true **kill-gate**: if Ultimate does not emulate a family with execution
fidelity, the plan records the finding and **shelves that family** rather than shipping empty
tools or unenforced faults. This generalizes the RDS-spike lesson, where security-group,
`max_connections`, and `kms:Decrypt` enforcement all turned out to be absent and forced
fault-mechanism fallbacks.

---

## Goal & scope

Produce **one framework spec (this document) + six implementation plans**:

| # | Plan | Covers |
|---|------|--------|
| 1 | Breadth — Containers | ECS / EKS / ECR |
| 2 | Breadth — Streaming & analytics | Kinesis / Firehose / OpenSearch (priority-1, serve arch02) + MSK / Glue / Athena / EMR (spike-gated) |
| 3 | Breadth — EventBridge Pipes | EventBridge Pipes |
| 4 | Breadth — Cognito | User / Identity pools |
| 5 | Breadth — AppSync | GraphQL APIs |
| 6 | Corpus migration | arch02 / arch08 / arch12 → X-Ray + re-baseline + breadth-tool adoption (3 tracks) |

The framework = spec only (no separate execution plan). Each breadth family and the migration
get a plan that references this spec.

### In scope
- The repeatable 6-task breadth lifecycle (Section 1).
- The LocalStack-load preamble every spike embeds (Section 2).
- The spike kill-gate methodology + capability×fidelity matrix + fault-enforcement
  empiricism rule (Section 2).
- The realism gate (Section 3).
- The discoverability QA gate — four checks, pass bar, judge model, remediation ladder
  (Section 4).
- Per-family spike targets and the migration's three-track shape (Section 5).
- Sequencing, gating, and inter-plan dependencies (Section 6).

### Out of scope
- Actually executing any spike or building any tool/corpus (that is each plan's job).
- LocalStack-proprietary introspection (App Inspector, IAM Policy Streams) — permanently
  excluded by the realism gate.
- `ace_get_service_graph` — remains dropped (non-functional on LocalStack) unless a future
  spike shows the service map populates.
- Any new runtime scoring dimension. The discoverability QA gate is a **build-time corpus-QA
  gate**, not a per-run score.

---

## Section 1 — The breadth lifecycle (6-task spine)

Every breadth plan (1–5) instantiates this spine. The migration plan (6) reuses Tasks 1, 5,
and 6 and replaces Tasks 2–4 with its three migration tracks.

### Task 1 — De-risking spike (KILL-GATE)
Exploratory, not TDD. Validates the family's premises on the current LocalStack build before
any fan-out. **Downstream tasks do not start until this passes.** Steps:
1. **Load LocalStack** (Section 2 preamble) and confirm the family's services appear in
   `/_localstack/health`.
2. Write a minimal CFN stack exercising the family's services + a gitignored
   `scratch/spike_<family>.mjs` that provisions, drives real traffic, and probes — **as two
   separate questions**:
   - **(a) Tool-data fidelity:** does each candidate tool's backing API return real,
     non-empty data after real activity?
   - **(b) Fault enforcement:** does each candidate fault mechanism actually get *enforced*
     by LocalStack (produces a real, Pass-1-detectable behavioral symptom), or is it
     posture-only (config accepted but no behavioral effect)?
3. Record a **capability×fidelity matrix** (✅ works / ⚠️ partial-scoped / ❌ dead) in the
   plan file. Lock the final tool list and the fault mechanisms (each with a **primary +
   fallback**), and decide whether to X-Ray-instrument the family's handlers.
4. Tear down. Nothing from the spike ships except the recorded findings.

**Gate rule:** any candidate tool whose backend is empty across the family is dropped; any
fault mechanism not enforced is replaced by its fallback or dropped. If the family has no
emulation fidelity at all, the plan stops here with a documented "shelved" finding.

### Task 2 — MCP diagnostic tooling (TDD)
New `harness/mcp_server/tools/<family>.js`, wired into `index.js` (import + spread), with
`node:test` cases (membership, missing-arg error handling, happy-path against seeded data,
graceful empty-result). Add SDK deps to `harness/mcp_server/package.json`. Tools follow the
`probe_rds.js` conventions: `{ name, description, inputSchema, async handler(args) }`, return
a plain object (never throw), LocalStack endpoint + `test`/`test` creds, `{ error }` on
failure. Descriptions must satisfy the static rubric in Section 4 (API mapped, fields
returned, when-to-reach-for-it).

### Task 3 — Corpus architecture (known-good)
`corpus/arch_NN_<family>/` with `known_good.yaml`, `functional_test.py` (arch01 harness
conventions — `emit_pass`/`emit_fail`/`finalize`, `ASSERT pass|fail` output), `traffic_flow.md`,
and `deployment/` handlers (vendored deps as needed). Deploys clean (`CREATE_COMPLETE`) under
IAM enforcement; functional test passes. X-Ray-instrument handlers if Task 1 approved it
(reuse the arch01/arch03 `xray_instrument.py` pattern — `XRayTracedConn` for SQL/DB clients,
`patch_all` + `PutSegmentsEmitter` otherwise).

### Task 4 — Fault scenarios
N behavior-manifesting faults, each in `scenarios/arch0N_fault0M_<class>/` with `faulted.yaml`
(known-good + ONE injected fault), symptom-only `scenario.md`, and `fault_manifest.json`
(never exposed). **Mandatory fault-design rule:** every fault produces an observable symptom
Pass-1 functional verification detects; `scenario.md` states only the symptom, never the
cause; no posture-only faults. Use the Task 1-locked mechanisms (primary or fallback).

### Task 5 — Discoverability QA gate
Run the four checks in Section 4 for every scenario; record pass/fail and any remediation.

### Task 6 — Documentation
Bump tool counts and corpus/scenario inventory consistently across `README.md`, `RUN.md`,
`CLAUDE.md`. Verify counts with the import-and-count node one-liner (as in the arch03 plan).

---

## Section 2 — LocalStack-load preamble + spike methodology

Because the executor — not the plan author — runs LocalStack, **every Task 1 (and every
migration verification) opens with this preamble** verbatim:

```bash
# Start LocalStack Ultimate with IAM enforcement (required for valid scoring).
LOCALSTACK_AUTH_TOKEN=ls-... ENFORCE_IAM=1 IAM_SOFT_MODE=0 localstack start -d
until localstack status services 2>/dev/null | grep -q running; do sleep 2; done
# Confirm the family's services are emulated on this build:
curl -s localhost:4566/_localstack/health | grep -oE '"<svc1>"\s*:\s*"[a-z]+"'
# Record the LocalStack version for the findings block:
curl -s localhost:4566/_localstack/info | grep -oE '"version":\s*"[^"]+"'
```

**Spike script conventions:** gitignored under `scratch/` (already in `.gitignore`),
`scratch/spike_<family>.mjs` (+ `scratch/spike_<family>_stack.yaml` for the CFN). Uses the
`@aws-sdk/client-*` for the family at LocalStack endpoint with `test`/`test`. Probes provision
→ traffic → tool-data fidelity → fault enforcement, printing one labeled line per probe.

**Findings block:** each plan's Task 1 ends by appending a `## Task 1 findings` section to the
plan file: the LocalStack version, the capability×fidelity matrix, the locked tool list, and
the locked fault mechanisms (primary + fallback) — exactly the shape the arch03 plan used.

**Fault-enforcement empiricism rule (mandatory):** a fault mechanism ships **only if the spike
empirically proved LocalStack enforces it** with a behavioral symptom. Config that is accepted
but inert (the RDS SG / `max_connections` / `kms:Decrypt` outcomes, or `DBInstanceClass`
latency) is forbidden as a fault — it is posture-only. When the obvious mechanism is inert, the
plan falls back to an enforced one (e.g. wrong env var, removed IAM action, too-low Lambda
timeout) and records the substitution.

---

## Section 3 — Realism gate (carried forward)

The benchmark measures debugging skill that transfers to real AWS. Therefore:
- **Prefer real AWS APIs.** Tools map to genuine AWS SDK calls (`Describe*`, `Get*`,
  `Lookup*`, raw TCP probes) whose outputs an engineer would also see on real AWS.
- **No LocalStack-proprietary introspection** — App Inspector and IAM Policy Streams stay
  excluded; they expose captured payloads / internal allow-deny logs with no real-AWS
  equivalent and would be a transfer-defeating crutch.
- **Handler instrumentation must look real.** X-Ray instrumentation uses the AWS X-Ray SDK
  idioms (`@traced` / `XRayTracedConn` / `patch_all`); only the emitter wiring is
  environment-specific.

---

## Section 4 — Discoverability QA gate (build-time, per scenario)

A scenario is only a fair benchmark item if the fault is **discoverable** from what the model
sees: the symptom-only `scenario.md`, the faulted deployment, and the tool list (names +
descriptions + input schemas) — never the manifest or known-good. The gate runs four checks at
build time inside Task 5 and records results in the plan.

1. **Agent-exposure plumbing.** Each new tool flows through `mcp_to_openai_tool` /
   `filter_model_tools` and is visible in the model's runtime tool list; score tools
   (`ace_verify_fix`, `ace_score_run`) remain filtered out.
2. **Diagnostic-path reachability.** A spike walks the manifest's `optimal_diagnostic_path`
   with the *real* MCP tools against the deployed faulted stack and confirms the tools surface
   the signal that pinpoints the fault.
3. **Blind-triggering.** Two stages:
   - **3a — Static rubric (cheap pre-gate).** Before spending judge tokens, every tool
     description must state: (a) the real AWS API it maps to, (b) the concrete fields/signals
     it returns, (c) when to reach for it (symptom / fault-class). Missing (c) fails here.
   - **3b — LLM-judge blind selection.** An **LLM judge — a cheaper model distinct from the
     primary eval target** — is given ONLY the symptom + the full tool list (no manifest) and
     asked which tools it would call, in order, to diagnose the symptom. Run **N=5** trials.
     **Pass = every tool on `optimal_diagnostic_path` is named in the judge's first-K picks in
     ≥3/5 trials**, where K = (number of tools on the optimal path) + 1 slack. This tests the
     *tool/corpus*, not the model: the bar is "the right tool is reachable from its
     description," never "the model solved it."
   **Remediation ladder on failure (cheapest + most honest first):**
   1. Improve the **tool description** (add the signal it surfaces + the fault-class it serves)
      — preferred, because it helps every scenario.
   2. Sharpen the **`scenario.md` symptom** to be more faithful to what is observable — still
      never naming the cause.
   3. Re-baseline the **`optimal_diagnostic_path`** to an equally-short, more naturally-taken
      diagnostic route.
   4. Last resort: **redesign or drop** the fault (same reasoning that dropped posture-only and
      non-reproducible faults).
   **Hard guardrail:** the fix makes honest signals *legible* — it never leaks the faulted
   resource/property into the symptom or pads a description with hints.
4. **Trace + scoring integration.** New tool calls land correctly in `tool_call_trace.json`;
   efficiency/identification scoring reads them; `optimal_tool_calls` is re-baselined against
   the expanded surface.

---

## Section 5 — Per-family spike targets + migration shape

### Breadth families — what each spike must specifically de-risk

| Family | Corpus shape (candidate) | Highest-risk spike questions | Candidate tools | Candidate faults (subject to enforcement check) |
|--------|--------------------------|------------------------------|-----------------|------------------------------------------------|
| **Containers** (ECS/EKS/ECR) | API/Lambda or ALB → ECS service (Fargate) → backing store; image in ECR | Does a task actually reach RUNNING and report `stoppedReason`? Does ECR push/pull work? | describe service/task/deployment, task health + stopped-reason, ECR image/tag presence | bad image tag (ImagePull fail), task-def env/secret misconfig, insufficient task-role perms, wrong container port |
| **Streaming & analytics** | arch02-shaped ingest → Kinesis → Firehose → OpenSearch + S3 (priority-1); analytics variants priority-2 | Priority-1: real Kinesis→Firehose→OpenSearch delivery. Priority-2: Glue job runs? Athena query executes? MSK lag? EMR steps? (expect most ❌) | stream/shard + consumer-lag, Firehose delivery state, OpenSearch domain/health; (gated) Glue/Athena/MSK/EMR status | wrong stream/delivery target, dropped/blocked delivery, missing put/consume IAM, index/mapping mismatch |
| **EventBridge Pipes** | source (SQS/Kinesis/DDB stream) → Pipe (filter/enrich) → target (Lambda/SQS) | Does a Pipe poll source → filter/enrich → invoke target end-to-end? | pipe state, source/target/enrichment wiring, DLQ | broken target wiring, missing pipe-role perms, filter pattern that silently drops events |
| **Cognito** | API GW (Cognito authorizer) → Lambda → store; User Pool + app client | Does LocalStack issue real JWTs and validate them at an API GW authorizer? | pool config, app-client config, token/authorizer probe | misconfigured app client, wrong token audience/issuer, missing scope/claim |
| **AppSync** | AppSync GraphQL API → resolver → data source (DynamoDB/Lambda) | Does a resolver actually resolve a query against its data source? | API/resolver/data-source config, schema/resolver wiring | broken resolver mapping template, wrong data source, missing resolver IAM |

Each family's known-good corpus is assigned the next free `arch_NN` (arch03 is taken; arch04+
allocated in plan order, confirmed against the live `corpus/` listing at build time). Fault
count per family follows the corpus norm (target ~4 behavior-manifesting faults, more if the
family supports distinct enforced mechanisms; fewer only if the spike limits them — as arch03
landed at 3 after performance proved unreproducible).

### Migration plan — one plan, three tracks

- **Shared prelude:** load LocalStack; treat arch01's `_shared/xray_instrument.py` +
  `vendor_xray.sh` as the canonical instrumentation pattern.
- **Track A — arch08 (SNS FIFO) + arch12 (SQS):** X-Ray-instrument all handlers (vendoring +
  `TracingConfig: Active` + `xray:PutTraceSegments`/`PutTelemetryRecords` IAM) → **spike the
  async-hop trace fidelity per arch** (the depth design flagged that X-Ray may break across
  SNS/SQS/S3-event boundaries — confirm whether the trace survives the hop or the tool is
  scoped to the synchronous segment) → add ≥1 trace-diagnosable fault per arch → add the
  long-deferred **≥1 Pass-4 concurrency-probe scenario** (the event probe is implemented but
  unused) → re-baseline `optimal_*`.
- **Track B — arch02 (Kinesis/Firehose/OpenSearch):** X-Ray-instrument → **adopt the new
  Streaming breadth tools** for its diagnostic paths → re-baseline. **Blocked by the Streaming
  plan** (its tools must exist and have passed their spike).
- **Cross-cutting:** re-run the full discoverability QA gate (Section 4) on every migrated
  scenario; re-baseline `optimal_tool_calls`/`optimal_files_changed`/`optimal_lines_changed`
  across all three archs against the expanded tool surface.

---

## Section 6 — Sequencing, gating, dependencies

1. **Framework spec (this doc) first** — committed before any plan; all plans reference it.
2. **Breadth plans 1–5** — mutually independent; each is self-gated by its own Task 1 spike
   and can be executed in any order or in parallel. **Streaming (plan 2) is prioritized**
   because the migration's Track B depends on its tools.
3. **Migration plan (6) last.** Track A (arch08/12 X-Ray) is *not* blocked by any breadth plan
   (X-Ray is already confirmed on arch01) and could start early; Track B (arch02) is **blocked
   by the Streaming plan**. The plan states both dependencies explicitly.

**Pre-flight for every plan execution:** `cd harness/mcp_server && npm install` (the AWS-SDK
clients added across these plans must be installed — the current checkout is missing
`@aws-sdk/client-{xray,cloudtrail,rds}` in `node_modules`, which blocks the Node suite).

---

## Success criteria

- This framework spec is committed; six plans exist, each opening with the LocalStack-load
  preamble and a spike kill-gate as Task 1.
- Each breadth plan's spike empirically decides its tool list and fault mechanisms (no tool
  ships against an empty backend; no posture-only fault ships).
- Every scenario (new and migrated) passes the four-check discoverability QA gate, with N=5 /
  ≥3/5 blind-triggering judged by a cheaper distinct model, and any remediation recorded.
- The migration brings arch02/08/12 onto X-Ray with re-baselined efficiency metrics and at
  least one Pass-4 concurrency-probe scenario.
- Realism gate held throughout: real AWS APIs only; no LocalStack-proprietary introspection.
- Tool counts and corpus inventory stay consistent across README/RUN/CLAUDE after each plan.
