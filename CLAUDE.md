# ACE-Bench Harness

A benchmark for evaluating any model's ability to diagnose and fix intentionally broken cloud infrastructure. Not a generation benchmark — a **debugging benchmark**.

A fixed scenario corpus is built once via a HITL agent pipeline. Any model under evaluation then receives a deployed broken architecture and a vague business-language symptom. It uses MCP diagnostic tools in whatever order and combination it chooses to trace the failure, then submits a fix. The corpus is model-agnostic — the same scenarios run against any model being benchmarked.

Scoring covers five dimensions. Quality is a hard gate: below a minimum quality threshold the run scores zero regardless of speed or efficiency. Above the threshold, quality remains the dominant weight and cannot be overcome by speed and iterations alone.

---

## Commands & Tooling

### LocalStack

```bash
# Start LocalStack (required before any harness phase)
localstack start -d

# Poll until healthy (use before running tests or harness)
until localstack status services 2>/dev/null | grep -q "running"; do sleep 2; done

# Stop LocalStack
localstack stop

# Check status
localstack status services
```
**Use case:** LocalStack must be running before any harness phase executes. Phase A unit tests mock it; Phase B and E2E tests require a live instance. Health is also checked programmatically via `localstack_client.health_check()` at run startup.

### Python tests

```bash
# Run all tests
pytest tests/

# Run phase-specific tests
pytest tests/test_shared.py     # Phase A gate
pytest tests/test_mcp_server.py # Phase B gate (requires LocalStack)
pytest tests/test_runner.py     # Phase C gate
pytest tests/test_verify.py     # Phase D gate

# Verbose with short tracebacks
pytest tests/ -v --tb=short
```
**Use case:** Each phase's tests are a hard gate — do not begin the next phase until the current phase's tests pass. Working code without passing tests does not count as complete.

### Node.js MCP server tests

```bash
# Run Phase B tests (Node built-in test runner)
node --test tests/test_mcp_server.js
```
**Use case:** Phase B verification only. Requires LocalStack running with minimal fixtures (Lambda, DynamoDB, SQS) created as part of test setup.

### MCP server registration

```bash
# Register the diagnostic MCP server with Claude Code (run once after Phase B)
claude mcp add ace-bench-diagnostic-mcp \
  -e HARNESS_API_KEY=$(openssl rand -hex 32) \
  -e LOCALSTACK_ENDPOINT=http://localhost:4566 \
  -- node harness/mcp_server/index.js
```
**Use case:** Run once after Phase B is complete. Store the generated `HARNESS_API_KEY` in `.env` at the project root. Never commit `.env`. Phase C runner reads this key from `.env`.

### Harness entry point

```bash
# Run one full evaluation scenario
python harness/run.py scenarios/<scenario_dir>/ [--run-id <id>]

# E2E integration test
python harness/run.py scenarios/arch01_fault01_security/ --run-id e2e-test
```
**Use case:** Phase E entry point. Requires LocalStack running, MCP server registered, and `.env` with HARNESS_API_KEY present. Exits 0 on completed verification, 1 otherwise. Results written to `results/<run_id>/`.

### cfn-lint

```bash
# Lint a CloudFormation template (used internally by harness)
cfn-lint --format json <template.yaml>

# Install if missing
pip install cfn-lint
```
**Use case:** Invoked by `harness/shared/cfn_lint_runner.py` as a subprocess during deployment pre-flight in Phase C. Fatal E-rule errors block deployment; W-rule warnings are logged only.

### localstack-deployer

```bash
# Deploy faulted template at scenario start (Phase C runner)
localstack-deployer create-stack --stack-name ace-bench-stack --template faulted.yaml

# Model-triggered fix submission (intercepted by harness)
localstack-deployer update-stack --stack-name ace-bench-stack
```
**Use case:** Phase C runner calls `create-stack` to set up the broken scenario. The model calls `update-stack` to submit its fix — the harness intercepts this on the MCP stderr stream. The actual CloudFormation API call is then made internally by the harness via `cf_client.update_stack()`.

---

## Runtime

| Component        | Runtime                                                            |
|------------------|--------------------------------------------------------------------|
| Harness code     | Python 3.11                                                        |
| MCP server       | Node.js v22+                                                       |
| LocalStack       | http://localhost:4566 (free tier)                                  |
| AWS credentials  | accessKeyId=`test`, secretAccessKey=`test`, region=`us-east-1`    |
| IAM fake account | `000000000000`                                                     |

