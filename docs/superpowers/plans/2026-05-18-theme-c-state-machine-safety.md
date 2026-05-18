# Theme C: State-Machine Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three bugs where the submission/deployment state machine can enter an incorrect or unrecoverable state: a crash-window during manifest hiding leaves the scenario unrunnable on restart; `submitted.yaml` is read by Pass 3 but never written; and a failed retry deploy silently overwrites the successful initial-deploy outcome used for scoring.

**Architecture:** Three independent surgical fixes — (1) idempotent startup recovery for the manifest rename race, (2) snapshot `faulted.yaml` → `submitted.yaml` at the moment `submitted=True` is set, (3) a separate `initial_deployment_outcome` field that freezes the scored outcome at first success and a `_resolve_deployment_outcome()` helper in `run.py`. No new modules. All changes are additive with unit-test coverage; no behavioral change on the happy path.

**Tech Stack:** Python 3.11, pytest, pytest-mock (existing tooling).

---

## Background — Three Bugs

### Bug 4.7: Manifest rename race condition

`harness/run.py` lines 314–324 temporarily rename `fault_manifest.json` → `fault_manifest.json.hidden` before calling `build_context` (which raises `ValueError` if the manifest is readable in `scenario_dir`), then restores it in a `finally` block. If the process receives SIGKILL, runs out of memory, or loses power between the rename (line 319) and the `finally` restoration (line 324), the `.hidden` file is left on disk with no real manifest. The next run calls `_validate_scenario` at line 282, which fails to find `fault_manifest.json` and exits with code 1 — with no recovery mechanism.

### Bug 4.10: `submitted.yaml` fallback is dead code

