# Phase F — Step 7 Scoring

## What this phase builds

An autonomous scoring agent powered by Claude Sonnet that evaluates
every completed run in `results/`. The agent reads the artifacts
produced by Phases A–E, reasons across five scoring dimensions, and
writes a structured `score.json` per run.

The agent is not a rule engine. It uses Claude Sonnet's judgment to
evaluate dimensions that require reasoning — identification, quality,
and efficiency interpretation — while applying deterministic formulas
to dimensions that are purely computational. This separation is
explicit in each module below.

The scoring agent runs after every run completes. It is called by
`harness/run.py` automatically as the final step of the pipeline.

---

## Why Claude Sonnet for scoring

Three of the five dimensions require judgment that rule-based code
cannot reliably produce:

**Identification** — did the model's tool-call sequence demonstrate
that it understood what was broken before it fixed it? A rules engine
can check whether a specific tool was called. It cannot determine
whether the sequence of calls reflects genuine diagnostic reasoning
or accidental convergence on the correct resource.

**Quality** — is the fix production-viable? The rubric has five
classifications. Distinguishing a clean root-cause fix from an
over-permissive one (wildcard IAM vs. scoped ARN) requires reading
the diff and reasoning about its implications — not matching strings.

**Efficiency rationale** — the formula is deterministic, but the
interpretation of why a model was or wasn't efficient requires
looking at the tool-call trace and explaining which calls were
redundant, which were necessary, and which revealed the diagnosis.

Claude Sonnet receives the full artifact set for a run — trace,
diff, verify result, manifest, and the deployed template — and
produces structured scores with explicit rationale for each
dimension. The rationale is written to `score.json` alongside
the numeric scores, making results auditable.

---

## Pre-conditions

Phases A–E are complete. Each run produces:

```
results/[run_id]/
├── scenario_id.txt
├── tool_call_trace.json      — [{turn, tool, input, output, timestamp}]
├── file_change_log.json      — {files_added, files_modified,
│                                per_file_line_changes, total_lines_changed}
├── faulted_baseline.json     — functional_test.py output on faulted deploy
└── verify_result.json        — {outcome, pass1_functional, pass2_regression,
                                 pass3_classification, pass4_concurrency|null}
```

The scoring agent also reads from the scenario directory:
```
scenarios/[scenario_id]/
├── fault_manifest.json       — {optimal_tool_calls, optimal_files_changed,
│                                optimal_lines_changed, target_resource,
│                                target_property, original_value,
│                                valid_fixes, invalid_patches, fault_class}
└── faulted.yaml              — the template the model received
```

And from the corpus:
```
corpus/[arch_id]/
└── known_good.yaml           — the ground-truth template
```

---

## Deliverables

```
harness/
└── scoring/
    ├── agent.py              # F1 — Claude Sonnet agent caller
    ├── scorer.py             # F2 — orchestrator: loads inputs, calls agent,
    │                         #      applies formulas, writes score.json
    ├── dimensions/
    │   ├── identification.py # F3 — builds identification prompt + parses result
    │   ├── fix_correctness.py # F4 — deterministic, no agent call needed
    │   ├── regression.py     # F5 — deterministic, no agent call needed
    │   ├── efficiency.py     # F6 — formula deterministic; agent writes rationale
    │   └── quality.py        # F7 — builds quality prompt + parses result
    └── gate.py               # F8 — quality gate check before scoring runs

results/[run_id]/
└── score.json                # written by scorer after every completed run
```

`harness/run.py` is updated (F9) to call `scorer.score_run()` as
the final step after the verify loop.

---

## F1 — `harness/scoring/agent.py`

The Claude Sonnet client used by all agent-evaluated dimensions.
Every call to the scoring agent goes through this module.

```python
import anthropic

client = anthropic.Anthropic()
SCORING_MODEL = "claude-sonnet-4-5"

def call_scoring_agent(system_prompt: str, user_prompt: str) -> str:
    """
    Send a scoring prompt to Claude Sonnet and return the text response.
    Used by identification.py and quality.py for agent-evaluated dimensions.
    All scoring prompts instruct the model to return only valid JSON.
    """
    message = client.messages.create(
        model=SCORING_MODEL,
        max_tokens=1024,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}]
    )
    return message.content[0].text.strip()
```