---

## Standard Imports (Python)

All harness Python modules import shared utilities from `harness/shared/`:

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

- **Submission is final.** The model's first `UPDATE_COMPLETE` is the scored submission. A second redeployment must be blocked — `ScenarioRunner.submitted` flag enforces this.
- **`fault_manifest.json` is never exposed to the model** under any circumstance. `build_context` raises `ValueError` if it is readable from the model-accessible scenario path.
- **`known_good.yaml` is never exposed to the model** under any circumstance.
- **File edits do not count as tool calls.** `tool_call_trace.json` only records MCP diagnostic tool invocations, not file system changes.
- **Tool calls are logged individually** with input, output, and timestamp via `result_logger.log_tool_call`.
- **Score tools require `HARNESS_API_KEY`** in the request. The key is set as an env var and never passed to the model. Any call without it returns `{"error": "unauthorized"}`.
- **Phases are strictly sequential.** No phase begins until all phases it depends on have passing tests. Dependency order: A → B → C → D → E.

---

## Project Layout

```
ace-bench/
├── CLAUDE.md
├── SPEC.md
├── .env                          # HARNESS_API_KEY — never commit
├── harness/
│   ├── shared/                   # Phase A
│   │   ├── localstack_client.py
│   │   ├── cfn_lint_runner.py
│   │   ├── file_differ.py
│   │   └── result_logger.py
│   ├── mcp_server/               # Phase B
│   │   ├── index.js
│   │   ├── package.json
│   │   └── tools/
│   │       ├── probe.js
│   │       ├── observe.js
│   │       └── score.js
│   ├── runner/                   # Phase C
│   │   ├── scenario_runner.py
│   │   ├── context_builder.py
│   │   └── deployment_handler.py
│   ├── verify/                   # Phase D
│   │   ├── verify_loop.py
│   │   ├── pass1_functional.py
│   │   ├── pass2_regression.py
│   │   ├── pass3_classification.py
│   │   └── pass4_concurrency.py
│   └── run.py                    # Phase E
├── corpus/
│   └── arch_01_[name]/
│       ├── known_good.yaml       # read-only — never expose to model
│       ├── functional_test.py
│       └── traffic_flow.md
├── scenarios/
│   └── arch01_fault01_[class]/
│       ├── scenario.md
│       ├── faulted.yaml
│       ├── fault_manifest.json   # read-only — never expose to model
│       └── deployment/
│           └── lambda/handler.py
├── results/
│   └── [run_id]/
│       ├── scenario_id.txt
│       ├── tool_call_trace.json
│       ├── file_change_log.json
│       ├── faulted_baseline.json  # written by C3, used by D3
│       └── verify_result.json
└── tests/
    ├── test_shared.py            # Phase A gate
    ├── test_mcp_server.js        # Phase B gate
    ├── test_runner.py            # Phase C gate
    └── test_verify.py            # Phase D gate
```

---

## `fault_manifest.json` Fields Used by Harness

| Field                   | Used in                               |
|-------------------------|---------------------------------------|
| `fault_class`           | Pass 4 gate (concurrency probe)       |
| `optimal_tool_calls`    | Efficiency scoring (Step 7, deferred) |
| `optimal_files_changed` | Efficiency scoring (Step 7, deferred) |
| `optimal_lines_changed` | Efficiency scoring (Step 7, deferred) |
| `valid_fixes`           | Pass 3 semantic classification        |
| `invalid_patches`       | Pass 3 semantic classification        |
| `target_resource`       | Pass 3 structural diff                |
| `target_property`       | Pass 3 structural diff (dot-notation) |
| `original_value`        | Pass 3 structural diff                |
| `concurrency_probe_n`   | Pass 4 (default 10 if absent)         |

---

## Phase A — Shared Utilities

**Depends on:** nothing  
**Blocks:** all phases  
**Gate:** `pytest tests/test_shared.py` — all four tests must pass