`harness/verify/pass3_classification.py` lines 73–78 read `results/<run_id>/submitted.yaml` if it exists, otherwise fall back to the live `faulted.yaml` (the model's edited version). But nothing in the harness ever writes `submitted.yaml`. Pass 3 therefore always reads the live `faulted.yaml`, and the fallback silently masks intent — if template editing ever moves locations, the classification would silently read the wrong file.

### Bug 4.13: Retry failure overwrites initial-deploy outcome

`harness/runner/scenario_runner.py:deploy()` stores `result.outcome` in `submission_state.last_outcome` on every call. After the sequence: (1) `is_initial=True` deploy succeeds → `submitted=True`, `last_outcome="deploy_success"`; (2) tests fail; (3) `is_initial=False` redeploy fails → `last_outcome="deploy_fail"`. The `run.py` Step 8 check at line 364 (`if runner._last_deployment_outcome == "unknown"`) passes because the outcome is `"deploy_fail"`, not `"unknown"`. Step 9 then passes `"deploy_fail"` to `run_verify_loop` as if the fix never deployed — even though the model's submission did deploy and should be evaluated.

---

## File Structure

Changes are confined to four production files and one test file:

- **Modify** `harness/run.py` — add `_recover_hidden_manifest()` called before `_validate_scenario`; add `_resolve_deployment_outcome()` helper; use it in Step 9.
- **Modify** `harness/shared/types.py` — add `initial_deployment_outcome` field to `SubmissionState`.
- **Modify** `harness/runner/scenario_runner.py` — freeze `initial_deployment_outcome` when `submitted=True`; write `submitted.yaml`; add property accessor.
- **Modify** `tests/test_runner.py` — new tests for all three fixes.

No new files. No reorganization.

---

## Task 1: Manifest rename recovery (Bug 4.7)

**Files:**
- Modify: `harness/run.py`
- Test: `tests/test_runner.py`

### Why

`_validate_scenario` runs at scenario startup and exits with code 1 if `fault_manifest.json` is missing. A crash between the `os.rename` call and the `finally` restoration leaves the scenario permanently unrunnable — no user-visible error explains that the `.hidden` file is the culprit. The fix is an idempotent one-liner called before validation: if the real manifest is absent but the `.hidden` artifact exists, rename it back.

### Behavior change

New `_recover_hidden_manifest(scenario_dir)` function in `run.py`. Called as the very first step in `main()` before `_validate_scenario`. Idempotent: does nothing if the manifest is present, does nothing if neither file exists; only acts if `.hidden` is present without a real manifest.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_runner.py`:

```python
from harness.run import _recover_hidden_manifest


def test_recover_hidden_manifest_restores_when_only_hidden_exists(tmp_path):
    hidden = tmp_path / "fault_manifest.json.hidden"
    hidden.write_text('{"architecture": "arch_01"}')
    _recover_hidden_manifest(str(tmp_path))
    assert (tmp_path / "fault_manifest.json").exists()
    assert not hidden.exists()


def test_recover_hidden_manifest_no_op_when_manifest_present(tmp_path):
    real = tmp_path / "fault_manifest.json"
    real.write_text("{}")
    hidden = tmp_path / "fault_manifest.json.hidden"
    hidden.write_text("{}")
    _recover_hidden_manifest(str(tmp_path))
    # Both still present — function does not touch them when real manifest exists.
    assert real.exists()
    assert hidden.exists()


def test_recover_hidden_manifest_no_op_when_neither_exists(tmp_path):
    _recover_hidden_manifest(str(tmp_path))  # must not raise
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/test_runner.py::test_recover_hidden_manifest_restores_when_only_hidden_exists tests/test_runner.py::test_recover_hidden_manifest_no_op_when_manifest_present tests/test_runner.py::test_recover_hidden_manifest_no_op_when_neither_exists -v
```

Expected: `ImportError` — `_recover_hidden_manifest` does not yet exist.

- [ ] **Step 3: Add `_recover_hidden_manifest` to `run.py`**

Add the function after the existing `_validate_scenario` function definition (around line 35):

```python
def _recover_hidden_manifest(scenario_dir: str) -> None:
    manifest = os.path.join(scenario_dir, "fault_manifest.json")
    hidden = manifest + ".hidden"
    if not os.path.isfile(manifest) and os.path.isfile(hidden):
        os.rename(hidden, manifest)
```

Then call it as the very first statement inside `main()` before `_validate_scenario`, replacing the current line 282:

```python
    # Step 3 — validate scenario directory structure
    _recover_hidden_manifest(scenario_dir)
    _validate_scenario(scenario_dir)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
pytest tests/test_runner.py -k "recover_hidden_manifest" -v
```

Expected: 3 passed.

- [ ] **Step 5: Run the full test suite to confirm no regression**

```bash
pytest tests/test_runner.py -v
```

Expected: all PASS.

---

## Task 2: Write `submitted.yaml` on successful initial deploy (Bug 4.10)

**Files:**
- Modify: `harness/runner/scenario_runner.py`
- Test: `tests/test_runner.py`

### Why

`pass3_classification.run_pass3()` uses `submitted.yaml` to classify the exact template the model submitted — not the current on-disk version, which might have been mutated by later retry edits. Since nothing writes it, Pass 3 always reads the live `faulted.yaml`. If the model makes multiple edits and retries, the classification sees the *last* retry's template, not the one that was actually scored. Writing it at the exact moment `submitted=True` is set guarantees Pass 3 reads the correct snapshot.

### Behavior change

In `deploy()`, immediately after `self.submission_state.submitted = True` is set (line 184), copy `faulted.yaml` from the scenario directory to `results/<run_id>/submitted.yaml`. Uses `shutil.copy2` (preserves metadata). The `results/<run_id>/` directory is already created by `init_run` before `deploy()` is ever called.

- [ ] **Step 1: Write failing test**

Append to `tests/test_runner.py`:

```python
def test_deploy_initial_writes_submitted_yaml(tmp_path, mocker):
    mocker.patch("harness.runner.scenario_runner.init_run")
    mocker.patch("harness.runner.scenario_runner.snapshot", return_value={})
    # Create a minimal faulted.yaml in the scenario dir (tmp_path acts as scenario_dir).
    (tmp_path / "faulted.yaml").write_text("AWSTemplateFormatVersion: '2010-09-09'\n")
    # Create the results/run-xyz/ directory (normally done by init_run).
    results_dir = tmp_path / "results" / "run-xyz"
    results_dir.mkdir(parents=True)
    mocker.patch(
        "harness.runner.scenario_runner.handle_submission",
        return_value=DeploymentResult(outcome="deploy_success"),
    )
    # Patch RESULTS_DIR so the runner writes into tmp_path/results/.
    mocker.patch("harness.runner.scenario_runner.RESULTS_DIR", str(tmp_path / "results"))
    runner = ScenarioRunner(str(tmp_path), "run-xyz")
    runner.deploy(is_initial=True)
    submitted_yaml = tmp_path / "results" / "run-xyz" / "submitted.yaml"
    assert submitted_yaml.exists(), "submitted.yaml must be written on initial deploy success"
    assert "AWSTemplateFormatVersion" in submitted_yaml.read_text()


def test_deploy_initial_does_not_write_submitted_yaml_on_failure(tmp_path, mocker):
    mocker.patch("harness.runner.scenario_runner.init_run")
    mocker.patch("harness.runner.scenario_runner.snapshot", return_value={})
    (tmp_path / "faulted.yaml").write_text("AWSTemplateFormatVersion: '2010-09-09'\n")
    results_dir = tmp_path / "results" / "run-xyz"
    results_dir.mkdir(parents=True)
    mocker.patch(
        "harness.runner.scenario_runner.handle_submission",
        return_value=DeploymentResult(outcome="no_changes", error="no diff"),
    )
    mocker.patch("harness.runner.scenario_runner.RESULTS_DIR", str(tmp_path / "results"))
    runner = ScenarioRunner(str(tmp_path), "run-xyz")
    runner.deploy(is_initial=True)
    submitted_yaml = tmp_path / "results" / "run-xyz" / "submitted.yaml"
    assert not submitted_yaml.exists(), "submitted.yaml must NOT be written on deploy failure"
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/test_runner.py::test_deploy_initial_writes_submitted_yaml tests/test_runner.py::test_deploy_initial_does_not_write_submitted_yaml_on_failure -v
```

Expected: FAIL — `submitted.yaml` is never written.

- [ ] **Step 3: Update `scenario_runner.py`**

Add `import shutil` at the top if not already present. Then also add `RESULTS_DIR = "results"` as a module-level constant (so the test can patch it), if not already present.

In `deploy()`, replace the block (currently lines 182–185):

```python
        if is_initial and result.success:
            with self._lock:
                self.submission_state.submitted = True
```

with:

```python
        if is_initial and result.success:
            with self._lock:
                self.submission_state.submitted = True
            submitted_yaml_dst = os.path.join(
                RESULTS_DIR, self.run_id, "submitted.yaml"
            )
            shutil.copy2(
                os.path.join(self.scenario_dir, "faulted.yaml"),
                submitted_yaml_dst,
            )
```

- [ ] **Step 4: Run the new tests to verify they pass**

```bash
pytest tests/test_runner.py -k "submitted_yaml" -v
```

Expected: 2 passed.

- [ ] **Step 5: Run the full test suite to confirm no regression**

```bash
pytest tests/test_runner.py -v
```

Expected: all PASS.

---

## Task 3: Freeze initial deployment outcome, fix verify-loop outcome source (Bug 4.13)

**Files:**
- Modify: `harness/shared/types.py` (`SubmissionState`)
- Modify: `harness/runner/scenario_runner.py` (`deploy()`, new property)
- Modify: `harness/run.py` (add `_resolve_deployment_outcome`, use it in Step 9)
- Test: `tests/test_runner.py`

### Why

`run_verify_loop` must receive the outcome of the deployment that was *actually evaluated* — the initial successful submit. After a retry failure, `submission_state.last_outcome` is `"deploy_fail"` even though `submitted=True`, and Step 9 passes that stale outcome to the verify loop. The verify loop interprets `"deploy_fail"` as "nothing was deployed" and runs accordingly, misclassifying the model's result.

The fix adds a separate `initial_deployment_outcome` field that is set once and never overwritten. A `_resolve_deployment_outcome()` helper in `run.py` selects the correct outcome:
- If `submitted=True`: use `initial_deployment_outcome` (the locked value).
- If not submitted and outcome is still `"unknown"`: use `"no_submission"`.
- Otherwise: use `last_outcome` as-is.

### Behavior change

`SubmissionState` gains a new `initial_deployment_outcome: DeploymentOutcome = "unknown"` field. `deploy()` sets it alongside `submitted=True` (it is never set by retry paths). `run.py` Step 8 is replaced by a call to `_resolve_deployment_outcome(runner)` and Step 9 uses the returned value.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_runner.py`:

```python
from harness.run import _resolve_deployment_outcome


def test_resolve_outcome_returns_initial_when_submitted(tmp_path, mocker):
    mocker.patch("harness.runner.scenario_runner.init_run")
    mocker.patch("harness.runner.scenario_runner.snapshot", return_value={})
    runner = ScenarioRunner(str(tmp_path), "run-xyz")
    # Simulate: initial deploy succeeded, retry then failed.
    runner.submission_state.submitted = True
    runner.submission_state.initial_deployment_outcome = "deploy_success"
    runner.submission_state.last_outcome = "deploy_fail"
    assert _resolve_deployment_outcome(runner) == "deploy_success"


def test_resolve_outcome_returns_no_submission_when_never_deployed(tmp_path, mocker):
    mocker.patch("harness.runner.scenario_runner.init_run")
    mocker.patch("harness.runner.scenario_runner.snapshot", return_value={})
    runner = ScenarioRunner(str(tmp_path), "run-xyz")
    # Default state: nothing deployed yet.
    assert runner.submission_state.submitted is False
    assert runner.submission_state.last_outcome == "unknown"
    assert _resolve_deployment_outcome(runner) == "no_submission"


def test_resolve_outcome_returns_last_outcome_when_not_submitted_but_deployed(tmp_path, mocker):
    mocker.patch("harness.runner.scenario_runner.init_run")
    mocker.patch("harness.runner.scenario_runner.snapshot", return_value={})
    runner = ScenarioRunner(str(tmp_path), "run-xyz")
    # A deploy happened but failed — submitted flag never set.
    runner.submission_state.submitted = False
    runner.submission_state.last_outcome = "deploy_fail"
    assert _resolve_deployment_outcome(runner) == "deploy_fail"


def test_resolve_outcome_returns_deploy_success_on_clean_submission(tmp_path, mocker):
    mocker.patch("harness.runner.scenario_runner.init_run")
    mocker.patch("harness.runner.scenario_runner.snapshot", return_value={})
    runner = ScenarioRunner(str(tmp_path), "run-xyz")
    runner.submission_state.submitted = True
    runner.submission_state.initial_deployment_outcome = "deploy_success"
    runner.submission_state.last_outcome = "deploy_success"
    assert _resolve_deployment_outcome(runner) == "deploy_success"
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/test_runner.py -k "resolve_outcome" -v
```

Expected: `AttributeError` or `ImportError` — neither the field nor the helper exist yet.

- [ ] **Step 3: Add `initial_deployment_outcome` to `SubmissionState`**

In `harness/shared/types.py`, update `SubmissionState` (currently lines 70–74):

```python
@dataclass
class SubmissionState:
    """Persistent state across submit_fix attempts within one scenario run."""
    submitted: bool = False
    last_outcome: DeploymentOutcome = "unknown"
    deploy_attempts: int = 0
    initial_deployment_outcome: DeploymentOutcome = "unknown"
```

- [ ] **Step 4: Freeze `initial_deployment_outcome` in `deploy()`**

In `harness/runner/scenario_runner.py`, update the `is_initial and result.success` block (whether or not Task 2 has already landed — add the new line alongside `submitted=True` inside the lock):

```python
        if is_initial and result.success:
            with self._lock:
                self.submission_state.submitted = True
                self.submission_state.initial_deployment_outcome = result.outcome
            submitted_yaml_dst = os.path.join(
                RESULTS_DIR, self.run_id, "submitted.yaml"
            )
            shutil.copy2(
                os.path.join(self.scenario_dir, "faulted.yaml"),
                submitted_yaml_dst,
            )
```

Then add a property accessor below `_last_deployment_outcome`:

```python
    @property
    def _initial_deployment_outcome(self) -> str:
        return self.submission_state.initial_deployment_outcome
```

- [ ] **Step 5: Add `_resolve_deployment_outcome` to `run.py` and wire into Step 9**

Add the helper function after `_recover_hidden_manifest` in `run.py`:

```python
def _resolve_deployment_outcome(runner) -> str:
    """Select the deployment outcome to pass to run_verify_loop.

    Once submitted=True, the scored outcome is locked to the initial deploy
    so retry failures cannot retroactively change what is evaluated.
    """
    if runner.submission_state.submitted:
        return runner._initial_deployment_outcome
    if runner._last_deployment_outcome == "unknown":
        return "no_submission"
    return runner._last_deployment_outcome
```

Then replace Step 8 in `main()` (currently lines 362–365 and 368–371):

```python
    # Step 8 — resolve deployment outcome for verify loop
    # Once submitted=True, use the initial deploy outcome (locked); never let
    # a failed retry overwrite it. If nothing deployed at all, record no_submission.
    _deployment_outcome = _resolve_deployment_outcome(runner)

    # Step 9 — verify loop
    verify_result = run_verify_loop(
        scenario_dir=scenario_dir,
        run_id=run_id,
        deployment_outcome=_deployment_outcome,
        manifest_path=manifest_path,
        corpus_dir=corpus_dir,
        api_endpoint=ctx["stack_outputs"].get("ApiEndpoint", ""),
    )
```

Remove the old lines:
```python
    if runner._last_deployment_outcome == "unknown":
        runner._last_deployment_outcome = "no_submission"
```

- [ ] **Step 6: Run the resolve_outcome tests to verify they pass**

```bash
pytest tests/test_runner.py -k "resolve_outcome" -v
```

Expected: 4 passed.

- [ ] **Step 7: Verify `deploy()` sets `initial_deployment_outcome` correctly**

Append a targeted test:

```python
def test_deploy_initial_freezes_initial_deployment_outcome(tmp_path, mocker):
    mocker.patch("harness.runner.scenario_runner.init_run")
    mocker.patch("harness.runner.scenario_runner.snapshot", return_value={})
    mocker.patch(
        "harness.runner.scenario_runner.handle_submission",
        return_value=DeploymentResult(outcome="deploy_success"),
    )
    mocker.patch("harness.runner.scenario_runner.RESULTS_DIR", str(tmp_path / "results"))
    (tmp_path / "results" / "run-xyz").mkdir(parents=True)
    (tmp_path / "faulted.yaml").write_text("AWSTemplateFormatVersion: '2010-09-09'\n")
    runner = ScenarioRunner(str(tmp_path), "run-xyz")
    runner.deploy(is_initial=True)
    assert runner.submission_state.initial_deployment_outcome == "deploy_success"

    # Simulate a retry that fails — initial_deployment_outcome must stay frozen.
    mocker.patch(
        "harness.runner.scenario_runner.handle_submission",
        return_value=DeploymentResult(outcome="deploy_fail"),
    )
    runner.deploy(is_initial=False)
    assert runner.submission_state.last_outcome == "deploy_fail"
    assert runner.submission_state.initial_deployment_outcome == "deploy_success"
```

```bash
pytest tests/test_runner.py::test_deploy_initial_freezes_initial_deployment_outcome -v
```

Expected: PASS.

- [ ] **Step 8: Run the full runner test suite**

```bash
pytest tests/test_runner.py -v
```

Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add harness/shared/types.py harness/runner/scenario_runner.py harness/run.py tests/test_runner.py
git commit -m "$(cat <<'EOF'
fix(runner): state-machine safety — manifest recovery, submitted.yaml, outcome lock

Three surgical fixes to the submission/deployment state machine:

1. _recover_hidden_manifest(): idempotent startup recovery for the
   manifest rename race. If fault_manifest.json is missing but the
   .hidden artifact exists (from a SIGKILL mid-rename), restores it
   before _validate_scenario runs.

2. submitted.yaml snapshot: deploy() now writes faulted.yaml to
   results/<run_id>/submitted.yaml when submitted=True is set, making
   the pass3_classification fallback live instead of dead code.

3. initial_deployment_outcome: SubmissionState gains a new field that
   freezes the scored outcome at first success. _resolve_deployment_outcome()
   in run.py uses this field when submitted=True, so a failed retry cannot
   retroactively pass "deploy_fail" to run_verify_loop.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Final Verification

- [ ] **Run the full test suite**

```bash
pytest tests/ -v
```

Expected: 0 failures.

- [ ] **Confirm the three bugs are closed**

```bash
git log --oneline -3
```

Expected: one commit with the three-fix message above.

---

## Findings Reference (audit breakpoints)

| # | Bug | Location | Fix |
|---|-----|----------|-----|
| 4.7 | Manifest rename race | `harness/run.py` lines 319/324 | `_recover_hidden_manifest()` before `_validate_scenario` |
| 4.10 | `submitted.yaml` never written | `harness/runner/scenario_runner.py:deploy()` | `shutil.copy2` after `submitted=True` |
| 4.13 | Retry failure overwrites initial outcome | `harness/run.py` Step 8/9, `SubmissionState` | `initial_deployment_outcome` field + `_resolve_deployment_outcome()` |
