# ACE-Bench Harness

## Subagent-Driven Development Model Preferences

When using `superpowers:subagent-driven-development`:
- **Worker subagents:** always use `claude-sonnet-4-6` (model: `sonnet`)
- **Overseer:** always use `claude-opus-4-8` (model: `opus`)

A benchmark for evaluating a model's ability to diagnose and fix intentionally broken cloud infrastructure. Supports any LLM provider via LiteLLM (Anthropic, OpenAI, Gemini, Ollama, etc.). Phases A→G are strictly sequential. Full spec: SPEC.md. Run guide: RUN.md.

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
pytest tests/test_runner.py          # Phase C gate
pytest tests/test_verify.py          # Phase D gate
pytest tests/test_scoring.py         # Phase F gate
pytest tests/test_agent_loop.py      # Phase G gate

# Node MCP server tests (Phase B only)
node --test tests/test_mcp_server.js

# Run a scenario
python harness/run.py scenarios/<scenario_dir>/ \
  --model <provider/model> [--api-key <key>] [--base-url <url>]
```

---

## Runtime

| Component        | Runtime                                                         |
|------------------|-----------------------------------------------------------------|
| Harness code     | Python 3.11                                                     |
| MCP server       | Node.js v22+                                                    |
| LocalStack       | http://localhost:4566 (Ultimate license; `ENFORCE_IAM=1 IAM_SOFT_MODE=0` required) |
| AWS credentials  | accessKeyId=`test`, secretAccessKey=`test`, region=`us-east-1` |
| IAM fake account | `000000000000`                                                  |

## Python Dependencies

Defined in `pyproject.toml`:
- `boto3` — AWS SDK for LocalStack interaction
- `requests` — HTTP calls for API Gateway probing
- `python-dotenv` — loads `.env` at startup
- `cfn-lint` — CloudFormation template validation
- `pytest`, `pytest-mock` — test suite
- `litellm` — universal LLM adapter (Anthropic, OpenAI, Gemini, Ollama, etc.)
- `mcp` — MCP client for spawning the diagnostic server from Python
- `anthropic` — used by the scoring agent (Phase F) only

---

## Standard Imports (Python)

```python
# Shared utilities (Phase A)
from harness.shared.localstack_client import (
    cf_client, lambda_client, s3_client, sqs_client,
    iam_client, logs_client
)
from harness.shared.cfn_lint_runner import run_lint
from harness.shared.file_differ import diff_directories
from harness.shared.result_logger import log_result, log_tool_call, log_file_change

# Agent package (Phase G)
from harness.agent.tools import (
    mcp_to_openai_tool, filter_model_tools,
    dispatch_file_tool, FILE_TOOL_DEFINITIONS
)
from harness.agent.loop import run_agent_loop
```

---

## Key Invariants — Never Violate

- **Submission is final.** First `UPDATE_COMPLETE` is the scored submission; `ScenarioRunner.submitted` blocks a second redeployment.
- **`fault_manifest.json` is never exposed to the model.** `build_context` raises `ValueError` if readable from the model-accessible path. The inline agent's `read_file` tool blocks it explicitly.
- **`known_good.yaml` is never exposed to the model.** The inline agent's `read_file` tool blocks it explicitly.
- **File edits do not count as tool calls.** `tool_call_trace.json` records MCP diagnostic invocations only. The inline agent dispatches file tools locally and does not log them.
- **Tool calls are logged individually** with input, output, and timestamp.
- **Score tools require `HARNESS_API_KEY`.** Calls without it return `{"error": "unauthorized"}`. The inline agent filters `ace_verify_fix` and `ace_score_run` out of the model's tool list entirely.
- **The inline agent's `write_file` is restricted** to `deployment/` and `faulted.yaml`. All other write paths are rejected. Path traversal is prevented via `resolve()` + `relative_to()`.
- **`HARNESS_API_KEY` is required** when using `--model`. The harness validates this at startup and exits with a clear error if missing.
- **IAM enforcement is required.** `run.py` calls `assert_iam_enforcement()` after the health check and exits if LocalStack was not started with `ENFORCE_IAM=1` / `IAM_SOFT_MODE=0`. Test fixtures that create Lambdas must define a real assumable role.

---

## Project Layout

```
ace-bench/
├── CLAUDE.md
├── SPEC.md
├── RUN.md
├── pyproject.toml
├── .env                          # HARNESS_API_KEY — never commit
├── harness/
│   ├── shared/                   # Phase A
│   │   ├── localstack_client.py
│   │   ├── cfn_lint_runner.py
│   │   ├── file_differ.py
│   │   └── result_logger.py
│   ├── mcp_server/               # Phase B — 54 diagnostic + 2 score tools across 27 LocalStack services
│   │   ├── index.js              # spreads all 6 tool arrays into MCP server
│   │   ├── package.json
│   │   └── tools/
│   │       ├── probe.js           # 6 original probe tools
│   │       ├── probe_extended.js  # 22 extended probe tools (SNS→IAM sim)
│   │       ├── observe.js         # 6 original observe tools
│   │       ├── observe_extended.js # 21 extended observe tools (SNS→CloudWatch)
│   │       ├── observe_tracing.js  # 1 tool: ace_lookup_events (CloudTrail); X-Ray deferred to next phase
│   │       └── score.js           # 2 score tools
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
│   ├── scoring/                  # Phase F
│   │   ├── agent.py
│   │   ├── scorer.py
│   │   ├── gate.py
│   │   └── dimensions/
│   │       ├── identification.py
│   │       ├── fix_correctness.py
│   │       ├── regression.py
│   │       ├── efficiency.py
│   │       └── quality.py
│   ├── agent/                    # Phase G — inline agent runner
│   │   ├── __init__.py
│   │   ├── tools.py              # tool definitions, MCP conversion, file dispatch
│   │   └── loop.py               # async LiteLLM agent loop
│   └── run.py                    # Phase E — entry point
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
│   ├── verify_result.json
│   └── score.json
└── tests/
    ├── test_shared.py            # Phase A
    ├── test_mcp_server.js        # Phase B
    ├── test_runner.py            # Phase C
    ├── test_verify.py            # Phase D
    ├── test_scoring.py           # Phase F
    └── test_agent_loop.py        # Phase G
```