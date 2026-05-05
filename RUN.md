# Running the ACE-Bench Harness End-to-End

This document walks through a complete evaluation run: starting LocalStack, wiring in an LLM model, running the agent against a scenario, letting the verify loop execute, and viewing scoring output.

---

## Prerequisites

- Python 3.11 with the project venv active (`.venv/bin/activate`)
- Node.js v22+ (for the MCP diagnostic server)
- LocalStack CLI installed (`pip install localstack` or via Homebrew)
- `ANTHROPIC_API_KEY` set in environment (required by the scoring agent)
- MCP server registered (one-time setup; see below)

---

## Step 1 — Start LocalStack

```bash
localstack start -d
until localstack status services 2>/dev/null | grep -q "running"; do sleep 2; done
```

The harness communicates with LocalStack at `http://localhost:4566` using fake credentials (`test`/`test`). All CloudFormation stacks, Lambda functions, SQS queues, and S3 buckets are created there.

---

## Step 2 — Register the MCP Diagnostic Server (one-time)

The benchmarked model receives diagnostic tools via MCP. Register the server with Claude Code once:

```bash
claude mcp add ace-bench-diagnostic-mcp \
  -e HARNESS_API_KEY=$(openssl rand -hex 32) \
  -e LOCALSTACK_ENDPOINT=http://localhost:4566 \
  -- node harness/mcp_server/index.js
```

Copy the generated `HARNESS_API_KEY` value into a `.env` file at the project root:

```
HARNESS_API_KEY=<value printed above>
```

The harness reads this key at startup. The key is never passed to the model — it gates internal score tools only.

---

## Step 3 — Add an LLM Model to Evaluate

Pass `--model` (a LiteLLM provider/model string) and, where required, `--api-key` to
`run.py`. The harness runs the model in-process with full access to the registered MCP
diagnostic tools and the scenario's `deployment/` directory.

### Supported providers (examples)

| Provider | `--model` | Auth |
|----------|-----------|------|
| Anthropic | `anthropic/claude-sonnet-4-6` | `--api-key` or `ANTHROPIC_API_KEY` |
| OpenAI | `openai/gpt-4o` | `--api-key` or `OPENAI_API_KEY` |
| Google Gemini | `gemini/gemini-1.5-pro` | `--api-key` or `GEMINI_API_KEY` |
| Ollama (local) | `ollama/qwen2.5` | none — use `--base-url http://localhost:11434` |
| Ollama (local) | `ollama/glm4` | none — use `--base-url http://localhost:11434` |
| Any OpenAI-compatible | `openai/your-model` | `--api-key` + `--base-url` |

### Example invocations

```bash
# Anthropic
python harness/run.py scenarios/arch01_fault01_security/ \
  --model anthropic/claude-sonnet-4-6 \
  --api-key sk-ant-...

# OpenAI
python harness/run.py scenarios/arch01_fault01_security/ \
  --model openai/gpt-4o \
  --api-key sk-...

# Gemini
python harness/run.py scenarios/arch01_fault01_security/ \
  --model gemini/gemini-1.5-pro \
  --api-key AIza...

# Ollama (Qwen, GLM, Gemma — no key needed)
python harness/run.py scenarios/arch01_fault01_security/ \
  --model ollama/qwen2.5 \
  --base-url http://localhost:11434
```

Without `--model`, the harness prints context to stdout and waits up to 30 minutes for
an external agent to write `/tmp/ace-bench-update.json`.

---

## Step 4 — Choose a Scenario

Available scenarios are under `scenarios/`. Each directory is one fault injected into a corpus architecture:

```
scenarios/
├── arch01_fault01_security/     # ESM disabled (config fault)
├── arch01_fault02_connectivity/ # SQS endpoint misconfigured
└── arch01_fault03_performance/  # Lambda concurrency throttled
```

The corpus architecture the scenario targets is under `corpus/`:

```
corpus/
└── arch_01_order_processing/
    ├── known_good.yaml      # correct template (never exposed to model)
    ├── functional_test.py   # assertion suite
    └── traffic_flow.md      # hop-by-hop request flow description
```

The `fault_manifest.json` inside each scenario directory encodes what was injected and what the optimal fix looks like. It is never exposed to the model.

---

## Step 5 — Run the Harness

```bash
python harness/run.py scenarios/arch01_fault01_security/ [--run-id <id>]
```

If `--run-id` is omitted, an 8-character hex ID is generated automatically.

**What happens internally:**

1. Loads `.env` and checks LocalStack is reachable.
2. Validates the scenario directory (requires `scenario.md`, `faulted.yaml`, `fault_manifest.json`, `deployment/`).
3. Deploys `faulted.yaml` to LocalStack and records a baseline assertion result (`faulted_baseline.json`).
4. Builds and prints the model context (scenario brief, template path, deployment dir, stack outputs, instructions).
5. Blocks for up to 30 minutes waiting for the model to trigger redeployment.
6. On redeployment: runs cfn-lint, packages any modified Lambda handlers, calls CloudFormation `update-stack`, waits for `UPDATE_COMPLETE`.

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
Scenario: arch01_fault01_security
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
for scenario in scenarios/arch01_fault*/; do
    python harness/run.py "$scenario"
done
```

Each run gets a unique auto-generated `run_id`, so results do not collide.

---

## Stopping LocalStack

```bash
localstack stop
```
