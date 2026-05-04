# ACE-Bench Harness

A benchmark for evaluating a model's ability to diagnose and fix intentionally broken cloud infrastructure. A fixed scenario corpus is built once via HITL; any model under evaluation receives a deployed broken architecture and a vague symptom, uses MCP diagnostic tools to trace the failure, then submits a fix. Scoring covers five dimensions; quality is a hard gate.

---

## Commands & Tooling

```bash
# LocalStack
localstack start -d
until localstack status services 2>/dev/null | grep -q "running"; do sleep 2; done
localstack stop

# Python tests
pytest tests/                        # all tests
pytest tests/test_shared.py          # Phase A gate
pytest tests/test_mcp_server.py      # Phase B gate (requires LocalStack)
pytest tests/test_runner.py          # Phase C gate
pytest tests/test_verify.py          # Phase D gate

# Node MCP server tests (Phase B only)
node --test tests/test_mcp_server.js

# Register MCP server (once after Phase B)
claude mcp add ace-bench-diagnostic-mcp \
  -e HARNESS_API_KEY=$(openssl rand -hex 32) \
  -e LOCALSTACK_ENDPOINT=http://localhost:4566 \
  -- node harness/mcp_server/index.js

# Run a scenario
python harness/run.py scenarios/<scenario_dir>/ [--run-id <id>]
```

---

## Runtime

| Component        | Runtime                                                         |
|------------------|-----------------------------------------------------------------|
| Harness code     | Python 3.11                                                     |
| MCP server       | Node.js v22+                                                    |
| LocalStack       | http://localhost:4566 (free tier)                               |
| AWS credentials  | accessKeyId=`test`, secretAccessKey=`test`, region=`us-east-1` |
| IAM fake account | `000000000000`                                                  |

---

## Standard Imports (Python)

```python
from harness.shared.localstack_client import (
    cf_client, lambda_client, s3_client, sqs_client,
    iam_client, logs_client
)
from harness.shared.cfn_lint_runner import run_lint
from harness.shared.file_differ import diff_directories
from harness.shared.result_logger import log_result, log_tool_call, log_file_change
```

---

## Key Invariants — Never Violate

- **Submission is final.** First `UPDATE_COMPLETE` is the scored submission; `ScenarioRunner.submitted` blocks a second redeployment.
- **`fault_manifest.json` is never exposed to the model.** `build_context` raises `ValueError` if readable from the model-accessible path.
- **`known_good.yaml` is never exposed to the model.**
- **File edits do not count as tool calls.** `tool_call_trace.json` records MCP diagnostic invocations only.
- **Tool calls are logged individually** with input, output, and timestamp.
- **Score tools require `HARNESS_API_KEY`.** Calls without it return `{"error": "unauthorized"}`.
- **Phases are strictly sequential:** A → B → C → D → E.

---

## Project Layout

```
ace-bench/
├── CLAUDE.md
├── SPEC.md
├── .env                          # HARNESS_API_KEY — never commit
├── harness/
│   ├── shared/                   # Phase A
│   ├── mcp_server/               # Phase B
│   │   └── tools/
│   │       ├── probe.js
│   │       ├── observe.js
│   │       └── score.js
│   ├── runner/                   # Phase C
│   ├── verify/                   # Phase D
│   └── run.py                    # Phase E
├── corpus/
│   └── arch_01_[name]/
│       ├── known_good.yaml       # never expose to model
│       ├── functional_test.py
│       └── traffic_flow.md
├── scenarios/
│   └── arch01_fault01_[class]/
│       ├── scenario.md
│       ├── faulted.yaml
│       ├── fault_manifest.json   # never expose to model
│       └── deployment/lambda/handler.py
├── results/[run_id]/
│   ├── scenario_id.txt
│   ├── tool_call_trace.json
│   ├── file_change_log.json
│   ├── faulted_baseline.json
│   └── verify_result.json
└── tests/
    ├── test_shared.py
    ├── test_mcp_server.js
    ├── test_runner.py
    └── test_verify.py
```

---

## `fault_manifest.json` Fields

| Field                   | Used in                               |
|-------------------------|---------------------------------------|
| `fault_class`           | Pass 4 gate (concurrency probe)       |
| `optimal_tool_calls`    | Efficiency scoring (deferred)         |
| `optimal_files_changed` | Efficiency scoring (deferred)         |
| `optimal_lines_changed` | Efficiency scoring (deferred)         |
| `valid_fixes`           | Pass 3 semantic classification        |
| `invalid_patches`       | Pass 3 semantic classification        |
| `target_resource`       | Pass 3 structural diff                |
| `target_property`       | Pass 3 structural diff (dot-notation) |
| `original_value`        | Pass 3 structural diff                |
| `concurrency_probe_n`   | Pass 4 (default 10 if absent)         |
| `injected_value`        | Phase F identification prompt         |

---