**System prompt used for all scoring agent calls:**

```
You are an autonomous infrastructure debugging benchmark scorer.
You evaluate AI model runs against a known-good AWS architecture.

You will be given:
- The fault that was injected into the deployment
- The model's tool-call trace (what it called and what it observed)
- The model's file changes (what it edited and by how much)
- The verify loop result (functional test, regression, classification)

You must return ONLY valid JSON matching the schema specified in each
prompt. No explanation outside the JSON. No markdown fences.
Every numeric score must be a float between 0.0 and 1.0.
Every rationale field must be 1–2 sentences explaining the score.
```

---

## F2 — `harness/scoring/scorer.py`

Orchestrates the full scoring run. Loads all inputs, calls the agent
for judgment-based dimensions, applies deterministic formulas for
computational dimensions, enforces the quality gate, and writes
`score.json`.

```python
def score_run(run_id: str, scenario_dir: str) -> dict
```

**Execution order:**

**1. Load all inputs.** Read `verify_result.json`,
`tool_call_trace.json`, `file_change_log.json`, `fault_manifest.json`,
`faulted.yaml`, and `known_good.yaml`. If any file is missing, write
`score.json` with `final_score: 0.0, zero_reason: "missing_artifacts"`
and return.

**2. Check deploy outcome.** If `verify_result["outcome"] !=
"completed"`, write score.json with `final_score: 0.0` and
`zero_reason: verify_result["outcome"]`. Return early — no agent
calls made.

**3. Quality gate.** Call `gate.check_quality_gate(verify_result)`.
If it returns False, write score.json with `final_score: 0.0,
quality_threshold_met: false, zero_reason: "quality_gate_failed"`.
Return early — no agent calls made.

**4. Score all five dimensions** (agent calls happen here):
```python
d1 = identification.score(tool_trace, manifest, verify_result)
d2 = fix_correctness.score(verify_result)           # deterministic
d3 = regression.compute(verify_result)              # deterministic
d4 = efficiency.score(tool_trace, file_log, manifest)  # formula + rationale
d5 = quality.score(verify_result, manifest, file_log)  # agent
```

**5. Apply composite formula:**
```python
weighted  = (d1["score"] * 0.20) + (d2["score"] * 0.25) \
          + (d4["score"] * 0.15) + (d5["score"] * 0.40)
composite = max(0.0, round(weighted - d3["penalty"], 4))
final_score = composite
```

**6. Interpret composite:**
```python
def interpret(score: float) -> str:
    if score >= 0.90: return "Root cause identified, clean fix, no regressions, efficient"
    if score >= 0.75: return "Correct fix with minor inefficiency or implementation concern"
    if score >= 0.50: return "Fix works but via workaround, or has regressions"
    if score >= 0.25: return "Partial resolution, significant issues"
    return "Failed quality gate or no improvement"
```

**7. Write `results/[run_id]/score.json`:**
```json
{
  "run_id": "<id>",
  "scenario_id": "<id>",
  "scored_by": "claude-sonnet-4-5",
  "quality_threshold_met": true,
  "zero_reason": null,
  "dimensions": {
    "identification":     {"score": 0.0, "rationale": "..."},
    "fix_correctness":    {"score": 0.0, "rationale": "..."},
    "regression_penalty": {"penalty": 0.0, "rationale": "..."},
    "efficiency": {
      "score": 0.0,
      "rationale": "...",
      "tool_calls":    {"actual": 0, "optimal": 0, "ratio": 0.0, "score": 0.0},
      "files_changed": {"actual": 0, "optimal": 0, "ratio": 0.0, "score": 0.0},
      "lines_changed": {"actual": 0, "optimal": 0, "ratio": 0.0, "score": 0.0}
    },
    "quality": {"score": 0.0, "classification": "...", "rationale": "..."}
  },
  "weighted": 0.0,
  "composite": 0.0,
  "final_score": 0.0,
  "interpretation": "..."
}
```

---

## F3 — `harness/scoring/dimensions/identification.py`

**Dimension 1 — Issue Identification (weight: 0.20)**
**Evaluated by:** Claude Sonnet

```python
def score(tool_trace: list, manifest: dict, verify_result: dict) -> dict
```

