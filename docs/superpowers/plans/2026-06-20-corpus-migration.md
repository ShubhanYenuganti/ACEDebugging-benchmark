# Corpus Migration (arch02 / arch08 / arch12) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Worker subagents use model `sonnet`; overseer uses model `opus`.

---

## Goal

Bring the three pre-Ultimate corpora — **arch08 (SNS FIFO)**, **arch12 (SQS)**, and **arch02 (fuzzy movie search)** — onto the depth+breadth tooling that landed after they were authored. Concretely: X-Ray-instrument every handler so the X-Ray trace tools (`ace_get_trace_summaries`, `ace_get_trace`) and CloudTrail (`ace_lookup_events`) become usable on these archs; adopt the Streaming breadth tools on arch02's diagnostic paths; add at least one trace-diagnosable fault per event-driven arch and at least one Pass-4 concurrency-probe scenario (the event probe is implemented but no scenario yet declares it); and re-baseline every `optimal_*` metric against the expanded tool surface.

This is the framework's **migration plan (#6 of 6)**. It reuses the breadth lifecycle's Task 1 (spike), Task 5 (discoverability QA), and Task 6 (docs), and replaces Tasks 2–4 with **three migration tracks**.

## Tracks at a glance

| Track | Archs | Work | Blocking dependency |
|---|---|---|---|
| **A** | arch08 (SNS FIFO), arch12 (SQS) | X-Ray-instrument all handlers; add ≥1 trace-diagnosable fault per arch; add ≥1 Pass-4 concurrency-probe scenario; re-baseline | **None** — X-Ray already confirmed on arch01; can start immediately |
| **B** | arch02 (Kinesis/Firehose/OpenSearch) | X-Ray-instrument; adopt the new Streaming breadth tools on diagnostic paths; re-baseline | **Blocked by the Streaming plan** (`2026-06-20-breadth-streaming-analytics.md`) — its tools must exist and have passed their spike |
| **Cross-cutting** | all three | re-run discoverability QA (§4); re-baseline `optimal_tool_calls`/`optimal_files_changed`/`optimal_lines_changed` across all migrated scenarios | Tracks A + B complete |

---

## Global Constraints

- LocalStack runs with `ENFORCE_IAM=1 IAM_SOFT_MODE=0`; X-Ray emission requires each instrumented Lambda's role to allow `xray:PutTraceSegments` and `xray:PutTelemetryRecords`, and the function must set `TracingConfig: Active`.
- AWS creds: accessKeyId=`test`, secretAccessKey=`test`, region=`us-east-1`, endpoint `http://localhost:4566`. IAM fake account `000000000000`.
- Prefer real AWS APIs; never use LocalStack-proprietary introspection (App Inspector, IAM Policy Streams).
- `fault_manifest.json` and `known_good.yaml` are NEVER exposed to the model.
- **Fault design principle (mandatory):** every new fault must produce an observable behavioral symptom Pass-1 functional verification detects; `scenario.md` states only the symptom, never the cause. No posture-only faults. A "trace-diagnosable" fault is still behavior-manifesting first — the trace makes it *legible*, it does not replace the Pass-1 symptom.
- **No regressions:** the existing arch02/08/12 scenarios already pass deployment + functional + scoring. Instrumentation must NOT change their pass/fail outcome. After instrumenting, every pre-existing scenario must still deploy `CREATE_COMPLETE` and its functional test must still produce the same result.
- **Canonical instrumentation pattern (reuse, do not reinvent):**
  - `corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/deployment/lambda/_shared/xray_instrument.py` — the proven `PutSegmentsEmitter` + explicit-segment + `patch_all` pattern (handles the two LocalStack quirks documented in its header: LambdaContext bypass and `streaming_threshold`).
  - `scripts/vendor_xray.sh` — vendors the `aws-xray-sdk` into each handler dir so the Lambda zip is self-contained.
- MCP tool files live in `harness/mcp_server/tools/`. This plan adds **no new tools** — it adopts the existing X-Ray/CloudTrail tools and the Streaming-plan tools.
- Node MCP tests: `node --test tests/test_mcp_server.js`. Spike + verification run against a live LocalStack.
- **Pre-flight before any execution step:** `cd harness/mcp_server && npm install` (ensures `@aws-sdk/client-xray`, `@aws-sdk/client-cloudtrail`, and the Streaming clients are present).