## Phase Specs

### Phase A — Shared Utilities
**Gate:** `pytest tests/test_shared.py`

- `localstack_client.py` — module-level boto3 singletons for CF, Lambda, S3, SQS, IAM, CW Logs, API Gateway; `health_check()` raises `RuntimeError` if unreachable
- `cfn_lint_runner.py` — subprocess wrapper for `cfn-lint --format json`; returns `{passed, fatal_errors, warnings}`; only E-rules set `passed=False`; raises `EnvironmentError` if not installed
- `file_differ.py` — `snapshot(dir)` and `diff_snapshots(before, after, dir)`; line diff via `difflib.unified_diff`; returns added/modified/removed counts and line changes
- `result_logger.py` — `init_run`, `log_tool_call` (thread-safe append), `log_file_change`, `log_verify_result`; all write to `results/[run_id]/`

### Phase B — Diagnostic MCP Server
**Gate:** `node --test tests/test_mcp_server.js`

AWS client config shared across all tools: `endpoint: LOCALSTACK_ENDPOINT ?? "http://localhost:4566"`, region `us-east-1`, creds `test/test`.

**Probe tools** (`probe.js`):

| Tool | Returns |
|------|---------|
| `ace_invoke_endpoint(path, method, payload)` | `{status_code, latency_ms, body, error_type}` |
| `ace_invoke_lambda(function_name, payload)` | `{status_code, response_body, error_type, duration_ms, billed_duration_ms}` |
| `ace_check_queue_depth(queue_name)` | `{messages_available, messages_in_flight, oldest_message_age_seconds}` |
| `ace_read_table_item(table_name, key)` | `{item\|null, consumed_read_capacity}` |
| `ace_check_event_source(function_name)` | `[{source_arn, source_type, enabled, batch_size, state}]` |
| `ace_check_s3_object(bucket, key)` | `{exists, size_bytes, last_modified}` |

**Observe tools** (`observe.js`):

| Tool | Returns |
|------|---------|
| `ace_describe_resource(logical_resource_id)` | `{resource_type, physical_id, properties, status}` |
| `ace_list_resources(resource_type\|null)` | `[{logical_id, physical_id, resource_type, status}]` |
| `ace_get_iam_role(role_name)` | `{assume_role_policy, attached_policies, inline_policies}` |
| `ace_get_log_tail(function_name, line_count)` | `[{timestamp, request_id, level, message}]` most-recent-first |
| `ace_get_stack_outputs()` | `{output_key: output_value}` |
| `ace_get_environment_variables(function_name)` | `{key: value}` |

**Score tools** (`score.js`): `ace_verify_fix` and `ace_score_run` — return `{"error": "unauthorized"}` without correct key; `{"status": "not_implemented"}` with it until Phase D.

All tools: validate params before AWS call, catch all SDK errors, log `{tool, timestamp}` to stderr.

### Phase C — Scenario Runner & Deployment Handler
**Gate:** `pytest tests/test_runner.py`

`build_context(scenario_dir)` returns `{scenario_brief, template_path, deployment_dir, stack_outputs, instruction}`. Raises `ValueError` if `fault_manifest.json` is readable. Fixed instruction (verbatim):
> A deployed instance of this system is running in your local environment. The deployment directory and CloudFormation template are available to you directly. Diagnostic tools are available via MCP. Diagnose the reported symptom, edit whatever files need changing, and redeploy using localstack-deployer when ready. Your first successful redeployment is your scored submission.

`ScenarioRunner` — `start()` deploys faulted template; `intercept_tool_call()` logs to trace; `on_model_redeploy()` calls `handle_submission()` and sets `submitted=True`; subsequent calls return `{outcome: "already_submitted"}`.

`handle_submission()` pipeline:
1. Snapshot diff → `log_file_change`
2. cfn-lint gate → return `{outcome: "lint_fail"}` on E-rule errors
3. Lambda packaging → zip modified `.py` files → upload to `ace-bench-artifacts` S3 → patch `S3Key` in template (in memory only)
4. `cf_client.update_stack` → poll for `UPDATE_COMPLETE`; return `{outcome: "deploy_success"}` or `{outcome: "deploy_fail", events: [...]}`

### Phase D — Verify Loop
**Gate:** `pytest tests/test_verify.py`

Pass execution order:

| Pass | Runs when |
|------|-----------|
| Pass 1 — functional | Always |
| Pass 2 — regression | Always |
| Pass 3 — classification | Always |
| Pass 4 — concurrency | `fault_class` in `["performance", "reliability"]` |

**Pass 1:** Runs `functional_test.py` subprocess; parses `ASSERT [pass|fail] <name>: <msg>` lines.

**Pass 2:** Finds assertions that were `pass` on faulted baseline but `fail` after fix. `_secondary` names → `non_critical`; others → `critical`.

