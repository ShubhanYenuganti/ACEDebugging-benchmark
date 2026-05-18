# Track D — Test-Result Retry Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After each successful deployment the harness runs the scenario's functional and performance tests inline. If any tests fail the results are injected back into the conversation as the `submit_fix` tool result in a structured `passed (...) / failed (...)` format, and the agent is allowed up to 5 retries to pass all tests. The retry loop is separate from the existing 5-retry deployment-failure loop.

**Architecture:** `ScenarioRunner` gains two new methods: `run_functional_tests()` (runs `functional_test.py` via subprocess, parses per-test pass/fail, returns a structured dict) and `attempt_redeployment()` (deploys without checking/setting `self.submitted`, used for test-retry deploys after the initial `attempt_deployment` already set `submitted=True`). `run_agent_loop` gains `verify_callback`, `redeploy_callback`, and `max_test_retries` params plus an `all_tests_passed` flag; the existing `if submitted: break` exit condition is replaced with `if submitted and (all_tests_passed or test_retry_count >= max_test_retries): break`. `run.py` wires `verify_callback=runner.run_functional_tests` and `redeploy_callback=runner.attempt_redeployment` alongside the existing `deploy_callback`. A new module-level helper `_format_test_summary(result, attempt, max_retries) -> str` in `loop.py` formats the message the model receives.

**Result format injected as tool result for `submit_fix`:**
```
Tests: 2 passed, 3 failed.
passed (test_accept_state: Accepting a request transitions both sides to Friends)
passed (test_read_list: Reading the friend list returns the correct entries)
failed (test_request_pending: Sending a request creates a Pending record — AssertionError: state 'None' != 'Pending')
failed (test_reject_clears: Rejecting removes the pending state — KeyError: 'state')
failed (test_unfriend_removes: Unfriending removes entries on both sides — AssertionError: expected 0 items, got 2)
Revise your fix with write_file and call submit_fix again. (Attempt 1 of 5.)
```

**Tech Stack:** Python 3.11, asyncio, subprocess, pytest (already a dev dependency), LiteLLM

---

## File Structure

| File | Change |
|------|--------|
| `harness/runner/context_builder.py` | Add `corpus_dir_for_scenario(scenario_dir, corpus_root=None) -> Path` helper |
| `harness/runner/scenario_runner.py` | Add `run_functional_tests()` and `attempt_redeployment()` methods; add module-level `_parse_pytest_output()` |
| `harness/agent/loop.py` | Add `verify_callback`, `redeploy_callback`, `max_test_retries` params; add `all_tests_passed` + `test_retry_count` counters; add `_format_test_summary()`; replace `if submitted: break` with compound exit; extend submit_fix handler |
| `harness/run.py` | Pass `verify_callback`, `redeploy_callback` to `run_agent_loop` |
| `tests/test_runner.py` | Add tests for `corpus_dir_for_scenario`, `run_functional_tests`, `attempt_redeployment` |
| `tests/test_agent_loop.py` | Add tests for verify-after-deploy, test-retry continuation, max-test-retries exit, redeploy_callback routing |

---

### Task 1: Add `corpus_dir_for_scenario()` to `context_builder.py`

**Files:**
- Modify: `harness/runner/context_builder.py`

Maps a scenario directory path to its corpus directory by matching the two-digit arch number prefix (`arch01_…` → `corpus/arch_01_*/`).

- [ ] **Step 1: Write failing tests**

In `tests/test_runner.py`, add:

```python
from pathlib import Path
from harness.runner.context_builder import corpus_dir_for_scenario

def test_corpus_dir_for_scenario_resolves_arch01(tmp_path):
    corpus = tmp_path / "corpus" / "arch_01_serverless_microservices"
    corpus.mkdir(parents=True)
    (tmp_path / "scenarios" / "arch01_fault07_data_correctness").mkdir(parents=True)
    scenario = tmp_path / "scenarios" / "arch01_fault07_data_correctness"
    result = corpus_dir_for_scenario(scenario, corpus_root=tmp_path / "corpus")
    assert result == corpus

def test_corpus_dir_for_scenario_raises_on_unknown_arch(tmp_path):
    (tmp_path / "scenarios" / "arch99_fault01_connectivity").mkdir(parents=True)
    scenario = tmp_path / "scenarios" / "arch99_fault01_connectivity"
    with pytest.raises(FileNotFoundError):
        corpus_dir_for_scenario(scenario, corpus_root=tmp_path / "corpus")
```

