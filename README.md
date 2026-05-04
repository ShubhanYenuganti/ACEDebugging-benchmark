# ACE-Bench

A benchmark for evaluating any model's ability to diagnose and fix intentionally broken cloud infrastructure. Not a generation benchmark — a **debugging benchmark**.

A fixed scenario corpus is built once via a HITL agent pipeline. Any model under evaluation then receives a deployed broken architecture and a vague business-language symptom. It uses MCP diagnostic tools in whatever order and combination it chooses to trace the failure, then submits a fix. The corpus is model-agnostic — the same scenarios run against any model being benchmarked.

Scoring covers five dimensions. Quality is a hard gate: below a minimum quality threshold the run scores zero regardless of speed or efficiency. Above the threshold, quality remains the dominant weight and cannot be overcome by speed and iterations alone.

---

## How It Works

```
Corpus (built once, HITL)          Evaluation run (per model)
──────────────────────             ──────────────────────────
known_good.yaml                    1. Harness deploys faulted.yaml to LocalStack
fault_manifest.json          ──►   2. Model receives scenario.md + stack outputs
faulted.yaml                       3. Model uses MCP tools to diagnose
functional_test.py                 4. Model edits files, calls update-stack
                                   5. Harness intercepts, deploys fix
                                   6. Verify loop scores the result
```

The model never sees `fault_manifest.json` or `known_good.yaml`. It only sees the deployed broken environment and a vague symptom description. Scoring is automatic — no human grader.

---

## Project Status

| Phase | What it builds | Status |
|-------|---------------|--------|
| **A** | Shared Python utilities (LocalStack client, cfn-lint runner, file differ, result logger) | ✅ Complete — 17/17 tests passing |
| **B** | Diagnostic MCP server with 14 tools (6 probe, 6 observe, 2 score stubs) | ✅ Complete — 15/15 tests passing, server registered |
| **C** | Scenario runner + deployment handler (deploy faulted template, intercept fix submission) | ✅ Complete — 8/8 tests passing |
| **D** | Verify loop — 4 scoring passes (functional, regression, classification, concurrency) | ✅ Complete — 20/20 tests passing |
| **E** | Harness entry point `run.py` — ties all phases together end-to-end | ✅ Complete — E2E test passing (45/45 Python tests, exit 0, classification `root_cause`) |
| **F** | Autonomous scoring agent — Claude Sonnet scores 5 dimensions, writes `score.json` | 🔲 Not started |

