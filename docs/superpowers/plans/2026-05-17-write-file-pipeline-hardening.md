`# Write-File Pipeline Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close three silent-failure holes in the write_file → deploy → grade pipeline where an agent's fix can appear successful even though it never deployed or was never actually evaluated.

**Architecture:** Three independent fixes — (1) reject "zero assertion" pass1 grader outputs, (2) surface silent Lambda packaging skips to the agent, (3) track the latest deployment outcome through redeploys — all under the existing inline-agent + scenario_runner + verify_loop architecture. No new modules. Strictly additive guards with unit-test coverage; no behavioral changes for the happy path.

**Tech Stack:** Python 3.11, pytest, pytest-mock (existing tooling).

---

## Background — Edge Cases Found

The full write_file lifecycle review (see "Findings" section at end of this plan) identified three silent-failure paths worth fixing now. Two are CRITICAL (grader/deployer can return success when the fix never landed), one is HIGH (state tracking can desync after retries).

### Critical 1: Zero-assertion grader silently passes

`harness/runner/scenario_runner.py:_parse_assertion_output()` and `harness/verify/pass1_functional.py:run_pass1()` both treat an empty assertion set as `all_passed=True`. If `functional_test.py` crashes before emitting any `ASSERT` line (import error, network blip, missing dependency, raised exception in setup), both parsers see zero failures → `all_passed=True` → agent loop reports "Fix deployed and all tests passed" and `fix_correctness.score = 1.0`. The fix never ran but scores perfectly.

### Critical 2: Lambda packaging silently skips files without matching S3Key

`harness/runner/deployment_handler.py:handle_submission()` at lines 124-143 iterates modified `.py` files under `deployment/lambda/` and matches each by stem (e.g., `foo.py` → `S3Key: foo.zip`). If no S3Key matches, the file is silently skipped with no error returned. The agent then sees one of:
- `deploy_success` (because the *other* files matched and produced a real CFN diff), OR
- `no_changes` (because no template diff was produced)

In either case, the model never learns *which* file failed to deploy, and may submit a "fixed" file that never actually became live Lambda code.

### High: `attempt_redeployment` doesn't update `_last_deployment_outcome`

`harness/runner/scenario_runner.py:attempt_redeployment()` (lines 211-220) calls `handle_submission()` and returns the result, but never updates `self._last_deployment_outcome`. The verify loop in `run.py:Step 9` keys off this attribute. After a test-retry redeploy that *fails*, the recorded outcome is still the older `deploy_success` from the first attempt → the verify pipeline runs on stale state, or worse, the scorer scores a stale outcome.

---

## File Structure

Changes are confined to four production files and one test file:

- **Modify** `harness/runner/scenario_runner.py` — strengthen `_parse_assertion_output`; track outcome in `attempt_redeployment` & `run_functional_tests`.
- **Modify** `harness/verify/pass1_functional.py` — surface zero-assertion / non-zero-exit-code failure.
- **Modify** `harness/runner/deployment_handler.py` — emit `skipped_lambda_files` in result; treat them as a soft failure when nothing else changed.
- **Modify** `harness/agent/loop.py` — relay packaging skip warnings to the model so it can correct the file path.
- **Modify** `tests/test_runner.py` — new tests for each fix.

No new files. No reorganization.

---

## Task 1: Surface zero-assertion / non-zero-exit-code in pass1 grader

**Files:**
- Modify: `harness/runner/scenario_runner.py` (`_parse_assertion_output`, `run_functional_tests`)
- Modify: `harness/verify/pass1_functional.py` (`run_pass1`)
- Test: `tests/test_runner.py`

### Why

Pass1 is the authoritative correctness check. Right now, a functional_test.py that crashes before any `ASSERT` line emits returns `{"all_passed": True, "passed": [], "failed": []}`. This is wrong on its face — zero assertions doesn't mean "everything passed", it means "nothing was tested." Same bug exists in two places: the agent-loop callback (`_parse_assertion_output`) and the final scorer-facing pipeline (`run_pass1`). Both must be fixed; they evaluate the same test for different consumers.

### Behavior change

If the assertion parser sees zero matched `ASSERT` lines OR the subprocess exited with a non-zero return code, return `all_passed=False` (or `primary_assertions_passed=False` for `run_pass1`) with a single synthetic failed entry named `__no_assertions__` (or `__test_crashed__` for non-zero exit). This makes the failure visible to the agent retry loop and to the scorer.

- [ ] **Step 1: Write failing test for `_parse_assertion_output` zero-assertion case**

Append to `tests/test_runner.py` near the existing `test_parse_assertion_output_*` tests:

```python
def test_parse_assertion_output_zero_assertions_is_failure():
    # Functional test crashed before emitting any ASSERT line.
    # This must NOT be treated as success — it means nothing ran.
    output = "Traceback (most recent call last):\n  ImportError: boto3\n"
    result = _parse_assertion_output(output)
    assert result["all_passed"] is False
    assert len(result["failed"]) == 1
    assert result["failed"][0]["name"] == "__no_assertions__"
    assert "no ASSERT" in result["failed"][0]["short_error"].lower()