- [ ] **Step 2: Run to confirm tests fail**

```bash
pytest tests/test_runner.py -k "corpus_dir_for_scenario" -v
```

Expected: `ImportError` or `AttributeError`.

- [ ] **Step 3: Implement in `context_builder.py`**

Add at the bottom of the file:

```python
def corpus_dir_for_scenario(
    scenario_dir: "Path | str",
    corpus_root: "Path | str | None" = None,
) -> "Path":
    import re
    from pathlib import Path as _Path
    scenario_dir = _Path(scenario_dir).resolve()
    if corpus_root is None:
        corpus_root = scenario_dir.parent.parent / "corpus"
    corpus_root = _Path(corpus_root)
    m = re.match(r"arch(\d+)_", scenario_dir.name)
    if not m:
        raise ValueError(f"Cannot parse arch number from: {scenario_dir.name}")
    arch_num = m.group(1).zfill(2)
    candidates = [
        d for d in corpus_root.iterdir()
        if d.is_dir() and re.match(rf"arch_{arch_num}_", d.name)
    ]
    if not candidates:
        raise FileNotFoundError(
            f"No corpus directory for arch {arch_num} under {corpus_root}"
        )
    return candidates[0]
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_runner.py -k "corpus_dir_for_scenario" -v
```

Expected: both pass.

- [ ] **Step 5: Run full suite**

```bash
pytest tests/
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add harness/runner/context_builder.py tests/test_runner.py
git commit -m "feat(runner): add corpus_dir_for_scenario helper to context_builder"
```

---

### Task 2: Add `run_functional_tests()` to `ScenarioRunner`

**Files:**
- Modify: `harness/runner/scenario_runner.py`

Runs `functional_test.py` via `subprocess` with `pytest -v --tb=line -q`, parses the output into per-test pass/fail entries, and returns a structured dict. A module-level `_parse_pytest_output(output: str) -> dict` does the parsing so it can be unit-tested independently.

- [ ] **Step 1: Write failing tests**

In `tests/test_runner.py`, add:

```python
from unittest.mock import MagicMock
from harness.runner.scenario_runner import ScenarioRunner, _parse_pytest_output

def test_parse_pytest_output_all_passing():
    output = (
        "PASSED functional_test.py::test_friend_request\n"
        "PASSED functional_test.py::test_read_list\n"
        "2 passed in 1.23s\n"
    )
    result = _parse_pytest_output(output)
    assert result["all_passed"] is True
    assert len(result["passed"]) == 2
    assert len(result["failed"]) == 0
    assert result["passed"][0]["name"] == "test_friend_request"

def test_parse_pytest_output_with_failures():
    output = (
        "PASSED functional_test.py::test_friend_request\n"
        "FAILED functional_test.py::test_accept_state\n"
        "E   AssertionError: state 'Requested' != 'Friends'\n"
        "1 passed, 1 failed in 2.00s\n"
    )
    result = _parse_pytest_output(output)
    assert result["all_passed"] is False
    assert len(result["passed"]) == 1
    assert len(result["failed"]) == 1
    assert result["failed"][0]["name"] == "test_accept_state"
    assert "Requested" in result["failed"][0]["short_error"]

def test_run_functional_tests_calls_subprocess(tmp_path, mocker):
    runner = ScenarioRunner(str(tmp_path), "run-test")
    mocker.patch(
        "harness.runner.scenario_runner.corpus_dir_for_scenario",
        return_value=tmp_path / "corpus" / "arch_01_x",
    )
    mocker.patch(
        "subprocess.run",
        return_value=MagicMock(
            returncode=0,
            stdout="PASSED functional_test.py::test_x\n1 passed in 0.5s\n",
            stderr="",
        ),
    )
    result = runner.run_functional_tests()
    assert result["all_passed"] is True
    assert result["passed"][0]["name"] == "test_x"
```

- [ ] **Step 2: Run to confirm tests fail**

```bash
pytest tests/test_runner.py -k "parse_pytest_output or run_functional_tests" -v
```

Expected: `ImportError` — `_parse_pytest_output` and `run_functional_tests` do not exist yet.