### Files
- `harness/shared/localstack_client.py` — module-level boto3 singletons for CF, Lambda, S3, SQS, IAM, CloudWatch Logs, API Gateway; all point to `http://localhost:4566` with fake creds; exports `health_check()` which raises `RuntimeError` if unreachable
- `harness/shared/cfn_lint_runner.py` — subprocess wrapper for `cfn-lint --format json`; returns `{passed, fatal_errors, warnings}`; only E-rules set `passed=False`; raises `EnvironmentError` if cfn-lint not installed
- `harness/shared/file_differ.py` — `snapshot(dir) -> {path: hash}`; `diff_snapshots(before, after, dir) -> {files_added, files_modified, files_removed, total_files_changed, per_file_line_changes, total_lines_changed}`; line diff via `difflib.unified_diff`; `total_lines_changed = lines_added + lines_removed`
- `harness/shared/result_logger.py` — `init_run`, `log_tool_call` (appends to JSON array without full rewrite), `log_file_change`, `log_verify_result`; all write to `results/[run_id]/`

### Test requirements
- `health_check()` raises when LocalStack unreachable (mock boto3)
- cfn-lint returns `passed: True` on valid template, `passed: False` on E-rule error
- `diff_snapshots` correctly counts added/modified/removed files and lines
- result logger writes valid JSON across all four functions, no corruption under concurrent calls

---

## Phase B — Diagnostic MCP Server

**Depends on:** Phase A  
**Blocks:** Phase C  
**Gate:** `node --test tests/test_mcp_server.js` — all tools tested, no uncaught exceptions

### Files
- `harness/mcp_server/index.js` — imports tool definitions from `tools/`, registers all tools, starts stdio transport
- `harness/mcp_server/tools/probe.js` — six probe tools
- `harness/mcp_server/tools/observe.js` — six observe tools
- `harness/mcp_server/tools/score.js` — two stubbed tools gated by `HARNESS_API_KEY`

### AWS client config (shared across all tools)
```js
const awsConfig = {
  endpoint: process.env.LOCALSTACK_ENDPOINT ?? "http://localhost:4566",
  region: "us-east-1",
  credentials: { accessKeyId: "test", secretAccessKey: "test" }
}
```

### Probe tools (`probe.js`)
| Tool | Implementation | Returns |
|------|----------------|---------|
| `ace_invoke_endpoint(path, method, payload)` | HTTP fetch to CF stack output `ApiEndpoint` | `{status_code, latency_ms, body, error_type\|null}` |
| `ace_invoke_lambda(function_name, payload)` | `LambdaClient.InvokeCommand` (RequestResponse) | `{status_code, response_body, error_type\|null, duration_ms, billed_duration_ms}` |
| `ace_check_queue_depth(queue_name)` | `SQSClient.GetQueueAttributes` | `{messages_available, messages_in_flight, oldest_message_age_seconds}` |
| `ace_read_table_item(table_name, key)` | `DynamoDBClient.GetItem`; tool converts plain key object to DDB format; item unmarshalled | `{item\|null, consumed_read_capacity}` |
| `ace_check_event_source(function_name)` | `LambdaClient.ListEventSourceMappings` | `[{source_arn, source_type, enabled, batch_size, state}]` |
| `ace_check_s3_object(bucket, key)` | `S3Client.HeadObject`; 404 = `{exists: false}` not error | `{exists, size_bytes, last_modified\|null}` |

### Observe tools (`observe.js`)
| Tool | Implementation | Returns |
|------|----------------|---------|
| `ace_describe_resource(logical_resource_id)` | CF `DescribeStackResource` + service API for full config | `{resource_type, physical_id, properties, status}` |
| `ace_list_resources(resource_type\|null)` | CF `ListStackResources` | `[{logical_id, physical_id, resource_type, status}]` |
| `ace_get_iam_role(role_name)` | `GetRole + ListRolePolicies + ListAttachedRolePolicies + GetRolePolicy` for each inline | `{assume_role_policy, attached_policies, inline_policies}` |
| `ace_get_log_tail(function_name, line_count)` | CW Logs `DescribeLogGroups` + `GetLogEvents` on most recent stream | `[{timestamp, request_id, level, message}]` most recent first |
| `ace_get_stack_outputs()` | CF `DescribeStacks` | `{output_key: output_value}` flat dict |
| `ace_get_environment_variables(function_name)` | `LambdaClient.GetFunctionConfiguration` | `{key: value}` |

