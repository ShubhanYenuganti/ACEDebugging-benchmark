# Phase E — Entry Point Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `harness/run.py` — the single CLI entry point that ties all phases together — and validate it with an end-to-end integration test using a stub model and a real scenario.

**Architecture:** `run.py` is a thin orchestrator: validates pre-conditions, starts the runner, prints the model context to stdout, blocks until submission or timeout, then runs the verify loop and prints a formatted summary. The E2E test uses a stub script that applies the correct fix and triggers redeployment — no real LLM involved.

**Tech Stack:** Python 3.11, argparse, python-dotenv, all Phase A–D modules, subprocess (for E2E stub), LocalStack (live for E2E)

---

## Manual Pre-Configuration — Builder Must Complete These Before Writing Any Files

**1. All prior phase tests pass**
```bash
pytest tests/test_shared.py tests/test_runner.py tests/test_verify.py -v
# Expected: 43 passed total (17 + 8 + 18)
```

**2. LocalStack is running**
```bash
localstack status services 2>/dev/null | grep -q "running" && echo "OK" || echo "NOT RUNNING"
# If not running: localstack start -d
# Then poll: until localstack status services 2>/dev/null | grep -q "running"; do sleep 2; done
```

**3. MCP server is registered**
```bash
claude mcp list
# Expected: ace-bench-diagnostic-mcp appears
```

**4. `.env` exists with HARNESS_API_KEY**
```bash
test -f .env && grep -q HARNESS_API_KEY .env && echo "OK" || echo "MISSING"
```

**5. python-dotenv is installed**
```bash
python -c "from importlib.metadata import version; printdotenv; print(dotenv.__version__)"
# If missing: pip install python-dotenv && echo "python-dotenv>=1.0.0" >> requirements.txt
```

**6. A scenario exists at `scenarios/arch01_fault01_security/`**

The E2E test requires one real scenario on disk matching the layout from SPEC.md Pre-conditions. If not yet seeded, create a minimal one:
```bash
mkdir -p scenarios/arch01_fault01_security/deployment/lambda
echo "The Lambda function returns 500 on all requests." > scenarios/arch01_fault01_security/scenario.md
# faulted.yaml, fault_manifest.json, deployment/lambda/handler.py must also exist
# See SPEC.md Pre-conditions for the full expected layout
```

---

## File Structure

| File | Responsibility |
|------|----------------|
| `harness/run.py` | CLI entry point: pre-conditions, runner, context print, block, verify, summary |
| `tests/stubs/__init__.py` | Package marker for stubs |
| `tests/stubs/stub_model.py` | E2E stub: reads context from stdin, applies known fix, triggers redeployment |
| `tests/test_e2e.py` | E2E integration test (requires live LocalStack + real scenario) |
---

## Task 1: Extend ScenarioRunner with `_last_deployment_outcome`

`run.py` needs to know what `handle_submission` returned so it can pass `deployment_outcome` to the verify loop. Add this to `ScenarioRunner` before writing `run.py`.

**Files:**
- Modify: `harness/runner/scenario_runner.py`

- [ ] **Step 1: Add `_last_deployment_outcome` field**

Open `harness/runner/scenario_runner.py`. In `__init__`, add after `self.submitted = False`:

```python
self._last_deployment_outcome: str = "unknown"
```

In `on_model_redeploy`, store the outcome before returning:

```python
def on_model_redeploy(self) -> dict:
    with self._lock:
        if self.submitted:
            return {"outcome": "already_submitted"}
        self.submitted = True
    result = handle_submission(self.scenario_dir, self.run_id, self.start_snapshot)
    self._last_deployment_outcome = result.get("outcome", "unknown")
    return result
```

- [ ] **Step 2: Verify existing tests still pass**

```bash
pytest tests/test_runner.py::TestScenarioRunner -v
```

Expected: 2 passed (no regressions)

- [ ] **Step 3: Commit**

```bash
git add harness/runner/scenario_runner.py
git commit -m "feat: add _last_deployment_outcome to ScenarioRunner"
```

---

## Task 2: harness/run.py

**Files:**
- Create: `harness/run.py`

- [ ] **Step 1: Create `harness/run.py`**

