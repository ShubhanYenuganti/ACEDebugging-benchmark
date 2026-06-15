# Running the ACE-Bench Harness End-to-End

This document walks through a complete evaluation run: starting LocalStack, running a model against a scenario, and viewing scoring output.

---

## Prerequisites

- Python 3.11 with the project installed (`pip install -e .`)
- Node.js v22+ (for the MCP diagnostic server)
- LocalStack CLI installed (`pip install localstack` or via Homebrew)
- `ANTHROPIC_API_KEY` set in environment (required by the scoring agent)

---

## Step 1 — Start LocalStack

This project requires the LocalStack **Ultimate** license. The free/Hobby tier does not support CloudTrail or real IAM enforcement.

```bash
LOCALSTACK_AUTH_TOKEN=ls-... ENFORCE_IAM=1 IAM_SOFT_MODE=0 localstack start -d
until localstack status services 2>/dev/null | grep -q "running"; do sleep 2; done
```

The harness fails fast at startup (via `assert_iam_enforcement()`) if IAM enforcement is not active.

The harness communicates with LocalStack at `http://localhost:4566` using fake credentials (`test`/`test`). All CloudFormation stacks, Lambda functions, SQS queues, and S3 buckets are created there.

---

## Step 2 — Generate a HARNESS_API_KEY

The harness uses an API key to gate internal score tools (the evaluated model never sees this key). Generate one and save it to `.env`:

```bash
echo "HARNESS_API_KEY=$(openssl rand -hex 32)" >> .env
```

The harness loads `.env` at startup via `python-dotenv`. Never commit `.env`.

---

## Step 3 — Add an LLM Model to Evaluate

