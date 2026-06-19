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
| **A** | Shared Python utilities (LocalStack client, cfn-lint runner, file differ, result logger) | ✅ Complete — covered by `tests/test_shared.py` |
| **B** | Diagnostic MCP server with 63 tools (61 diagnostic + 2 score): 6 probe, 22 probe-ext, 6 observe, 21 observe-ext, 3 observe-tracing, 3 RDS probe, 2 score | ✅ Complete — 132 Node tests passing |
| **C** | Scenario runner + deployment handler (deploy faulted template, intercept fix submission) | ✅ Complete — 27 Python test functions |
| **D** | Verify loop — 4 scoring passes (functional, regression, classification, concurrency) | ✅ Complete — 2 Python test functions |
| **E** | Harness entry point `run.py` — ties all phases together end-to-end | ✅ Complete — 1 E2E Python test function |
| **F** | Autonomous scoring agent — Claude Sonnet scores 5 dimensions, writes `score.json` | ✅ Complete — 21 Python test functions |
| **G** | Inline agent runner — LiteLLM universal adapter, drives any model end-to-end | ✅ Complete — 42 Python test functions |

Current direct test inventory: 126 Python `def test_*` functions across `tests/*.py` plus 132 Node MCP test cases in `tests/test_mcp_server.js`.

