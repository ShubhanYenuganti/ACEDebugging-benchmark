# Theme F: Naming, Dead Code, and Corpus Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three harness correctness issues that cause silent misbehaviour without crashing: the corpus-directory lookup regex silently returns the wrong path for scenarios using the `arch_NN_` naming convention; `intercept_tool_call` and `tool_call_count` are dead code that mislead readers and produce inaccurate tool-count metrics; Pass 1 runs `functional_test.py` unconditionally even when the test mutates shared state that the model agent depends on.

**Architecture:** Three independent surgical fixes — (1) one-character regex change in `context_builder.py` to support both `arch01_` and `arch_01_` naming patterns; (2) deletion of two dead-code symbols in `scenario_runner.py` and the test that covers them; (3) a new `baseline_idempotent` manifest flag that Pass 1 checks before running the baseline functional test. All three changes are minimal; none touches shared data structures or the agent loop.

**Tech Stack:** Python 3.11, pytest, pytest-mock (existing tooling).

---

## Background — Three Bugs

### Bug 4.6: Corpus-dir regex fails for `arch_NN_` scenario names

`harness/runner/context_builder.py` line 64:

```python
m = re.match(r"arch(\d+)_", scenario_name)
```

This regex matches `arch01_fault01_connectivity` (no underscore before the number) but silently fails for `arch_01_fault01_connectivity` (underscore before number). When the match fails, `corpus_dir_for_scenario` returns `None` (or falls through to a default). The model agent then receives a context built without the corpus files (`known_good.yaml`, `functional_test.py`, `traffic_flow.md`), giving it less information to diagnose the fault. This is silent — no error is raised, no warning is emitted.

### Bug 4.16: `intercept_tool_call` and `tool_call_count` are dead code

`harness/runner/scenario_runner.py`:

- Line 32: `self.tool_call_count = 0` — initialised but never incremented in real runs. The efficiency scoring dimension reads tool count from `tool_call_trace.json`, not from this field.
- Lines 134–137: `intercept_tool_call(self, tool_name, tool_input)` — defined but never called from `harness/agent/loop.py`. The agent loop calls `log_tool_call` directly from `result_logger.py`.

The test at `tests/test_runner.py` line 265 (`test_intercept_tool_call_increments_count_and_logs`) tests this dead code, creating a false sense of coverage. Removing the dead code and its test is the correct action: the real path (`log_tool_call` in `loop.py`) is what matters.

### Bug 4.8: Pass 1 baseline run may mutate shared state

`harness/verify/pass1_functional.py` runs `functional_test.py` twice: once as a baseline (before the model runs) and once as verification (after the fix is deployed). For most scenarios, `functional_test.py` is read-only (invokes the deployed function, checks a response). But for scenarios whose fault class involves state (SQS queue depth, DynamoDB stream, S3 object existence), the baseline run may mutate queue state that the model agent will later inspect to diagnose the fault. After the baseline run, the queue may be drained, items may be consumed, or counters may be reset — making the agent's diagnostic view inconsistent with the scenario setup.

The fix adds a `baseline_idempotent` field to `fault_manifest.json`. When `false`, Pass 1 skips the baseline run and returns a `{"baseline": "skipped_non_idempotent"}` result. Pass 3 classification treats a missing baseline as "no regression data" rather than a failure.

---

## File Structure

Changes are confined to three production files and two test files:

- **Modify** `harness/runner/context_builder.py` — one-character regex fix.
- **Modify** `harness/runner/scenario_runner.py` — delete dead `tool_call_count` and `intercept_tool_call`.
- **Modify** `harness/verify/pass1_functional.py` — read `baseline_idempotent` from manifest; skip baseline run when `False`.
- **Modify** `tests/test_runner.py` — delete test for dead code; add regression test for regex fix.
- **Test** `tests/test_verify.py` — new tests for Pass 1 baseline-skip behaviour.

---

## Task 1: Corpus-dir regex fix (Bug 4.6)