- [ ] **Step 3: Implement in `scenario_runner.py`**

Add these imports at the top (if not already present):

```python
import subprocess
import re as _re
from harness.runner.context_builder import corpus_dir_for_scenario
```

Add the module-level parser before the `ScenarioRunner` class:

```python
def _parse_pytest_output(output: str) -> dict:
    passed, failed = [], []
    lines = output.splitlines()
    for i, line in enumerate(lines):
        m = _re.match(r"(PASSED|FAILED)\s+\S+::(\w+)", line)
        if not m:
            continue
        status, name = m.group(1), m.group(2)
        entry = {"name": name, "description": ""}
        if status == "PASSED":
            passed.append(entry)
        else:
            short_error = ""
            for j in range(i + 1, min(i + 10, len(lines))):
                err = lines[j].strip().lstrip("E").strip()
                if err.startswith(("AssertionError", "KeyError", "ValueError", "TypeError")):
                    short_error = err
                    break
            entry["short_error"] = short_error
            failed.append(entry)
    return {"all_passed": len(failed) == 0, "passed": passed, "failed": failed}
```

Add the method to `ScenarioRunner`:

```python
def run_functional_tests(self) -> dict:
    corpus_dir = corpus_dir_for_scenario(self.scenario_dir)
    functional_test = corpus_dir / "functional_test.py"
    proc = subprocess.run(
        [
            "python", "-m", "pytest", str(functional_test),
            "-v", "--tb=line", "--no-header", "-q",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    return _parse_pytest_output(proc.stdout + "\n" + proc.stderr)
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_runner.py -k "parse_pytest_output or run_functional_tests" -v
```

Expected: all three pass.

- [ ] **Step 5: Run full suite**

```bash
pytest tests/
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add harness/runner/scenario_runner.py tests/test_runner.py
git commit -m "feat(runner): add run_functional_tests() with per-test pass/fail parsing"
```

---

### Task 3: Add `attempt_redeployment()` to `ScenarioRunner`

**Files:**
- Modify: `harness/runner/scenario_runner.py`

Identical to `attempt_deployment()` but skips the `self.submitted` guard and never sets `self.submitted`. Used exclusively for test-retry redeploys after the initial `attempt_deployment` has already set `submitted = True`.

- [ ] **Step 1: Write failing tests**

In `tests/test_runner.py`, add:

```python
def test_attempt_redeployment_runs_when_already_submitted(tmp_path, mocker):
    runner = ScenarioRunner(str(tmp_path), "run-xyz")
    runner.submitted = True
    mocker.patch(
        "harness.runner.scenario_runner.handle_submission",
        return_value={"outcome": "deploy_success"},
    )
    result = runner.attempt_redeployment()
    assert result["success"] is True
    assert runner.submitted is True  # unchanged

def test_attempt_redeployment_never_sets_submitted_on_success(tmp_path, mocker):
    runner = ScenarioRunner(str(tmp_path), "run-xyz")
    runner.submitted = False
    mocker.patch(
        "harness.runner.scenario_runner.handle_submission",
        return_value={"outcome": "deploy_success"},
    )
    result = runner.attempt_redeployment()
    assert result["success"] is True
    assert runner.submitted is False  # not touched

def test_attempt_redeployment_returns_failure_dict(tmp_path, mocker):
    runner = ScenarioRunner(str(tmp_path), "run-xyz")
    mocker.patch(
        "harness.runner.scenario_runner.handle_submission",
        return_value={"outcome": "no_changes", "error": "no changes detected"},
    )
    result = runner.attempt_redeployment()
    assert result["success"] is False
    assert "no changes" in result["error"]
```

- [ ] **Step 2: Run to confirm tests fail**

```bash
pytest tests/test_runner.py -k "attempt_redeployment" -v
```

Expected: `AttributeError`.

- [ ] **Step 3: Implement in `scenario_runner.py`**

Add after `attempt_deployment`:

```python
def attempt_redeployment(self) -> dict:
    result = handle_submission(self.scenario_dir, self.run_id, self.start_snapshot)
    outcome = result.get("outcome", "unknown")
    return {
        "success": outcome == "deploy_success",
        "error": result.get("error", outcome),
        "result": result,
    }
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_runner.py -k "attempt_redeployment" -v
```