Constructs a prompt giving the agent the tool-call trace, the injected
fault definition, and the model's fix. The agent reasons about whether
the sequence of tool calls demonstrates that the model understood
what was broken before it submitted a fix.

**User prompt template:**
```
Fault injected:
  Resource:  {manifest["target_resource"]}
  Property:  {manifest["target_property"]}
  Injected:  {manifest["injected_value"]}
  Original:  {manifest["original_value"]}

Tool calls made by the model (in order):
{json.dumps(tool_trace, indent=2)}

Fix classification from verify loop:
  {verify_result["pass3_classification"]["classification"]}

Score the model's identification on this rubric:
  1.0 — Correct resource and property explicitly surfaced in the trace
        before the fix was submitted
  0.5 — Correct resource identified but wrong property, or correct
        property surfaced only after several unrelated probes
  0.3 — Fix targets the correct resource but the trace shows no clear
        identification — the model may have guessed
  0.1 — Wrong resource targeted; fix happened to work by coincidence
  0.0 — No identifiable diagnosis traceable to the fix submission

Return JSON only:
{"score": <float>, "rationale": "<1-2 sentences>"}
```

Parse the agent's JSON response. Return:
```python
{"score": float, "rationale": str}
```

---

## F4 — `harness/scoring/dimensions/fix_correctness.py`

**Dimension 2 — Fix Correctness (weight: 0.25)**
**Evaluated by:** deterministic formula — no agent call

```python
def score(verify_result: dict) -> dict
```

Derives score purely from Pass 1 and Pass 3 of the verify loop.
No Claude Sonnet call needed — the verify loop already produced
the signals.

```python
p1 = verify_result["pass1_functional"]
p3 = verify_result["pass3_classification"]

if p1["all_assertions_passed"]:
    s, rationale = 1.0, "All assertions passed."
elif p1["primary_assertions_passed"]:
    s, rationale = 0.6, "Primary symptom resolved; secondary assertions failed."
elif len(p1["failed_assertion_names"]) < len(p1["assertions"]):
    s, rationale = 0.3, "Symptom partially reduced; primary assertion still fails."
else:
    s, rationale = 0.0, "No improvement over faulted state."
```

Return: `{"score": float, "rationale": str}`

---

## F5 — `harness/scoring/dimensions/regression.py`

**Dimension 3 — Regression Penalty (subtracted from composite)**
**Evaluated by:** deterministic formula — no agent call

```python
def compute(verify_result: dict) -> dict
```

```python
p2 = verify_result["pass2_regression"]
critical    = p2["critical_regression_count"]
non_critical = p2["non_critical_regression_count"]

if critical > 1 or (critical >= 1 and non_critical >= 1):
    penalty, rationale = 0.28, "Multiple regressions or critical + non-critical combination."
elif critical == 1:
    penalty, rationale = 0.18, "One critical regression on primary traffic path."
elif non_critical == 1:
    penalty, rationale = 0.08, "One non-critical regression on secondary assertion."
else:
    penalty, rationale = 0.00, "No regressions introduced."
```

Return: `{"penalty": float, "rationale": str}`

---

## F6 — `harness/scoring/dimensions/efficiency.py`

**Dimension 4 — Diagnostic and Implementation Efficiency (weight: 0.15)**
**Formula:** deterministic
**Rationale:** Claude Sonnet writes the explanation of why the model
was or wasn't efficient — one call after all three sub-scores are computed

```python
def score(tool_trace: list, file_log: dict, manifest: dict) -> dict
```

**Sub-score formula (applies to all three signals):**
```python
def threshold_score(actual: int, optimal: int) -> float:
    if optimal == 0:
        return 1.0 if actual == 0 else 0.0
    ratio = actual / optimal
    if ratio <= 1.5:   return 1.0
    elif ratio <= 2.5: return 1.0 - 0.4 * (ratio - 1.5)
    elif ratio <= 4.0: return 0.6 - 0.4 * (ratio - 2.5)
    else:              return 0.0
```

**Actuals:**
```python
actual_tool_calls    = len(tool_trace)
actual_files_changed = file_log["total_files_changed"]
actual_lines_changed = file_log["total_lines_changed"]
```