---

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js v22+
- [LocalStack](https://docs.localstack.cloud/getting-started/installation/) (free tier)
- `cfn-lint` (`pip install cfn-lint`)

```bash
# Clone and set up Python environment
git clone <repo>
cd ace-bench
python -m venv .venv && source .venv/bin/activate
pip install boto3 cfn-lint pytest

# Install Node dependencies
cd harness/mcp_server && npm install && cd ../..

# Start LocalStack
localstack start -d
until localstack status services 2>/dev/null | grep -q "running"; do sleep 2; done
```

### Run the test suites

```bash
# Phase A — Python shared utilities (mocked LocalStack)
pytest tests/test_shared.py -v

# Phase B — MCP server tools (requires LocalStack running)
LOCALSTACK_ENDPOINT=http://localhost:4566 node --test tests/test_mcp_server.js

# Phase C — Scenario runner & deployment handler (fully mocked)
pytest tests/test_runner.py -v

# Phase D — Verify loop (fully mocked)
pytest tests/test_verify.py -v

# Phase E — End-to-end harness (requires LocalStack + .env with HARNESS_API_KEY)
pytest tests/test_e2e.py -v -s

# All Python phases at once
pytest tests/test_shared.py tests/test_runner.py tests/test_verify.py -v
```

### Register the MCP server

Run once after Phase B to make the diagnostic tools available to Claude Code:

```bash
HARNESS_API_KEY=$(openssl rand -hex 32)
echo "HARNESS_API_KEY=$HARNESS_API_KEY" > .env
claude mcp add ace-bench-diagnostic-mcp \
  -e HARNESS_API_KEY=$HARNESS_API_KEY \
  -e LOCALSTACK_ENDPOINT=http://localhost:4566 \
  -- node $(pwd)/harness/mcp_server/index.js
```

---

## Repository Layout

```
ace-bench/
├── harness/
│   ├── shared/               # Phase A — Python utilities
│   │   ├── localstack_client.py    # boto3 singletons for all AWS services
│   │   ├── cfn_lint_runner.py      # cfn-lint subprocess wrapper
│   │   ├── file_differ.py          # snapshot + diff for deployment dir
│   │   └── result_logger.py        # thread-safe JSON result writer
│   ├── mcp_server/           # Phase B — Node.js MCP server
│   │   ├── index.js                # McpServer + StdioTransport, stderr logging
│   │   ├── package.json
│   │   └── tools/
│   │       ├── probe.js            # 6 active-probe tools
│   │       ├── observe.js          # 6 passive-observe tools
│   │       └── score.js            # 2 gated score stubs (Phase D)
│   ├── runner/               # Phase C — Scenario runner
│   │   ├── context_builder.py      # build_context: reads scenario files, guards manifest
│   │   ├── deployment_handler.py   # handle_submission: lint → zip → CF update
│   │   └── scenario_runner.py      # ScenarioRunner: lifecycle, tool call interception
│   ├── verify/               # Phase D — Verify loop
│   │   ├── pass1_functional.py     # Run functional_test.py, parse ASSERT lines
│   │   ├── pass2_regression.py     # Detect pass→fail regressions vs faulted baseline
│   │   ├── pass3_classification.py # Structural diff + invalid patch detection
│   │   ├── pass4_concurrency.py    # N concurrent requests, classify by status code
│   │   └── verify_loop.py          # Orchestrate all 4 passes, write verify_result.json
│   ├── run.py                # Phase E — CLI entry point (argparse + dotenv + verify orchestration)
│   └── scoring/              # Phase F — autonomous scoring agent
│       ├── agent.py                # Claude Sonnet client (call_scoring_agent)
│       ├── scorer.py               # Orchestrator: load inputs → score → write score.json
│       ├── gate.py                 # Re-exports check_gate from quality.py
│       └── dimensions/
│           ├── identification.py   # D1: agent-evaluated (weight 0.20)
│           ├── fix_correctness.py  # D2: deterministic from pass1 (weight 0.25)
│           ├── regression.py       # D3: deterministic penalty (subtracted)
│           ├── efficiency.py       # D4: threshold formula + agent rationale (weight 0.15)
│           └── quality.py          # D5: agent-evaluated + quality gate (weight 0.40)
├── corpus/                   # Known-good templates + functional tests (HITL-built)
├── scenarios/                # Faulted deployments for evaluation runs
├── results/                  # Per-run output (gitignored)
│   └── [run_id]/
│       ├── scenario_id.txt, tool_call_trace.json, file_change_log.json
│       ├── faulted_baseline.json, verify_result.json
│       └── score.json              # Written by Phase F after every completed run
├── tests/
│   ├── stubs/
│   │   └── stub_model.py     # E2E: applies known fix, triggers redeployment
│   ├── test_shared.py        # Phase A gate (pytest) — 17 tests
│   ├── test_mcp_server.js    # Phase B gate (node:test) — 15 tests
│   ├── test_runner.py        # Phase C gate (pytest) — 8 tests
│   ├── test_verify.py        # Phase D gate (pytest) — 20 tests
│   ├── test_e2e.py           # Phase E gate (pytest, requires live LocalStack) — 1 test
│   └── test_scoring.py       # Phase F gate (pytest, mocked Anthropic API)
└── SPEC.md                   # Full design spec
```

---

## MCP Diagnostic Tools

The 14 tools available to a model under evaluation:

### Probe tools — active inspection

| Tool | Description |
|------|-------------|
| `ace_invoke_endpoint` | HTTP call to the deployed API Gateway endpoint |
| `ace_invoke_lambda` | Direct Lambda invocation by function name |
| `ace_check_queue_depth` | SQS queue depth (available, in-flight, oldest age) |
| `ace_read_table_item` | DynamoDB single-item read by key |
| `ace_check_event_source` | Lambda event source mapping list |
| `ace_check_s3_object` | S3 object existence + metadata |

### Observe tools — configuration inspection

| Tool | Description |
|------|-------------|
| `ace_describe_resource` | Full config of a CloudFormation stack resource |
| `ace_list_resources` | All resources in the stack, filterable by type |
| `ace_get_iam_role` | IAM role with inline and attached policies, decoded |
| `ace_get_log_tail` | Recent CloudWatch log lines for a Lambda function |
| `ace_get_stack_outputs` | CloudFormation stack outputs as a flat dict |
| `ace_get_environment_variables` | Lambda function environment variables |

### Score tools — harness only

| Tool | Description |
|------|-------------|
| `ace_verify_fix` | Trigger verify loop (requires `HARNESS_API_KEY`) |
| `ace_score_run` | Score a completed run (requires `HARNESS_API_KEY`) |

Score tools return `{"error": "unauthorized"}` without a valid key — they are never exposed to the model being evaluated.

---

## Scoring

### Verify loop (Phases D/E) — four passes

1. **Functional** — runs `functional_test.py`; checks `ASSERT pass/fail` lines
2. **Regression** — compares assertions against the faulted baseline; flags anything that passed before the fix and fails after
3. **Classification** — structural diff of the submitted vs faulted template against `fault_manifest.json`; classifies as `root_cause`, `workaround`, `partial`, or `none`
4. **Concurrency** — for `performance`/`reliability` fault classes: N concurrent requests; passes only if zero throttles and zero timeouts

### Scoring agent (Phase F) — five dimensions

After every completed run, a Claude Sonnet agent reads all run artifacts and writes `results/[run_id]/score.json`:

| Dimension | Weight | Method |
|-----------|--------|--------|
| Identification | 0.20 | Claude Sonnet — did the tool-call trace show diagnostic reasoning? |
| Fix correctness | 0.25 | Deterministic — derived from pass1 functional test result |
| Regression penalty | subtracted | Deterministic — 0.00 / 0.08 / 0.18 / 0.28 by severity |
| Efficiency | 0.15 | Formula (threshold curve on tool calls, files, lines) + agent rationale |
| Quality | 0.40 | Claude Sonnet — is the fix production-viable and correctly scoped? |

**Quality gate:** before any scoring, `check_gate` verifies classification is `root_cause` or `workaround`, primary assertions passed, and no regressions. Failure zeros the entire score.

**Composite:** `max(0, (d1×0.20 + d2×0.25 + d4×0.15 + d5×0.40) − regression_penalty)`

Quality is the dominant weight and cannot be overcome by speed or efficiency alone.

---

## Runtime

| Component | Value |
|-----------|-------|
| Harness language | Python 3.11 |
| MCP server | Node.js v22+ |
| LocalStack endpoint | `http://localhost:4566` (free tier) |
| AWS credentials | `accessKeyId=test`, `secretAccessKey=test` |
| AWS region | `us-east-1` |
| IAM account ID | `000000000000` |