---

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js v22+
- [LocalStack](https://docs.localstack.cloud/getting-started/installation/) **Ultimate** license (not free/Hobby tier — CloudTrail and IAM enforcement require Ultimate)
- `cfn-lint` (`pip install cfn-lint`)

```bash
# Clone and set up Python environment
git clone <repo>
cd ace-bench
python -m venv .venv && source .venv/bin/activate
pip install boto3 cfn-lint pytest

# Install Node dependencies
cd harness/mcp_server && npm install && cd ../..

# Start LocalStack (Ultimate license required; IAM enforcement must be enabled)
LOCALSTACK_AUTH_TOKEN=ls-... ENFORCE_IAM=1 IAM_SOFT_MODE=0 localstack start -d
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

# Phase F — Scoring agent (mocked Anthropic API)
pytest tests/test_scoring.py -v

# Phase G — Inline agent runner
pytest tests/test_agent_loop.py -v

# All Python phases at once
pytest tests/ -v
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
│   │   ├── result_logger.py        # thread-safe JSON result writer
│   │   └── template_parser.py      # extract S3Key stems from CloudFormation YAML
│   ├── mcp_server/           # Phase B — Node.js MCP server (61 diagnostic + 2 score tools, 28 services)
│   │   ├── index.js                # McpServer + StdioTransport, spreads all 7 tool arrays
│   │   ├── package.json
│   │   └── tools/
│   │       ├── probe.js            # 6 core probe tools
│   │       ├── probe_extended.js   # 22 extended probe tools
│   │       ├── observe.js          # 6 core observe tools
│   │       ├── observe_extended.js # 21 extended observe tools
│   │       ├── observe_tracing.js  # 3 tools: ace_lookup_events (CloudTrail) + ace_get_trace_summaries/ace_get_trace (X-Ray)
│   │       ├── probe_rds.js        # 3 RDS tools: ace_describe_db_instance, ace_describe_db_parameters, ace_check_db_connectivity
│   │       └── score.js            # 2 gated score tools
│   ├── runner/               # Phase C — Scenario runner
│   │   ├── context_builder.py      # build_context: reads scenario files, guards manifest
│   │   ├── deployment_handler.py   # handle_submission: lint → zip → CF update
│   │   └── scenario_runner.py      # ScenarioRunner: lifecycle, submitted.yaml snapshot
│   ├── verify/               # Phase D — Verify loop
│   │   ├── pass1_functional.py     # Run functional_test.py, parse ASSERT lines
│   │   ├── pass2_regression.py     # Detect pass→fail regressions vs faulted baseline
│   │   ├── pass3_classification.py # Structural diff + invalid patch detection
│   │   ├── pass4_concurrency.py    # N concurrent requests, classify by status code
│   │   └── verify_loop.py          # Orchestrate all 4 passes, write verify_result.json
│   ├── run.py                # Phase E — CLI entry point (argparse + dotenv + LiteLLM agent)
│   ├── scoring/              # Phase F — autonomous scoring agent
│   │   ├── agent.py                # Claude Sonnet client (call_scoring_agent)
│   │   ├── scorer.py               # Orchestrator: load inputs → score → write score.json
│   │   ├── gate.py                 # Re-exports check_gate from quality.py
│   │   └── dimensions/
│   │       ├── identification.py   # D1: agent-evaluated (weight 0.20)
│   │       ├── fix_correctness.py  # D2: deterministic from pass1 (weight 0.25)
│   │       ├── regression.py       # D3: deterministic penalty (subtracted)
│   │       ├── efficiency.py       # D4: threshold formula + agent rationale (weight 0.15)
│   │       └── quality.py          # D5: agent-evaluated + quality gate (weight 0.40)
│   └── agent/                # Phase G — inline agent runner
│       ├── __init__.py
│       ├── tools.py                # MCP→OpenAI conversion, file tool dispatch, path guards
│       └── loop.py                 # async LiteLLM loop, text-mode retry, verbose streaming
├── corpus/                   # 5 architectures, each with known_good.yaml + functional_test.py
│   ├── arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/
│   ├── arch_02_fuzzy_movie_search/
│   ├── arch_03_serverless_api_with_rds_postgres/
│   ├── arch_08_event_driven_architecture_with_sns_fifo_dynamodb_lambda_and_s3/
│   └── arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3/
├── scenarios/                # 43 faulted scenarios across 5 corpus architectures
├── results/                  # Per-run output (gitignored)
│   └── [run_id]/
│       ├── scenario_id.txt, tool_call_trace.json, file_change_log.json
│       ├── faulted_baseline.json, submitted.yaml, verify_result.json
│       └── score.json              # Written by Phase F after every completed run
├── tests/
│   ├── stubs/
│   │   └── stub_model.py     # E2E: applies known fix, triggers redeployment
│   ├── test_shared.py        # Phase A gate (pytest)
│   ├── test_mcp_server.js    # Phase B gate (node:test) — 124 tests
│   ├── test_runner.py        # Phase C gate (pytest) — 27 test functions
│   ├── test_verify.py        # Phase D gate (pytest) — 2 test functions
│   ├── test_e2e.py           # Phase E gate (pytest, requires live LocalStack) — 1 test
│   ├── test_scoring.py       # Phase F gate (pytest, mocked Anthropic API) — 21 test functions
│   ├── test_agent_loop.py    # Phase G gate (pytest) — 42 test functions
│   ├── test_functional_test_helpers.py # Functional test helper parsing — 6 test functions
│   ├── test_assertion_parser.py        # ASSERT parser — 10 test functions
│   ├── test_template_parser.py         # CloudFormation template parsing — 8 test functions
│   └── test_types.py                   # Shared type contracts — 8 test functions
└── SPEC.md                   # Full design spec
```

---

## MCP Diagnostic Tools

63 tools across 28 LocalStack services (61 diagnostic + 2 score). The model under evaluation sees 61 — score tools are filtered out at the agent layer. Requires LocalStack **Ultimate** license; IAM enforcement (`ENFORCE_IAM=1 IAM_SOFT_MODE=0`) must be active. A CloudTrail tool (`ace_lookup_events`) surfaces recent API-call history, and X-Ray trace tools (`ace_get_trace_summaries`, `ace_get_trace`) expose the distributed-trace segment tree. The X-Ray tools return data only for X-Ray-instrumented architectures: arch01 is instrumented via `aws-xray-sdk` (`xray_instrument.py`); other architectures are not yet instrumented.

### Probe tools — active inspection (6)

| Tool | Description |
|------|-------------|
| `ace_invoke_endpoint` | HTTP call to the deployed API Gateway endpoint |
| `ace_invoke_lambda` | Direct Lambda invocation by function name |
| `ace_check_queue_depth` | SQS queue depth (available, in-flight, oldest age) |
| `ace_read_table_item` | DynamoDB single-item read by key |
| `ace_check_event_source` | Lambda event source mappings; accepts `event_source_arn` for reverse lookup |
| `ace_check_s3_object` | S3 object existence + metadata |

### Extended probe tools — active inspection (22)

| Tool | Description |
|------|-------------|
| `ace_publish_sns` | Publish a message to an SNS topic |
| `ace_put_events` | Send custom events to an EventBridge bus |
| `ace_start_execution` | Start a Step Functions execution and poll for result |
| `ace_count_open_executions` | Count open SWF workflow executions in a domain |
| `ace_send_test_email` | Send a test email via SES |
| `ace_check_instance_state` | EC2 instance state, type, and IP addresses |
| `ace_check_hosted_zone` | Route 53 hosted zone record count and type |
| `ace_list_resolver_endpoints` | Route 53 Resolver endpoints, filterable by direction |
| `ace_put_kinesis_record` | Put a record to a Kinesis stream |
| `ace_put_firehose_record` | Put a record to a Kinesis Firehose delivery stream |
| `ace_get_stream_records` | Read records from all shards of a DynamoDB Stream |
| `ace_encrypt_decrypt` | KMS encrypt then decrypt a payload, confirm roundtrip |
| `ace_get_secret` | Retrieve a Secrets Manager secret value |
| `ace_get_caller_identity` | STS GetCallerIdentity (account, user, ARN) |
| `ace_assume_role` | STS AssumeRole and return temporary credentials |
| `ace_get_parameter` | Read an SSM Parameter Store value |
| `ace_list_access_points` | List S3 Control access points for a bucket |
| `ace_put_metric_data` | Publish a CloudWatch metric data point |
| `ace_simulate_policy` | IAM policy simulation for action/resource pairs |
| `ace_scan_table` | Full DynamoDB Scan with optional filter expression |
| `ace_scan_table_range` | DynamoDB Query with key condition expression; supports GSI |
| `ace_peek_queue_messages` | SQS ReceiveMessage peek without deleting (up to 10) |

### Observe tools — configuration inspection (6)

| Tool | Description |
|------|-------------|
| `ace_describe_resource` | Full config of any CloudFormation stack resource (type-dispatched) |
| `ace_list_resources` | All resources in the stack, filterable by type |
| `ace_get_iam_role` | IAM role with inline and attached policies fully expanded |
| `ace_get_log_tail` | Recent CloudWatch log lines across multiple streams, merged by timestamp |
| `ace_get_stack_outputs` | CloudFormation stack outputs as a flat dict |
| `ace_get_environment_variables` | Lambda function environment variables |

### Extended observe tools — configuration inspection (21)

| Tool | Description |
|------|-------------|
| `ace_get_sns_topic` | SNS topic attributes, subscription counts, and policy |
| `ace_get_eventbridge_rule` | EventBridge rule state, schedule/pattern, and targets |
| `ace_get_schedule` | EventBridge Scheduler schedule expression and target |
| `ace_describe_state_machine` | Step Functions state machine definition and role |
| `ace_describe_swf_domain` | SWF domain status and retention period |
| `ace_get_ses_identity` | SES identity verification status |
| `ace_describe_security_group` | EC2 security group inbound and outbound rules |
| `ace_list_dns_records` | Route 53 record sets in a hosted zone, filterable by type |
| `ace_get_resolver_endpoint` | Route 53 Resolver endpoint details and IP count |
| `ace_describe_kinesis_stream` | Kinesis stream status, shard count, and retention |
| `ace_describe_firehose_stream` | Firehose delivery stream destinations and encryption |
| `ace_describe_dynamo_stream` | DynamoDB Stream view type, status, and shards |
| `ace_describe_kms_key` | KMS key state, usage, spec, and rotation status |
| `ace_describe_secret` | Secrets Manager secret rotation config and tags |
| `ace_describe_parameters` | SSM Parameter Store parameters by path prefix or type |
| `ace_get_public_access_block` | S3 account-level public access block configuration |
| `ace_get_metric_statistics` | CloudWatch metric statistics over a time window |
| `ace_get_s3_object_content` | S3 GetObject with 256 KB cap and UTF-8 decoding |
| `ace_filter_log_events` | CloudWatch Logs FilterLogEvents with pattern matching |
| `ace_get_stack_events` | CloudFormation stack event history with optional FAILED filter |
| `ace_get_lambda_metrics` | Lambda invocations, errors, throttles, and duration summary |

### Tracing observe tools — API-call history & X-Ray traces (3)

| Tool | Description |
|------|-------------|
| `ace_lookup_events` | CloudTrail LookupEvents — recent API-call history (event name, source, resources, error_code/message). |
| `ace_get_trace_summaries` | X-Ray GetTraceSummaries — list recent trace summaries over a window (id, duration, error/fault/throttle flags, entry service). Note: on LocalStack the `only_errors`/`filter_expression` server-side filter is **not implemented** (returns an error) and summary-level `has_fault`/`has_error` are not populated — list traces with `window_minutes` and inspect them via `ace_get_trace`. |
| `ace_get_trace` | X-Ray BatchGetTraces — full segment tree for one trace id; per-segment + per-subsegment `error`/`fault`/`http_status` and the downstream `aws_operation`. Returns data only for X-Ray-instrumented handlers (arch01 via `aws-xray-sdk`). |

### RDS probe tools — active inspection (3)

| Tool | Description |
|------|-------------|
| `ace_describe_db_instance` | RDS DescribeDBInstances — status, engine/version, instance class, endpoint host/port, publicly_accessible, storage_encrypted, attached VPC security group IDs, DB subnet group, parameter group name |
| `ace_describe_db_parameters` | RDS DescribeDBParameters — list parameters for a named DB parameter group (name, value, source, apply_type); optionally filter to specific parameter names |
| `ace_check_db_connectivity` | Open a raw TCP socket to a DB endpoint host:port and report whether it is reachable (`connected`, `refused`, `timeout`, or `error`). Use with `ace_describe_security_group` and `ace_describe_db_instance` to diagnose connectivity-class faults. |

### Score tools — harness only (2)

| Tool | Description |
|------|-------------|
| `ace_verify_fix` | Trigger verify loop (requires `HARNESS_API_KEY`) |
| `ace_score_run` | Score a completed run (requires `HARNESS_API_KEY`) |

Score tools return `{"error": "unauthorized"}` without a valid key — they are filtered from the model's tool list by the inline agent.

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
| LocalStack endpoint | `http://localhost:4566` (Ultimate license; `ENFORCE_IAM=1 IAM_SOFT_MODE=0` required) |
| AWS credentials | `accessKeyId=test`, `secretAccessKey=test` |
| AWS region | `us-east-1` |
| IAM account ID | `000000000000` |