```python
#!/usr/bin/env python3
"""harness/run.py — ACE-Bench evaluation entry point."""

import argparse
import json
import os
import sys
import time
import uuid

from dotenv import load_dotenv

from harness.shared.localstack_client import health_check
from harness.shared.result_logger import init_run, log_verify_result
from harness.runner.context_builder import build_context
from harness.runner.scenario_runner import ScenarioRunner
from harness.verify.verify_loop import run_verify_loop

_TIMEOUT_SECONDS = 30 * 60  # 30 minutes


def _validate_scenario(scenario_dir: str) -> None:
    for item in ["scenario.md", "faulted.yaml", "fault_manifest.json", "deployment"]:
        if not os.path.exists(os.path.join(scenario_dir, item)):
            print(f"ERROR: Missing required item in scenario_dir: {item}", file=sys.stderr)
            sys.exit(1)


def _print_context(ctx: dict) -> None:
    print("=" * 60)
    print("SCENARIO BRIEF")
    print("=" * 60)
    print(ctx["scenario_brief"])
    print()
    print("TEMPLATE:", ctx["template_path"])
    print("DEPLOYMENT DIR:", ctx["deployment_dir"])
    print()
    if ctx["stack_outputs"]:
        print("STACK OUTPUTS:")
        for k, v in ctx["stack_outputs"].items():
            print(f"  {k}: {v}")
        print()
    print("INSTRUCTION:")
    print(ctx["instruction"])
    print("=" * 60)
    sys.stdout.flush()


def _print_summary(run_id: str, scenario_id: str, verify_result: dict, runner: "ScenarioRunner") -> None:
    p1 = verify_result.get("pass1_functional") or {}
    p2 = verify_result.get("pass2_regression") or {}
    p3 = verify_result.get("pass3_classification") or {}
    p4 = verify_result.get("pass4_concurrency")
    outcome = verify_result.get("outcome", "unknown")

    deployment_status = "PASS" if outcome == "completed" else "FAIL"

    if p1:
        if p1.get("all_assertions_passed"):
            functional_status = "PASS"
        elif p1.get("primary_assertions_passed"):
            functional_status = "PARTIAL"
        else:
            functional_status = "FAIL"
    else:
        functional_status = "SKIPPED"

    reg_count = p2.get("regression_count", 0)
    crit = p2.get("critical_regression_count", 0)
    noncrit = p2.get("non_critical_regression_count", 0)
    regressions_str = "none" if reg_count == 0 else f"{crit} critical, {noncrit} non-critical"

    classification = p3.get("classification", "n/a") if p3 else "n/a"

    if p4 is None:
        concurrency_str = "SKIPPED"
    elif p4.get("passed"):
        concurrency_str = "PASS"
    else:
        concurrency_str = "FAIL"

    file_change_path = os.path.join("results", run_id, "file_change_log.json")
    files_changed = lines_changed = 0
    if os.path.isfile(file_change_path):
        with open(file_change_path) as f:
            fc = json.load(f)
        files_changed = fc.get("total_files_changed", 0)
        lines_changed = fc.get("total_lines_changed", 0)

    print()
    print("═" * 39)
    print(f"ACE-Bench Run: {run_id}")
    print(f"Scenario: {scenario_id}")
    print("═" * 39)
    print()
    print(f"Deployment:       {deployment_status}")
    print(f"Functional test:  {functional_status}")
    print(f"Regressions:      {regressions_str}")
    print(f"Classification:   {classification}")
    print(f"Concurrency:      {concurrency_str}")
    print()
    print(f"Tool calls made:  {runner.tool_call_count}")
    print(f"Files changed:    {files_changed}")
    print(f"Lines changed:    {lines_changed}")
    print()
    print(f"Full results:     results/{run_id}/")
    print("═" * 39)
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="ACE-Bench evaluation harness")
    parser.add_argument("scenario_dir", help="Path to scenario directory")
    parser.add_argument("--run-id", default=None, help="Run identifier (auto-generated if omitted)")
    args = parser.parse_args()

    load_dotenv()

    scenario_dir = os.path.abspath(args.scenario_dir)
    run_id = args.run_id or uuid.uuid4().hex[:8]
    scenario_id = os.path.basename(scenario_dir)

    # Step 2 — health check
    try:
        health_check()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    # Step 3 — validate scenario
    _validate_scenario(scenario_dir)

    # fault_manifest.json lives in scenario_dir for E2E but must not be
    # model-accessible at run time; build_context will raise if it can read it.
    # We read the manifest path before calling build_context (which would raise).
    manifest_path = os.path.join(scenario_dir, "fault_manifest.json")

    # corpus functional_test.py: assume corpus/<arch_id>/functional_test.py
    # where arch_id is derived from scenario_id prefix (e.g. arch01_fault01_security -> arch_01_)
    parts = scenario_id.split("_")
    arch_prefix = "_".join(parts[:2]) if len(parts) >= 2 else parts[0]
    corpus_dir = os.path.join(os.path.dirname(scenario_dir), "..", "corpus",
                              f"arch_{arch_prefix[len('arch'):]}_default")
    if not os.path.isdir(corpus_dir):
        corpus_dir = scenario_dir  # fallback: functional_test.py co-located

    # Step 5 — init run
    init_run(run_id, scenario_id)

    # Step 6 — start runner (deploy faulted, capture baseline)
    runner = ScenarioRunner(scenario_dir, run_id)
    try:
        runner.start()
    except RuntimeError as e:
        print(f"ERROR: Scenario deployment failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Step 8 — build context (raises if manifest readable — remove manifest first if needed)
    # For production: fault_manifest.json is outside scenario_dir.
    # For E2E: temporarily rename to hide it, then restore after build_context.
    manifest_hidden = manifest_path + ".hidden"
    manifest_was_present = os.path.isfile(manifest_path)
    if manifest_was_present:
        os.rename(manifest_path, manifest_hidden)
    try:
        ctx = build_context(scenario_dir)
    finally:
        if manifest_was_present:
            os.rename(manifest_hidden, manifest_path)

    _print_context(ctx)

    # Step 10 — block until submitted or timeout
    deadline = time.monotonic() + _TIMEOUT_SECONDS
    while not runner.submitted:
        if time.monotonic() > deadline:
            log_verify_result(run_id, {"outcome": "timed_out"})
            print("ERROR: Timed out waiting for model redeployment.", file=sys.stderr)
            sys.exit(1)
        time.sleep(2)

    # Step 12 — verify loop
    verify_result = run_verify_loop(
        scenario_dir=scenario_dir,
        run_id=run_id,
        deployment_outcome=runner._last_deployment_outcome,
        manifest_path=manifest_path,
        corpus_dir=corpus_dir,
        api_endpoint=ctx["stack_outputs"].get("ApiEndpoint", ""),
    )

    # Step 13 — print summary
    _print_summary(run_id, scenario_id, verify_result, runner)

    # Step 14 — exit code
    sys.exit(0 if verify_result.get("outcome") == "completed" else 1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify help output**

```bash
python harness/run.py --help
```

Expected:
```
usage: run.py [-h] [--run-id RUN_ID] scenario_dir
ACE-Bench evaluation harness
positional arguments:
  scenario_dir  Path to scenario directory