def test_parse_assertion_output_empty_output_is_failure():
    result = _parse_assertion_output("")
    assert result["all_passed"] is False
    assert len(result["failed"]) == 1
    assert result["failed"][0]["name"] == "__no_assertions__"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_runner.py::test_parse_assertion_output_zero_assertions_is_failure tests/test_runner.py::test_parse_assertion_output_empty_output_is_failure -v
```

Expected: FAIL — current parser returns `all_passed=True` on empty input.

- [ ] **Step 3: Update `_parse_assertion_output` to fail on zero assertions**

Modify `harness/runner/scenario_runner.py` — replace the return block at the end of `_parse_assertion_output` (currently lines 55-60):

```python
    primary_failed = [t for t in failed if "_secondary" not in t["name"]]
    if not passed and not failed:
        # Functional test produced no parseable assertions — almost always a
        # crashed or mis-configured test. Treat as failure so the agent retries
        # and the scorer doesn't credit a non-run as success.
        synthetic = {
            "name": "__no_assertions__",
            "description": "functional_test.py emitted no ASSERT lines",
            "short_error": (
                "no ASSERT pass|fail lines were produced. The test likely "
                "crashed before reaching any assertion (import error, "
                "missing dependency, network error in setup, etc.)."
            ),
        }
        return {"all_passed": False, "passed": [], "failed": [synthetic]}
    return {
        "all_passed": len(primary_failed) == 0,
        "passed": passed,
        "failed": failed,
    }
```

- [ ] **Step 4: Run the new tests to verify they pass; existing assertion tests still pass**

```bash
pytest tests/test_runner.py -k parse_assertion -v
```

Expected: 5 passed (3 existing + 2 new).

- [ ] **Step 5: Write failing test for `run_functional_tests` non-zero-exit-code**

Append to `tests/test_runner.py`:

```python
def test_run_functional_tests_nonzero_exit_is_failure(tmp_path, mocker):
    mocker.patch("harness.runner.scenario_runner.init_run")
    mocker.patch("harness.runner.scenario_runner.snapshot", return_value={})
    runner = ScenarioRunner(str(tmp_path), "run-test")
    corpus_dir = tmp_path / "corpus" / "arch_01_x"
    corpus_dir.mkdir(parents=True)
    (corpus_dir / "functional_test.py").write_text("raise SystemExit(2)\n")
    mocker.patch(
        "harness.runner.scenario_runner.corpus_dir_for_scenario",
        return_value=corpus_dir,
    )
    mocker.patch(
        "harness.runner.scenario_runner.subprocess.run",
        return_value=MagicMock(
            returncode=2,
            stdout="ASSERT pass test_a: ok\n",
            stderr="some traceback",
        ),
    )
    result = runner.run_functional_tests()
    assert result["all_passed"] is False
    # A test that crashed mid-run shouldn't be credited even if some asserts passed.
    crash_names = [f["name"] for f in result["failed"]]
    assert "__test_crashed__" in crash_names