**Pass 3:** Two signals — structural diff at `target_resource.target_property` vs `original_value`, and invalid-patch substring scan of diff text. Classifications: `root_cause`, `workaround`, `partial`, `none`. `_navigate` handles bracket-indexed paths (e.g. `Policies[0].Statement[0].Action`).

**Pass 4:** N concurrent `requests.post` via `ThreadPoolExecutor`. `passed = throttled_count == 0 and timeout_count == 0`. Override rule: Pass 4 fail + Pass 1 primary pass → downgrade Pass 3 to `"partial"`.

If deployment did not reach `UPDATE_COMPLETE`: returns `{outcome: "did_not_deploy"}` with all passes skipped.

### Phase F — Scoring Agent
**Gate:** `pytest tests/test_scoring.py`

`harness/scoring/` — called by `run.py` as the final step via `score_run(run_id, scenario_dir)`.

**Model:** `claude-sonnet-4-5` (constant `SCORING_MODEL` in `agent.py`). All agent calls go through `call_scoring_agent(system_prompt, user_prompt) -> str` in `agent.py`.

**Dimensions and methods:**

| Module | Dimension | Weight | Method |
|--------|-----------|--------|--------|
| `identification.py` | Issue identification | 0.20 | Claude Sonnet |
| `fix_correctness.py` | Fix correctness | 0.25 | Deterministic (pass1 result) |
| `regression.py` | Regression penalty | subtracted | Deterministic (0/0.08/0.18/0.28) |
| `efficiency.py` | Diagnostic efficiency | 0.15 | Threshold formula + agent rationale |
| `quality.py` | Fix quality | 0.40 | Claude Sonnet (dominant weight) |

**Composite:** `max(0, (d1×0.20 + d2×0.25 + d4×0.15 + d5×0.40) − regression_penalty)`

**Quality gate** (`gate.py` re-exports `check_gate` from `quality.py`): classification must be `root_cause` or `workaround`, primary assertions must pass, zero regressions. Gate failure → `final_score: 0.0` with no agent calls.

**Early exits in `scorer.py`** (write zero score, no agent calls):
- Any required artifact file missing → `zero_reason: "missing_artifacts"`
- `verify_result["outcome"] != "completed"` → `zero_reason: <outcome>`
- Quality gate fails → `zero_reason: "quality_gate_failed"`

**`run.py` update (F9):** import `score_run` and call after `run_verify_loop`; extend terminal summary with scoring block showing per-dimension scores, final score, and interpretation string.

**`score.json`** written to `results/[run_id]/score.json` after every completed run.

---

### Phase E — Entry Point
**Gate:** E2E integration test — exit 0, `classification: root_cause`, `regression_count: 0`

`python harness/run.py <scenario_dir> [--run-id <id>]`

Startup: load `.env` → health check → validate scenario dir → deploy faulted template → capture baseline → build context → print to stdout → poll for submission (30 min timeout) → run verify loop → print summary → exit 0/1.

Redeployment detected via `ACE_BENCH_SIGNAL_FILE` (`/tmp/ace-bench-update.json`) written by a `localstack-deployer update-stack` shim.

Output summary:
```
═══════════════════════════════════════
ACE-Bench Run: [run_id]
Scenario: [scenario_id]
═══════════════════════════════════════
Deployment:       [PASS | FAIL]
Functional test:  [PASS | PARTIAL | FAIL]
Regressions:      [none | N critical, M non-critical]
Classification:   [root_cause | workaround | partial | none]
Concurrency:      [PASS | FAIL | SKIPPED]
Tool calls made:  [N]
Files changed:    [N]
Lines changed:    [N]
Full results:     results/[run_id]/
═══════════════════════════════════════
```

---

## Dependency Graph

```
Phase A (shared utilities)
    │
    ├──► Phase B (MCP server)
    │
    └──► Phase C (runner + deployment handler)
              │
         Phase D (verify loop)
              │
         Phase E (entry point + e2e test)
```

---

## Implementation Status

- **Phase A** ✅ — `pytest tests/test_shared.py` 17/17 passing
- **Phase B** ✅ — `node --test tests/test_mcp_server.js` 15/15 passing; MCP server registered
- **Phase C** ✅ — `pytest tests/test_runner.py` 8/8 passing
- **Phase D** ✅ — `pytest tests/test_verify.py` 20/20 passing (includes bracket-index path tests added in Phase E)
- **Phase E** ✅ — `pytest tests/test_e2e.py` 1/1 passing; full suite 45/45 passing
- **Phase F** 🔲 — `pytest tests/test_scoring.py` not yet written

**Known follow-up (not blocking):** `handler.py` only handles SQS events; the ingestion lambda receives HTTP-shaped events so `ingestion_accepts_post` fails regardless of the IAM fix. Add HTTP-event branching to `handler.py` (or a separate `ingestion.py`) to make functional assertions exercise a true end-to-end happy path.