**Optimals** (from manifest):
```python
optimal_tool_calls    = manifest["optimal_tool_calls"]
optimal_files_changed = manifest["optimal_files_changed"]
optimal_lines_changed = manifest["optimal_lines_changed"]
```

**Sub-scores:**
```python
tc_score = threshold_score(actual_tool_calls,    optimal_tool_calls)
fc_score = threshold_score(actual_files_changed, optimal_files_changed)
lc_score = threshold_score(actual_lines_changed, optimal_lines_changed)

combined = (tc_score * 0.50) + (fc_score * 0.25) + (lc_score * 0.25)
```

**Agent rationale call** — after computing sub-scores, call
`agent.call_scoring_agent` with this user prompt:

```
Tool calls:    actual={actual_tool_calls}, optimal={optimal_tool_calls},
               ratio={ratio:.2f}, score={tc_score:.2f}
Files changed: actual={actual_files_changed}, optimal={optimal_files_changed},
               ratio={ratio:.2f}, score={fc_score:.2f}
Lines changed: actual={actual_lines_changed}, optimal={optimal_lines_changed},
               ratio={ratio:.2f}, score={lc_score:.2f}

Tool call trace summary (tool names only, in order):
{[call["tool"] for call in tool_trace]}

In 1-2 sentences, explain what the efficiency scores reveal about
how the model approached this diagnosis and fix. Note any redundant
calls, unnecessary file edits, or conversely any efficient paths.

Return JSON only:
{"rationale": "<1-2 sentences>"}
```

Return:
```python
{
  "score": round(combined, 4),
  "rationale": str,
  "tool_calls":    {"actual": int, "optimal": int, "ratio": float, "score": float},
  "files_changed": {"actual": int, "optimal": int, "ratio": float, "score": float},
  "lines_changed": {"actual": int, "optimal": int, "ratio": float, "score": float}
}
```

---

## F7 — `harness/scoring/dimensions/quality.py`

**Dimension 5 — Fix Quality (weight: 0.40 — dominant)**
**Also contains:** quality gate check
**Evaluated by:** Claude Sonnet

This is the most important dimension. The agent reads the full diff
and reasons about whether the fix is production-viable, correctly
scoped, and genuinely addresses the root cause rather than working
around it.

```python
def check_gate(verify_result: dict) -> bool:
    """
    Quality gate — applied before any scoring.
    Returns False (zero score) if:
      - fix_classification is not root_cause or workaround
      - primary assertions did not pass
      - any regression was introduced
    """
    p1 = verify_result["pass1_functional"]
    p2 = verify_result["pass2_regression"]
    p3 = verify_result["pass3_classification"]

    classification_ok = p3["classification"] in ("root_cause", "workaround")
    assertions_ok     = p1["primary_assertions_passed"]
    no_regressions    = p2["regression_count"] == 0

    return classification_ok and assertions_ok and no_regressions


def score(verify_result: dict, manifest: dict, file_log: dict) -> dict:
```

**User prompt template:**

```
Fault that was injected:
  Resource:   {manifest["target_resource"]}
  Property:   {manifest["target_property"]}
  Injected:   {manifest["injected_value"]}
  Original:   {manifest["original_value"]}

Valid fixes (any of these would be a correct root-cause fix):
{json.dumps(manifest["valid_fixes"], indent=2)}

Invalid patches (these resolve the symptom but not the root cause):
{json.dumps(manifest["invalid_patches"], indent=2)}

Files the model changed:
{json.dumps(file_log["files_modified"] + file_log["files_added"], indent=2)}

Line-level changes per file:
{json.dumps(file_log["per_file_line_changes"], indent=2)}

Fix classification from verify loop:
  {verify_result["pass3_classification"]["classification"]}
  structural_match: {verify_result["pass3_classification"]["structural_match"]}
  invalid_patch_detected: {verify_result["pass3_classification"]["invalid_patch_detected"]}

Functional test result:
  primary_assertions_passed: {verify_result["pass1_functional"]["primary_assertions_passed"]}
  all_assertions_passed: {verify_result["pass1_functional"]["all_assertions_passed"]}

Score the quality of the fix on this rubric:
  1.00 — Root cause addressed, no regressions, clean and minimal implementation
  0.85 — Root cause addressed with a minor implementation concern
         (e.g. slightly broader than necessary but not wildcard)
  0.60 — Root cause addressed via an over-permissive fix
         (e.g. wildcard IAM Resource or overly broad managed policy)
  0.35 — Workaround — symptom resolved but root cause not addressed
  0.15 — Partial fix — symptom reduced but primary assertion still fails
  0.00 — No meaningful change from the faulted state

Return JSON only:
{
  "score": <float>,
  "classification": "<root_cause|workaround|partial|none>",
  "rationale": "<1-2 sentences explaining the score>"
}
```