```

- [ ] **Step 6: Run new test to verify it fails**

```bash
pytest tests/test_runner.py::test_run_functional_tests_nonzero_exit_is_failure -v
```

Expected: FAIL — current `run_functional_tests` ignores `returncode`.

- [ ] **Step 7: Update `run_functional_tests` to surface non-zero exit code**

In `harness/runner/scenario_runner.py`, replace the body of `run_functional_tests` (currently `return _parse_assertion_output(proc.stdout + "\n" + proc.stderr)`):

```python
    def run_functional_tests(self) -> dict:
        corpus_dir = corpus_dir_for_scenario(self.scenario_dir)
        functional_test = corpus_dir / "functional_test.py"
        if not functional_test.exists():
            raise FileNotFoundError(f"functional_test.py not found: {functional_test}")
        # functional_test.py is a standalone script using the
        # `ASSERT pass|fail <name>: <message>` protocol — not pytest.
        proc = subprocess.run(
            [sys.executable, str(functional_test)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        parsed = _parse_assertion_output(proc.stdout + "\n" + proc.stderr)
        if proc.returncode != 0:
            # Tests that crashed mid-run leave inconsistent state — don't credit.
            crash = {
                "name": "__test_crashed__",
                "description": f"functional_test.py exited with code {proc.returncode}",
                "short_error": (
                    f"test process exited with code {proc.returncode}; "
                    f"stderr tail: {proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else '(empty)'}"
                ),
            }
            parsed["all_passed"] = False
            parsed["failed"] = list(parsed.get("failed", [])) + [crash]
        return parsed
```

- [ ] **Step 8: Run the test to verify it passes**

```bash
pytest tests/test_runner.py::test_run_functional_tests_nonzero_exit_is_failure -v
```

Expected: PASS.

- [ ] **Step 9: Mirror the zero-assertion guard in `run_pass1` (scorer-facing)**

In `harness/verify/pass1_functional.py`, replace the return block at the end of `run_pass1`:

```python
    failed = [n for n, v in assertions.items() if v["result"] == "fail"]
    primary_failed = [n for n in failed if "_secondary" not in n]

    if not assertions:
        # Functional test crashed before emitting any ASSERT line.
        synthetic_name = "__no_assertions__"
        assertions = {
            synthetic_name: {
                "result": "fail",
                "message": (
                    "functional_test.py produced no ASSERT pass|fail lines "
                    "(likely crashed or mis-configured)."
                ),
            }
        }
        failed = [synthetic_name]
        primary_failed = [synthetic_name]

    return {
        "assertions": assertions,
        "primary_assertions_passed": len(primary_failed) == 0,
        "all_assertions_passed": len(failed) == 0,
        "failed_assertion_names": failed,
    }
```

- [ ] **Step 10: Write a test for the run_pass1 zero-assertion guard**

Add to `tests/test_runner.py` (or `tests/test_verify.py` if it exists — check first):

```bash
ls /Users/shubhan/ACEDebugging-benchmark/tests/test_verify.py 2>/dev/null && echo "USE_VERIFY" || echo "USE_RUNNER"
```

If `test_verify.py` exists, add the test there; otherwise append to `tests/test_runner.py`:

```python
def test_run_pass1_zero_assertions_is_failure(tmp_path, mocker):
    from harness.verify.pass1_functional import run_pass1
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "functional_test.py").write_text("print('hello')\n")
    mocker.patch(
        "harness.verify.pass1_functional.subprocess.run",
        return_value=MagicMock(returncode=0, stdout="hello\n", stderr=""),
    )
    result = run_pass1(str(corpus))
    assert result["primary_assertions_passed"] is False
    assert result["all_assertions_passed"] is False
    assert "__no_assertions__" in result["failed_assertion_names"]
```

- [ ] **Step 11: Run all assertion-related tests**

```bash
pytest tests/test_runner.py -k "parse_assertion or pass1 or functional_tests" -v
```

Expected: all PASS.

- [ ] **Step 12: Commit**

```bash
git add harness/runner/scenario_runner.py harness/verify/pass1_functional.py tests/test_runner.py
git commit -m "$(cat <<'EOF'
fix(grader): treat zero-assertion or crashed test as failure

A functional_test.py that crashes before emitting any ASSERT line, or
exits with a non-zero return code, was previously parsed as
all_passed=True (zero failures). The agent loop reported "Fix deployed
and all tests passed" and fix_correctness scored 1.0 — even though the
fix was never actually evaluated.

Both _parse_assertion_output (agent-loop verify_callback) and run_pass1
(scorer pipeline) now synthesize an __no_assertions__ / __test_crashed__
failure when this happens.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Surface silent Lambda packaging skips to the agent

**Files:**
- Modify: `harness/runner/deployment_handler.py` (`handle_submission`)
- Modify: `harness/agent/loop.py` (relay warnings in deploy success path)
- Test: `tests/test_runner.py`

### Why

`handle_submission()` silently skips modified `.py` files in `deployment/lambda/` whose stem doesn't match any `S3Key` in the template. The agent's write_file landed on disk, the no-op detector at `tools.py:128` didn't trigger (content *is* different from disk), `submit_fix` succeeded, but the new code never made it into the deployed Lambda. The agent gets no feedback that a specific file was orphaned.

### Behavior change

`handle_submission` returns a new `skipped_lambda_files` list in the result dict (alongside `outcome`). The list contains relative paths of modified `.py` files under `lambda/` that had no matching S3Key. The agent loop, on `deploy_success`, prepends a warning to the test-result message naming each skipped file so the model can correct the path. On `no_changes`, the error message embeds the skipped list so the model understands the gap.

- [ ] **Step 1: Write failing test — skipped file is reported in result**

Append to `tests/test_runner.py`:

```python
def test_handle_submission_reports_skipped_lambda_files(tmp_path, mocker):
    scenario = tmp_path / "scenario"
    scenario.mkdir()
    # Template references handler.zip only.
    (scenario / "faulted.yaml").write_text(
        "AWSTemplateFormatVersion: '2010-09-09'\n"
        "Resources:\n"
        "  MyFn:\n"
        "    Type: AWS::Lambda::Function\n"
        "    Properties:\n"
        "      Handler: index.handler\n"
        "      Code:\n"
        "        S3Bucket: ace-bench-artifacts\n"
        "        S3Key: handler.zip\n"
    )
    (scenario / "deployment").mkdir()
    lam = scenario / "deployment" / "lambda"
    lam.mkdir()
    (lam / "handler.py").write_text("def handler(e,c): return 200\n")
    # Agent also wrote a NEW file whose stem (typo_handler) does not match any S3Key.
    (lam / "typo_handler.py").write_text("def handler(e,c): return 500\n")

    mocker.patch(
        "harness.runner.deployment_handler.run_lint",
        return_value={"passed": True, "fatal_errors": [], "warnings": []},
    )
    mocker.patch("harness.runner.deployment_handler.snapshot", return_value={})
    mocker.patch(
        "harness.runner.deployment_handler.diff_snapshots",
        return_value={
            "files_added": [os.path.join("lambda", "typo_handler.py")],
            "files_modified": [os.path.join("lambda", "handler.py")],
            "files_removed": [],
            "total_files_changed": 2,
            "per_file_line_changes": {},
            "total_lines_changed": 0,
        },
    )
    mocker.patch("harness.runner.deployment_handler.log_file_change")
    mocker.patch("harness.runner.deployment_handler._ensure_artifact_bucket")
    mocker.patch("harness.runner.deployment_handler.s3_client.put_object")
    cf = mocker.patch("harness.runner.deployment_handler.cf_client")
    cf.update_stack.return_value = {}
    cf.get_waiter.return_value.wait.return_value = None

    result = handle_submission(str(scenario), "run-skip", {})
    assert result["outcome"] == "deploy_success"
    assert "lambda/typo_handler.py" in result["skipped_lambda_files"]
    assert "lambda/handler.py" not in result["skipped_lambda_files"]
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pytest tests/test_runner.py::test_handle_submission_reports_skipped_lambda_files -v
```

Expected: FAIL — `result` has no `skipped_lambda_files` key.

- [ ] **Step 3: Update `handle_submission` to collect skipped files**

In `harness/runner/deployment_handler.py`, modify the packaging pre-flight loop (currently starts around line 124) and the three `return` statements that emit `deploy_success`, `no_changes`, and `deploy_fail`.

First, add a list before the loop:

```python
    lambda_rel_prefix = "lambda" + os.sep
    skipped_lambda_files: list[str] = []
    for rel_path in diff["files_modified"] + diff["files_added"]:
        if not (rel_path.startswith(lambda_rel_prefix) and rel_path.endswith(".py")):
            continue
        abs_path = os.path.join(deployment_dir, "lambda", os.path.basename(rel_path))
        fn_stem = os.path.splitext(os.path.basename(abs_path))[0]

        original_s3_key = find_s3key_for_stem(template_body, fn_stem)
        if original_s3_key is None:
            # No Lambda in the template uses this file — record so the agent
            # can correct the file name; do NOT silently drop.
            skipped_lambda_files.append(rel_path.replace(os.sep, "/"))
            continue
```

Then attach `skipped_lambda_files` to every return at the end of the function. Replace the final returns with:

```python
    # Step 4 — CloudFormation update
    try:
        cf_client.update_stack(
            StackName=_STACK_NAME,
            TemplateBody=template_body,
            Capabilities=["CAPABILITY_IAM", "CAPABILITY_NAMED_IAM"],
        )
    except ClientError as e:
        msg = str(e)
        if "No updates are to be performed" in msg:
            extra = ""
            if skipped_lambda_files:
                extra = (
                    " Note: the following modified Lambda file(s) had no "
                    f"matching S3Key in the template and were not deployed: "
                    f"{skipped_lambda_files}. Rename to match an S3Key stem "
                    "or edit the template's S3Key to match your filename."
                )
            return {
                "outcome": "no_changes",
                "error": (
                    "CloudFormation rejected the update: no changes detected. "
                    "Your edits did not produce any diff in the deployed template or Lambda code. "
                    "Verify your write_file call changed the correct file and property." + extra
                ),
                "skipped_lambda_files": skipped_lambda_files,
            }
        raise

    try:
        waiter = cf_client.get_waiter("stack_update_complete")
        waiter.wait(StackName=_STACK_NAME, WaiterConfig={"Delay": 5, "MaxAttempts": 60})
        return {
            "outcome": "deploy_success",
            "skipped_lambda_files": skipped_lambda_files,
        }
    except WaiterError:
        events_res = cf_client.describe_stack_events(StackName=_STACK_NAME)
        events = [
            {
                "logical_id": e.get("LogicalResourceId"),
                "status": e.get("ResourceStatus"),
                "reason": e.get("ResourceStatusReason"),
            }
            for e in events_res.get("StackEvents", [])
            if e.get("ResourceStatusReason")
        ]
        return {
            "outcome": "deploy_fail",
            "events": events,
            "skipped_lambda_files": skipped_lambda_files,
        }
```

Also update the `lint_fail` early return to carry an empty list for shape consistency:

```python
    if not lint_result["passed"]:
        return {"outcome": "lint_fail", "errors": lint_result["fatal_errors"],
                "skipped_lambda_files": []}
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
pytest tests/test_runner.py::test_handle_submission_reports_skipped_lambda_files -v
```

Expected: PASS.

- [ ] **Step 5: Re-run existing deployment_handler tests to confirm no regression**

```bash
pytest tests/test_runner.py -k "deployment_handler or submission or attempt_deployment or no_changes" -v
```

Expected: all PASS. The existing tests that read `result["outcome"]` and `result["error"]` should still work since the new key is additive.

- [ ] **Step 6: Update agent loop to relay skipped-file warnings on deploy_success**

In `harness/agent/loop.py`, locate the deploy-success path (around line 343 — `if deploy_result["success"]:`). The `deploy_result["result"]` dict now carries `skipped_lambda_files`. Modify the success branch to prepend a warning:

Find this block:

```python
                                if deploy_result["success"]:
                                    submitted = True
                                    if verify_callback is not None:
                                        verify_result = await asyncio.get_running_loop().run_in_executor(
                                            None, verify_callback
                                        )
```

Insert the warning capture immediately after `submitted = True` and before the `if verify_callback is not None:` block:

```python
                                if deploy_result["success"]:
                                    submitted = True
                                    _skipped = (
                                        deploy_result.get("result", {}).get(
                                            "skipped_lambda_files", []
                                        )
                                    )
                                    _skipped_msg = ""
                                    if _skipped:
                                        _skipped_msg = (
                                            "WARNING: the following Lambda file(s) you edited "
                                            "had no matching S3Key in the template and were "
                                            f"NOT deployed: {_skipped}. Their content is on "
                                            "disk but the live Lambda still runs the old code. "
                                            "Either rename your edit to match a template S3Key "
                                            "stem, or edit the template's S3Key to match your "
                                            "filename, then submit_fix again.\n\n"
                                        )
                                    if verify_callback is not None:
```

Then inside the existing `if verify_result["all_passed"]:` branch, prepend `_skipped_msg` to `content`:

```python
                                        if verify_result["all_passed"]:
                                            all_tests_passed = True
                                            result_lines = ["Fix deployed and all tests passed."]
                                            for t in verify_result.get("passed", []):
                                                result_lines.append(f"[{t['name']}]: PASS")
                                            for t in verify_result.get("failed", []):
                                                err = f" — {t['short_error']}" if t.get("short_error") else ""
                                                result_lines.append(f"[{t['name']}]: FAIL{err}")
                                            content = _skipped_msg + "\n".join(result_lines)
```

And the `else` (test-fail-with-retries) branch:

```python
                                        else:
                                            submitted = False
                                            test_retry_count += 1
                                            writes_since_last_submit = 0
                                            content = _skipped_msg + _format_test_summary(
                                                verify_result, test_retry_count, max_test_retries
                                            )
```

And the no-verify-callback branch:

```python
                                    else:
                                        all_tests_passed = True
                                        content = _skipped_msg + "Fix deployed successfully."
```

- [ ] **Step 7: Write a test for the agent loop relay**

Append to `tests/test_agent_loop.py` (find the existing pattern first):

```bash
grep -n "def test_" /Users/shubhan/ACEDebugging-benchmark/tests/test_agent_loop.py | head -20
```

If the file has tests mocking `deploy_callback`, add:

```python
def test_loop_relays_skipped_lambda_files_in_success_message(mocker):
    # Verify that when deploy_callback returns skipped_lambda_files, the
    # message handed back to the model includes the WARNING block.
    from harness.agent.loop import _format_test_summary  # smoke import
    # Full integration is exercised via the live agent; here we just verify
    # the data path is wired by inspecting that the success branch reads
    # deploy_result['result']['skipped_lambda_files'].
    import harness.agent.loop as loop
    src = open(loop.__file__).read()
    assert "skipped_lambda_files" in src
    assert "had no matching S3Key" in src
```

(This is intentionally a low-cost grep-style smoke test — the full async agent loop is integration-tested via real scenarios. The unit test here just guards against accidental deletion of the relay wiring.)

- [ ] **Step 8: Run the smoke test**

```bash
pytest tests/test_agent_loop.py::test_loop_relays_skipped_lambda_files_in_success_message -v
```

Expected: PASS.

- [ ] **Step 9: Run the full runner+loop test suite**

```bash
pytest tests/test_runner.py tests/test_agent_loop.py -v
```

Expected: all PASS.

- [ ] **Step 10: Commit**

```bash
git add harness/runner/deployment_handler.py harness/agent/loop.py tests/test_runner.py tests/test_agent_loop.py
git commit -m "$(cat <<'EOF'
fix(deploy): surface silently-skipped Lambda files to the agent

When write_file modifies a .py file under deployment/lambda/ whose stem
doesn't match any S3Key in the template, handle_submission previously
skipped it silently. The agent then saw deploy_success or no_changes
with no indication that a specific file was orphaned — and the fix
never reached the live Lambda.

handle_submission now returns skipped_lambda_files in every outcome
dict. The agent loop relays a WARNING to the model on deploy_success
naming each skipped file, prompting the agent to correct the filename
or template S3Key before the test-retry budget runs out.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Track latest deployment outcome through redeploys

**Files:**
- Modify: `harness/runner/scenario_runner.py` (`attempt_redeployment`)
- Test: `tests/test_runner.py`

### Why

`ScenarioRunner.attempt_redeployment()` (lines 211-220) does not write to `self._last_deployment_outcome`. `run.py` Step 9 reads that attribute to decide whether to skip `run_verify_loop` (verify only runs on `deploy_success`). After a sequence of [submit → deploy_success → tests fail → write more → submit again → deploy_fail], the attribute remains `"deploy_success"` from the first attempt, so the final verify runs against state that no longer reflects the agent's last live deployment.

### Behavior change

`attempt_redeployment()` updates `self._last_deployment_outcome` to the outcome of the latest call, exactly mirroring `attempt_deployment()`. No change to the `submitted` flag (redeploys intentionally don't re-lock; that contract is unchanged).

- [ ] **Step 1: Write failing test**

Append to `tests/test_runner.py`:

```python
def test_attempt_redeployment_updates_last_deployment_outcome(tmp_path, mocker):
    mocker.patch("harness.runner.scenario_runner.init_run")
    mocker.patch("harness.runner.scenario_runner.snapshot", return_value={})
    runner = ScenarioRunner(str(tmp_path), "run-xyz")
    runner._last_deployment_outcome = "deploy_success"  # from earlier attempt

    mocker.patch(
        "harness.runner.scenario_runner.handle_submission",
        return_value={"outcome": "deploy_fail", "events": []},
    )
    runner.attempt_redeployment()
    assert runner._last_deployment_outcome == "deploy_fail"


def test_attempt_redeployment_updates_outcome_on_success(tmp_path, mocker):
    mocker.patch("harness.runner.scenario_runner.init_run")
    mocker.patch("harness.runner.scenario_runner.snapshot", return_value={})
    runner = ScenarioRunner(str(tmp_path), "run-xyz")
    runner._last_deployment_outcome = "deploy_fail"
    mocker.patch(
        "harness.runner.scenario_runner.handle_submission",
        return_value={"outcome": "deploy_success"},
    )
    runner.attempt_redeployment()
    assert runner._last_deployment_outcome == "deploy_success"
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/test_runner.py::test_attempt_redeployment_updates_last_deployment_outcome tests/test_runner.py::test_attempt_redeployment_updates_outcome_on_success -v
```

Expected: FAIL — current implementation never writes the attribute.

- [ ] **Step 3: Update `attempt_redeployment`**

In `harness/runner/scenario_runner.py`, replace the body of `attempt_redeployment` (currently lines 211-220):

```python
    def attempt_redeployment(self) -> dict:
        result = handle_submission(self.scenario_dir, self.run_id, self.start_snapshot, self.start_faulted_yaml)
        outcome = result.get("outcome", "unknown")
        # Mirror attempt_deployment: keep _last_deployment_outcome reflecting
        # the LATEST deploy state so run.py's verify gate uses fresh data.
        self._last_deployment_outcome = outcome
        return {
            "success": outcome == "deploy_success",
            "error": result.get("error", outcome),
            "result": result,
        }
```

- [ ] **Step 4: Run the tests to verify pass**

```bash
pytest tests/test_runner.py -k attempt_redeployment -v
```

Expected: all PASS (including the existing 3 tests `_runs_when_already_submitted`, `_never_sets_submitted_on_success`, `_returns_failure_dict`).

- [ ] **Step 5: Run the full runner test suite to confirm no regression**

```bash
pytest tests/test_runner.py -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add harness/runner/scenario_runner.py tests/test_runner.py
git commit -m "$(cat <<'EOF'
fix(runner): track latest deployment outcome through redeploys

attempt_redeployment() previously did not update _last_deployment_outcome,
so after a failed test-retry redeploy, run.py's Step 9 verify gate read
the stale 'deploy_success' from the first attempt and proceeded as if the
fix were still live.

attempt_redeployment now mirrors attempt_deployment in updating
_last_deployment_outcome on every call. The submitted flag intentionally
remains untouched (the redeploy lifecycle is unchanged).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Final Verification

- [ ] **Step 1: Run the full test suite**

```bash
pytest tests/ -v
```

Expected: 0 failures, no skips beyond the pre-existing 1.

- [ ] **Step 2: Confirm git log shows three new commits in this branch**

```bash
git log --oneline -5
```

Expected: three new `fix(...)` commits stacked on top of the previous HEAD.

---

## Findings (review summary, for future readers)

The full review identified additional medium/low-priority items not addressed in this plan. They are catalogued here for future hardening passes:

| # | Severity | Location | Issue |
|---|----------|----------|-------|
| F1 | LOW | `harness/agent/tools.py:dispatch_file_tool` | Empty-string content is permitted — would clobber a file to zero bytes. Could add a length guard. |
| F2 | LOW | `harness/runner/deployment_handler.py:handler_to_arcname` | Nested handler `foo.bar.baz` produces arcname `foo.bar.py` rather than `foo/bar.py`. Lambda runtime would fail at invocation. Benchmark currently uses flat handlers, so latent. |
| F3 | LOW | `harness/runner/scenario_runner.py:_parse_assertion_output` regex | Test names must match `\w+`. Names with hyphens, dots, or spaces silently fail to parse. |
| F4 | LOW | `harness/runner/scenario_runner.py:run_functional_tests` | `subprocess.run(timeout=120)` raises `TimeoutExpired` un-caught — propagates to agent loop crash. Should be caught and surfaced as a synthetic failed assertion. |
| F5 | MEDIUM | `harness/agent/loop.py` retry budget | `max_test_retries=5` and `max_deploy_retries=5` are independent; combined worst-case is ~10 wasted submits. Reasonable, but worth considering a unified budget for cost tracking. |
| F6 | LOW | `harness/runner/deployment_handler.py` packaging | Only files starting with `lambda{sep}` are packaged. Files in `deployment/<other>/` are deployed to disk but never re-zipped — same silent-skip class as Task 2 but for non-Lambda code paths. Currently no scenarios use non-Lambda code, so latent. |

**These were left out of this plan** because their fix-vs-risk ratio is lower than the three above. Worth a dedicated future plan if their assumptions break.