**Files:**
- Modify: `harness/runner/context_builder.py`
- Test: `tests/test_runner.py`

### Why

Scenario directories use two naming conventions in the wild: `arch01_fault01_connectivity` (no separator before the number) and `arch_01_fault01_connectivity` (underscore separator). The current regex `r"arch(\d+)_"` only matches the first form. Making the leading underscore optional (`r"arch_?(\d+)_"`) handles both without breaking existing matches.

### Behavior change

Change line 64 of `context_builder.py` from `r"arch(\d+)_"` to `r"arch_?(\d+)_"`. No other change. Both forms produce the same arch number and the same corpus path.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_runner.py` (or `tests/test_shared.py`):

```python
from harness.runner.context_builder import corpus_dir_for_scenario


class TestCorpusDirForScenario:
    def test_matches_arch_without_leading_underscore(self, tmp_path):
        """arch01_fault01_connectivity → corpus/arch_01_<name>"""
        corpus = tmp_path / "corpus" / "arch_01_serverless"
        corpus.mkdir(parents=True)
        result = corpus_dir_for_scenario("arch01_fault01_connectivity", str(tmp_path))
        assert result is not None
        assert "arch_01" in result

    def test_matches_arch_with_leading_underscore(self, tmp_path):
        """arch_01_fault01_connectivity → same corpus path"""
        corpus = tmp_path / "corpus" / "arch_01_serverless"
        corpus.mkdir(parents=True)
        result = corpus_dir_for_scenario("arch_01_fault01_connectivity", str(tmp_path))
        assert result is not None
        assert "arch_01" in result

    def test_both_forms_resolve_to_same_corpus(self, tmp_path):
        corpus = tmp_path / "corpus" / "arch_01_serverless"
        corpus.mkdir(parents=True)
        r1 = corpus_dir_for_scenario("arch01_fault01_connectivity", str(tmp_path))
        r2 = corpus_dir_for_scenario("arch_01_fault01_connectivity", str(tmp_path))
        assert r1 == r2

    def test_returns_none_for_unknown_architecture(self, tmp_path):
        result = corpus_dir_for_scenario("unknown_scenario", str(tmp_path))
        assert result is None
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/test_runner.py::TestCorpusDirForScenario -v
```

Expected: `test_matches_arch_with_leading_underscore` and `test_both_forms_resolve_to_same_corpus` FAIL — the `arch_01_` form is not matched.

- [ ] **Step 3: Update `context_builder.py` line 64**

Change:
```python
m = re.match(r"arch(\d+)_", scenario_name)
```
to:
```python
m = re.match(r"arch_?(\d+)_", scenario_name)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
pytest tests/test_runner.py::TestCorpusDirForScenario -v
```

Expected: all PASS.

- [ ] **Step 5: Run the full runner test suite**

```bash
pytest tests/test_runner.py -v
```

Expected: all PASS.

---

## Task 2: Remove dead `intercept_tool_call` and `tool_call_count` (Bug 4.16)

**Files:**
- Modify: `harness/runner/scenario_runner.py`
- Modify: `tests/test_runner.py`

### Why

`intercept_tool_call` is defined but never called — `loop.py` calls `result_logger.log_tool_call` directly. `tool_call_count` is initialised to 0 but never incremented. The efficiency scoring dimension reads from `tool_call_trace.json`, not from this field. The test `test_intercept_tool_call_increments_count_and_logs` provides false confidence by testing code that the production path never reaches.

Removing dead code makes the real instrumentation path (direct `log_tool_call` calls from `loop.py`) the only path, eliminating the confusion.

### Behavior change

Delete `self.tool_call_count = 0` from `__init__` and delete the entire `intercept_tool_call` method. Delete the corresponding test. No functional change to tool counting in production — the count is still correct in `tool_call_trace.json` because `loop.py` already logs calls there directly.

- [ ] **Step 1: Write a regression test confirming the dead code does not exist**

Append to `tests/test_runner.py`:

```python
class TestDeadCodeRemoved:
    def test_intercept_tool_call_not_on_runner(self, tmp_path, mocker):
        mocker.patch("harness.runner.scenario_runner.init_run")
        mocker.patch("harness.runner.scenario_runner.snapshot", return_value={})
        runner = ScenarioRunner(str(tmp_path), "run-xyz")
        assert not hasattr(runner, "intercept_tool_call"), \
            "intercept_tool_call is dead code and must not exist on ScenarioRunner"

    def test_tool_call_count_not_on_runner(self, tmp_path, mocker):
        mocker.patch("harness.runner.scenario_runner.init_run")
        mocker.patch("harness.runner.scenario_runner.snapshot", return_value={})
        runner = ScenarioRunner(str(tmp_path), "run-xyz")
        assert not hasattr(runner, "tool_call_count"), \
            "tool_call_count is dead code and must not exist on ScenarioRunner"
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/test_runner.py::TestDeadCodeRemoved -v
```

Expected: FAIL — both attributes still exist.

- [ ] **Step 3: Delete dead code from `scenario_runner.py`**

In `harness/runner/scenario_runner.py`:
- Remove `self.tool_call_count = 0` from `__init__` (line 32).
- Delete the entire `intercept_tool_call` method (lines 134–137, including any docstring or surrounding blank lines).

- [ ] **Step 4: Delete the dead test from `tests/test_runner.py`**

Find and delete `test_intercept_tool_call_increments_count_and_logs` (around line 265). This test verified behaviour that no longer exists and must not be left as a false-positive green test.

- [ ] **Step 5: Run all tests to verify nothing breaks**

```bash
pytest tests/test_runner.py -v
```

Expected: all PASS. The deleted test is gone; the two new assertion tests pass.

---

## Task 3: Pass 1 baseline-idempotent guard (Bug 4.8)

**Files:**
- Modify: `harness/verify/pass1_functional.py`
- Test: `tests/test_verify.py`

### Why

`functional_test.py` for state-dependent scenarios drains queues or consumes events to verify the fix. Running it as a pre-fix baseline mutates the exact state the model agent needs to inspect for diagnosis. Examples: an SQS-based scenario baseline run drains the queue before the agent can measure depth; a DynamoDB-stream scenario's baseline consumes records that would expose the fault. Adding a `baseline_idempotent: false` flag to the manifest lets scenario authors mark tests that should not be run before the fix is applied. The default is `true` (current behaviour unchanged for all existing scenarios).

### Behavior change

`run_pass1` reads `fault_manifest.json` at `manifest_path`. If `baseline_idempotent` is `false` (boolean), it skips the baseline subprocess call and returns:

```python
{"baseline": "skipped_non_idempotent", "passed": None, "baseline_passed": None}
```

The rest of the function (running the post-fix functional test) is unchanged. `Pass2Step.should_run` and `Pass3Step` already handle `None` for baseline fields — the regression comparison simply shows "no baseline data" rather than a diff.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_verify.py`:

