# Phase F — Scoring Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the Phase F scoring agent with Claude Sonnet, where fix_correctness and regression gain agent-generated rationale (score stays deterministic), all five dimension prompts are tight with zero open assumptions, and every prompt receives known_good.yaml and traffic_flow.md as explicit context.

**Architecture:** scorer.py loads known_good.yaml and traffic_flow.md from corpus/[arch_id]/, passes both to all five dimension functions. F4 and F5 compute scores deterministically then call the agent for rationale only — mirroring the efficiency.py pattern. F3, F6, F7 rebuild their prompts with the new context and tighter rubrics.

**Tech Stack:** Python 3.11, anthropic SDK, pytest

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `harness/scoring/__init__.py` | package marker |
| Create | `harness/scoring/agent.py` | F1 — Claude Sonnet client + system prompt |
| Create | `harness/scoring/scorer.py` | F2 — orchestrator: load inputs, call dimensions, write score.json |
| Create | `harness/scoring/dimensions/__init__.py` | package marker |
| Create | `harness/scoring/dimensions/identification.py` | F3 — agent prompt + parse |
| Create | `harness/scoring/dimensions/fix_correctness.py` | F4 — deterministic score |
| Create | `harness/scoring/dimensions/regression.py` | F5 — deterministic penalty |
| Create | `harness/scoring/dimensions/efficiency.py` | F6 — formula + agent rationale |
| Create | `harness/scoring/dimensions/quality.py` | F7 — quality gate + agent score |
| Create | `harness/scoring/gate.py` | F8 — thin re-export of check_gate |
| Modify | `harness/run.py` | F9 — call score_run after verify loop |
| Create | `tests/test_scoring.py` | unit tests for all scoring modules |

---
### Task 1: Package scaffolding

**Files:**
- Create: `harness/scoring/__init__.py`
- Create: `harness/scoring/dimensions/__init__.py`

- [ ] **Step 1: Create both package markers**

```bash
touch /path/to/project/harness/scoring/__init__.py
touch /path/to/project/harness/scoring/dimensions/__init__.py
```

Run from repo root:
```bash
mkdir -p harness/scoring/dimensions
touch harness/scoring/__init__.py harness/scoring/dimensions/__init__.py
```

- [ ] **Step 2: Verify imports work**

```bash
python -c "import harness.scoring; import harness.scoring.dimensions; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add harness/scoring/__init__.py harness/scoring/dimensions/__init__.py
git commit -m "feat(scoring): add harness/scoring package scaffolding"
```

---

### Task 2: F1 — `harness/scoring/agent.py`

**Files:**
- Create: `harness/scoring/agent.py`

The system prompt is updated to mention that known_good.yaml and traffic_flow.md are always provided. This is the single shared client — all five dimensions call through here.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scoring.py — add this first test block

import pytest
from unittest.mock import patch, MagicMock

def make_mock_response(text: str):
    msg = MagicMock()
    msg.content = [MagicMock(text=text)]
    return msg

def test_call_scoring_agent_returns_text():
    with patch("harness.scoring.agent.client") as mock_client:
        mock_client.messages.create.return_value = make_mock_response('{"score": 1.0}')
        from harness.scoring.agent import call_scoring_agent
        result = call_scoring_agent("sys", "user")
        assert result == '{"score": 1.0}'

def test_call_scoring_agent_propagates_api_error():
    with patch("harness.scoring.agent.client") as mock_client:
        mock_client.messages.create.side_effect = Exception("API error")
        from harness.scoring.agent import call_scoring_agent
        with pytest.raises(Exception, match="API error"):
            call_scoring_agent("sys", "user")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_scoring.py::test_call_scoring_agent_returns_text -v
```

Expected: `ModuleNotFoundError: No module named 'harness.scoring.agent'`

- [ ] **Step 3: Write `harness/scoring/agent.py`**

```python
import anthropic

client = anthropic.Anthropic()
SCORING_MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """You are an infrastructure debugging benchmark scorer.
You evaluate AI model runs against a known-good AWS architecture.

Each prompt provides exactly:
- known_good.yaml: the correct architecture template
- traffic_flow.md: how requests flow through the architecture
- fault_manifest fields: what was injected and what the correct value is
- tool_call_trace and/or verify_result: what the model did

Return ONLY valid JSON matching the schema in each prompt.
No markdown fences. No explanation outside the JSON.
Scores are floats. Use only the values listed in each rubric — no interpolation.
Rationale is exactly 1-2 sentences. Do not reference information not given in the prompt."""


