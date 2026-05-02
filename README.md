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
| **C** | Scenario runner + deployment handler (deploy faulted template, intercept fix submission) | Not started |
| **D** | Verify loop — 4 scoring passes (functional, regression, classification, concurrency) | Not started |
| **E** | Harness entry point `run.py` — ties all phases together end-to-end | Not started |

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
│   ├── runner/               # Phase C (not yet built)
│   ├── verify/               # Phase D (not yet built)
│   └── run.py                # Phase E (not yet built)
├── corpus/                   # Known-good templates + functional tests (HITL-built)
├── scenarios/                # Faulted deployments for evaluation runs
├── results/                  # Per-run output (gitignored)
├── tests/
│   ├── test_shared.py        # Phase A gate (pytest)
│   └── test_mcp_server.js    # Phase B gate (node:test)
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

A completed run is scored across four passes:

1. **Functional** — runs `functional_test.py`; checks `ASSERT pass/fail` lines
2. **Regression** — compares assertions against the faulted baseline; flags anything that passed before the fix and fails after
3. **Classification** — structural diff of the submitted vs faulted template against `fault_manifest.json`; classifies as `root_cause`, `workaround`, `partial`, or `none`
4. **Concurrency** — for `performance`/`reliability` fault classes: N concurrent requests; passes only if zero throttles and zero timeouts

Quality (classification) is the dominant scoring weight. A model that deploys a working workaround scores lower than one that addresses the root cause.

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