---

## Task 1: De-risking spike — async-hop trace fidelity (the gate)

Exploratory, not TDD. The depth design flagged that X-Ray traces may **break across async boundaries** (SNS→SQS→Lambda, S3-event→Lambda). Before instrumenting and before writing any trace-diagnosable fault, this spike answers **per arch**: does a single trace survive the async hop end-to-end, or is the trace scoped only to the synchronous segment of each Lambda? The answer decides whether a "trace-diagnosable" fault is honestly diagnosable via `ace_get_trace` or whether the tool is scoped to a single hop (in which case the fault's diagnostic path uses per-segment traces + CloudTrail instead). **Do not start Track A or B until this passes.**

**Files:**
- Create: `scratch/spike_trace_arch08.mjs`, `scratch/spike_trace_arch12.mjs` (gitignored)
- Create: `scratch/spike_trace_arch02.mjs` (gitignored; only meaningful once the Streaming plan's tools/corpus exist — may be deferred to Track B start)

**Interfaces:**
- Consumes: the existing arch08/12/02 corpora; the `_shared/xray_instrument.py` pattern; `ace_get_trace_summaries`/`ace_get_trace`/`ace_lookup_events`.
- Produces: a per-arch trace-fidelity verdict (full-trace / per-segment-only / no-trace) and the locked trace-diagnosable-fault diagnostic shape — recorded in the `## Task 1 findings` section.

- [ ] **Step 1: Load LocalStack with IAM enforcement and confirm X-Ray + the event services**

```bash
# Start LocalStack Ultimate with IAM enforcement (required for valid scoring).
LOCALSTACK_AUTH_TOKEN=ls-... ENFORCE_IAM=1 IAM_SOFT_MODE=0 localstack start -d
until localstack status services 2>/dev/null | grep -q running; do sleep 2; done
# Confirm xray + the event-driven services are emulated on this build:
for s in xray sns sqs s3 dynamodb cloudtrail kinesis firehose opensearch; do
  curl -s localhost:4566/_localstack/health | grep -oE "\"$s\"\s*:\s*\"[a-z]+\""
done
# Record the LocalStack version for the findings block:
curl -s localhost:4566/_localstack/info | grep -oE '"version":\s*"[^"]+"'
```
Expected: `xray`, `sns`, `sqs`, `s3`, `dynamodb`, `cloudtrail` present (Track A). `kinesis`/`firehose`/`opensearch` presence gates Track B (and is already established by the Streaming plan's spike).

- [ ] **Step 2: Instrument ONE handler per arch in a throwaway copy + provision**

For each of arch08 and arch12 (arch02 deferred to Track B): in a scratch copy of the corpus, apply the `_shared/xray_instrument.py` pattern to its handlers (vendoring via `scripts/vendor_xray.sh`), add `TracingConfig: Active` + the two `xray:*` IAM actions, and deploy as `ace-bench-stack`. Drive the corpus's real traffic (the `functional_test.py` flow: arch08 = SNS FIFO publish → anti-corruption → inventory/analytics; arch12 = S3 upload → csv-to-sqs → sqs-to-dynamodb).

- [ ] **Step 3: Probe trace fidelity across the async hop**

Write `scratch/spike_trace_arch08.mjs` / `scratch/spike_trace_arch12.mjs` to, after driving traffic:
1. Call `ace_get_trace_summaries` over the traffic window — record how many trace summaries appear and whether one trace ID spans multiple handlers.
2. For the most-complete trace ID, call `ace_get_trace` — record the segment/subsegment tree: does it contain segments from BOTH the producer handler AND the consumer handler (full async trace), or only one handler per trace (per-segment-only)?
3. Cross-check with `ace_lookup_events` (CloudTrail) over the same window — record whether the management-event trail captures the hop (publish/sendMessage/putItem) as a complementary signal.

Run: `node scratch/spike_trace_arch08.mjs` and `node scratch/spike_trace_arch12.mjs`. Print one labeled line per probe: `[arch08][TRACE] span_multi_handler=true/false segments=N`, `[arch08][CLOUDTRAIL] hop_events=N`, etc.

- [ ] **Step 4: Decide the trace-diagnosable-fault shape per arch**

From Step 3, lock per arch:
- **full-trace** (one trace spans producer+consumer): a fault that breaks the hop (e.g. wrong target, dropped message) is diagnosable via `ace_get_trace` showing a missing downstream segment. Diagnostic path = `ace_get_trace_summaries` → `ace_get_trace`.
- **per-segment-only** (trace scoped to one Lambda): the fault is diagnosable via the consumer's *absent* trace + `ace_lookup_events` showing the producer succeeded but no consumer invocation. Diagnostic path = `ace_get_trace_summaries` (consumer trace missing) + `ace_lookup_events`.
- **no-trace** (X-Ray emits nothing on this arch): X-Ray instrumentation is shelved for that arch; the trace-diagnosable fault is replaced by a CloudTrail-diagnosable fault (or dropped). Record and do not ship a trace tool that returns empty on the arch.

- [ ] **Step 5: Record findings + lock decisions**

Append a `## Task 1 findings` section to THIS plan file with: the LocalStack version; the per-arch trace-fidelity verdict; the locked trace-diagnosable-fault diagnostic path per arch; and whether arch02 instrumentation is greenlit (deferred check at Track B start). Commit:
```bash
git add docs/superpowers/plans/2026-06-20-corpus-migration.md
git commit -m "docs(plan): record corpus-migration async-hop trace fidelity spike findings"
```

- [ ] **Step 6: Tear down the scratch stacks** (`delete-stack` + waiter; scratch copies are gitignored).

---

## Task 1 findings

> **[EXECUTOR: fill after running the spike. Do NOT fabricate results. Lock the per-arch trace verdict and diagnostic shapes; Track A/B reference these.]**

### LocalStack version

`version: <fill>`, `edition: <fill>`, IAM enforcement: `<active/inactive>`

### Per-arch trace-fidelity verdict

| Arch | Async boundary | Trace spans hop? | Verdict | Locked trace-fault diagnostic path |
|---|---|---|---|---|
| arch08 (SNS FIFO) | SNS→(SQS)→Lambda | | full-trace / per-segment / no-trace | |
| arch12 (SQS) | S3-event→Lambda; SQS→Lambda | | full-trace / per-segment / no-trace | |
| arch02 (streaming) | API→Lambda→Kinesis→Firehose→OpenSearch | `<deferred to Track B>` | | |

### CloudTrail complementary-signal note

`<does ace_lookup_events capture the hop events as a fallback diagnostic? yes/no per arch>`

### X-Ray instrumentation greenlight

- arch08: `<instrument / shelve>`
- arch12: `<instrument / shelve>`
- arch02: `<instrument / shelve — confirm at Track B start>`

---

## Task 2 — Track A: arch08 + arch12 X-Ray migration

X-Ray-instrument all arch08 and arch12 handlers, add ≥1 trace-diagnosable fault per arch (using the Task 1-locked diagnostic shape), and add ≥1 Pass-4 concurrency-probe scenario (the SQS/SNS event probe in `harness/verify/pass4_concurrency.py` is implemented but no scenario declares it). **Start only after Task 1 greenlights instrumentation for the arch.**

**Files:**
- Modify: each handler under `corpus/arch_08_.../deployment/lambda/{anti-corruption,analytics,inventory}/index.py` and `corpus/arch_12_.../deployment/lambda/{csv-to-sqs,sqs-to-dynamodb}/index.py` — add the `xray_instrument` import + `@traced`/`patch_all` usage.
- Create: `corpus/arch_08_.../deployment/lambda/_shared/xray_instrument.py` and `corpus/arch_12_.../deployment/lambda/_shared/xray_instrument.py` (copies of the arch01 canonical file, adjusted only for handler names).
- Modify: `corpus/arch_08_.../known_good.yaml` and `corpus/arch_12_.../known_good.yaml` — add `TracingConfig: Active` + `xray:PutTraceSegments`/`xray:PutTelemetryRecords` IAM to every function/role.
- Create: new scenario dirs `scenarios/arch08_fault1N_trace/`, `scenarios/arch12_fault1N_trace/` (trace-diagnosable faults) and `scenarios/arch08_fault1N_concurrency/` or `scenarios/arch12_fault1N_concurrency/` (Pass-4 probe).
- Modify: every existing `scenarios/arch08_fault0N_*/faulted.yaml` and `scenarios/arch12_fault0N_*/faulted.yaml` — propagate the same instrumentation so they stay deployable and consistent with the migrated known-good.

**Interfaces:**
- Consumes: the arch01 `_shared/xray_instrument.py` + `scripts/vendor_xray.sh`; Task 1 per-arch verdict; the existing arch08/12 corpora + scenarios.
- Produces: instrumented arch08/12 corpora whose existing functional tests still pass unchanged; ≥1 new trace-diagnosable fault per arch; ≥1 Pass-4 concurrency-probe scenario; updated manifests.

- [ ] **Step 1: Pre-flight + copy the canonical instrumentation file**

```bash
cd harness/mcp_server && npm install && cd -
for arch in arch_08_event_driven_architecture_with_sns_fifo_dynamodb_lambda_and_s3 arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3; do
  mkdir -p corpus/$arch/deployment/lambda/_shared
  cp corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/deployment/lambda/_shared/xray_instrument.py \
     corpus/$arch/deployment/lambda/_shared/xray_instrument.py
done
```
Review each copied file's docstring header — it documents the two LocalStack quirks. No per-arch code change is needed unless a handler name collides.

- [ ] **Step 2: Vendor the X-Ray SDK into each handler dir**

```bash
# scripts/vendor_xray.sh vendors aws-xray-sdk into the given handler dir (see its usage header).
for d in corpus/arch_08_*/deployment/lambda/{anti-corruption,analytics,inventory} \
         corpus/arch_12_*/deployment/lambda/{csv-to-sqs,sqs-to-dynamodb}; do
  bash scripts/vendor_xray.sh "$d"
done
```
Expected: each handler dir gains a vendored `aws_xray_sdk/` (and `_shared/xray_instrument.py` is importable). Confirm `scripts/vendor_xray.sh`'s actual argument contract first (`bash scripts/vendor_xray.sh --help` or read its header) and adjust the invocation to match.

- [ ] **Step 3: Wire instrumentation into each handler**

In each `index.py`, mirror the arch01 usage exactly: import `from xray_instrument import traced, patch_boto3` (or the names the canonical file exports — read it), call the patch at module load, and wrap the handler entrypoint with the explicit-segment decorator. Do not change handler business logic. Keep the diff minimal — the goal is trace emission, not refactoring.

- [ ] **Step 4: Add `TracingConfig` + X-Ray IAM to the known-good templates**

In `corpus/arch_08_.../known_good.yaml` and `corpus/arch_12_.../known_good.yaml`:
- Add `TracingConfig: { Mode: Active }` to every `AWS::Lambda::Function`.
- Add to each function's execution-role policy:
  ```yaml
  - Effect: Allow
    Action: [ xray:PutTraceSegments, xray:PutTelemetryRecords ]
    Resource: '*'
  ```

- [ ] **Step 5: Re-deploy each known-good and confirm no regression**

For each arch: deploy the migrated `known_good.yaml` as `ace-bench-stack` (`create_stack` + `stack_create_complete` waiter), run its `functional_test.py`, and confirm **the same pass result as before instrumentation**. Then drive traffic and confirm `ace_get_trace_summaries` now returns non-empty traces (per the Task 1 verdict shape). Tear down.

- [ ] **Step 6: Propagate instrumentation to existing faulted scenarios**

For every existing `scenarios/arch08_fault0N_*/faulted.yaml` and `scenarios/arch12_fault0N_*/faulted.yaml`, apply the SAME `TracingConfig` + IAM additions (so the faulted templates remain a known-good + ONE injected fault, with instrumentation as shared baseline — not a second change). Copy the `_shared/xray_instrument.py` + vendored SDK into each scenario's `deployment/lambda/*` dirs if the scenario carries its own handler copies. Re-deploy a sample (e.g. arch08_fault01, arch12_fault01) and confirm the fault still reproduces (functional test still fails on the same assertion) and the instrumentation did not mask or alter the symptom.

- [ ] **Step 7: Add ≥1 trace-diagnosable fault per arch**

Using the Task 1-locked diagnostic shape, add one new fault per arch where the trace (or trace-absence + CloudTrail) is the natural diagnostic signal:
- **arch08** (`scenarios/arch08_fault1N_trace/`): e.g. the anti-corruption→inventory hop is broken (wrong target queue/topic or a dropped-on-filter message) so the inventory handler is never invoked. Symptom (Pass-1): a published event never lands in the inventory table. Diagnostic path (per Task 1 verdict): `ace_get_trace_summaries` shows the producer trace but no consumer segment (full-trace) OR the consumer trace is absent + `ace_lookup_events` shows the publish succeeded but no consumer invocation (per-segment).
- **arch12** (`scenarios/arch12_fault1N_trace/`): e.g. the S3-event→csv-to-sqs or sqs-to-dynamodb hop is broken so rows never reach DynamoDB. Symptom: uploaded CSV rows never appear in the table. Diagnostic path: analogous trace/trace-absence signal.

Each new fault gets `faulted.yaml` (instrumented known-good + ONE fault), symptom-only `scenario.md`, and `fault_manifest.json` (never exposed) with the trace-aware `optimal_diagnostic_path`. Verify reproduction + diagnosability exactly as in the breadth plans' Task 4 Step 8.

- [ ] **Step 8: Add ≥1 Pass-4 concurrency-probe scenario**

Pick the arch best suited to a backlog/throughput symptom (arch12 SQS is the natural fit). Add a scenario whose `fault_manifest.json` declares the event probe — the shape `harness/verify/pass4_concurrency.py` consumes:
```json
"concurrency_probe": { "type": "sqs", "queue_url": "<resolved-at-verify>", "message_body": "{\"id\": \"probe\"}" },
"concurrency_probe_n": 10
```
(For an SNS-fronted arch08 probe, use `{ "type": "sns", "topic_arn": "...", "message": "{...}" }`.) The fault is one that degrades under concurrent load (e.g. a consumer that loses messages or builds unbounded backlog — the arch12 consumer's known missing idempotency/error-handling is a candidate). The Pass-4 probe must observe a real symptom: `error_count > 0` or `backlog_count >= n`. Confirm `run_pass4` returns `passed: false` on the faulted stack and `passed: true` on the known-good. Document the probe in the manifest's `observability_check`.

> **Queue-URL resolution:** the probe's `queue_url`/`topic_arn` must be resolvable at verify time. Confirm how `run_pass4` receives it for non-HTTP scenarios (it reads `manifest["concurrency_probe"]` directly) and ensure the manifest carries a value the verify environment can reach, or document the resolution step the runner performs.

- [ ] **Step 9: Commit Track A**

```bash
git add corpus/arch_08_*/ corpus/arch_12_*/ scenarios/arch08_* scenarios/arch12_*
git commit -m "feat(migration): X-Ray-instrument arch08+arch12, add trace-diagnosable + Pass-4 concurrency scenarios"
```

---

## Task 3 — Track B: arch02 streaming-tool adoption (BLOCKED by the Streaming plan)

> **HARD DEPENDENCY:** This track does not start until `2026-06-20-breadth-streaming-analytics.md` has completed — its tools (`ace_describe_kinesis_stream`, `ace_list_kinesis_shards`, `ace_describe_firehose_delivery_stream`, `ace_describe_opensearch_domain`, `ace_count_opensearch_docs`, and the others its spike locked) must exist in `harness/mcp_server/tools/` and have passed their spike. If the Streaming plan shelved a tool, do not reference it here.

X-Ray-instrument arch02's handlers and re-point its diagnostic paths at the Streaming breadth tools (arch02 was authored before those tools existed, so its scenarios currently diagnose via generic Lambda/log tools). This is a **diagnostic-path + metric re-baseline**, not a fault redesign — the existing arch02 faults stay, but their `optimal_diagnostic_path` and `optimal_tool_calls` are updated to the now-available, more-precise streaming tools.

**Files:**
- Create: `corpus/arch_02_fuzzy_movie_search/deployment/lambda/_shared/xray_instrument.py` (canonical copy).
- Modify: arch02 handlers (`deployment/lambda/{ingest,search}/index.py`) — add instrumentation.
- Modify: `corpus/arch_02_*/known_good.yaml` — `TracingConfig: Active` + X-Ray IAM (arch02 may already be X-Ray-instrumented per commit `511b747`; if so, this step is a no-op — verify first).
- Modify: every `scenarios/arch02_fault0N_*/fault_manifest.json` — re-baseline `optimal_diagnostic_path` to the streaming tools where they are the more natural signal.

**Interfaces:**
- Consumes: the Streaming plan's locked tool list; the existing arch02 corpus + 10 scenarios; Task 1 arch02 verdict.
- Produces: arch02 scenarios whose diagnostic paths reference the streaming tools; re-baselined metrics; no change to fault mechanisms or pass/fail outcomes.

- [ ] **Step 1: Confirm the Streaming plan landed**

```bash
ls harness/mcp_server/tools/ | grep -iE "kinesis|firehose|opensearch|stream" || echo "STREAMING TOOLS MISSING — STOP, Track B is blocked"
node -e "import('./harness/mcp_server/index.js').catch(e=>{console.error(e);process.exit(1)})" && echo "index loads"
```
If the streaming tools are absent, STOP — execute the Streaming plan first, then return.

- [ ] **Step 2: Verify/complete arch02 X-Ray instrumentation**

Check whether arch02 handlers already import `xray_instrument` (commit `511b747` X-Ray-instrumented the arch02 handler). If present and traces emit (Task 1 arch02 probe), skip to Step 3. If not, apply the canonical pattern as in Track A Steps 1–4 (copy `_shared/xray_instrument.py`, vendor the SDK, wire handlers, add `TracingConfig`+IAM), re-deploy the known-good, and confirm `functional_test.py` still passes unchanged.

- [ ] **Step 3: Walk each arch02 fault with the streaming tools + re-baseline the path**

For each `scenarios/arch02_fault0N_*`: deploy the faulted stack, drive traffic, and walk the fault's diagnosis using the streaming tools where they apply (e.g. a Firehose delivery fault → `ace_describe_firehose_delivery_stream`; an OpenSearch index/mapping fault → `ace_count_opensearch_docs` / `ace_describe_opensearch_domain`; a Kinesis shard/consumer-lag fault → `ace_list_kinesis_shards`). Record the shortest natural path and update the manifest's `optimal_diagnostic_path` + `optimal_tool_calls`. Where a generic tool remains the more natural signal, leave the path unchanged — do not force a streaming tool in.

- [ ] **Step 4: Re-baseline arch02 metrics + confirm no regression**

For each arch02 scenario, re-measure `optimal_tool_calls` against the expanded surface; `optimal_files_changed`/`optimal_lines_changed` stay as the minimal fix unless the fix path changed. Re-run a sample arch02 scenario end-to-end (`harness/run.py`) and confirm verify + score still complete.

- [ ] **Step 5: Commit Track B**

```bash
git add corpus/arch_02_*/ scenarios/arch02_*
git commit -m "feat(migration): adopt streaming tools + X-Ray on arch02 diagnostic paths, re-baseline metrics"
```

---

## Task 4 — Cross-cutting re-baseline

Re-baseline `optimal_tool_calls`/`optimal_files_changed`/`optimal_lines_changed` across ALL migrated scenarios (arch02 + arch08 + arch12) against the expanded tool surface, so efficiency/identification scoring reads consistent baselines.

**Files:**
- Modify: every `scenarios/arch0{2,8}_fault0N_*/fault_manifest.json` and `scenarios/arch12_fault0N_*/fault_manifest.json` touched by Tracks A/B.

**Interfaces:**
- Consumes: the instrumented corpora + updated diagnostic paths (Tasks 2–3); the scoring dimensions that read `optimal_*`.
- Produces: a consistent set of re-baselined manifests.

- [ ] **Step 1: Recompute `optimal_*` per scenario**

For each migrated scenario, confirm `optimal_tool_calls` = the count of MCP calls on the (possibly updated) `optimal_diagnostic_path`, and `optimal_files_changed`/`optimal_lines_changed` reflect the minimal fix. The X-Ray instrumentation is shared baseline, NOT part of any fix — it must not inflate `optimal_files_changed`.

- [ ] **Step 2: Sanity-check the efficiency/identification scoring reads them**

Run one migrated scenario per arch through `harness/run.py` and confirm `score.json` is produced and the efficiency dimension uses the re-baselined `optimal_tool_calls` (no crash, sane values).

- [ ] **Step 3: Commit**

```bash
git add scenarios/arch02_* scenarios/arch08_* scenarios/arch12_*
git commit -m "chore(migration): re-baseline optimal_* across arch02/08/12 scenarios"
```

---

## Task 5 — Discoverability QA gate (re-run on migrated scenarios)

Run the four checks from §4 of the framework spec for every **new or path-changed** migrated scenario (the new trace-diagnosable + concurrency scenarios in Track A, and every arch02 scenario whose `optimal_diagnostic_path` changed in Track B). Pre-existing scenarios whose paths did not change need only Check 4 (pipeline still works).

**Files:** none created; results recorded inline.

**Interfaces:**
- Consumes: the migrated scenarios (Tasks 2–4); the X-Ray/CloudTrail/streaming tools.
- Produces: a pass/fail record per check per affected scenario; all four must pass before Task 6.

- [ ] **Step 1: Check 1 — Agent-exposure plumbing.** Confirm the X-Ray (`ace_get_trace_summaries`, `ace_get_trace`), CloudTrail (`ace_lookup_events`), and adopted Streaming tools all flow through `mcp_to_openai_tool`/`filter_model_tools` and appear in the model's runtime list; `ace_verify_fix`/`ace_score_run` stay filtered. (Reuse the plumbing script from the Cognito/AppSync plans' Task 5 Check 1, swapping the tool-name list.)

- [ ] **Step 2: Check 2 — Diagnostic-path reachability.** For each new/changed scenario, deploy the faulted stack, walk the `optimal_diagnostic_path` with the REAL tools, and confirm the trace/CloudTrail/streaming tool surfaces the signal that pinpoints the fault. Record `[Check2] <scenario>: PASS|FAIL — <reason>`. **Remediation:** if a trace tool returns empty where the path expects a signal, fall back to the Task 1 per-segment + CloudTrail path and update the manifest.

- [ ] **Step 3: Check 3 — Blind-triggering.** (3a) Static rubric — the adopted tools' descriptions already satisfy the rubric (they shipped under depth/streaming plans); confirm no migrated `scenario.md` symptom leaks the cause. (3b) LLM-judge blind selection (N=5, cheaper distinct judge) for each new trace-diagnosable + concurrency scenario and each path-changed arch02 scenario: pass = every tool on `optimal_diagnostic_path` in the first-K picks in ≥3/5. Use the `scratch/blind_trigger_check.py` shape from the Cognito plan, loading the full runtime tool list (not a single tool file). **Remediation ladder:** sharpen the `scenario.md` symptom to be more faithful to what the trace/backlog actually shows (never naming the cause) → re-baseline the path to the route the judge naturally takes → last resort redesign/drop.

- [ ] **Step 4: Check 4 — Trace + scoring integration.** For one new scenario per arch, run `harness/run.py` end-to-end and confirm the new tool calls (trace/CloudTrail/streaming) land in `tool_call_trace.json`, the Pass-4 result appears in `verify_result.json` for the concurrency scenario, and `score.json` reads the re-baselined `optimal_tool_calls`.

- [ ] **Step 5: Apply remediation + re-run failed checks.** Repeat until all four pass for every affected scenario. Shelve+document any fault that cannot be made discoverable.

- [ ] **Step 6: Commit remediation (if any)**

```bash
git add scenarios/arch02_* scenarios/arch08_* scenarios/arch12_*
git commit -m "fix(migration): remediate migrated scenarios for discoverability QA gate"
```

---

## Task 6 — Documentation

Bring the guides in sync with the migration: arch02/08/12 are now X-Ray-instrumented; arch02 adopts streaming tools; new scenarios exist; tool counts are unchanged (no new tools) but the scenario inventory and per-arch capability notes change.

**Files:**
- Modify: `CLAUDE.md` (Project Layout — note arch08/12/02 X-Ray instrumentation; add the new scenario dirs; note the Pass-4 concurrency scenario now exists, removing the "implemented but unused" caveat).
- Modify: `README.md`, `RUN.md` (architecture/corpus inventory — note instrumentation + new scenarios; correct any "no Pass-4 scenario declared" wording).

**Interfaces:**
- Consumes: the migrated corpora/scenarios.
- Produces: consistent docs; tool counts unchanged; scenario inventory updated.

- [ ] **Step 1: Update `CLAUDE.md`** — add the new `scenarios/arch08_fault1N_*` / `scenarios/arch12_fault1N_*` entries and note that arch02/08/12 handlers are X-Ray-instrumented; update the memory/spec note that the "event probe is implemented but unused" — it is now used by the new concurrency scenario.

- [ ] **Step 2: Update `README.md` + `RUN.md`** — reflect the instrumentation, the new scenarios, and arch02's streaming-tool diagnostic paths. Tool counts are unchanged (this plan adds no tools) — do NOT bump diagnostic-tool counts.

- [ ] **Step 3: Verify counts/inventory consistency**

```bash
# Scenario count sanity (should rise by the number of new Track A scenarios):
ls scenarios/ | grep -E "arch(02|08|12)_fault" | wc -l
# Confirm no doc claims a diagnostic-tool count change from this plan:
grep -rEn "diagnostic" CLAUDE.md README.md RUN.md | head
```

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md README.md RUN.md
git commit -m "docs: document arch02/08/12 X-Ray migration, streaming-tool adoption, and Pass-4 concurrency scenario"
```

---

## Commit Cadence Summary

| Task | Commit message |
|---|---|
| Task 1 (after spike) | `docs(plan): record corpus-migration async-hop trace fidelity spike findings` |
| Task 2 (Track A) | `feat(migration): X-Ray-instrument arch08+arch12, add trace-diagnosable + Pass-4 concurrency scenarios` |
| Task 3 (Track B) | `feat(migration): adopt streaming tools + X-Ray on arch02 diagnostic paths, re-baseline metrics` |
| Task 4 (re-baseline) | `chore(migration): re-baseline optimal_* across arch02/08/12 scenarios` |
| Task 5 (if remediation) | `fix(migration): remediate migrated scenarios for discoverability QA gate` |
| Task 6 | `docs: document arch02/08/12 X-Ray migration, streaming-tool adoption, and Pass-4 concurrency scenario` |

---

## Self-Review Notes (author)

- **Spec coverage:** the framework's migration shape (§5 "Migration plan — one plan, three tracks") maps onto the 6-task spine as: Task 1 = shared spike (async-hop trace fidelity, the depth design's flagged risk); Tasks 2–4 = Track A + Track B + cross-cutting re-baseline; Task 5 = discoverability QA re-run; Task 6 = docs. §2 LocalStack-load preamble → Task 1 Step 1. §3 Realism gate → reuses real X-Ray/CloudTrail/streaming SDK tools; no LocalStack-proprietary introspection. §4 QA gate → Task 5.
- **Dependency honesty:** Track A is explicitly NOT blocked (X-Ray confirmed on arch01); Track B is explicitly blocked by the Streaming plan, with a Step-1 hard stop if its tools are absent. Both stated in the tracks-at-a-glance table and Section 6 of the framework.
- **Trace-fidelity kill-gate:** Task 1 answers the async-hop question per arch BEFORE any trace-diagnosable fault is written, so no fault claims a trace signal the build does not emit. The per-segment + CloudTrail fallback path means a "no full trace" verdict does not block the migration — it reshapes the diagnostic path honestly.
- **No-regression guard:** instrumentation is shared baseline applied to known-good AND every existing faulted.yaml, so faulted templates stay "known-good + ONE fault"; Step 5/6 re-verify that the pre-existing functional outcomes are unchanged and `optimal_files_changed` is not inflated by instrumentation.
- **Closes a known gap:** adds the long-deferred Pass-4 concurrency-probe scenario (the SQS/SNS event probe in `pass4_concurrency.py` is implemented but, per memory S1113, "deferred for future pickup") — Track A Step 8 wires the first one.
- **No new tools:** this plan adopts existing tools only — Task 6 must NOT bump the diagnostic-tool count.