Return: `{"score": float, "classification": str, "rationale": str}`

---

## F8 — `harness/scoring/gate.py`

Thin wrapper so the orchestrator imports the gate from one place.

```python
from harness.scoring.dimensions.quality import check_gate
```

---

## F9 — Update `harness/run.py`

After the verify loop call in Phase E, add:

```python
from harness.scoring.scorer import score_run

# ... existing verify loop call ...
verify_result = verify_loop.run_verify_loop(scenario_dir, run_id)

# Step 7 — autonomous scoring
print("[scorer] Running Step 7 scoring agent (Claude Sonnet)...")
score = score_run(run_id, scenario_dir)
```

Extend the terminal summary (E2) to include the score:

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

── Scoring (Claude Sonnet) ─────────────
Quality gate:     [PASS | FAIL → score zeroed]
Identification:   [0.00]  [rationale]
Fix correctness:  [0.00]  [rationale]
Regression:      −[0.00]  [rationale]
Efficiency:       [0.00]  [rationale]
Quality:          [0.00]  [rationale]
────────────────────────────────────────
Final score:      [0.0000]
Interpretation:   [interpretation string]
────────────────────────────────────────

Tool calls made:  [N]
Files changed:    [N]
Lines changed:    [N]

Full results:     results/[run_id]/
═══════════════════════════════════════
```

---

## F — Verification

Write `tests/test_scoring.py`:

**F1 — agent.py:** mock `anthropic.Anthropic().messages.create` to
return a known response. Assert `call_scoring_agent` returns the
text content and does not raise on a valid response. Assert it raises
clearly if the API call fails.

**F3 — identification.py:** mock `call_scoring_agent` to return
`{"score": 0.5, "rationale": "test"}`. Construct a hand-crafted
tool trace that includes `ace_get_iam_role` and one that does not.
Assert the prompt sent to the agent differs between the two cases
in a meaningful way. Assert score and rationale are correctly
extracted from the agent response.

**F4 — fix_correctness.py:** construct four hand-crafted
`verify_result` dicts covering all four score outcomes (1.0, 0.6,
0.3, 0.0). Assert each returns the correct score without any agent
call.

**F5 — regression.py:** construct verify_results with 0, 1
non-critical, 1 critical, and multiple regressions. Assert penalties
are 0.00, 0.08, 0.18, 0.28 respectively.

**F6 — efficiency.py:** test the `threshold_score` function directly
with ratios at 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0 — assert scores
match the curve. Test combined score with known actuals and optimals.
Mock the agent rationale call and assert the rationale appears in
the return dict.

**F7 — quality.py gate:** construct verify_results for each gate
failure mode (bad classification, primary assertions failed,
regression present) and assert `check_gate` returns False for each.
Assert it returns True for a clean root_cause with no regressions.

**F7 — quality.py score:** mock `call_scoring_agent` to return each
of the six classification outcomes. Assert score is correctly
extracted and the classification field is populated.

**F2 — scorer.py integration:** mock all dimension modules and
`call_scoring_agent`. Run `score_run` with a hand-crafted input set.
Assert `score.json` is written with correct structure and that the
composite formula is applied correctly. Assert early-exit paths
(did_not_deploy, quality gate failure) write zero-score JSON and
make no agent calls.

---

## Dependency on existing phases

```
Phase A (shared utilities)
    │
    └──► Phase F (scoring)
              │
         depends on: verify_result.json, tool_call_trace.json,
                     file_change_log.json (all from Phase D/C)
              │
         called by: harness/run.py (Phase E update F9)
```

Phase F has no dependency on Phase B or Phase C directly — it reads
their output from disk. It can be built and unit-tested entirely
with mocked input files before being wired into run.py.