### Score tools (`score.js`) — stubbed until Phase D
Any call without matching `HARNESS_API_KEY`:
```json
{"error": "unauthorized", "message": "score tools require harness_api_key"}
```
With correct key: `{"status": "not_implemented"}` until Phase D.

### Tool call requirements (all tools)
1. Validate required params before AWS call — return structured error, never throw
2. Catch all AWS SDK errors — return structured error, never crash server
3. Log tool name + timestamp to **stderr** — Phase C tails this stream to build `tool_call_trace`

---

## Phase C — Scenario Runner & Deployment Handler

**Depends on:** Phase A, Phase B (MCP server registered)  
**Blocks:** Phase D  
**Gate:** `pytest tests/test_runner.py`

### Files
- `harness/runner/context_builder.py` — `build_context(scenario_dir) -> dict`
- `harness/runner/scenario_runner.py` — `ScenarioRunner` class
- `harness/runner/deployment_handler.py` — `handle_submission(scenario_dir, run_id, start_snapshot) -> dict`

### context_builder.py — `build_context` return shape
```python
{
  "scenario_brief": str,     # contents of scenario.md
  "template_path": str,      # absolute path to faulted.yaml
  "deployment_dir": str,     # absolute path to deployment/
  "stack_outputs": dict,     # from ace_get_stack_outputs()
  "instruction": str         # fixed verbatim text — do not vary between scenarios
}
```
Fixed instruction (verbatim):
> A deployed instance of this system is running in your local environment. The deployment directory and CloudFormation template are available to you directly. Diagnostic tools are available via MCP. Diagnose the reported symptom, edit whatever files need changing, and redeploy using localstack-deployer when ready. Your first successful redeployment is your scored submission.

Raises `ValueError` if `fault_manifest.json` is readable from `scenario_dir`.

### scenario_runner.py — `ScenarioRunner`
| Method | Behavior |
|--------|----------|
| `__init__(scenario_dir, run_id)` | Validates pre-conditions; starts MCP stderr tail; takes `start_snapshot` via `file_differ.snapshot(deployment_dir)` |
| `start()` | Deploys `faulted.yaml` via localstack-deployer; waits for CREATE_COMPLETE; runs `functional_test.py` and writes `faulted_baseline.json`; raises if deploy fails |
| `intercept_tool_call(tool_name, input, output)` | Increments `tool_call_count`; writes to trace log |
| `on_model_redeploy()` | Calls `deployment_handler.handle_submission()`; sets `self.submitted = True`; blocks second redeployment |

Redeployment detected by monitoring `localstack-deployer update-stack` calls on MCP server stderr stream.

### deployment_handler.py — `handle_submission` steps
1. Take end snapshot → `log_file_change`
2. cfn-lint on submitted template → return `{"outcome": "lint_fail", "errors": [...]}` on E-rule errors
3. Packaging pre-flight: for each modified `deployment/lambda/` file → zip → upload to `ace-bench-artifacts` S3 bucket → patch `S3Key` in template body string (in memory only, do not write to disk)
4. `cf_client.update_stack(StackName="ace-bench-stack", TemplateBody=..., Capabilities=["CAPABILITY_IAM", "CAPABILITY_NAMED_IAM"])`; poll for UPDATE_COMPLETE or UPDATE_ROLLBACK_COMPLETE
5. Rollback → `{"outcome": "deploy_fail", "events": [...]}` | Success → `{"outcome": "deploy_success"}`

### Test requirements
- `build_context` raises `ValueError` when `fault_manifest.json` present in model-accessible path
- `submitted` flag prevents second redeployment
- `handle_submission` returns `lint_fail` on cfn-lint E-rule errors
- Packaging pre-flight: modified Lambda handler → zip created → S3 upload called with correct bucket/key
- Mocked UPDATE_ROLLBACK_COMPLETE → returns `deploy_fail` with events

---

## Phase D — Verify Loop

**Depends on:** Phase A, Phase C  
**Blocks:** Phase E  
**Gate:** `pytest tests/test_verify.py`

### Files
- `harness/verify/verify_loop.py` — `run_verify_loop(scenario_dir, run_id) -> dict`
- `harness/verify/pass1_functional.py` — `run_pass1(scenario_dir) -> dict`
- `harness/verify/pass2_regression.py` — `run_pass2(scenario_dir, run_id, pass1_result) -> dict`
- `harness/verify/pass3_classification.py` — `run_pass3(scenario_dir, run_id) -> dict`
- `harness/verify/pass4_concurrency.py` — `run_pass4(scenario_dir) -> dict`