options:
  --run-id RUN_ID  Run identifier (auto-generated if omitted)
```

- [ ] **Step 3: Commit**

```bash
git add harness/run.py
git commit -m "feat: add harness/run.py entry point"
```

---

## Task 3: E2E stub model

**Files:**
- Create: `tests/stubs/__init__.py`
- Create: `tests/stubs/stub_model.py`

- [ ] **Step 1: Create stub directory**

```bash
mkdir -p tests/stubs
touch tests/stubs/__init__.py
```

- [ ] **Step 2: Create `tests/stubs/stub_model.py`**

```python
#!/usr/bin/env python3
"""
Stub model for E2E testing. Consumes harness context from stdin,
applies the known-correct fix from fault_manifest.json, and triggers
redeployment via localstack-deployer.

Usage (run after harness/run.py starts):
    python harness/run.py scenarios/arch01_fault01_security/ --run-id e2e-test | \
        python tests/stubs/stub_model.py scenarios/arch01_fault01_security/ \
                                         scenarios/arch01_fault01_security/fault_manifest.json
"""

import json
import os
import re
import subprocess
import sys


def _apply_sequence_fix(
    template_text: str,
    injected: list,
    original: list,
) -> tuple:
    """
    Replace the YAML block-sequence representation of `injected` with
    `original`, also stripping any preceding # FAULT INJECTED comment
    lines at the same indentation level.

    Returns (changed: bool, new_text: str).
    """
    first = re.escape(injected[0])
    m = re.search(r"^(\s*)- " + first + r"\s*$", template_text, re.MULTILINE)
    if not m:
        return False, template_text

    pad = m.group(1)
    comment_block = r"(?:" + re.escape(pad) + r"#[^\n]*\n)*"
    injected_block = re.escape("\n".join(f"{pad}- {v}" for v in injected))
    original_block = "\n".join(f"{pad}- {v}" for v in original)

    new_text = re.sub(comment_block + injected_block, original_block, template_text, count=1)
    return new_text != template_text, new_text


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: stub_model.py <scenario_dir> <manifest_path>", file=sys.stderr)
        sys.exit(1)

    scenario_dir = os.path.abspath(sys.argv[1])
    manifest_path = os.path.abspath(sys.argv[2])

    # Consume all stdin (unblocks harness stdout pipe)
    _ = sys.stdin.read()

    with open(manifest_path) as f:
        manifest = json.load(f)

    template_path = os.path.join(scenario_dir, "faulted.yaml")
    with open(template_path) as f:
        template = f.read()

    injected_value = manifest.get("injected_value")  # list e.g. ["dynamodb:GetItem"]
    original_value = manifest.get("original_value")  # list e.g. ["dynamodb:GetItem", "dynamodb:PutItem"]

    if not injected_value or not original_value:
        print("stub_model: manifest missing injected_value or original_value", file=sys.stderr)
        sys.exit(1)

    if isinstance(injected_value, list):
        changed, fixed = _apply_sequence_fix(template, injected_value, original_value)
    else:
        changed = str(injected_value) in template
        fixed = template.replace(str(injected_value), str(original_value), 1)

    if not changed:
        print("stub_model: injected_value not found in template — no patch applied", file=sys.stderr)
        sys.exit(1)

    with open(template_path, "w") as f:
        f.write(fixed)
    print(f"stub_model: applied fix — injected={injected_value!r} -> original={original_value!r}")

    result = subprocess.run(
        ["localstack-deployer", "update-stack", "--stack-name", "ace-bench-stack"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"stub_model: update-stack failed:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)

    print("stub_model: redeployment triggered successfully")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Commit**

```bash
git add tests/stubs/__init__.py tests/stubs/stub_model.py
git commit -m "test: add stub model for E2E harness testing"
```

---

## Task 4: E2E integration test

**Files:**
- Create: `tests/test_e2e.py`

- [ ] **Step 1: Create `tests/test_e2e.py`**

```python
"""
E2E integration test for the ACE-Bench harness.

Requirements before running:
- LocalStack running:      localstack start -d
- MCP server registered:   claude mcp list shows ace-bench-diagnostic-mcp
- .env with HARNESS_API_KEY present at project root
- scenarios/arch01_fault01_security/ exists with:
    scenario.md, faulted.yaml, fault_manifest.json (with faulted_value field),
    deployment/lambda/handler.py

Run:
    pytest tests/test_e2e.py -v -s
"""

import json
import os
import subprocess
import sys

import pytest

SCENARIO_DIR = os.path.abspath("scenarios/arch01_fault01_security")
MANIFEST_PATH = os.path.join(SCENARIO_DIR, "fault_manifest.json")
RUN_ID = "e2e-test"
RESULTS_DIR = os.path.abspath("results")


@pytest.fixture(scope="module", autouse=True)
def check_prerequisites():
    if not os.path.isdir(SCENARIO_DIR):
        pytest.skip(f"Scenario directory not found: {SCENARIO_DIR}")
    if not os.path.isfile(MANIFEST_PATH):
        pytest.skip(f"fault_manifest.json not found at: {MANIFEST_PATH}")
    ls_result = subprocess.run(
        ["localstack", "status", "services"],
        capture_output=True, text=True,
    )
    if "running" not in ls_result.stdout:
        pytest.skip("LocalStack is not running — start it with: localstack start -d")


def test_e2e_run_exits_0_with_root_cause():
    """Full harness run using stub model — must exit 0, classification=root_cause, regressions=0."""
    harness_cmd = [sys.executable, "harness/run.py", SCENARIO_DIR, "--run-id", RUN_ID]
    stub_cmd = [sys.executable, "tests/stubs/stub_model.py", SCENARIO_DIR, MANIFEST_PATH]

    # Pipe harness stdout into stub model stdin
    harness_proc = subprocess.Popen(
        harness_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stub_proc = subprocess.Popen(
        stub_cmd,
        stdin=harness_proc.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    harness_stdout, harness_stderr = harness_proc.communicate(timeout=600)
    stub_stdout, stub_stderr = stub_proc.communicate(timeout=60)

    print("\n--- harness stdout ---")
    print(harness_stdout)
    print("--- harness stderr ---")
    print(harness_stderr)
    print("--- stub stdout ---")
    print(stub_stdout)
    print("--- stub stderr ---")
    print(stub_stderr)

    assert harness_proc.returncode == 0, (
        f"harness/run.py exited with code {harness_proc.returncode}\n"
        f"stderr:\n{harness_stderr}"
    )

    verify_path = os.path.join(RESULTS_DIR, RUN_ID, "verify_result.json")
    assert os.path.isfile(verify_path), f"verify_result.json not written: {verify_path}"

    with open(verify_path) as f:
        verify_result = json.load(f)

    assert verify_result.get("outcome") == "completed", (
        f"Expected outcome=completed, got: {verify_result.get('outcome')}\n"
        f"Full result: {json.dumps(verify_result, indent=2)}"
    )

    classification = (verify_result.get("pass3_classification") or {}).get("classification")
    assert classification == "root_cause", (
        f"Expected classification=root_cause, got: {classification}"
    )

    regression_count = (verify_result.get("pass2_regression") or {}).get("regression_count", -1)
    assert regression_count == 0, (
        f"Expected regression_count=0, got: {regression_count}"
    )
```

- [ ] **Step 2: Commit**

```bash
git add tests/test_e2e.py
git commit -m "test: add E2E integration test for full harness run"
```

---

## Task 5: Phase E Gate — E2E Integration Test

- [ ] **Step 1: Ensure LocalStack is running**

```bash
until localstack status services 2>/dev/null | grep -q "running"; do sleep 2; done
echo "LocalStack ready"
```

- [ ] **Step 2: Run the E2E test**

```bash
pytest tests/test_e2e.py -v -s
```

Expected:
```
tests/test_e2e.py::test_e2e_run_exits_0_with_root_cause PASSED
1 passed
```

- [ ] **Step 3: Verify result files were written correctly**

```bash
ls results/e2e-test/
# Expected: scenario_id.txt  tool_call_trace.json  file_change_log.json  verify_result.json

python -m json.tool results/e2e-test/verify_result.json
# Expected: {"outcome": "completed", "pass1_functional": {...}, "pass3_classification": {"classification": "root_cause", ...}, ...}
```

- [ ] **Step 4: Run the full test suite across all prior phases**

```bash
pytest tests/test_shared.py tests/test_runner.py tests/test_verify.py -v
# Expected: 43 passed
```

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "feat: Phase E complete — harness entry point and E2E test passing, exit 0 with root_cause classification"
```

**All phases complete. The ACE-Bench harness is fully implemented.**

---

## Self-Review

### Spec coverage

| Spec requirement | Covered by |
|-----------------|------------|
| E1 — `python harness/run.py <scenario_dir> [--run-id]` | Task 2, argparse |
| E1 — Load `.env` for HARNESS_API_KEY | `load_dotenv()` in `main()` |
| E1 — `health_check()` fail-fast | Step 2 in `main()`, `sys.exit(1)` on failure |
| E1 — Validate scenario has required files | `_validate_scenario()` checks all 4 required items |
| E1 — `init_run(run_id, scenario_id)` | Step 5 in `main()` |
| E1 — `ScenarioRunner.start()` deploy + baseline | Step 6 in `main()` |
| E1 — `build_context` → print to stdout | `_print_context(ctx)` |
| E1 — Block until `runner.submitted` or 30-min timeout | `while not runner.submitted` loop with `deadline` |
| E1 — Timeout → write `{outcome: "timed_out"}`, exit 1 | Timeout branch with `log_verify_result` + `sys.exit(1)` |
| E1 — `run_verify_loop` after submission | Step 12 in `main()` |
| E1 — Print human-readable summary | `_print_summary()` with all 8 required fields |
| E1 — Exit 0 if `outcome == "completed"`, else exit 1 | Final `sys.exit()` |
| E2 — Summary uses `═` borders with run_id and scenario | `_print_summary()` uses `═ * 39` and both identifiers |
| E2 — All 8 summary fields present | Deployment, Functional, Regressions, Classification, Concurrency, Tool calls, Files changed, Lines changed |
| E — E2E: exit 0, valid JSON, root_cause, regression_count=0 | `test_e2e_run_exits_0_with_root_cause` |

### Placeholder scan

No TBD, TODO, or vague steps found.

### Type consistency

- `ScenarioRunner._last_deployment_outcome: str` — initialized to `"unknown"` in Task 1; set to `result.get("outcome", "unknown")` in `on_model_redeploy`; read in `run.py` as `runner._last_deployment_outcome`
- `run_verify_loop(scenario_dir, run_id, deployment_outcome, manifest_path, corpus_dir, api_endpoint)` — 6 named params, called identically in `run.py` and matching Phase D definition
- `_print_summary(run_id, scenario_id, verify_result, runner)` — accesses `verify_result["pass1_functional"]`, `["pass2_regression"]`, `["pass3_classification"]`, `["pass4_concurrency"]`; all present in Phase D returned dict
- `log_verify_result(run_id, result)` — 2 positional args, matches Phase A `result_logger.log_verify_result(run_id: str, result: dict)` exactly
- `health_check()` — imported from `harness.shared.localstack_client`, raises `RuntimeError`, matches Phase A implementation