Expected: all three pass.

- [ ] **Step 5: Run full suite**

```bash
pytest tests/
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add harness/runner/scenario_runner.py tests/test_runner.py
git commit -m "feat(runner): add attempt_redeployment() for post-test-failure redeploy"
```

---

### Task 4: Add test-retry logic to `loop.py`

**Files:**
- Modify: `harness/agent/loop.py`

Four changes: (1) new parameters, (2) new counters + `_format_test_summary` helper, (3) verify step wired into submit_fix success path, (4) compound loop-exit condition.

- [ ] **Step 1: Write failing tests**

In `tests/test_agent_loop.py`, add:

```python
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from harness.agent.loop import run_agent_loop

@pytest.mark.asyncio
async def test_loop_calls_verify_after_successful_deploy(tmp_path):
    """verify_callback fires once when deploy succeeds and tests pass."""
    (tmp_path / "deployment").mkdir()
    (tmp_path / "faulted.yaml").write_text(
        "AWSTemplateFormatVersion: '2010-09-09'\nResources:\n  X:\n    Type: AWS::CloudFormation::WaitConditionHandle\n"
    )
    deploy_cb = MagicMock(return_value={"success": True})
    verify_cb = MagicMock(return_value={"all_passed": True, "passed": [], "failed": []})

    # write_file then submit_fix
    responses = _make_write_submit_responses(tmp_path)  # use existing test helper pattern
    with patch("litellm.completion", side_effect=responses):
        await run_agent_loop(
            ...,
            deploy_callback=deploy_cb,
            verify_callback=verify_cb,
        )
    assert verify_cb.call_count == 1


@pytest.mark.asyncio
async def test_loop_continues_on_test_failure_then_exits_on_pass(tmp_path):
    """Loop injects test summary on failure and exits when tests pass on retry."""
    (tmp_path / "deployment").mkdir()
    (tmp_path / "faulted.yaml").write_text(
        "AWSTemplateFormatVersion: '2010-09-09'\nResources:\n  X:\n    Type: AWS::CloudFormation::WaitConditionHandle\n"
    )
    deploy_cb = MagicMock(return_value={"success": True})
    redeploy_cb = MagicMock(return_value={"success": True})
    verify_results = [
        {"all_passed": False, "passed": [], "failed": [{"name": "test_x", "description": "", "short_error": "AssertionError"}]},
        {"all_passed": True, "passed": [{"name": "test_x", "description": ""}], "failed": []},
    ]
    verify_cb = MagicMock(side_effect=verify_results)

    responses = _make_two_write_submit_responses(tmp_path)
    with patch("litellm.completion", side_effect=responses):
        await run_agent_loop(
            ...,
            deploy_callback=deploy_cb,
            redeploy_callback=redeploy_cb,
            verify_callback=verify_cb,
            max_test_retries=5,
        )
    assert verify_cb.call_count == 2
    assert redeploy_cb.call_count == 1


@pytest.mark.asyncio
async def test_loop_exits_after_max_test_retries(tmp_path):
    """Loop exits after max_test_retries even if tests keep failing."""
    (tmp_path / "deployment").mkdir()
    (tmp_path / "faulted.yaml").write_text(
        "AWSTemplateFormatVersion: '2010-09-09'\nResources:\n  X:\n    Type: AWS::CloudFormation::WaitConditionHandle\n"
    )
    deploy_cb = MagicMock(return_value={"success": True})
    redeploy_cb = MagicMock(return_value={"success": True})
    verify_cb = MagicMock(return_value={
        "all_passed": False,
        "passed": [],
        "failed": [{"name": "test_x", "description": "", "short_error": "AssertionError"}],
    })

    responses = _make_n_write_submit_responses(tmp_path, n=6)  # 1 initial + 5 retries
    with patch("litellm.completion", side_effect=responses):
        await run_agent_loop(
            ...,
            deploy_callback=deploy_cb,
            redeploy_callback=redeploy_cb,
            verify_callback=verify_cb,
            max_test_retries=5,
        )
    assert verify_cb.call_count == 6


@pytest.mark.asyncio
async def test_loop_routes_retry_submit_to_redeploy_callback(tmp_path):
    """First submit_fix uses deploy_callback; subsequent retries use redeploy_callback."""
    (tmp_path / "deployment").mkdir()
    (tmp_path / "faulted.yaml").write_text(
        "AWSTemplateFormatVersion: '2010-09-09'\nResources:\n  X:\n    Type: AWS::CloudFormation::WaitConditionHandle\n"
    )
    deploy_cb = MagicMock(return_value={"success": True})
    redeploy_cb = MagicMock(return_value={"success": True})
    verify_results = [
        {"all_passed": False, "passed": [], "failed": [{"name": "t", "description": "", "short_error": "err"}]},
        {"all_passed": True, "passed": [], "failed": []},
    ]
    verify_cb = MagicMock(side_effect=verify_results)

    responses = _make_two_write_submit_responses(tmp_path)
    with patch("litellm.completion", side_effect=responses):
        await run_agent_loop(
            ...,
            deploy_callback=deploy_cb,
            redeploy_callback=redeploy_cb,
            verify_callback=verify_cb,
            max_test_retries=5,
        )
    assert deploy_cb.call_count == 1
    assert redeploy_cb.call_count == 1
```