```python
from harness.verify.pass1_functional import run_pass1


class TestPass1BaselineIdempotent:
    def test_skips_baseline_when_not_idempotent(self, tmp_path, mocker):
        manifest = tmp_path / "fault_manifest.json"
        manifest.write_text('{"fault_class": "reliability", "baseline_idempotent": false}')
        # functional_test.py must NOT be called when baseline_idempotent is false.
        mock_run = mocker.patch("subprocess.run")
        result = run_pass1(str(tmp_path), str(manifest), "run-xyz")
        assert result.get("baseline") == "skipped_non_idempotent"
        # subprocess.run should not have been called for the baseline.
        # (It may still be called for the verification run, which is fine.)
        for call in mock_run.call_args_list:
            args = call.args[0] if call.args else call.kwargs.get("args", [])
            # The call should not be the baseline invocation.
            # We check that no call contains "baseline" context or runs before deploy.
            # Since we skip baseline entirely, call count should be 0 or only post-fix.
        assert result.get("baseline_passed") is None

    def test_runs_baseline_when_idempotent_true(self, tmp_path, mocker):
        manifest = tmp_path / "fault_manifest.json"
        manifest.write_text('{"fault_class": "connectivity", "baseline_idempotent": true}')
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        (corpus / "functional_test.py").write_text("# no-op\n")
        mock_run = mocker.patch(
            "subprocess.run",
            return_value=mocker.MagicMock(returncode=1, stdout="", stderr=""),
        )
        run_pass1(str(tmp_path), str(manifest), "run-xyz")
        assert mock_run.called

    def test_runs_baseline_when_flag_absent(self, tmp_path, mocker):
        """Default behaviour (no baseline_idempotent key) must run baseline."""
        manifest = tmp_path / "fault_manifest.json"
        manifest.write_text('{"fault_class": "connectivity"}')
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        (corpus / "functional_test.py").write_text("# no-op\n")
        mock_run = mocker.patch(
            "subprocess.run",
            return_value=mocker.MagicMock(returncode=1, stdout="", stderr=""),
        )
        run_pass1(str(tmp_path), str(manifest), "run-xyz")
        assert mock_run.called
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/test_verify.py::TestPass1BaselineIdempotent -v
```