### Pass execution order
| Pass | Runs when | Notes |
|------|-----------|-------|
| Pass 1 (functional) | Always | |
| Pass 2 (regression) | Always | Even if Pass 1 fails |
| Pass 3 (classification) | Always | |
| Pass 4 (concurrency) | `fault_class` in `["performance", "reliability"]` | Pass 4 fail + Pass 1 pass → override Pass 3 to `"partial"` |

If deployment did not reach UPDATE_COMPLETE: write `{outcome: "did_not_deploy"}` with all passes skipped.

### Pass 1 — Functional (`pass1_functional.py`)
Runs `functional_test.py` as subprocess. Parses `ASSERT [pass|fail] [name]: [msg]` output lines.
```python
{
  "assertions": {name: {"result": "pass"|"fail", "message": str}},
  "primary_assertions_passed": bool,   # all non-_secondary assertions pass
  "all_assertions_passed": bool,
  "failed_assertion_names": [str]
}
```

### Pass 2 — Regression (`pass2_regression.py`)
Loads `results/[run_id]/faulted_baseline.json`. Finds assertions that were `"pass"` on faulted but `"fail"` after fix.
```python
{
  "regression_count": int,
  "regressions": [{"assertion": str, "severity": "critical"|"non_critical"}],
  "critical_regression_count": int,
  "non_critical_regression_count": int
}
```
Severity: `"critical"` if assertion name does not contain `_secondary`; else `"non_critical"`.

### Pass 3 — Classification (`pass3_classification.py`)
Two signals from `fault_manifest.json`:
1. **Structural diff:** navigate `target_resource.target_property` in submitted vs faulted YAML; check value matches `original_value`
2. **Semantic:** check if any string from `invalid_patches` appears as substring in diff text

```python
if structural_match and no_invalid_patches_in_diff:
    classification = "root_cause"
elif pass1_result["primary_assertions_passed"] and not structural_match:
    classification = "workaround"
elif not pass1_result["primary_assertions_passed"]:
    classification = "partial" if any_improvement else "none"
else:
    classification = "none"
```

Returns: `{structural_match, invalid_patch_detected, classification, root_cause_addressed}`

### Pass 4 — Concurrency (`pass4_concurrency.py`)
Only for `fault_class` in `["performance", "reliability"]`. Sends N concurrent requests via `ThreadPoolExecutor` + `requests.post`. Classifies: 200=success, 429=throttled, 504=timeout, other=error.
```python
{
  "requests_sent": int, "success_count": int, "throttled_count": int,
  "timeout_count": int, "error_count": int,
  "passed": bool   # True if throttled_count == 0 and timeout_count == 0
}
```

---

## Phase E — Entry Point

**Depends on:** Phases A, B, C, D  
**Blocks:** nothing  
**Gate:** E2E integration test — exit 0, valid `verify_result.json`, `classification: root_cause`, `regression_count: 0`

### File
`harness/run.py` — `python harness/run.py <scenario_dir> [--run-id <id>]`

### Startup sequence
1. Load `.env` (HARNESS_API_KEY)
2. `localstack_client.health_check()` — fail fast
3. Validate `scenario_dir` has: `scenario.md`, `faulted.yaml`, `fault_manifest.json`, `deployment/`
4. Validate `fault_manifest.json` not accessible at model-visible path
5. `result_logger.init_run(run_id, scenario_id)`
6. `ScenarioRunner(scenario_dir, run_id).start()` — deploy, capture baseline, write `faulted_baseline.json`
7. `context_builder.build_context(scenario_dir)` → print to stdout (handoff to model)
8. Block until `runner.submitted` or 30-minute timeout
9. Timeout → write `{outcome: "timed_out"}`, exit 1
10. `verify_loop.run_verify_loop(scenario_dir, run_id)` → print human-readable summary
11. Exit 0 if `outcome == "completed"`, else exit 1

### Output summary format
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
    │         │
    │    [B tests pass]
    │         │
    └──► Phase C (runner + deployment handler)
              │
         [C tests pass]
              │
         Phase D (verify loop)
              │
         [D tests pass]
              │
         Phase E (entry point + e2e test)
```