> **Implementation note for helpers:** `_make_write_submit_responses(tmp_path)` and `_make_two_write_submit_responses(tmp_path)` follow the same mock-response pattern already in `test_agent_loop.py`. Each "cycle" is one `write_file` tool call followed by one `submit_fix` tool call. Wire them using the fixture helpers already present in the file.

- [ ] **Step 2: Run to confirm tests fail**

```bash
pytest tests/test_agent_loop.py -k "verify or test_retry or redeploy_callback" -v
```

Expected: `TypeError: run_agent_loop() got unexpected keyword argument 'verify_callback'`.

- [ ] **Step 3: Update `run_agent_loop` signature**

```python
async def run_agent_loop(
    client,
    model: str,
    context: dict,
    mcp_tools: list,
    scenario_dir: str,
    verbose: bool = False,
    max_turns: int = 30,
    deploy_callback=None,
    max_deploy_retries: int = 5,
    redeploy_callback=None,      # NEW
    verify_callback=None,         # NEW
    max_test_retries: int = 5,   # NEW
):
```

- [ ] **Step 4: Add counters after existing counters**

After `writes_since_last_submit = 0`, add:

```python
all_tests_passed = verify_callback is None  # True by default if no verify step configured
test_retry_count = 0
```

- [ ] **Step 5: Add `_format_test_summary` as a module-level helper**

Add before `run_agent_loop`:

```python
def _format_test_summary(result: dict, attempt: int, max_retries: int) -> str:
    n_passed = len(result.get("passed", []))
    n_failed = len(result.get("failed", []))
    lines = [f"Tests: {n_passed} passed, {n_failed} failed."]
    for t in result.get("passed", []):
        desc = f": {t['description']}" if t.get("description") else ""
        lines.append(f"passed ({t['name']}{desc})")
    for t in result.get("failed", []):
        desc = f": {t['description']}" if t.get("description") else ""
        err = f" — {t['short_error']}" if t.get("short_error") else ""
        lines.append(f"failed ({t['name']}{desc}{err})")
    lines.append(
        f"Revise your fix with write_file and call submit_fix again. "
        f"(Attempt {attempt} of {max_retries}.)"
    )
    return "\n".join(lines)
```

- [ ] **Step 6: Wire verify step into the submit_fix success path**

Find the block in loop.py where `deploy_result["success"]` is True and `submitted = True` / `content = "Fix deployed successfully."` is set. Extend it to:

```python
if deploy_result["success"]:
    submitted = True
    if verify_callback is not None:
        loop = asyncio.get_running_loop()
        verify_result = await loop.run_in_executor(None, verify_callback)
        if verify_result["all_passed"]:
            all_tests_passed = True
            content = "Fix deployed and all tests passed."
        elif test_retry_count >= max_test_retries:
            all_tests_passed = False
            content = (
                f"Maximum test retries ({max_test_retries}) reached. "
                + _format_test_summary(verify_result, test_retry_count, max_test_retries)
            )
        else:
            test_retry_count += 1
            writes_since_last_submit = 0
            content = _format_test_summary(
                verify_result, test_retry_count, max_test_retries
            )
    else:
        all_tests_passed = True
        content = "Fix deployed successfully."
```