def call_scoring_agent(system_prompt: str, user_prompt: str) -> str:
    message = client.messages.create(
        model=SCORING_MODEL,
        max_tokens=1024,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return message.content[0].text.strip()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_scoring.py::test_call_scoring_agent_returns_text tests/test_scoring.py::test_call_scoring_agent_propagates_api_error -v
```

Expected: both PASS

- [ ] **Step 5: Commit**

```bash
git add harness/scoring/agent.py tests/test_scoring.py
git commit -m "feat(scoring): F1 agent.py — Claude Sonnet client"
```

---

### Task 3: F3 — `harness/scoring/dimensions/identification.py`

**Files:**
- Create: `harness/scoring/dimensions/identification.py`

Prompt is tightened: rubric uses exact float values only, agent is told explicitly to use only the tool-call sequence (not to infer from fix outcome), known_good.yaml and traffic_flow.md are labeled sections.

- [ ] **Step 1: Write the failing tests**

```python
# Add to tests/test_scoring.py

import json

SAMPLE_MANIFEST = {
    "target_resource": "ProcessorLambdaESM",
    "target_property": "Enabled",
    "original_value": True,
    "injected_value": False,
    "optimal_tool_calls": 5,
    "optimal_files_changed": 1,
    "optimal_lines_changed": 3,
    "valid_fixes": [{"Enabled": True}],
    "invalid_patches": [],
    "fault_class": "config",
}

SAMPLE_VERIFY = {
    "outcome": "completed",
    "pass1_functional": {
        "all_assertions_passed": True,
        "primary_assertions_passed": True,
        "failed_assertion_names": [],
        "assertions": ["a", "b"],
    },
    "pass2_regression": {
        "critical_regression_count": 0,
        "non_critical_regression_count": 0,
        "regression_count": 0,
    },
    "pass3_classification": {
        "classification": "root_cause",
        "structural_match": True,
        "invalid_patch_detected": False,
    },
    "pass4_concurrency": None,
}

TRACE_WITH_TARGET = [
    {"turn": 1, "tool": "ace_get_event_source", "input": {"function_name": "ace-bench-processor"}, "output": "{}"},
    {"turn": 2, "tool": "ace_update_template", "input": {}, "output": "{}"},
]

TRACE_WITHOUT_TARGET = [
    {"turn": 1, "tool": "ace_invoke_endpoint", "input": {}, "output": "{}"},
    {"turn": 2, "tool": "ace_update_template", "input": {}, "output": "{}"},
]

KNOWN_GOOD_YAML = "Resources:\n  ProcessorLambdaESM:\n    Type: AWS::Lambda::EventSourceMapping"
TRAFFIC_FLOW_MD = "Hop 4: SQS triggers Processor Lambda via EventSourceMapping."


def test_identification_prompt_differs_by_trace():
    with patch("harness.scoring.dimensions.identification.call_scoring_agent") as mock_agent:
        mock_agent.return_value = '{"score": 0.5, "rationale": "test"}'
        from harness.scoring.dimensions.identification import score

        score(TRACE_WITH_TARGET, SAMPLE_MANIFEST, SAMPLE_VERIFY, KNOWN_GOOD_YAML, TRAFFIC_FLOW_MD)
        prompt_with = mock_agent.call_args[0][1]

        mock_agent.return_value = '{"score": 0.0, "rationale": "test"}'
        score(TRACE_WITHOUT_TARGET, SAMPLE_MANIFEST, SAMPLE_VERIFY, KNOWN_GOOD_YAML, TRAFFIC_FLOW_MD)
        prompt_without = mock_agent.call_args[0][1]

    assert prompt_with != prompt_without


def test_identification_parses_agent_response():
    with patch("harness.scoring.dimensions.identification.call_scoring_agent") as mock_agent:
        mock_agent.return_value = '{"score": 0.5, "rationale": "correct resource found"}'
        from harness.scoring.dimensions.identification import score
        result = score(TRACE_WITH_TARGET, SAMPLE_MANIFEST, SAMPLE_VERIFY, KNOWN_GOOD_YAML, TRAFFIC_FLOW_MD)

    assert result["score"] == 0.5
    assert result["rationale"] == "correct resource found"


def test_identification_known_good_in_prompt():
    with patch("harness.scoring.dimensions.identification.call_scoring_agent") as mock_agent:
        mock_agent.return_value = '{"score": 1.0, "rationale": "ok"}'
        from harness.scoring.dimensions.identification import score
        score(TRACE_WITH_TARGET, SAMPLE_MANIFEST, SAMPLE_VERIFY, KNOWN_GOOD_YAML, TRAFFIC_FLOW_MD)
        prompt = mock_agent.call_args[0][1]

    assert "known_good.yaml" in prompt.lower() or "ProcessorLambdaESM" in prompt
    assert "traffic_flow" in prompt.lower() or "Hop 4" in prompt
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_scoring.py::test_identification_prompt_differs_by_trace tests/test_scoring.py::test_identification_parses_agent_response tests/test_scoring.py::test_identification_known_good_in_prompt -v
```

Expected: all FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `harness/scoring/dimensions/identification.py`**

```python
import json
from harness.scoring.agent import call_scoring_agent, SYSTEM_PROMPT


def score(
    tool_trace: list,
    manifest: dict,
    verify_result: dict,
    known_good_yaml: str,
    traffic_flow_md: str,
) -> dict:
    tool_sequence = [
        {"turn": c["turn"], "tool": c["tool"], "input_keys": sorted(c["input"].keys())}
        for c in tool_trace
    ]

    user_prompt = f"""=== known_good.yaml ===
{known_good_yaml}

=== traffic_flow.md ===
{traffic_flow_md}

=== Fault injected ===
Resource:  {manifest["target_resource"]}
Property:  {manifest["target_property"]}
Original:  {manifest["original_value"]}
Injected:  {manifest["injected_value"]}

=== Model tool-call sequence (in order) ===
{json.dumps(tool_sequence, indent=2)}

=== Fix classification (deterministic, from verify loop) ===
{verify_result["pass3_classification"]["classification"]}

=== Task ===
Score whether the tool-call sequence demonstrates the model identified the fault
before submitting a fix. Base your score ONLY on the tool-call sequence above.
Do NOT infer identification from the fix result or classification.

A "fix attempt" is any call to ace_update_template, ace_apply_patch, or equivalent write tool.

Rubric (use these exact values only — no interpolation):
  1.0 — Target resource AND target property explicitly surfaced in a read/inspect
        tool call BEFORE the first fix attempt
  0.5 — Target resource surfaced before the first fix attempt, but target property
        was not, OR target property surfaced only after a fix attempt
  0.3 — No explicit identification; model wrote to the correct resource without
        a prior read/inspect call targeting it
  0.1 — Model targeted the wrong resource; the fix worked coincidentally
  0.0 — No tool calls targeted the fault; fix submission was blind

Return JSON only, no markdown fences:
{{"score": <one of: 1.0, 0.5, 0.3, 0.1, 0.0>, "rationale": "<exactly 1-2 sentences>"}}"""

    raw = call_scoring_agent(SYSTEM_PROMPT, user_prompt)
    parsed = json.loads(raw)
    return {"score": float(parsed["score"]), "rationale": str(parsed["rationale"])}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_scoring.py::test_identification_prompt_differs_by_trace tests/test_scoring.py::test_identification_parses_agent_response tests/test_scoring.py::test_identification_known_good_in_prompt -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add harness/scoring/dimensions/identification.py tests/test_scoring.py
git commit -m "feat(scoring): F3 identification.py — tight prompt, known_good + traffic_flow context"
```

---

### Task 4: F4 — `harness/scoring/dimensions/fix_correctness.py`

**Files:**
- Create: `harness/scoring/dimensions/fix_correctness.py`

Score is deterministic (unchanged). Agent call added for rationale — agent receives fault context, known_good.yaml, traffic_flow.md, the deterministic score, and the raw assertion results. Function signature gains `manifest`, `known_good_yaml`, `traffic_flow_md`.

- [ ] **Step 1: Write the failing tests**

```python
# Add to tests/test_scoring.py

def _make_verify(all_passed, primary_passed, failed_names, total_count, crit_reg=0, noncrit_reg=0):
    return {
        "outcome": "completed",
        "pass1_functional": {
            "all_assertions_passed": all_passed,
            "primary_assertions_passed": primary_passed,
            "failed_assertion_names": failed_names,
            "assertions": [f"a{i}" for i in range(total_count)],
        },
        "pass2_regression": {
            "critical_regression_count": crit_reg,
            "non_critical_regression_count": noncrit_reg,
            "regression_count": crit_reg + noncrit_reg,
        },
        "pass3_classification": {
            "classification": "root_cause",
            "structural_match": True,
            "invalid_patch_detected": False,
        },
        "pass4_concurrency": None,
    }


def test_fix_correctness_scores_deterministic():
    with patch("harness.scoring.dimensions.fix_correctness.call_scoring_agent") as mock_agent:
        mock_agent.return_value = '{"rationale": "ok"}'
        from harness.scoring.dimensions.fix_correctness import score

        # 1.0 — all passed
        r = score(_make_verify(True, True, [], 2), SAMPLE_MANIFEST, KNOWN_GOOD_YAML, TRAFFIC_FLOW_MD)
        assert r["score"] == 1.0

        # 0.6 — primary passed, secondaries failed
        r = score(_make_verify(False, True, ["b"], 2), SAMPLE_MANIFEST, KNOWN_GOOD_YAML, TRAFFIC_FLOW_MD)
        assert r["score"] == 0.6

        # 0.3 — partial (some passed, primary failed)
        r = score(_make_verify(False, False, ["a"], 2), SAMPLE_MANIFEST, KNOWN_GOOD_YAML, TRAFFIC_FLOW_MD)
        assert r["score"] == 0.3

        # 0.0 — nothing passed
        r = score(_make_verify(False, False, ["a", "b"], 2), SAMPLE_MANIFEST, KNOWN_GOOD_YAML, TRAFFIC_FLOW_MD)
        assert r["score"] == 0.0


def test_fix_correctness_calls_agent_for_rationale():
    with patch("harness.scoring.dimensions.fix_correctness.call_scoring_agent") as mock_agent:
        mock_agent.return_value = '{"rationale": "all assertions passed cleanly"}'
        from harness.scoring.dimensions.fix_correctness import score
        r = score(_make_verify(True, True, [], 2), SAMPLE_MANIFEST, KNOWN_GOOD_YAML, TRAFFIC_FLOW_MD)

    mock_agent.assert_called_once()
    assert r["rationale"] == "all assertions passed cleanly"


def test_fix_correctness_prompt_includes_context():
    with patch("harness.scoring.dimensions.fix_correctness.call_scoring_agent") as mock_agent:
        mock_agent.return_value = '{"rationale": "ok"}'
        from harness.scoring.dimensions.fix_correctness import score
        score(_make_verify(True, True, [], 2), SAMPLE_MANIFEST, KNOWN_GOOD_YAML, TRAFFIC_FLOW_MD)
        prompt = mock_agent.call_args[0][1]

    assert "known_good.yaml" in prompt.lower() or "ProcessorLambdaESM" in prompt
    assert "traffic_flow" in prompt.lower() or "Hop 4" in prompt
    assert manifest_resource_in_prompt(prompt, SAMPLE_MANIFEST)


def manifest_resource_in_prompt(prompt, manifest):
    return manifest["target_resource"] in prompt
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_scoring.py::test_fix_correctness_scores_deterministic tests/test_scoring.py::test_fix_correctness_calls_agent_for_rationale tests/test_scoring.py::test_fix_correctness_prompt_includes_context -v
```

Expected: all FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `harness/scoring/dimensions/fix_correctness.py`**

```python
import json
from harness.scoring.agent import call_scoring_agent, SYSTEM_PROMPT


def score(
    verify_result: dict,
    manifest: dict,
    known_good_yaml: str,
    traffic_flow_md: str,
) -> dict:
    p1 = verify_result["pass1_functional"]

    if p1["all_assertions_passed"]:
        s = 1.0
    elif p1["primary_assertions_passed"]:
        s = 0.6
    elif len(p1["failed_assertion_names"]) < len(p1["assertions"]):
        s = 0.3
    else:
        s = 0.0

    user_prompt = f"""=== known_good.yaml ===
{known_good_yaml}

=== traffic_flow.md ===
{traffic_flow_md}

=== Fault injected ===
Resource:  {manifest["target_resource"]}
Property:  {manifest["target_property"]}
Original:  {manifest["original_value"]}
Injected:  {manifest["injected_value"]}

=== Functional test result ===
all_assertions_passed:    {p1["all_assertions_passed"]}
primary_assertions_passed: {p1["primary_assertions_passed"]}
failed_assertion_names:   {json.dumps(p1["failed_assertion_names"])}
total_assertions:         {len(p1["assertions"])}

=== Deterministic correctness score ===
{s}

=== Task ===
In exactly 1-2 sentences, explain what this score reveals about the fix.
State which assertions passed or failed and what that indicates about whether
the fault was resolved. Reference the traffic flow only if a specific hop is
implicated by the failed assertions.

Return JSON only, no markdown fences:
{{"rationale": "<exactly 1-2 sentences>"}}"""

    raw = call_scoring_agent(SYSTEM_PROMPT, user_prompt)
    parsed = json.loads(raw)
    return {"score": s, "rationale": str(parsed["rationale"])}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_scoring.py::test_fix_correctness_scores_deterministic tests/test_scoring.py::test_fix_correctness_calls_agent_for_rationale tests/test_scoring.py::test_fix_correctness_prompt_includes_context -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add harness/scoring/dimensions/fix_correctness.py tests/test_scoring.py
git commit -m "feat(scoring): F4 fix_correctness.py — agent rationale, known_good + traffic_flow context"
```

---

### Task 5: F5 — `harness/scoring/dimensions/regression.py`

**Files:**
- Create: `harness/scoring/dimensions/regression.py`

Penalty is deterministic (unchanged). Agent call added for rationale — agent receives regression counts, any regression details, known_good.yaml, and traffic_flow.md so it can name the affected hop. Function signature gains `manifest`, `known_good_yaml`, `traffic_flow_md`.

- [ ] **Step 1: Write the failing tests**

```python
# Add to tests/test_scoring.py

def test_regression_penalties_deterministic():
    with patch("harness.scoring.dimensions.regression.call_scoring_agent") as mock_agent:
        mock_agent.return_value = '{"rationale": "ok"}'
        from harness.scoring.dimensions.regression import compute

        cases = [
            (_make_verify(True, True, [], 2, 0, 0), 0.00),
            (_make_verify(True, True, [], 2, 0, 1), 0.08),
            (_make_verify(True, True, [], 2, 1, 0), 0.18),
            (_make_verify(True, True, [], 2, 1, 1), 0.28),
            (_make_verify(True, True, [], 2, 2, 0), 0.28),
        ]
        for verify, expected_penalty in cases:
            r = compute(verify, SAMPLE_MANIFEST, KNOWN_GOOD_YAML, TRAFFIC_FLOW_MD)
            assert r["penalty"] == expected_penalty, f"expected {expected_penalty}, got {r['penalty']}"


def test_regression_calls_agent_for_rationale():
    with patch("harness.scoring.dimensions.regression.call_scoring_agent") as mock_agent:
        mock_agent.return_value = '{"rationale": "one critical regression on hop 4"}'
        from harness.scoring.dimensions.regression import compute
        r = compute(_make_verify(True, True, [], 2, 1, 0), SAMPLE_MANIFEST, KNOWN_GOOD_YAML, TRAFFIC_FLOW_MD)

    mock_agent.assert_called_once()
    assert r["rationale"] == "one critical regression on hop 4"


def test_regression_no_agent_call_when_no_regressions():
    """When penalty is 0.0, we still call agent but it returns a no-regression rationale."""
    with patch("harness.scoring.dimensions.regression.call_scoring_agent") as mock_agent:
        mock_agent.return_value = '{"rationale": "no regressions"}'
        from harness.scoring.dimensions.regression import compute
        r = compute(_make_verify(True, True, [], 2, 0, 0), SAMPLE_MANIFEST, KNOWN_GOOD_YAML, TRAFFIC_FLOW_MD)

    mock_agent.assert_called_once()
    assert r["penalty"] == 0.0
    assert r["rationale"] == "no regressions"


def test_regression_prompt_includes_context():
    with patch("harness.scoring.dimensions.regression.call_scoring_agent") as mock_agent:
        mock_agent.return_value = '{"rationale": "ok"}'
        from harness.scoring.dimensions.regression import compute
        compute(_make_verify(True, True, [], 2, 1, 0), SAMPLE_MANIFEST, KNOWN_GOOD_YAML, TRAFFIC_FLOW_MD)
        prompt = mock_agent.call_args[0][1]

    assert "known_good.yaml" in prompt.lower() or "ProcessorLambdaESM" in prompt
    assert "traffic_flow" in prompt.lower() or "Hop 4" in prompt
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_scoring.py::test_regression_penalties_deterministic tests/test_scoring.py::test_regression_calls_agent_for_rationale tests/test_scoring.py::test_regression_no_agent_call_when_no_regressions tests/test_scoring.py::test_regression_prompt_includes_context -v
```

Expected: all FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `harness/scoring/dimensions/regression.py`**

```python
import json
from harness.scoring.agent import call_scoring_agent, SYSTEM_PROMPT


def compute(
    verify_result: dict,
    manifest: dict,
    known_good_yaml: str,
    traffic_flow_md: str,
) -> dict:
    p2 = verify_result["pass2_regression"]
    critical = p2["critical_regression_count"]
    non_critical = p2["non_critical_regression_count"]
    details = p2.get("regressions", [])

    if critical > 1 or (critical >= 1 and non_critical >= 1):
        penalty = 0.28
    elif critical == 1:
        penalty = 0.18
    elif non_critical == 1:
        penalty = 0.08
    else:
        penalty = 0.00

    user_prompt = f"""=== known_good.yaml ===
{known_good_yaml}

=== traffic_flow.md ===
{traffic_flow_md}

=== Fault that was fixed ===
Resource:  {manifest["target_resource"]}
Property:  {manifest["target_property"]}

=== Regression test result ===
critical_regression_count:     {critical}
non_critical_regression_count: {non_critical}
regression_details:            {json.dumps(details)}

=== Deterministic regression penalty ===
{penalty}

=== Task ===
In exactly 1-2 sentences, explain what these regression results mean for
the architecture. If regressions are present, name the affected traffic-flow
hop using the hop labels in traffic_flow.md. If no regressions, state that
the fix did not affect other paths.

Return JSON only, no markdown fences:
{{"rationale": "<exactly 1-2 sentences>"}}"""

    raw = call_scoring_agent(SYSTEM_PROMPT, user_prompt)
    parsed = json.loads(raw)
    return {"penalty": penalty, "rationale": str(parsed["rationale"])}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_scoring.py::test_regression_penalties_deterministic tests/test_scoring.py::test_regression_calls_agent_for_rationale tests/test_scoring.py::test_regression_no_agent_call_when_no_regressions tests/test_scoring.py::test_regression_prompt_includes_context -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add harness/scoring/dimensions/regression.py tests/test_scoring.py
git commit -m "feat(scoring): F5 regression.py — agent rationale, known_good + traffic_flow context"
```

---

### Task 6: F6 — `harness/scoring/dimensions/efficiency.py`

**Files:**
- Create: `harness/scoring/dimensions/efficiency.py`

Scores are deterministic (unchanged). Prompt tightened: gives exact computed numbers, asks only for a 1-2 sentence explanation, adds known_good.yaml and traffic_flow.md as labeled sections. Agent told explicitly not to re-derive the scores.

- [ ] **Step 1: Write the failing tests**

```python
# Add to tests/test_scoring.py

SAMPLE_FILE_LOG = {
    "total_files_changed": 1,
    "total_lines_changed": 3,
    "files_modified": ["harness/scenarios/arch_01/known_good.yaml"],
    "files_added": [],
    "per_file_line_changes": {"harness/scenarios/arch_01/known_good.yaml": 3},
}

SAMPLE_TRACE = [
    {"turn": i, "tool": f"tool_{i}", "input": {}, "output": "{}"} for i in range(6)
]


def test_threshold_score_curve():
    from harness.scoring.dimensions.efficiency import threshold_score
    assert threshold_score(5, 5) == 1.0    # ratio 1.0 → 1.0
    assert threshold_score(7, 5) == 1.0    # ratio 1.4 → 1.0 (≤1.5)
    assert threshold_score(10, 5) == 0.6   # ratio 2.0 → 1.0 - 0.4*(2.0-1.5) = 0.8? 
    # ratio=2.0: 1.0 - 0.4*(2.0-1.5) = 1.0 - 0.2 = 0.8
    assert threshold_score(10, 5) == pytest.approx(0.8, abs=0.001)
    assert threshold_score(12, 5) == pytest.approx(0.6, abs=0.001)  # ratio=2.4 → 1.0-0.4*0.9=0.64
    assert threshold_score(15, 5) == pytest.approx(0.4, abs=0.001)  # ratio=3.0 → 0.6-0.4*0.5=0.4
    assert threshold_score(20, 5) == 0.0   # ratio=4.0 → 0.0 boundary
    assert threshold_score(25, 5) == 0.0   # ratio=5.0 → 0.0


def test_efficiency_combined_score():
    with patch("harness.scoring.dimensions.efficiency.call_scoring_agent") as mock_agent:
        mock_agent.return_value = '{"rationale": "efficient"}'
        from harness.scoring.dimensions.efficiency import score

        # actual == optimal across the board → all sub-scores 1.0 → combined 1.0
        manifest = dict(SAMPLE_MANIFEST)
        manifest["optimal_tool_calls"] = len(SAMPLE_TRACE)
        manifest["optimal_files_changed"] = SAMPLE_FILE_LOG["total_files_changed"]
        manifest["optimal_lines_changed"] = SAMPLE_FILE_LOG["total_lines_changed"]

        r = score(SAMPLE_TRACE, SAMPLE_FILE_LOG, manifest, KNOWN_GOOD_YAML, TRAFFIC_FLOW_MD)
        assert r["score"] == 1.0
        assert "tool_calls" in r
        assert "files_changed" in r
        assert "lines_changed" in r


def test_efficiency_agent_rationale_called_once():
    with patch("harness.scoring.dimensions.efficiency.call_scoring_agent") as mock_agent:
        mock_agent.return_value = '{"rationale": "some explanation"}'
        from harness.scoring.dimensions.efficiency import score
        r = score(SAMPLE_TRACE, SAMPLE_FILE_LOG, SAMPLE_MANIFEST, KNOWN_GOOD_YAML, TRAFFIC_FLOW_MD)

    mock_agent.assert_called_once()
    assert r["rationale"] == "some explanation"


def test_efficiency_prompt_includes_context():
    with patch("harness.scoring.dimensions.efficiency.call_scoring_agent") as mock_agent:
        mock_agent.return_value = '{"rationale": "ok"}'
        from harness.scoring.dimensions.efficiency import score
        score(SAMPLE_TRACE, SAMPLE_FILE_LOG, SAMPLE_MANIFEST, KNOWN_GOOD_YAML, TRAFFIC_FLOW_MD)
        prompt = mock_agent.call_args[0][1]

    assert "known_good.yaml" in prompt.lower() or "ProcessorLambdaESM" in prompt
    assert "traffic_flow" in prompt.lower() or "Hop 4" in prompt
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_scoring.py::test_threshold_score_curve tests/test_scoring.py::test_efficiency_combined_score tests/test_scoring.py::test_efficiency_agent_rationale_called_once tests/test_scoring.py::test_efficiency_prompt_includes_context -v
```

Expected: all FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `harness/scoring/dimensions/efficiency.py`**

```python
import json
from harness.scoring.agent import call_scoring_agent, SYSTEM_PROMPT


def threshold_score(actual: int, optimal: int) -> float:
    if optimal == 0:
        return 1.0 if actual == 0 else 0.0
    ratio = actual / optimal
    if ratio <= 1.5:
        return 1.0
    elif ratio <= 2.5:
        return 1.0 - 0.4 * (ratio - 1.5)
    elif ratio <= 4.0:
        return 0.6 - 0.4 * (ratio - 2.5)
    else:
        return 0.0


def score(
    tool_trace: list,
    file_log: dict,
    manifest: dict,
    known_good_yaml: str,
    traffic_flow_md: str,
) -> dict:
    actual_tc = len(tool_trace)
    actual_fc = file_log["total_files_changed"]
    actual_lc = file_log["total_lines_changed"]

    opt_tc = manifest["optimal_tool_calls"]
    opt_fc = manifest["optimal_files_changed"]
    opt_lc = manifest["optimal_lines_changed"]

    tc_score = threshold_score(actual_tc, opt_tc)
    fc_score = threshold_score(actual_fc, opt_fc)
    lc_score = threshold_score(actual_lc, opt_lc)

    combined = round((tc_score * 0.50) + (fc_score * 0.25) + (lc_score * 0.25), 4)

    tc_ratio = round(actual_tc / opt_tc, 2) if opt_tc else None
    fc_ratio = round(actual_fc / opt_fc, 2) if opt_fc else None
    lc_ratio = round(actual_lc / opt_lc, 2) if opt_lc else None

    tool_sequence = [c["tool"] for c in tool_trace]

    user_prompt = f"""=== known_good.yaml ===
{known_good_yaml}

=== traffic_flow.md ===
{traffic_flow_md}

=== Efficiency sub-scores (all computed deterministically — do not re-derive) ===
Tool calls:    actual={actual_tc}, optimal={opt_tc}, ratio={tc_ratio}, score={round(tc_score, 2)}
Files changed: actual={actual_fc}, optimal={opt_fc}, ratio={fc_ratio}, score={round(fc_score, 2)}
Lines changed: actual={actual_lc}, optimal={opt_lc}, ratio={lc_ratio}, score={round(lc_score, 2)}
Combined:      {combined}

=== Tool call sequence (tool names only, in order) ===
{json.dumps(tool_sequence)}

=== Task ===
In exactly 1-2 sentences, explain what the efficiency sub-scores reveal about
how the model approached diagnosis and fix. Note redundant calls or unnecessary
edits if present. Reference the traffic flow only if a specific hop explains
why calls were redundant or necessary.

Return JSON only, no markdown fences:
{{"rationale": "<exactly 1-2 sentences>"}}"""

    raw = call_scoring_agent(SYSTEM_PROMPT, user_prompt)
    parsed = json.loads(raw)

    return {
        "score": combined,
        "rationale": str(parsed["rationale"]),
        "tool_calls":    {"actual": actual_tc, "optimal": opt_tc, "ratio": tc_ratio, "score": round(tc_score, 4)},
        "files_changed": {"actual": actual_fc, "optimal": opt_fc, "ratio": fc_ratio, "score": round(fc_score, 4)},
        "lines_changed": {"actual": actual_lc, "optimal": opt_lc, "ratio": lc_ratio, "score": round(lc_score, 4)},
    }
```

- [ ] **Step 4: Fix the test curve expectations and run**

Note: re-check `threshold_score` at ratio=2.0: `1.0 - 0.4*(2.0-1.5) = 0.8`, and ratio=2.4: `1.0 - 0.4*(2.4-1.5) = 0.64`, ratio=3.0: `0.6 - 0.4*(3.0-2.5) = 0.4`, ratio=4.0: `0.6 - 0.4*(4.0-2.5) = 0.0`. Update test to match:

```python
# Corrected test_threshold_score_curve assertions:
assert threshold_score(5, 5) == 1.0                        # ratio 1.0
assert threshold_score(7, 5) == 1.0                        # ratio 1.4 ≤ 1.5
assert threshold_score(10, 5) == pytest.approx(0.8, abs=0.001)   # ratio 2.0
assert threshold_score(12, 5) == pytest.approx(0.64, abs=0.001)  # ratio 2.4
assert threshold_score(15, 5) == pytest.approx(0.4, abs=0.001)   # ratio 3.0
assert threshold_score(20, 5) == pytest.approx(0.0, abs=0.001)   # ratio 4.0
assert threshold_score(25, 5) == 0.0                       # ratio 5.0
```

```bash
pytest tests/test_scoring.py::test_threshold_score_curve tests/test_scoring.py::test_efficiency_combined_score tests/test_scoring.py::test_efficiency_agent_rationale_called_once tests/test_scoring.py::test_efficiency_prompt_includes_context -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add harness/scoring/dimensions/efficiency.py tests/test_scoring.py
git commit -m "feat(scoring): F6 efficiency.py — tight prompt, known_good + traffic_flow context"
```

---

### Task 7: F7 — `harness/scoring/dimensions/quality.py`

**Files:**
- Create: `harness/scoring/dimensions/quality.py`

Prompt tightened: rubric uses exact float values, agent given valid_fixes and invalid_patches inline, known_good.yaml and traffic_flow.md as labeled sections. `check_gate` stays deterministic with no agent call.

- [ ] **Step 1: Write the failing tests**

```python
# Add to tests/test_scoring.py

def _make_verify_for_gate(classification, primary_passed, regression_count):
    return {
        "outcome": "completed",
        "pass1_functional": {
            "all_assertions_passed": primary_passed and regression_count == 0,
            "primary_assertions_passed": primary_passed,
            "failed_assertion_names": [],
            "assertions": ["a"],
        },
        "pass2_regression": {
            "critical_regression_count": regression_count,
            "non_critical_regression_count": 0,
            "regression_count": regression_count,
        },
        "pass3_classification": {
            "classification": classification,
            "structural_match": classification == "root_cause",
            "invalid_patch_detected": classification not in ("root_cause", "workaround"),
        },
        "pass4_concurrency": None,
    }


def test_check_gate_passes_for_clean_root_cause():
    from harness.scoring.dimensions.quality import check_gate
    v = _make_verify_for_gate("root_cause", True, 0)
    assert check_gate(v) is True


def test_check_gate_fails_bad_classification():
    from harness.scoring.dimensions.quality import check_gate
    for cls in ("partial", "none", "unknown"):
        v = _make_verify_for_gate(cls, True, 0)
        assert check_gate(v) is False, f"expected False for classification={cls}"


def test_check_gate_fails_primary_not_passed():
    from harness.scoring.dimensions.quality import check_gate
    v = _make_verify_for_gate("root_cause", False, 0)
    assert check_gate(v) is False


def test_check_gate_fails_with_regression():
    from harness.scoring.dimensions.quality import check_gate
    v = _make_verify_for_gate("root_cause", True, 1)
    assert check_gate(v) is False


def test_quality_score_parses_agent_response():
    with patch("harness.scoring.dimensions.quality.call_scoring_agent") as mock_agent:
        mock_agent.return_value = '{"score": 0.85, "classification": "root_cause", "rationale": "clean fix"}'
        from harness.scoring.dimensions.quality import score
        r = score(SAMPLE_VERIFY, SAMPLE_MANIFEST, SAMPLE_FILE_LOG, KNOWN_GOOD_YAML, TRAFFIC_FLOW_MD)

    assert r["score"] == 0.85
    assert r["classification"] == "root_cause"
    assert r["rationale"] == "clean fix"


def test_quality_prompt_includes_context():
    with patch("harness.scoring.dimensions.quality.call_scoring_agent") as mock_agent:
        mock_agent.return_value = '{"score": 1.0, "classification": "root_cause", "rationale": "ok"}'
        from harness.scoring.dimensions.quality import score
        score(SAMPLE_VERIFY, SAMPLE_MANIFEST, SAMPLE_FILE_LOG, KNOWN_GOOD_YAML, TRAFFIC_FLOW_MD)
        prompt = mock_agent.call_args[0][1]

    assert "known_good.yaml" in prompt.lower() or "ProcessorLambdaESM" in prompt
    assert "traffic_flow" in prompt.lower() or "Hop 4" in prompt
    assert "valid_fixes" in prompt or str(SAMPLE_MANIFEST["valid_fixes"]) in prompt
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_scoring.py::test_check_gate_passes_for_clean_root_cause tests/test_scoring.py::test_check_gate_fails_bad_classification tests/test_scoring.py::test_check_gate_fails_primary_not_passed tests/test_scoring.py::test_check_gate_fails_with_regression tests/test_scoring.py::test_quality_score_parses_agent_response tests/test_scoring.py::test_quality_prompt_includes_context -v
```

Expected: all FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `harness/scoring/dimensions/quality.py`**

```python
import json
from harness.scoring.agent import call_scoring_agent, SYSTEM_PROMPT


def check_gate(verify_result: dict) -> bool:
    p1 = verify_result["pass1_functional"]
    p2 = verify_result["pass2_regression"]
    p3 = verify_result["pass3_classification"]
    classification_ok = p3["classification"] in ("root_cause", "workaround")
    assertions_ok = p1["primary_assertions_passed"]
    no_regressions = p2["regression_count"] == 0
    return classification_ok and assertions_ok and no_regressions


def score(
    verify_result: dict,
    manifest: dict,
    file_log: dict,
    known_good_yaml: str,
    traffic_flow_md: str,
) -> dict:
    p1 = verify_result["pass1_functional"]
    p3 = verify_result["pass3_classification"]

    user_prompt = f"""=== known_good.yaml ===
{known_good_yaml}

=== traffic_flow.md ===
{traffic_flow_md}

=== Fault injected ===
Resource:  {manifest["target_resource"]}
Property:  {manifest["target_property"]}
Original:  {manifest["original_value"]}
Injected:  {manifest["injected_value"]}

=== Valid fixes (any of these constitutes a correct root-cause fix) ===
{json.dumps(manifest["valid_fixes"], indent=2)}

=== Invalid patches (resolve the symptom but not the root cause) ===
{json.dumps(manifest["invalid_patches"], indent=2)}

=== Files the model changed ===
{json.dumps(file_log["files_modified"] + file_log["files_added"], indent=2)}

=== Line-level changes per file ===
{json.dumps(file_log["per_file_line_changes"], indent=2)}

=== Verify loop results (deterministic) ===
fix_classification:           {p3["classification"]}
structural_match:             {p3["structural_match"]}
invalid_patch_detected:       {p3["invalid_patch_detected"]}
primary_assertions_passed:    {p1["primary_assertions_passed"]}
all_assertions_passed:        {p1["all_assertions_passed"]}

=== Task ===
Score the quality of the fix using known_good.yaml as the reference for what
the correct state looks like. Determine whether the model's changes match a
valid fix, an invalid patch, or neither.

Rubric (use these exact values only — no interpolation):
  1.00 — Root cause addressed: change matches a valid fix, no regressions,
          implementation is minimal (does not change more than necessary)
  0.85 — Root cause addressed: change matches a valid fix but touches
          additional properties or resources beyond what is needed
  0.60 — Root cause addressed via an over-permissive fix (e.g. wildcard IAM
          Resource, overly broad managed policy) — not in valid_fixes list
  0.35 — Workaround: symptom resolved but change matches an invalid_patch
          or does not address the root-cause property
  0.15 — Partial: primary assertion still fails; some improvement visible
  0.00 — No meaningful change from the faulted state

Return JSON only, no markdown fences:
{{
  "score": <one of: 1.00, 0.85, 0.60, 0.35, 0.15, 0.00>,
  "classification": "<one of: root_cause, workaround, partial, none>",
  "rationale": "<exactly 1-2 sentences>"
}}"""

    raw = call_scoring_agent(SYSTEM_PROMPT, user_prompt)
    parsed = json.loads(raw)
    return {
        "score": float(parsed["score"]),
        "classification": str(parsed["classification"]),
        "rationale": str(parsed["rationale"]),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_scoring.py::test_check_gate_passes_for_clean_root_cause tests/test_scoring.py::test_check_gate_fails_bad_classification tests/test_scoring.py::test_check_gate_fails_primary_not_passed tests/test_scoring.py::test_check_gate_fails_with_regression tests/test_scoring.py::test_quality_score_parses_agent_response tests/test_scoring.py::test_quality_prompt_includes_context -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add harness/scoring/dimensions/quality.py tests/test_scoring.py
git commit -m "feat(scoring): F7 quality.py — tight prompt, known_good + traffic_flow context"
```

---

### Task 8: F8 — `harness/scoring/gate.py`

**Files:**
- Create: `harness/scoring/gate.py`

Thin re-export so orchestrator imports from one stable location.

- [ ] **Step 1: Write `harness/scoring/gate.py`**

```python
from harness.scoring.dimensions.quality import check_gate

__all__ = ["check_gate"]
```

- [ ] **Step 2: Verify import works**

```bash
python -c "from harness.scoring.gate import check_gate; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add harness/scoring/gate.py
git commit -m "feat(scoring): F8 gate.py — re-export check_gate"
```

---

### Task 9: F2 — `harness/scoring/scorer.py`

**Files:**
- Create: `harness/scoring/scorer.py`

Orchestrator loads `known_good.yaml` (already in Phase F spec) and now also `traffic_flow.md` from `corpus/[arch_id]/traffic_flow.md`. Derives `arch_id` from `scenario_id` (which encodes it). Passes both to all five dimension functions. F4 and F5 signatures now accept manifest + context params.

The `arch_id` is derived from `scenario_id` using the convention `scenario_id = arch_id + "_fault_NN"`. The scorer resolves `arch_id` by reading `scenarios/[scenario_id]/arch_id.txt` if present, otherwise splits on `_fault_`.

- [ ] **Step 1: Write the failing integration tests**

```python
# Add to tests/test_scoring.py

import os, tempfile, pathlib

def _write_file(path: pathlib.Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _make_run_dir(base: pathlib.Path, run_id: str, scenario_id: str, verify: dict, trace: list, file_log: dict) -> pathlib.Path:
    run_dir = base / "results" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "scenario_id.txt").write_text(scenario_id)
    (run_dir / "verify_result.json").write_text(json.dumps(verify))
    (run_dir / "tool_call_trace.json").write_text(json.dumps(trace))
    (run_dir / "file_change_log.json").write_text(json.dumps(file_log))
    return run_dir


def _make_scenario_dir(base: pathlib.Path, scenario_id: str, manifest: dict, faulted_yaml: str) -> pathlib.Path:
    s_dir = base / "scenarios" / scenario_id
    s_dir.mkdir(parents=True)
    (s_dir / "fault_manifest.json").write_text(json.dumps(manifest))
    (s_dir / "faulted.yaml").write_text(faulted_yaml)
    return s_dir


def _make_corpus_dir(base: pathlib.Path, arch_id: str, known_good: str, traffic_flow: str) -> pathlib.Path:
    c_dir = base / "corpus" / arch_id
    c_dir.mkdir(parents=True)
    (c_dir / "known_good.yaml").write_text(known_good)
    (c_dir / "traffic_flow.md").write_text(traffic_flow)
    return c_dir


@patch("harness.scoring.dimensions.identification.call_scoring_agent")
@patch("harness.scoring.dimensions.fix_correctness.call_scoring_agent")
@patch("harness.scoring.dimensions.regression.call_scoring_agent")
@patch("harness.scoring.dimensions.efficiency.call_scoring_agent")
@patch("harness.scoring.dimensions.quality.call_scoring_agent")
def test_scorer_writes_score_json(mock_q, mock_e, mock_r, mock_fc, mock_id, tmp_path):
    mock_id.return_value = '{"score": 1.0, "rationale": "identified"}'
    mock_fc.return_value = '{"rationale": "all passed"}'
    mock_r.return_value = '{"rationale": "no regressions"}'
    mock_e.return_value = '{"rationale": "efficient"}'
    mock_q.return_value = '{"score": 1.0, "classification": "root_cause", "rationale": "clean"}'

    scenario_id = "arch_01_order_processing_fault_01"
    arch_id = "arch_01_order_processing"
    run_id = "test-run-001"

    _make_run_dir(tmp_path, run_id, scenario_id, SAMPLE_VERIFY, SAMPLE_TRACE, SAMPLE_FILE_LOG)
    _make_scenario_dir(tmp_path, scenario_id, SAMPLE_MANIFEST, "faulted: yaml")
    _make_corpus_dir(tmp_path, arch_id, KNOWN_GOOD_YAML, TRAFFIC_FLOW_MD)

    from harness.scoring.scorer import score_run
    result = score_run(run_id, str(tmp_path))

    score_path = tmp_path / "results" / run_id / "score.json"
    assert score_path.exists(), "score.json was not written"

    written = json.loads(score_path.read_text())
    assert written["run_id"] == run_id
    assert written["final_score"] >= 0.0
    assert "dimensions" in written
    assert "identification" in written["dimensions"]
    assert "fix_correctness" in written["dimensions"]
    assert "regression_penalty" in written["dimensions"]
    assert "efficiency" in written["dimensions"]
    assert "quality" in written["dimensions"]


@patch("harness.scoring.dimensions.identification.call_scoring_agent")
@patch("harness.scoring.dimensions.fix_correctness.call_scoring_agent")
@patch("harness.scoring.dimensions.regression.call_scoring_agent")
@patch("harness.scoring.dimensions.efficiency.call_scoring_agent")
@patch("harness.scoring.dimensions.quality.call_scoring_agent")
def test_scorer_zero_on_did_not_deploy(mock_q, mock_e, mock_r, mock_fc, mock_id, tmp_path):
    scenario_id = "arch_01_order_processing_fault_01"
    arch_id = "arch_01_order_processing"
    run_id = "test-run-002"
    verify_failed = dict(SAMPLE_VERIFY, outcome="did_not_deploy")

    _make_run_dir(tmp_path, run_id, scenario_id, verify_failed, SAMPLE_TRACE, SAMPLE_FILE_LOG)
    _make_scenario_dir(tmp_path, scenario_id, SAMPLE_MANIFEST, "faulted: yaml")
    _make_corpus_dir(tmp_path, arch_id, KNOWN_GOOD_YAML, TRAFFIC_FLOW_MD)

    from harness.scoring import scorer
    # reload to pick up tmp_path-based paths via monkeypatching isn't needed — score_run takes base_dir
    importlib.reload(scorer)
    from harness.scoring.scorer import score_run
    result = score_run(run_id, str(tmp_path))

    mock_id.assert_not_called()
    assert result["final_score"] == 0.0
    assert result["zero_reason"] == "did_not_deploy"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_scoring.py::test_scorer_writes_score_json tests/test_scoring.py::test_scorer_zero_on_did_not_deploy -v
```

Expected: both FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `harness/scoring/scorer.py`**

```python
import json
import pathlib
from harness.scoring import gate
from harness.scoring.dimensions import (
    identification,
    fix_correctness,
    regression,
    efficiency,
    quality,
)


def _load_text(path: pathlib.Path) -> str:
    return path.read_text()


def _load_json(path: pathlib.Path) -> dict:
    return json.loads(path.read_text())


def _derive_arch_id(scenario_id: str, scenario_dir: pathlib.Path) -> str:
    arch_id_file = scenario_dir / "arch_id.txt"
    if arch_id_file.exists():
        return arch_id_file.read_text().strip()
    # Convention: scenario_id = arch_id + "_fault_NN"
    parts = scenario_id.split("_fault_")
    return parts[0] if len(parts) == 2 else scenario_id


def _write_zero(run_dir: pathlib.Path, run_id: str, scenario_id: str, reason: str, quality_gate_met: bool = True) -> dict:
    result = {
        "run_id": run_id,
        "scenario_id": scenario_id,
        "scored_by": "claude-sonnet-4-6",
        "quality_threshold_met": quality_gate_met,
        "zero_reason": reason,
        "dimensions": {},
        "weighted": 0.0,
        "composite": 0.0,
        "final_score": 0.0,
        "interpretation": "Failed quality gate or no improvement",
    }
    (run_dir / "score.json").write_text(json.dumps(result, indent=2))
    return result


def score_run(run_id: str, base_dir: str) -> dict:
    base = pathlib.Path(base_dir)
    run_dir = base / "results" / run_id

    scenario_id = (run_dir / "scenario_id.txt").read_text().strip()
    scenario_dir = base / "scenarios" / scenario_id
    arch_id = _derive_arch_id(scenario_id, scenario_dir)
    corpus_dir = base / "corpus" / arch_id

    required = [
        run_dir / "verify_result.json",
        run_dir / "tool_call_trace.json",
        run_dir / "file_change_log.json",
        scenario_dir / "fault_manifest.json",
        scenario_dir / "faulted.yaml",
        corpus_dir / "known_good.yaml",
        corpus_dir / "traffic_flow.md",
    ]
    for f in required:
        if not f.exists():
            return _write_zero(run_dir, run_id, scenario_id, "missing_artifacts")

    verify_result = _load_json(run_dir / "verify_result.json")
    tool_trace    = _load_json(run_dir / "tool_call_trace.json")
    file_log      = _load_json(run_dir / "file_change_log.json")
    manifest      = _load_json(scenario_dir / "fault_manifest.json")
    known_good    = _load_text(corpus_dir / "known_good.yaml")
    traffic_flow  = _load_text(corpus_dir / "traffic_flow.md")

    if verify_result.get("outcome") != "completed":
        return _write_zero(run_dir, run_id, scenario_id, verify_result.get("outcome", "unknown_outcome"))

    if not gate.check_quality_gate(verify_result):
        return _write_zero(run_dir, run_id, scenario_id, "quality_gate_failed", quality_gate_met=False)

    d1 = identification.score(tool_trace, manifest, verify_result, known_good, traffic_flow)
    d2 = fix_correctness.score(verify_result, manifest, known_good, traffic_flow)
    d3 = regression.compute(verify_result, manifest, known_good, traffic_flow)
    d4 = efficiency.score(tool_trace, file_log, manifest, known_good, traffic_flow)
    d5 = quality.score(verify_result, manifest, file_log, known_good, traffic_flow)

    weighted  = (d1["score"] * 0.20) + (d2["score"] * 0.25) \
              + (d4["score"] * 0.15) + (d5["score"] * 0.40)
    composite = max(0.0, round(weighted - d3["penalty"], 4))

    def interpret(s: float) -> str:
        if s >= 0.90: return "Root cause identified, clean fix, no regressions, efficient"
        if s >= 0.75: return "Correct fix with minor inefficiency or implementation concern"
        if s >= 0.50: return "Fix works but via workaround, or has regressions"
        if s >= 0.25: return "Partial resolution, significant issues"
        return "Failed quality gate or no improvement"

    result = {
        "run_id": run_id,
        "scenario_id": scenario_id,
        "scored_by": "claude-sonnet-4-6",
        "quality_threshold_met": True,
        "zero_reason": None,
        "dimensions": {
            "identification":     d1,
            "fix_correctness":    d2,
            "regression_penalty": d3,
            "efficiency":         d4,
            "quality":            d5,
        },
        "weighted": round(weighted, 4),
        "composite": composite,
        "final_score": composite,
        "interpretation": interpret(composite),
    }
    (run_dir / "score.json").write_text(json.dumps(result, indent=2))
    return result
```

**Note:** `gate.py` must export `check_quality_gate` (not `check_gate`) since scorer calls `gate.check_quality_gate`. Update `harness/scoring/gate.py` to:

```python
from harness.scoring.dimensions.quality import check_gate as check_quality_gate

__all__ = ["check_quality_gate"]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_scoring.py::test_scorer_writes_score_json tests/test_scoring.py::test_scorer_zero_on_did_not_deploy -v
```

Expected: both PASS

- [ ] **Step 5: Run full test suite**

```bash
pytest tests/test_scoring.py -v
```

Expected: all tests PASS

- [ ] **Step 6: Commit**

```bash
git add harness/scoring/scorer.py harness/scoring/gate.py tests/test_scoring.py
git commit -m "feat(scoring): F2 scorer.py — orchestrator with traffic_flow.md loading, F8 gate.py fix"
```

---

### Task 10: F9 — Update `harness/run.py`

**Files:**
- Modify: `harness/run.py`

Add the scorer call and extend the terminal summary block.

- [ ] **Step 1: Read the current run.py to locate the verify loop call**

```bash
cat harness/run.py
```

Identify the line where `verify_loop.run_verify_loop(...)` is called and where the terminal summary is printed.

- [ ] **Step 2: Add scorer import at the top of run.py**

Add after existing imports:

```python
from harness.scoring.scorer import score_run
```

- [ ] **Step 3: Add scorer call after verify loop**

Immediately after the existing verify loop result is captured, add:

```python
# Step 7 — autonomous scoring
print("[scorer] Running Step 7 scoring agent (Claude Sonnet)...")
score = score_run(run_id, base_dir)
```

Where `base_dir` is the root directory passed to `run.py` (the directory containing `results/`, `scenarios/`, and `corpus/`).

- [ ] **Step 4: Extend the terminal summary**

Replace or extend the existing summary print block to add the scoring section:

```python
print(f"""
── Scoring (Claude Sonnet) ─────────────
Quality gate:     {"PASS" if score.get("quality_threshold_met") else "FAIL → score zeroed"}
Identification:   {score["dimensions"].get("identification", {}).get("score", "N/A"):.2f}  {score["dimensions"].get("identification", {}).get("rationale", "")}
Fix correctness:  {score["dimensions"].get("fix_correctness", {}).get("score", "N/A"):.2f}  {score["dimensions"].get("fix_correctness", {}).get("rationale", "")}
Regression:      -{score["dimensions"].get("regression_penalty", {}).get("penalty", "N/A"):.2f}  {score["dimensions"].get("regression_penalty", {}).get("rationale", "")}
Efficiency:       {score["dimensions"].get("efficiency", {}).get("score", "N/A"):.2f}  {score["dimensions"].get("efficiency", {}).get("rationale", "")}
Quality:          {score["dimensions"].get("quality", {}).get("score", "N/A"):.2f}  {score["dimensions"].get("quality", {}).get("rationale", "")}
────────────────────────────────────────
Final score:      {score["final_score"]:.4f}
Interpretation:   {score.get("interpretation", "")}
────────────────────────────────────────
""")
```

- [ ] **Step 5: Smoke-test the import (not a live API call)**

```bash
python -c "from harness.run import *; print('import ok')" 2>&1 | head -5
```

Expected: `import ok` (or module-level errors from missing deps — fix those, don't call the API)

- [ ] **Step 6: Commit**

```bash
git add harness/run.py
git commit -m "feat(scoring): F9 wire scorer into harness/run.py, extend terminal summary"
```

---

### Task 11: Full suite verification

- [ ] **Step 1: Run complete test suite**

```bash
pytest tests/test_scoring.py -v --tb=short
```

Expected: all tests PASS

- [ ] **Step 2: Check no regressions in existing tests**

```bash
pytest tests/ -v --tb=short --ignore=tests/test_scoring.py 2>&1 | tail -20
```

Expected: same pass/fail as before this feature branch

- [ ] **Step 3: Lint**

```bash
ruff check harness/scoring/ tests/test_scoring.py
```

Fix any issues, then:

```bash
git add -u
git commit -m "fix(scoring): ruff lint fixes"
```

---

## Self-Review Against Spec

### Spec coverage check

| Phase F requirement | Task covering it |
|---|---|
| F1 agent.py — Claude Sonnet client | Task 2 |
| F1 system prompt updated for known_good + traffic_flow | Task 2 |
| F2 scorer.py — loads known_good.yaml | Task 9 |
| F2 scorer.py — loads traffic_flow.md (new) | Task 9 |
| F2 scorer.py — early exit on missing artifacts | Task 9 |
| F2 scorer.py — early exit on non-completed outcome | Task 9 |
| F2 scorer.py — quality gate check before agent calls | Task 9 |
| F2 scorer.py — composite formula | Task 9 |
| F2 scorer.py — writes score.json | Task 9 |
| F3 identification.py — agent call + parse | Task 3 |
| F3 — known_good + traffic_flow in prompt | Task 3 |
| F3 — tight rubric (exact float values) | Task 3 |
| F4 fix_correctness.py — deterministic score (unchanged) | Task 4 |
| F4 — NEW: agent call for rationale | Task 4 |
| F4 — known_good + traffic_flow in prompt | Task 4 |
| F5 regression.py — deterministic penalty (unchanged) | Task 5 |
| F5 — NEW: agent call for rationale | Task 5 |
| F5 — known_good + traffic_flow in prompt | Task 5 |
| F6 efficiency.py — threshold_score formula | Task 6 |
| F6 — agent rationale call | Task 6 |
| F6 — known_good + traffic_flow in prompt | Task 6 |
| F7 quality.py — check_gate deterministic | Task 7 |
| F7 — agent score call | Task 7 |
| F7 — known_good + traffic_flow in prompt | Task 7 |
| F8 gate.py — re-export | Tasks 8, 9 |
| F9 run.py — scorer wired in | Task 10 |
| F9 run.py — extended terminal summary | Task 10 |
| Tests for all modules | Tasks 2–9 |

### Signature consistency

All five dimension functions have consistent signature suffix: `..., known_good_yaml: str, traffic_flow_md: str`.

F4 gains `manifest: dict` (needed so agent knows what fault was fixed). This is consistent with F3, F5, F6, F7 which all receive `manifest`.

scorer.py calls all five with positional args matching declared signatures — verified in Task 9 code.

`gate.py` exports `check_quality_gate` (not `check_gate`) to match scorer.py call. Noted and corrected in Task 9.