The harness drives any LLM directly using [LiteLLM](https://docs.litellm.ai/) as a universal adapter. The MCP diagnostic server is spawned automatically as a subprocess — no manual registration required.

```
python harness/run.py <scenario_dir> --model <provider/model> [--api-key <key>] [--base-url <url>]
```

#### Supported providers

| Provider | `--model` value | Authentication |
|----------|----------------|----------------|
| Anthropic | `anthropic/claude-sonnet-4-6` | `--api-key` or `ANTHROPIC_API_KEY` env var |
| Anthropic | `anthropic/claude-haiku-4` | `--api-key` or `ANTHROPIC_API_KEY` env var |
| OpenAI | `openai/gpt-4o` | `--api-key` or `OPENAI_API_KEY` env var |
| OpenAI | `openai/gpt-4.1-mini` | `--api-key` or `OPENAI_API_KEY` env var |
| Google Gemini | `gemini/gemini-2.5-pro` | `--api-key` or `GEMINI_API_KEY` env var |
| Google Gemini | `gemini/gemini-2.5-flash` | `--api-key` or `GEMINI_API_KEY` env var |
| Ollama (local) | `ollama/qwen2.5` | none — requires `--base-url` |
| Ollama (local) | `ollama/glm4` | none — requires `--base-url` |
| Ollama (local) | `ollama/llama3.1` | none — requires `--base-url` |
| Any OpenAI-compatible | `openai/<model-name>` | `--api-key` + `--base-url` |

The `--model` value uses LiteLLM's `provider/model` format. LiteLLM automatically converts OpenAI-format tool definitions to each provider's native format, so the same tool definitions work across all providers.

#### API key resolution

The `--api-key` flag takes priority. If omitted, LiteLLM falls back to the standard environment variable for each provider:

| Provider | Env var fallback |
|----------|-----------------|
| Anthropic | `ANTHROPIC_API_KEY` |
| OpenAI | `OPENAI_API_KEY` |
| Gemini | `GEMINI_API_KEY` |
| Ollama | none needed |

For Ollama and other self-hosted models, no API key is required — just set `--base-url`.

#### The `--base-url` flag

Use `--base-url` when the model is served at a custom endpoint:

- **Ollama**: `--base-url http://localhost:11434`
- **vLLM**: `--base-url http://localhost:8000/v1`
- **Any OpenAI-compatible API**: `--base-url https://your-server.example.com/v1`

#### Example invocations

```bash
# Anthropic Claude
python harness/run.py scenarios/arch01_fault01_connectivity/ \
  --model anthropic/claude-sonnet-4-6 \
  --api-key sk-ant-...

# OpenAI GPT-4o
python harness/run.py scenarios/arch01_fault01_connectivity/ \
  --model openai/gpt-4o \
  --api-key sk-...

# Google Gemini
python harness/run.py scenarios/arch01_fault01_connectivity/ \
  --model gemini/gemini-2.5-pro \
  --api-key AIza...

# Ollama (local, no API key needed)
python harness/run.py scenarios/arch01_fault01_connectivity/ \
  --model ollama/qwen2.5 \
  --base-url http://localhost:11434

# Using env vars instead of --api-key
export ANTHROPIC_API_KEY=sk-ant-...
python harness/run.py scenarios/arch01_fault01_connectivity/ \
  --model anthropic/claude-sonnet-4-6

# Verbose mode — streams reasoning text and write_file previews
python harness/run.py scenarios/arch01_fault01_connectivity/ \
  --model anthropic/claude-sonnet-4-6 \
  --verbose
```

#### VPS / self-hosted inference

For RunPod, vLLM, or any OpenAI-compatible endpoint, set environment variables instead of flags:

| Variable | Description |
|----------|-------------|
| `ACE_VPS_ENDPOINT` | Base URL of the inference server (e.g. `https://xyz.runpod.net/v1`) |
| `ACE_VPS_MODEL` | Model name passed to the server (default: `qwen2.5-coder:32b`) |
| `ACE_VPS_API_KEY` | API key for the server (default: `ollama`) |
| `ACE_VPS_AUTH_TOKEN` | Bearer token for RunPod Secure Cloud pods (adds `Authorization` header) |

When `ACE_VPS_ENDPOINT` is set (or `--base-url` is passed) and `--model` does not start with a recognized cloud-provider prefix (`anthropic/`, `openai/`, `gemini/`, …), the harness enters VPS mode automatically.

```bash
export ACE_VPS_ENDPOINT=https://xyz-pod.runpod.net/v1
export ACE_VPS_AUTH_TOKEN=rp_...
python harness/run.py scenarios/arch01_fault01_connectivity/ --model ollama/qwen2.5-coder:32b
```

#### How the inline agent works

When `--model` is provided:

1. The harness deploys the faulted scenario and prints context as usual.
2. A daemon thread starts `run_agent_loop`, which:
   - Spawns the Node.js MCP server as a stdio subprocess (no manual registration needed).
   - Discovers all 56 MCP tools at runtime (54 diagnostic + 2 score; see tool inventory below).
   - Filters out score tools (`ace_verify_fix`, `ace_score_run`) so the model cannot call them.
   - Adds Python-native file tools (`read_file`, `write_file`, `list_directory`, `submit_fix`).
   - Loops calling the LLM up to 50 turns (default), dispatching tool calls each turn.
3. When the model calls `submit_fix`, the agent writes the signal file (`/tmp/ace-bench-update.json`).
4. The main thread's polling loop detects the signal file and proceeds with deployment, verification, and scoring.

File tool restrictions enforced by the agent:
- `read_file`: reads any file in the scenario directory **except** `fault_manifest.json` and `known_good.yaml`. Path traversal is blocked.
- `write_file`: only allows writes to `deployment/` and `faulted.yaml`. All other paths are rejected.
- `submit_fix`: only accepted after at least one prior `write_file` call. First call is final — there is no second chance.

If the agent thread crashes (auth error, network failure, etc.), the harness exits immediately with a clear error message instead of silently timing out.

#### MCP diagnostic tool inventory

The MCP server exposes 56 tools across 27 LocalStack services (54 diagnostic + 2 score). The model has access to 54 (score tools are filtered out).

**Core probe tools (6)** — `probe.js`:
`ace_invoke_endpoint`, `ace_invoke_lambda`, `ace_check_queue_depth`, `ace_read_table_item`, `ace_check_event_source`, `ace_check_s3_object`

**Extended probe tools (22)** — `probe_extended.js`:
`ace_publish_sns`, `ace_put_events`, `ace_start_execution`, `ace_count_open_executions`, `ace_send_test_email`, `ace_check_instance_state`, `ace_check_hosted_zone`, `ace_list_resolver_endpoints`, `ace_put_kinesis_record`, `ace_put_firehose_record`, `ace_get_stream_records`, `ace_encrypt_decrypt`, `ace_get_secret`, `ace_get_caller_identity`, `ace_assume_role`, `ace_get_parameter`, `ace_list_access_points`, `ace_put_metric_data`, `ace_simulate_policy`, `ace_scan_table`, `ace_scan_table_range`, `ace_peek_queue_messages`

**Core observe tools (6)** — `observe.js`:
`ace_describe_resource`, `ace_list_resources`, `ace_get_iam_role`, `ace_get_log_tail`, `ace_get_stack_outputs`, `ace_get_environment_variables`

**Extended observe tools (21)** — `observe_extended.js`:
`ace_get_sns_topic`, `ace_get_eventbridge_rule`, `ace_get_schedule`, `ace_describe_state_machine`, `ace_describe_swf_domain`, `ace_get_ses_identity`, `ace_describe_security_group`, `ace_list_dns_records`, `ace_get_resolver_endpoint`, `ace_describe_kinesis_stream`, `ace_describe_firehose_stream`, `ace_describe_dynamo_stream`, `ace_describe_kms_key`, `ace_describe_secret`, `ace_describe_parameters`, `ace_get_public_access_block`, `ace_get_metric_statistics`, `ace_get_s3_object_content`, `ace_filter_log_events`, `ace_get_stack_events`, `ace_get_lambda_metrics`

**Tracing observe tools (1)** — `observe_tracing.js`:
`ace_lookup_events` (CloudTrail LookupEvents — recent API-call history; X-Ray tools deferred to a future phase)

**Score tools (2, harness-only)** — `score.js`:
`ace_verify_fix`, `ace_score_run`

**Services covered:** CloudFormation, Lambda, DynamoDB, DynamoDB Streams, SQS, IAM, CloudWatch Logs, CloudWatch Metrics, S3, S3 Control, SNS, EventBridge, EventBridge Scheduler, Step Functions, SWF, SES, EC2, Route 53, Route 53 Resolver, Kinesis Streams, Kinesis Firehose, KMS, Secrets Manager, STS, SSM, CloudTrail, API Gateway (via HTTP fetch)

---

## Step 4 — Choose a Scenario

Available scenarios are under `scenarios/`. 40 scenarios span 4 corpus architectures (10 faults each):

```
scenarios/
├── arch01_fault01_connectivity/   ─┐
├── arch01_fault02_data_correctness │ arch_01 — serverless microservices
├── ...                             │ (API Gateway, DynamoDB, SQS, Lambda)
├── arch01_fault10_data_correctness ─┘
├── arch02_fault01_data_correctness ─┐
├── ...                              │ arch_02 — fuzzy movie search
├── arch02_fault10_reliability      ─┘
├── arch08_fault01_connectivity     ─┐
├── ...                              │ arch_08 — event-driven SNS FIFO
├── arch08_fault10_data_correctness ─┘   (SNS, DynamoDB, Lambda, S3)
├── arch12_fault01_connectivity     ─┐
├── ...                              │ arch_12 — event-driven pipeline
└── arch12_fault10_security         ─┘   (SQS, Lambda, DynamoDB, S3)
```

Each scenario's fault class is one of: `connectivity`, `data_correctness`, `performance`, `reliability`, `security`.

The corpus architecture the scenario targets is under `corpus/`:

```
corpus/
├── arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/
│   ├── known_good.yaml      # correct template (never exposed to model)
│   ├── functional_test.py   # assertion suite
│   └── traffic_flow.md      # hop-by-hop request flow description
├── arch_02_fuzzy_movie_search/
├── arch_08_event_driven_architecture_with_sns_fifo_dynamodb_lambda_and_s3/
└── arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3/
```

The `fault_manifest.json` inside each scenario directory encodes what was injected and what the optimal fix looks like. It is never exposed to the model.

---

## Step 5 — Run the Harness

### Full command syntax

```bash
python harness/run.py <scenario_dir> \
  [--run-id <id>] \
  [--model PROVIDER/MODEL] \
  [--api-key KEY] \
  [--base-url URL] \
  [--verbose]
```

| Flag | Required | Description |
|------|----------|-------------|
| `scenario_dir` | yes | Path to scenario directory |
| `--run-id` | no | Run identifier (auto-generated 8-char hex if omitted) |
| `--model` | no | LiteLLM model string; enables inline agent mode |
| `--api-key` | no | API key for the model provider; falls back to env var |
| `--base-url` | no | Custom API endpoint (Ollama, vLLM, self-hosted); also activates VPS mode |
| `--verbose` | no | Stream the model's reasoning text and show write_file previews (first 30 lines) |

### What happens internally

1. Loads `.env` and checks LocalStack is reachable.
2. Validates the scenario directory (requires `scenario.md`, `faulted.yaml`, `fault_manifest.json`, `deployment/`).
3. Deploys `faulted.yaml` to LocalStack and records a baseline assertion result (`faulted_baseline.json`).
4. Builds and prints the model context (scenario brief, template path, deployment dir, stack outputs, instructions).
5. If `--model` is set: starts the inline agent in a daemon thread.
6. Blocks for up to 30 minutes waiting for the signal file (written by inline agent or external agent).
7. On redeployment: runs cfn-lint, packages any modified Lambda handlers, calls CloudFormation `update-stack`, waits for `UPDATE_COMPLETE`.
8. Runs the four-pass verify loop.
9. Runs the scoring agent (Claude Sonnet).
10. Prints the summary and exits.

---

## Step 6 — Verify Loop (automatic)

After `UPDATE_COMPLETE`, the harness runs four verification passes automatically:

| Pass | What it checks |
|------|----------------|
| Pass 1 — Functional | Runs `functional_test.py`; checks all assertions |
| Pass 2 — Regression | Compares Pass 1 results against the faulted baseline |
| Pass 3 — Classification | Structural diff + semantic check → `root_cause / workaround / partial / none` |
| Pass 4 — Concurrency | Concurrent load test (only for `performance` / `reliability` fault classes) |

Results are written to `results/<run_id>/verify_result.json`.

---

## Step 7 — Scoring Agent (automatic, runs after verify loop)

If the verify loop completes (outcome `completed`), the scoring agent runs automatically immediately after. It uses Claude Sonnet to evaluate three judgment dimensions and computes two deterministic dimensions:

| Dimension | Weight | Method |
|-----------|--------|--------|
| Identification | 0.20 | Claude Sonnet — did the model find the right resource before fixing? |
| Fix correctness | 0.25 | Deterministic — assertion pass/fail counts |
| Regression penalty | subtracted | Deterministic — critical/non-critical regression counts |
| Efficiency | 0.15 | Formula — actual vs optimal tool calls, files, lines; Sonnet rationale |
| Quality | 0.40 | Claude Sonnet — fix quality vs valid_fixes / invalid_patches |

**Quality gate:** if the fix is not classified as `root_cause` or `workaround`, primary assertions did not pass, or there are regressions, the gate fails and `final_score` is set to `0.0` without calling the scoring agent.

Composite formula:
```
weighted  = (identification * 0.20) + (fix_correctness * 0.25)
          + (efficiency * 0.15) + (quality * 0.40)
final     = max(0.0, weighted - regression_penalty)
```

Score output is written to `results/<run_id>/score.json`.

---

## Reading the Terminal Output

After the run completes, the harness prints a two-part summary:

```
═══════════════════════════════════════
ACE-Bench Run: a1b2c3d4
Scenario: arch01_fault01_connectivity
═══════════════════════════════════════

Deployment:       PASS
Functional test:  PASS
Regressions:      none
Classification:   root_cause
Concurrency:      SKIPPED

Tool calls made:  7
Files changed:    1
Lines changed:    3

Full results:     results/a1b2c3d4/
═══════════════════════════════════════

── Scoring (Claude Sonnet) ─────────────
Quality gate:     PASS
Identification:   0.90  Target resource and property surfaced before first fix attempt.
Fix correctness:  1.00  All assertions passed.
Regression:      -0.00  No regressions.
Efficiency:       0.85  Tool call count was 1.4× optimal; no redundant file edits.
Quality:          1.00  Change matches valid fix exactly with no unnecessary modifications.
────────────────────────────────────────
Final score:      0.9275
Interpretation:   Root cause identified, clean fix, no regressions, efficient
────────────────────────────────────────
```

Exit code `0` = verify loop completed; `1` = deployment failed, timed out, or verify loop did not complete.

---

## Result Files

```
results/<run_id>/
├── scenario_id.txt        — which scenario was run
├── tool_call_trace.json   — every MCP diagnostic call with input/output/timestamp
├── file_change_log.json   — files and lines changed by the model
├── faulted_baseline.json  — assertion results on the faulted deployment (pre-fix)
├── verify_result.json     — output of all four verification passes
└── score.json             — final score with per-dimension breakdown
```

---

## Running Multiple Scenarios

```bash
# Run all scenarios with GPT-4o
for scenario in scenarios/arch01_fault*/; do
    python harness/run.py "$scenario" \
      --model openai/gpt-4o \
      --api-key "$OPENAI_API_KEY"
done

# Run all scenarios with local Ollama
for scenario in scenarios/arch01_fault*/; do
    python harness/run.py "$scenario" \
      --model ollama/qwen2.5 \
      --base-url http://localhost:11434
done
```

Each run gets a unique auto-generated `run_id`, so results do not collide.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `ERROR: HARNESS_API_KEY env var is required when --model is used` | Missing `.env` file or key | Run `echo "HARNESS_API_KEY=$(openssl rand -hex 32)" >> .env` |
| `ERROR: Agent crashed: AuthenticationError` | Invalid or missing API key | Check `--api-key` or the provider's env var |
| `ERROR: Agent crashed: Connection refused` | Ollama not running or wrong `--base-url` | Start Ollama (`ollama serve`) and verify the URL |
| `ERROR: Timed out waiting for model redeployment` | Model did not call `submit_fix` within 30 minutes | Check model output; the model may be stuck or looping |
| `ModuleNotFoundError: No module named 'litellm'` | Dependencies not installed | Run `pip install -e .` |

---

## Stopping LocalStack

```bash
localstack stop
```