- [ ] **Step 7: Route retry submit_fix calls to `redeploy_callback`**

In the submit_fix handler, select the active deploy callable before calling it:

```python
active_deploy_cb = (
    redeploy_callback
    if (test_retry_count > 0 and redeploy_callback is not None)
    else deploy_callback
)
deploy_result = await asyncio.get_running_loop().run_in_executor(
    None, active_deploy_cb
)
```

- [ ] **Step 8: Replace the loop-exit condition**

Find every `if submitted: break` in the turn loop and replace with:

```python
if submitted and (all_tests_passed or test_retry_count >= max_test_retries):
    break
```

- [ ] **Step 9: Run new tests**

```bash
pytest tests/test_agent_loop.py -k "verify or test_retry or redeploy_callback" -v
```

Expected: all four pass.

- [ ] **Step 10: Run full suite**

```bash
pytest tests/
```

Expected: all pass.

- [ ] **Step 11: Commit**

```bash
git add harness/agent/loop.py tests/test_agent_loop.py
git commit -m "feat(agent): add verify_callback test-retry loop (up to 5 retries) to run_agent_loop"
```

---

### Task 5: Wire callbacks in `run.py`

**Files:**
- Modify: `harness/run.py`

- [ ] **Step 1: Locate the `run_agent_loop` call**

Find the `asyncio.run(run_agent_loop(...))` block. It currently passes `deploy_callback=runner.attempt_deployment`.

- [ ] **Step 2: Add new callbacks**

```python
asyncio.run(
    run_agent_loop(
        client=client,
        model=model,
        context=context,
        mcp_tools=mcp_tools,
        scenario_dir=scenario_dir,
        verbose=args.verbose,
        deploy_callback=runner.attempt_deployment,
        max_deploy_retries=5,
        redeploy_callback=runner.attempt_redeployment,  # NEW
        verify_callback=runner.run_functional_tests,     # NEW
        max_test_retries=5,                              # NEW
    )
)
```

- [ ] **Step 3: Run tests**

```bash
pytest tests/test_agent_loop.py tests/test_runner.py -v
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add harness/run.py
git commit -m "feat(run): wire verify_callback and redeploy_callback for test-retry loop"
```

---

## Self-Review

**Spec coverage:**
- Functional tests run after each successful deploy: Task 4 Step 6 ✓
- Results injected as `passed (...) / failed (...)` format: `_format_test_summary` (Task 4 Step 5) ✓
- 5 test retries separate from 5 deploy retries: `max_test_retries` param + `test_retry_count` counter (Task 4 Steps 3–4) ✓
- Test source code never exposed to model: tests run via subprocess; only `name + description + short_error` returned ✓
- `attempt_redeployment` bypasses submitted guard and never sets `submitted`: Task 3 ✓
- `verify_callback=None` degrades gracefully (`all_tests_passed = True` on init): Task 4 Step 4 ✓
- First `submit_fix` uses `deploy_callback`; retries use `redeploy_callback`: Task 4 Step 7 ✓
- Loop only exits when `all_tests_passed OR test_retry_count >= max_test_retries`: Task 4 Step 8 ✓
- `writes_since_last_submit` resets to 0 on each failed verify (prevents no-write retry): Task 4 Step 6 ✓

**Interaction with Track B (deploy retries):** Deploy retries and test retries are independent counters. `retry_count` (deploy) and `test_retry_count` (test) are incremented separately. Worst-case total `submit_fix` calls = `(1 + max_deploy_retries) + max_test_retries` = 11, comfortably within `max_turns=30`.

**Placeholder scan:** Task 4 Step 1 test helpers (`_make_write_submit_responses`, `_make_two_write_submit_responses`, `_make_n_write_submit_responses`) reference the existing mock-response fixture pattern in `test_agent_loop.py`. The assertions and intent are fully specified; only the mock-response wiring to existing helpers is left to the implementer.

**Type consistency:**
- `verify_callback` → synchronous callable, no args, returns `{"all_passed": bool, "passed": list[dict], "failed": list[dict]}`, called via `run_in_executor` ✓
- `redeploy_callback` → same signature as `deploy_callback`, returns `{"success": bool, "error": str}` ✓
- `_format_test_summary` → pure function, no I/O ✓
- `_parse_pytest_output` → pure function, module-level, independently testable ✓