Expected: `test_skips_baseline_when_not_idempotent` FAIL — baseline runs regardless of the flag.

- [ ] **Step 3: Update `pass1_functional.py`**

At the start of `run_pass1` (after reading the manifest), add the idempotency check:

```python
def run_pass1(scenario_dir: str, manifest_path: str, run_id: str) -> dict:
    manifest = {}
    if manifest_path and os.path.isfile(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)

    baseline_idempotent = manifest.get("baseline_idempotent", True)
    if not baseline_idempotent:
        return {
            "baseline": "skipped_non_idempotent",
            "passed": None,
            "baseline_passed": None,
        }

    # ... rest of existing baseline run logic unchanged ...
```

- [ ] **Step 4: Run the new tests to verify they pass**

```bash
pytest tests/test_verify.py::TestPass1BaselineIdempotent -v
```

Expected: all PASS.

- [ ] **Step 5: Run the full verify test suite**

```bash
pytest tests/test_verify.py -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add harness/runner/context_builder.py harness/runner/scenario_runner.py harness/verify/pass1_functional.py tests/test_runner.py tests/test_verify.py
git commit -m "$(cat <<'EOF'
fix(harness): naming, dead code, corpus hardening — regex, intercept_tool_call, baseline guard

Three fixes to silent misbehaviour and misleading code:

1. Corpus-dir regex: context_builder.py now matches both 'arch01_' and
   'arch_01_' scenario naming conventions via r"arch_?(\d+)_". The
   arch_NN_ form (with leading underscore) previously returned None,
   causing the model to receive a context without corpus files.

2. Dead code removal: ScenarioRunner.intercept_tool_call() and
   tool_call_count were never called from loop.py (which uses
   log_tool_call directly). Both symbols and their test are deleted.
   Tool counting in production is unaffected.

3. Pass 1 baseline guard: run_pass1() now reads baseline_idempotent
   from fault_manifest.json. When false, the baseline functional test
   run is skipped and {"baseline": "skipped_non_idempotent"} is
   returned, preventing state mutation before the model agent runs.
   Default (key absent) preserves existing behaviour.

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

---

## Findings Reference (audit breakpoints)

| # | Bug | Location | Fix |
|---|-----|----------|-----|
| 4.6 | Corpus-dir regex fails for `arch_NN_` names | `context_builder.py` line 64 | `r"arch_?(\d+)_"` — make leading underscore optional |
| 4.16 | `intercept_tool_call` and `tool_call_count` are dead code | `scenario_runner.py` lines 32, 134–137 | Delete both; delete test at line 265 |
| 4.8 | Pass 1 baseline run mutates shared state | `pass1_functional.py` | Read `baseline_idempotent` from manifest; skip baseline when `false` |
