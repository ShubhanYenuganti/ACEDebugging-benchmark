# Write-File → Deploy → Grade Pipeline Cleanup Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the seven structural smells in the write_file → deploy → grade pipeline that make silent bugs hard to find — replace stringly-typed result dicts with dataclasses, consolidate the duplicated assertion parser, move orphan detection to write-time, switch functional tests to a JSON contract, unify the three deploy methods, add a deployment audit log, and refactor the verify pipeline into composable steps.

**Architecture:** Pure refactor — no scenario behaviour changes. All 122 existing tests must continue to pass, with new tests added per task. Each task ends in a committable, runnable state so a reviewer can pause between tasks without leaving the codebase broken.

**Tech Stack:** Python 3.11, dataclasses, typing.Protocol, pytest, pytest-mock.

---

## Why these seven cleanups together

The three silent-failure bugs fixed by `docs/superpowers/plans/2026-05-17-write-file-pipeline-hardening.md` all required cross-file detective work to find. Each had the same root cause: stringly-typed result dicts, duplicated parsing logic, and scattered state. This plan converts those root causes — once — so the next bug surfaces at the keystroke or in a single dataclass field rather than via a 4-file investigation.

| Cleanup | Maps to Task |
|---------|--------------|
| #1 Dataclass result types (`DeploymentResult`, `AssertionRunResult`, `PackagingPlan`) | Task 1 |
| #2 Single assertion parser | Task 2 |
| #3 Move orphan detection to write_file time | Task 3 |
| #4 Switch functional_test.py to JSON contract | Task 4 |
| #5 Single `deploy()` method + `SubmissionState` | Task 5 |
| #6 Per-submission deployment audit log | Task 6 |
| #7 Composable verify pipeline | Task 7 |

Tasks are ordered by dependency. Task 1 lays the type foundation; later tasks consume it.

---

## File Structure

**New modules:**
- `harness/shared/types.py` — all dataclasses (Task 1)
- `harness/shared/assertion_parser.py` — single source for assertion parsing (Task 2)
- `harness/shared/functional_test_helpers.py` — emit JSON results from functional_test.py (Task 4)
- `harness/verify/pipeline.py` — composable step protocol + runner (Task 7)
- `tests/test_types.py` — dataclass unit tests (Task 1)
- `tests/test_assertion_parser.py` — parser tests (Task 2)
- `tests/test_functional_test_helpers.py` — JSON helpers tests (Task 4)

**Modified production files:**
- `harness/runner/deployment_handler.py` — return `DeploymentResult`, build `PackagingPlan`, emit audit log
- `harness/runner/scenario_runner.py` — single `deploy()`, `SubmissionState`, parser delegation
- `harness/verify/pass1_functional.py` — delegate to shared parser
- `harness/verify/pass2_regression.py` — accept `AssertionRunResult`
- `harness/verify/pass3_classification.py` — accept `AssertionRunResult`
- `harness/verify/verify_loop.py` — use composable pipeline
- `harness/agent/loop.py` — read `DeploymentResult` fields directly
- `harness/agent/tools.py` — orphan check in `dispatch_file_tool`
- `harness/scoring/dimensions/fix_correctness.py` — read `AssertionRunResult` properties
- `harness/scoring/dimensions/quality.py` — read `AssertionRunResult` properties
- `harness/shared/result_logger.py` — `log_deployment()` writer
- `harness/run.py` — read `AssertionRunResult` for the summary banner
- `corpus/*/functional_test.py` (4 files) — migrate to JSON contract (Task 4)

**Test files:** `tests/test_runner.py`, `tests/test_verify.py`, `tests/test_agent_loop.py`, `tests/test_scoring.py` updated throughout.

---

## Task 1: Introduce dataclass result types and refactor `handle_submission`

**Files:**
- Create: `harness/shared/types.py`
- Create: `tests/test_types.py`
- Modify: `harness/runner/deployment_handler.py` — return `DeploymentResult`, build `PackagingPlan`
- Modify: `harness/runner/scenario_runner.py:attempt_deployment`, `attempt_redeployment`, `on_model_redeploy` — read `.outcome`, `.success` instead of dict keys
- Modify: `harness/agent/loop.py:run_agent_loop` (around line 324) — read `.success`, `.error`, `.skipped_lambda_files`
- Modify: `tests/test_runner.py` — update tests to read dataclass fields

### Why

Every silent bug we found was rooted in dict-shaped results: missing fields silently became `None`, new fields were added to only some branches, and there was no schema check between the producer and consumer. Replacing dicts with dataclasses makes those failures `AttributeError` at runtime (or mypy errors at lint time) instead of `None`-then-mystery later.

### Behaviour change

None. Pure refactor. The fields on `DeploymentResult` are the union of all keys any branch of `handle_submission` previously returned. Callers read them as attributes instead of dict keys.

- [ ] **Step 1: Create `harness/shared/types.py`**

```python
"""Shared dataclasses for results that cross module boundaries.

Replacing dicts with these types catches missing/added fields at runtime
rather than via the silent None-then-mystery debugging pattern.
"""
from dataclasses import dataclass, field
from typing import Literal

DeploymentOutcome = Literal[
    "deploy_success",
    "no_changes",
    "lint_fail",
    "deploy_fail",
    "error",
    "unknown",
]


@dataclass
class LambdaUpload:
    """One Lambda file packaged for a submission."""
    rel_path: str            # e.g. "lambda/handler.py"
    stem: str                # e.g. "handler"
    s3_key_original: str     # e.g. "handler.zip"
    s3_key_new: str          # e.g. "lambdas/<run>/<sha>/handler.zip"
    sha256: str
    arcname: str             # e.g. "index.py" (derived from Handler)


@dataclass
class PackagingPlan:
    """Pre-flight plan for a submission: what to upload, what to skip."""
    uploads: list[LambdaUpload] = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)
    template_changed: bool = False

    @property
    def has_packaging_work(self) -> bool:
        return bool(self.uploads) or self.template_changed

    @property
    def has_orphans(self) -> bool:
        return bool(self.orphans)


@dataclass
class CfnEvent:
    logical_id: str | None
    status: str | None
    reason: str | None


@dataclass
class DeploymentResult:
    """Result of one call to handle_submission(). Always returned regardless
    of outcome; fields not relevant to the branch stay at their default."""
    outcome: DeploymentOutcome
    error: str = ""
    skipped_lambda_files: list[str] = field(default_factory=list)
    packaged_files: list[str] = field(default_factory=list)
    lint_errors: list = field(default_factory=list)
    cfn_events: list[CfnEvent] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.outcome == "deploy_success"


@dataclass
class AssertionResult:
    """One ASSERT line from functional_test.py."""
    name: str
    verdict: Literal["pass", "fail"]
    message: str = ""

    @property
    def is_secondary(self) -> bool:
        return "_secondary" in self.name


@dataclass
class AssertionRunResult:
    """Full result of running functional_test.py once."""
    assertions: list[AssertionResult] = field(default_factory=list)
    returncode: int = 0
    crash_reason: str = ""

    @property
    def primary_failed_names(self) -> list[str]:
        return [a.name for a in self.assertions if a.verdict == "fail" and not a.is_secondary]

    @property
    def all_failed_names(self) -> list[str]:
        return [a.name for a in self.assertions if a.verdict == "fail"]

    @property
    def passed(self) -> list[AssertionResult]:
        return [a for a in self.assertions if a.verdict == "pass"]

    @property
    def failed(self) -> list[AssertionResult]:
        return [a for a in self.assertions if a.verdict == "fail"]

    @property
    def primary_assertions_passed(self) -> bool:
        return len(self.primary_failed_names) == 0 and not self.crash_reason

    @property
    def all_assertions_passed(self) -> bool:
        return len(self.all_failed_names) == 0 and not self.crash_reason

    @property
    def assertions_by_name(self) -> dict[str, AssertionResult]:
        return {a.name: a for a in self.assertions}
```

- [ ] **Step 2: Write tests for the dataclasses**

Create `tests/test_types.py`:

```python
from harness.shared.types import (
    AssertionResult,
    AssertionRunResult,
    DeploymentResult,
    LambdaUpload,
    PackagingPlan,
)


def test_deployment_result_success_property():
    assert DeploymentResult(outcome="deploy_success").success is True
    assert DeploymentResult(outcome="deploy_fail").success is False
    assert DeploymentResult(outcome="no_changes").success is False


def test_deployment_result_default_lists_are_empty():
    r = DeploymentResult(outcome="lint_fail")
    assert r.skipped_lambda_files == []
    assert r.packaged_files == []
    assert r.lint_errors == []
    assert r.cfn_events == []
    assert r.error == ""


def test_packaging_plan_emptiness():
    empty = PackagingPlan()
    assert empty.has_packaging_work is False
    assert empty.has_orphans is False

    with_uploads = PackagingPlan(uploads=[LambdaUpload(
        rel_path="lambda/h.py", stem="h", s3_key_original="h.zip",
        s3_key_new="lambdas/r/abc/h.zip", sha256="abc", arcname="index.py",
    )])
    assert with_uploads.has_packaging_work is True

    with_template = PackagingPlan(template_changed=True)
    assert with_template.has_packaging_work is True

    with_orphans = PackagingPlan(orphans=["lambda/typo.py"])
    assert with_orphans.has_orphans is True


def test_assertion_result_is_secondary_detection():
    assert AssertionResult(name="foo", verdict="pass").is_secondary is False
    assert AssertionResult(name="foo_secondary", verdict="pass").is_secondary is True
    assert AssertionResult(name="latency_secondary_check", verdict="fail").is_secondary is True


def test_assertion_run_result_passed_failed_partition():
    r = AssertionRunResult(assertions=[
        AssertionResult(name="a", verdict="pass"),
        AssertionResult(name="b", verdict="fail", message="oops"),
        AssertionResult(name="c_secondary", verdict="fail"),
    ])
    assert [a.name for a in r.passed] == ["a"]
    assert [a.name for a in r.failed] == ["b", "c_secondary"]
    assert r.primary_failed_names == ["b"]
    assert r.all_failed_names == ["b", "c_secondary"]
    assert r.primary_assertions_passed is False
    assert r.all_assertions_passed is False


def test_assertion_run_result_all_pass_when_only_secondary_failed():
    r = AssertionRunResult(assertions=[
        AssertionResult(name="primary", verdict="pass"),
        AssertionResult(name="opt_secondary", verdict="fail"),
    ])
    assert r.primary_assertions_passed is True
    assert r.all_assertions_passed is False


def test_assertion_run_result_crash_reason_overrides_passed():
    # Even if every emitted assertion passed, a crash means we don't trust the run.
    r = AssertionRunResult(
        assertions=[AssertionResult(name="a", verdict="pass")],
        returncode=2,
        crash_reason="exit code 2",
    )
    assert r.primary_assertions_passed is False
    assert r.all_assertions_passed is False


def test_assertion_run_result_by_name_lookup():
    r = AssertionRunResult(assertions=[
        AssertionResult(name="a", verdict="pass"),
        AssertionResult(name="b", verdict="fail"),
    ])
    by_name = r.assertions_by_name
    assert by_name["a"].verdict == "pass"
    assert by_name["b"].verdict == "fail"
```

- [ ] **Step 3: Run tests; they should all pass**

```bash
source .venv/bin/activate && pytest tests/test_types.py -v
```

Expected: 7 passed.

- [ ] **Step 4: Refactor `handle_submission` to build `PackagingPlan` and return `DeploymentResult`**

In `harness/runner/deployment_handler.py`, replace the bottom of `handle_submission` (lines 88-onwards) so that:

a. The packaging pre-flight loop populates a `PackagingPlan` instead of mutating local variables;
b. Every return path constructs a `DeploymentResult`.

Replace the entire body of `handle_submission` (from `# Step 3b — packaging pre-flight` onwards):

```python
    # Step 3b — build packaging plan from diff + template
    plan = _build_packaging_plan(diff, template_body, deployment_dir, run_id)

    # Step 3c — execute the plan: upload zips, mutate template body
    for upload in plan.uploads:
        _ensure_artifact_bucket()
        zip_bytes = _zip_file(
            os.path.join(deployment_dir, "lambda", os.path.basename(upload.rel_path)),
            arcname=upload.arcname,
        )
        s3_client.put_object(Bucket=_ARTIFACT_BUCKET, Key=upload.s3_key_new, Body=zip_bytes)
        template_body = re.sub(
            r"(S3Key:\s*)" + re.escape(upload.s3_key_original) + r"(?=\s|$)",
            r"\g<1>" + upload.s3_key_new,
            template_body,
        )

    # Step 4 — CloudFormation update
    try:
        cf_client.update_stack(
            StackName=_STACK_NAME,
            TemplateBody=template_body,
            Capabilities=["CAPABILITY_IAM", "CAPABILITY_NAMED_IAM"],
        )
    except ClientError as e:
        if "No updates are to be performed" in str(e):
            extra = ""
            if plan.has_orphans:
                extra = (
                    " Note: the following modified Lambda file(s) had no "
                    f"matching S3Key in the template and were not deployed: "
                    f"{plan.orphans}. Rename the file to match an S3Key stem "
                    "in faulted.yaml, or edit the template's S3Key to match "
                    "your filename."
                )
            return DeploymentResult(
                outcome="no_changes",
                error=(
                    "CloudFormation rejected the update: no changes detected. "
                    "Your edits did not produce any diff in the deployed template "
                    "or Lambda code. Verify your write_file call changed the "
                    "correct file and property." + extra
                ),
                skipped_lambda_files=plan.orphans,
                packaged_files=[u.rel_path for u in plan.uploads],
            )
        raise

    try:
        waiter = cf_client.get_waiter("stack_update_complete")
        waiter.wait(StackName=_STACK_NAME, WaiterConfig={"Delay": 5, "MaxAttempts": 60})
        return DeploymentResult(
            outcome="deploy_success",
            skipped_lambda_files=plan.orphans,
            packaged_files=[u.rel_path for u in plan.uploads],
        )
    except WaiterError:
        events_res = cf_client.describe_stack_events(StackName=_STACK_NAME)
        events = [
            CfnEvent(
                logical_id=e.get("LogicalResourceId"),
                status=e.get("ResourceStatus"),
                reason=e.get("ResourceStatusReason"),
            )
            for e in events_res.get("StackEvents", [])
            if e.get("ResourceStatusReason")
        ]
        return DeploymentResult(
            outcome="deploy_fail",
            cfn_events=events,
            skipped_lambda_files=plan.orphans,
            packaged_files=[u.rel_path for u in plan.uploads],
        )
```

Also replace the lint-fail branch:

```python
    if not lint_result["passed"]:
        return DeploymentResult(
            outcome="lint_fail",
            lint_errors=lint_result["fatal_errors"],
        )
```

And add the helper function (above `handle_submission`):

```python
def _build_packaging_plan(diff: dict, template_body: str, deployment_dir: str, run_id: str) -> PackagingPlan:
    """Compute what to upload and what to skip from a deployment diff.

    A modified .py file under deployment/lambda/ becomes a LambdaUpload if its
    stem matches an S3Key in the template; otherwise it's recorded as an orphan
    so the caller can surface that to the agent.
    """
    plan = PackagingPlan()
    lambda_rel_prefix = "lambda" + os.sep
    for rel_path in diff["files_modified"] + diff["files_added"]:
        if not (rel_path.startswith(lambda_rel_prefix) and rel_path.endswith(".py")):
            continue
        abs_path = os.path.join(deployment_dir, "lambda", os.path.basename(rel_path))
        stem = os.path.splitext(os.path.basename(abs_path))[0]
        original_key = find_s3key_for_stem(template_body, stem)
        if original_key is None:
            plan.orphans.append(rel_path.replace(os.sep, "/"))
            continue
        handler = find_handler_for_s3key(template_body, original_key)
        arcname = handler_to_arcname(handler)
        # Hash the zip bytes (not the file bytes) so the S3Key reflects exactly
        # what Lambda will execute (arcname matters).
        zip_bytes = _zip_file(abs_path, arcname=arcname)
        sha = hashlib.sha256(zip_bytes).hexdigest()[:12]
        plan.uploads.append(LambdaUpload(
            rel_path=rel_path.replace(os.sep, "/"),
            stem=stem,
            s3_key_original=original_key,
            s3_key_new=f"lambdas/{run_id}/{sha}/{stem}.zip",
            sha256=sha,
            arcname=arcname,
        ))
    if diff.get("per_file_line_changes", {}).get("faulted.yaml"):
        plan.template_changed = True
    return plan
```

Add the imports at the top of `harness/runner/deployment_handler.py`:

```python
from harness.shared.types import (
    CfnEvent,
    DeploymentResult,
    LambdaUpload,
    PackagingPlan,
)
```

- [ ] **Step 5: Update `scenario_runner.py` to read `DeploymentResult` fields**

In `harness/runner/scenario_runner.py`, replace `attempt_deployment`, `attempt_redeployment`, and `on_model_redeploy`:

```python
    def on_model_redeploy(self) -> DeploymentResult:
        with self._lock:
            if self.submitted:
                # Signal "already submitted" by returning an unknown-outcome result.
                # Callers don't use on_model_redeploy after submit; this is defensive.
                return DeploymentResult(outcome="unknown", error="already_submitted")
            self.submitted = True
        try:
            result = handle_submission(
                self.scenario_dir, self.run_id,
                self.start_snapshot, self.start_faulted_yaml,
            )
        except Exception:
            self._last_deployment_outcome = "error"
            raise
        self._last_deployment_outcome = result.outcome
        return result

    def attempt_deployment(self) -> DeploymentResult:
        with self._lock:
            if self.submitted:
                return DeploymentResult(
                    outcome="unknown",
                    error="Already submitted (final).",
                )
        result = handle_submission(
            self.scenario_dir, self.run_id,
            self.start_snapshot, self.start_faulted_yaml,
        )
        if result.success:
            with self._lock:
                self.submitted = True
        self._last_deployment_outcome = result.outcome
        return result

    def attempt_redeployment(self) -> DeploymentResult:
        result = handle_submission(
            self.scenario_dir, self.run_id,
            self.start_snapshot, self.start_faulted_yaml,
        )
        self._last_deployment_outcome = result.outcome
        return result
```

Note these now return `DeploymentResult` directly — no more `{"success": ..., "error": ..., "result": ...}` wrapping.

Add import at top of `harness/runner/scenario_runner.py`:

```python
from harness.shared.types import DeploymentResult
```

- [ ] **Step 6: Update `harness/agent/loop.py` to consume `DeploymentResult`**

Replace lines around 324 (`deploy_result = await ...`). The new code reads attributes:

```python
                                deploy_result = await asyncio.get_running_loop().run_in_executor(None, active_deploy_cb)
                                if deploy_result.success:
                                    submitted = True
                                    _skipped = deploy_result.skipped_lambda_files
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
                                        verify_result = await asyncio.get_running_loop().run_in_executor(
                                            None, verify_callback
                                        )
                                        if verify_result["all_passed"]:
                                            all_tests_passed = True
                                            result_lines = ["Fix deployed and all tests passed."]
                                            for t in verify_result.get("passed", []):
                                                result_lines.append(f"[{t['name']}]: PASS")
                                            for t in verify_result.get("failed", []):
                                                err = f" — {t['short_error']}" if t.get("short_error") else ""
                                                result_lines.append(f"[{t['name']}]: FAIL{err}")
                                            content = _skipped_msg + "\n".join(result_lines)
                                        elif test_retry_count >= max_test_retries:
                                            all_tests_passed = False
                                            content = _skipped_msg + (
                                                f"Maximum test retries ({max_test_retries}) reached. "
                                                + _format_test_summary(verify_result, test_retry_count, max_test_retries)
                                            )
                                        else:
                                            submitted = False
                                            test_retry_count += 1
                                            writes_since_last_submit = 0
                                            content = _skipped_msg + _format_test_summary(
                                                verify_result, test_retry_count, max_test_retries
                                            )
                                    else:
                                        all_tests_passed = True
                                        content = _skipped_msg + "Fix deployed successfully."
                                elif retry_count >= max_deploy_retries:
                                    submitted = True
                                    content = (
                                        f"Maximum retries ({max_deploy_retries}) reached. "
                                        f"Last error: {deploy_result.error or 'unknown'}. Exiting."
                                    )
                                else:
                                    retry_count += 1
                                    writes_since_last_submit = 0
                                    content = (
                                        f"Deployment failed (attempt {retry_count}/{max_deploy_retries}): "
                                        f"{deploy_result.error or deploy_result.outcome}. "
                                        "Read the error carefully, revise your fix with write_file, "
                                        "then call submit_fix again."
                                    )
```

Note: `verify_result` still has dict shape — Task 2 will convert it.

- [ ] **Step 7: Update all `tests/test_runner.py` tests that mock `handle_submission`**

The existing tests mock `handle_submission` returning dicts. Update them to return `DeploymentResult` instances:

Search and replace patterns in `tests/test_runner.py`:

```python
# Before
return_value={"outcome": "deploy_success"}
# After
return_value=DeploymentResult(outcome="deploy_success")
```

Apply this transformation to every `mocker.patch("harness.runner.scenario_runner.handle_submission", return_value=...)` call. Add the import at the top of `tests/test_runner.py`:

```python
from harness.shared.types import DeploymentResult
```

Also update assertions that check dict keys to read attributes:

```python
# Before
assert result["success"] is True
assert result["error"] == "no_changes"
# After
assert result.success is True
assert result.outcome == "no_changes"  # error becomes a separate field
```

For `test_handle_submission_reports_skipped_lambda_files`, replace:

```python
    assert result["outcome"] == "deploy_success"
    assert "lambda/typo_handler.py" in result["skipped_lambda_files"]
```

With:

```python
    assert result.outcome == "deploy_success"
    assert "lambda/typo_handler.py" in result.skipped_lambda_files
```

For `test_handle_submission_returns_no_changes_on_no_updates_error` and `test_returns_lint_fail_on_fatal_errors`: change `result["outcome"]` → `result.outcome`, `result["error"]` → `result.error`, `result["errors"]` → `result.lint_errors`.

For `test_packaging_preflight_zips_and_uploads_lambda` and `test_deploy_fail_returns_rollback_outcome`: same treatment.

For tests asserting `result["success"]` from attempt_deployment/attempt_redeployment: change to `result.success`.

- [ ] **Step 8: Run the full test suite**

```bash
source .venv/bin/activate && pytest tests/ -v 2>&1 | tail -15
```

Expected: all tests pass. Any failures are most likely tests that still read dict keys — update them in place.

- [ ] **Step 9: Commit**

```bash
git add harness/shared/types.py harness/runner/deployment_handler.py \
        harness/runner/scenario_runner.py harness/agent/loop.py \
        tests/test_types.py tests/test_runner.py
git commit -F - <<'EOF'
refactor(types): introduce DeploymentResult/AssertionRunResult/PackagingPlan dataclasses

handle_submission now returns DeploymentResult; the agent loop and scenario
runner consume it via attribute access. Catches missing-field bugs at
runtime/type-check time instead of via None-then-mystery debugging.

PackagingPlan factors the silent-skip detection logic out of handle_submission
so future callers (Task 3's write-time orphan check) can reuse it.

No behavioural change. 122+ tests pass.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
```

---

## Task 2: Consolidate assertion parser

**Files:**
- Create: `harness/shared/assertion_parser.py`
- Create: `tests/test_assertion_parser.py`
- Modify: `harness/runner/scenario_runner.py` — delete local parser, delegate
- Modify: `harness/verify/pass1_functional.py` — delete local parser, delegate
- Modify: `harness/verify/pass2_regression.py` — accept `AssertionRunResult`
- Modify: `harness/verify/pass3_classification.py` — accept `AssertionRunResult`
- Modify: `harness/scoring/dimensions/fix_correctness.py` — read `AssertionRunResult` properties
- Modify: `harness/scoring/dimensions/quality.py` — read `AssertionRunResult` properties
- Modify: `harness/verify/verify_loop.py` — pass `AssertionRunResult` through
- Modify: `harness/run.py` (line 53-66 summary) — read `AssertionRunResult`
- Modify: `harness/agent/loop.py` `_format_test_summary` (line ~134) — accept `AssertionRunResult`
- Test: `tests/test_runner.py`, `tests/test_verify.py`, `tests/test_scoring.py`

### Why

Today the regex `r"ASSERT\s+(pass|fail)\s+(\w+):\s*(.*)"` and the zero-assertion guard exist in **two places**: `scenario_runner._parse_assertion_output` (agent loop) and `pass1_functional.run_pass1` (scorer). They return different shapes. Drift between them caused the bugs hardened in the previous plan. One parser, one shape, one set of tests.

### Behaviour change

None. Each call site that previously got a dict now gets an `AssertionRunResult`. Downstream code accesses `.primary_assertions_passed`, `.assertions_by_name`, etc.

- [ ] **Step 1: Create `harness/shared/assertion_parser.py`**

```python
"""Parse functional_test.py output into an AssertionRunResult.

Single source of truth for both the agent retry loop and the scorer pipeline.
The output protocol is one line per assertion:

    ASSERT pass|fail <name>: <message>

Primary assertions: names without '_secondary' suffix; their failure fails the run.
Secondary assertions: tracked for regression analysis, do not fail the run.
"""
import re

from harness.shared.types import AssertionResult, AssertionRunResult

_ASSERT_LINE = re.compile(r"ASSERT\s+(pass|fail)\s+(\w+):\s*(.*)")


def parse(output: str, returncode: int = 0) -> AssertionRunResult:
    """Parse functional_test.py output into an AssertionRunResult.

    A zero-assertion run is treated as a synthetic failure (`__no_assertions__`)
    because functional_test.py emitting nothing almost always means it crashed
    before any assertion ran. A non-zero returncode appends `__test_crashed__`.
    """
    assertions: list[AssertionResult] = []
    for line in output.splitlines():
        m = _ASSERT_LINE.match(line.strip())
        if not m:
            continue
        verdict, name, message = m.group(1), m.group(2), m.group(3)
        assertions.append(AssertionResult(name=name, verdict=verdict, message=message))

    crash_reason = ""
    if not assertions:
        assertions.append(AssertionResult(
            name="__no_assertions__",
            verdict="fail",
            message=(
                "functional_test.py produced no ASSERT lines (likely crashed "
                "before any assertion ran — import error, missing dependency, "
                "network error in setup, etc.)."
            ),
        ))
        crash_reason = "no_assertions_emitted"

    if returncode != 0:
        assertions.append(AssertionResult(
            name="__test_crashed__",
            verdict="fail",
            message=f"functional_test.py exited with code {returncode}",
        ))
        if not crash_reason:
            crash_reason = f"exit_code_{returncode}"

    return AssertionRunResult(
        assertions=assertions,
        returncode=returncode,
        crash_reason=crash_reason,
    )
```

- [ ] **Step 2: Write tests for the parser**

Create `tests/test_assertion_parser.py`:

```python
from harness.shared.assertion_parser import parse


def test_parse_pass_and_fail():
    out = (
        "ASSERT pass check_a: ok\n"
        "ASSERT fail check_b: bad value 42\n"
    )
    r = parse(out)
    assert len(r.assertions) == 2
    assert r.assertions[0].name == "check_a"
    assert r.assertions[0].verdict == "pass"
    assert r.assertions[1].verdict == "fail"
    assert r.assertions[1].message == "bad value 42"
    assert r.primary_assertions_passed is False
    assert r.all_assertions_passed is False
    assert r.crash_reason == ""


def test_secondary_failure_does_not_fail_primary():
    r = parse("ASSERT pass primary: ok\nASSERT fail optional_secondary: minor\n")
    assert r.primary_assertions_passed is True
    assert r.all_assertions_passed is False


def test_zero_assertions_synthesizes_failure():
    r = parse("Traceback (most recent call last):\nImportError: foo\n")
    assert r.crash_reason == "no_assertions_emitted"
    assert r.primary_assertions_passed is False
    assert any(a.name == "__no_assertions__" for a in r.assertions)


def test_empty_output_synthesizes_failure():
    r = parse("")
    assert r.crash_reason == "no_assertions_emitted"
    assert r.primary_assertions_passed is False


def test_nonzero_returncode_synthesizes_crash():
    r = parse("ASSERT pass a: ok\n", returncode=2)
    assert r.crash_reason == "exit_code_2"
    assert r.primary_assertions_passed is False
    assert any(a.name == "__test_crashed__" for a in r.assertions)


def test_zero_assertions_plus_crash_returns_both():
    r = parse("traceback...", returncode=137)
    names = [a.name for a in r.assertions]
    assert "__no_assertions__" in names
    assert "__test_crashed__" in names
    assert r.crash_reason == "no_assertions_emitted"  # first one wins


def test_all_pass_run_is_happy():
    r = parse("ASSERT pass a: ok\nASSERT pass b: ok\n", returncode=0)
    assert r.primary_assertions_passed is True
    assert r.all_assertions_passed is True
    assert r.crash_reason == ""
```

- [ ] **Step 3: Run the new parser tests**

```bash
source .venv/bin/activate && pytest tests/test_assertion_parser.py -v
```

Expected: 7 passed.

- [ ] **Step 4: Delete `_parse_assertion_output` from `scenario_runner.py`; update `run_functional_tests`**

In `harness/runner/scenario_runner.py`:

a. Delete the local `_parse_assertion_output` function (around lines 18-67).
b. Replace the body of `run_functional_tests` with:

```python
    def run_functional_tests(self) -> AssertionRunResult:
        corpus_dir = corpus_dir_for_scenario(self.scenario_dir)
        functional_test = corpus_dir / "functional_test.py"
        if not functional_test.exists():
            raise FileNotFoundError(f"functional_test.py not found: {functional_test}")
        proc = subprocess.run(
            [sys.executable, str(functional_test)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        return assertion_parser.parse(
            proc.stdout + "\n" + proc.stderr,
            returncode=proc.returncode,
        )
```

c. Update imports at the top of `scenario_runner.py`:

```python
from harness.shared import assertion_parser
from harness.shared.types import AssertionRunResult, DeploymentResult
```

- [ ] **Step 5: Rewrite `pass1_functional.py` to delegate**

Replace the entire contents of `harness/verify/pass1_functional.py`:

```python
"""Pass 1 — functional verification.

Runs the corpus functional_test.py and returns an AssertionRunResult.
The shared parser handles the ASSERT regex and crash detection.
"""
import os
import subprocess
import sys

from harness.shared import assertion_parser
from harness.shared.types import AssertionRunResult


def run_pass1(corpus_dir: str) -> AssertionRunResult:
    functional_test = os.path.join(corpus_dir, "functional_test.py")
    result = subprocess.run(
        [sys.executable, functional_test],
        capture_output=True,
        text=True,
        timeout=120,
    )
    return assertion_parser.parse(
        result.stdout + result.stderr,
        returncode=result.returncode,
    )
```

- [ ] **Step 6: Update `pass2_regression.py` to accept `AssertionRunResult`**

Replace `run_pass2` in `harness/verify/pass2_regression.py`:

```python
import json
import os

from harness.shared.types import AssertionRunResult

RESULTS_DIR = "results"


def run_pass2(scenario_dir: str, run_id: str, pass1_result: AssertionRunResult) -> dict:
    baseline_path = os.path.join(RESULTS_DIR, run_id, "faulted_baseline.json")
    with open(baseline_path, "r", encoding="utf-8") as f:
        faulted_baseline = json.load(f)

    current_by_name = pass1_result.assertions_by_name
    regressions = []
    for name, baseline_entry in faulted_baseline["assertions"].items():
        if baseline_entry["result"] != "pass":
            continue
        cur = current_by_name.get(name)
        if cur and cur.verdict == "fail":
            severity = "critical" if "_secondary" not in name else "non_critical"
            regressions.append({"assertion": name, "severity": severity})

    critical = sum(1 for r in regressions if r["severity"] == "critical")
    non_critical = sum(1 for r in regressions if r["severity"] == "non_critical")

    return {
        "regression_count": len(regressions),
        "regressions": regressions,
        "critical_regression_count": critical,
        "non_critical_regression_count": non_critical,
    }
```

Note: `faulted_baseline.json` is still a dict-shaped file on disk because it's persisted between runs. The schema there is `{"assertions": {name: {"result": "pass|fail", "message": "..."}}}`. We write it in Task 6 via a `to_baseline_dict()` helper; for now, `run.py:Step 5` continues to write it. Add that helper to `types.py`:

```python
    def to_baseline_dict(self) -> dict:
        """Snapshot shape for results/<run>/faulted_baseline.json.

        Pass2 reads this file from disk, so the on-disk format is part of the
        contract. Keep this writer and the pass2 reader together.
        """
        return {
            "assertions": {
                a.name: {"result": a.verdict, "message": a.message}
                for a in self.assertions
            },
            "primary_assertions_passed": self.primary_assertions_passed,
            "all_assertions_passed": self.all_assertions_passed,
            "failed_assertion_names": self.all_failed_names,
        }
```

(Add to `AssertionRunResult` in `harness/shared/types.py`.)

- [ ] **Step 7: Update `pass3_classification.py`**

In `harness/verify/pass3_classification.py`, change the signature and the two `pass1_result.get(...)` lines (around lines 53, 89-90):

```python
def run_pass3(
    scenario_dir: str, run_id: str, pass1_result: AssertionRunResult, manifest_path: str
) -> dict:
    ...
    primary_passed = pass1_result.primary_assertions_passed
    assertions = pass1_result.assertions_by_name
    ...
    elif not primary_passed:
        any_improvement = any(a.verdict == "pass" for a in assertions.values())
```

Note: `assertions` was previously a dict of dicts (`{"foo": {"result": "pass"}}`); now it's a dict of `AssertionResult`. Update the `.values()` iteration accordingly.

Add the import:

```python
from harness.shared.types import AssertionRunResult
```

- [ ] **Step 8: Update `verify_loop.py`**

In `harness/verify/verify_loop.py`, update the result-construction at the end of `run_verify_loop`. Pass1 is now an `AssertionRunResult`, but the on-disk `verify_result.json` (logged by `log_verify_result`) needs dict form. Add a serialization step:

```python
    pass1 = run_pass1(corpus_dir or scenario_dir)
    pass2 = run_pass2(scenario_dir, run_id, pass1)
    pass3 = run_pass3(scenario_dir, run_id, pass1, manifest_path)

    pass4 = None
    if manifest_path and os.path.isfile(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        if manifest.get("fault_class") in _CONCURRENCY_CLASSES:
            pass4 = run_pass4(scenario_dir, manifest_path, api_endpoint or "")
            if not pass4["passed"] and pass1.primary_assertions_passed:
                pass3 = dict(pass3)
                pass3["classification"] = "partial"
                pass3["root_cause_addressed"] = False

    result = {
        "outcome": "completed",
        "pass1_functional": pass1.to_baseline_dict(),
        "pass2_regression": pass2,
        "pass3_classification": pass3,
        "pass4_concurrency": pass4,
    }
    log_verify_result(run_id, result)
    return result
```

- [ ] **Step 9: Update scorer dimensions to read the dict shape from `verify_result.json`**

The scorer reads `verify_result.json` from disk, which contains the `to_baseline_dict()` shape. No change is required for `fix_correctness.py` and `quality.py` — their dict-key reads (`all_assertions_passed`, `primary_assertions_passed`, `failed_assertion_names`, `assertions`) are preserved by `to_baseline_dict()`.

Verify this by reading the schema match — open `harness/scoring/dimensions/fix_correctness.py` and confirm every key it reads is present in the `to_baseline_dict()` output. If any key is missing, add it to `to_baseline_dict()`.

- [ ] **Step 10: Update `run.py`**

In `harness/run.py`, the summary banner reads pass1 dict keys (line 53-66). It also reads from disk via `verify_result.json`, so the existing code keeps working — `verify_result["pass1_functional"]` returns the `to_baseline_dict()` shape. No change needed.

However, `run.py:Step 5` (faulted_baseline) still calls `run_pass1` directly and writes the result:

```python
    _baseline = run_pass1(corpus_dir)
    _baseline_path = os.path.join("results", run_id, "faulted_baseline.json")
    with open(_baseline_path, "w") as _f:
        json.dump(_baseline, _f, indent=2)
```

Update to serialize the dataclass:

```python
    _baseline = run_pass1(corpus_dir)
    _baseline_path = os.path.join("results", run_id, "faulted_baseline.json")
    with open(_baseline_path, "w") as _f:
        json.dump(_baseline.to_baseline_dict(), _f, indent=2)
```

- [ ] **Step 11: Update `agent/loop.py`**

`verify_callback = runner.run_functional_tests` now returns `AssertionRunResult` instead of a dict. Update `loop.py`:

a. The `_format_test_summary` function (line ~134) currently reads `result.get("passed", [])`. Replace with attribute access:

```python
def _format_test_summary(result: AssertionRunResult, attempt: int, max_retries: int) -> str:
    n_passed = len(result.passed)
    n_failed = len(result.failed)
    lines = [f"Tests: {n_passed} passed, {n_failed} failed."]
    for a in result.passed:
        lines.append(f"[{a.name}]: PASS")
    for a in result.failed:
        err = f" — {a.message}" if a.message else ""
        lines.append(f"[{a.name}]: FAIL{err}")
    lines.append(
        f"Revise your fix with write_file and call submit_fix again. "
        f"(Attempt {attempt} of {max_retries}.)"
    )
    return "\n".join(lines)
```

b. In `run_agent_loop`, where `verify_result` is consumed (around line 331), change the dict access:

```python
                                        if verify_result.all_passed:  # was: verify_result["all_passed"]
                                            ...
                                            result_lines = ["Fix deployed and all tests passed."]
                                            for a in verify_result.passed:
                                                result_lines.append(f"[{a.name}]: PASS")
                                            for a in verify_result.failed:
                                                err = f" — {a.message}" if a.message else ""
                                                result_lines.append(f"[{a.name}]: FAIL{err}")
                                            content = _skipped_msg + "\n".join(result_lines)
```

Wait — `AssertionRunResult.all_passed` doesn't exist; the equivalent is `primary_assertions_passed`. The previous dict had `all_passed = primary_failed == 0`. Add an alias to `AssertionRunResult`:

```python
    @property
    def all_passed(self) -> bool:
        """Agent-loop alias: True iff no primary failures (mirrors old dict shape)."""
        return self.primary_assertions_passed
```

Add the import at the top of `harness/agent/loop.py`:

```python
from harness.shared.types import AssertionRunResult
```

- [ ] **Step 12: Update `tests/test_runner.py` parser-related tests**

The local `_parse_assertion_output` is gone. Remove those tests OR convert them to call the shared parser. Move them into `tests/test_assertion_parser.py` (some are already there). Specifically delete:

```python
def test_parse_assertion_output_all_passing(): ...
def test_parse_assertion_output_with_failures(): ...
def test_parse_assertion_output_secondary_failure_not_fatal(): ...
def test_parse_assertion_output_zero_assertions_is_failure(): ...
def test_parse_assertion_output_empty_output_is_failure(): ...
```

(They are now covered by `tests/test_assertion_parser.py`.)

Update `test_run_functional_tests_calls_subprocess`:

```python
def test_run_functional_tests_calls_subprocess(tmp_path, mocker):
    mocker.patch("harness.runner.scenario_runner.init_run")
    mocker.patch("harness.runner.scenario_runner.snapshot", return_value={})
    runner = ScenarioRunner(str(tmp_path), "run-test")
    corpus_dir = tmp_path / "corpus" / "arch_01_x"
    corpus_dir.mkdir(parents=True)
    (corpus_dir / "functional_test.py").write_text("def test_x(): pass\n")
    mocker.patch(
        "harness.runner.scenario_runner.corpus_dir_for_scenario",
        return_value=corpus_dir,
    )
    mocker.patch(
        "harness.runner.scenario_runner.subprocess.run",
        return_value=MagicMock(returncode=0, stdout="ASSERT pass test_x: ok\n", stderr=""),
    )
    result = runner.run_functional_tests()
    assert result.primary_assertions_passed is True
    assert result.passed[0].name == "test_x"
```

Update `test_run_functional_tests_nonzero_exit_is_failure` similarly:

```python
    result = runner.run_functional_tests()
    assert result.primary_assertions_passed is False
    crash_names = [a.name for a in result.failed]
    assert "__test_crashed__" in crash_names
```

Remove the `_parse_assertion_output` import at the top of `tests/test_runner.py`.

- [ ] **Step 13: Update `tests/test_verify.py`**

The existing pass1 tests use `_make_corpus` (real subprocess). They read `result["primary_assertions_passed"]` etc. — update to dataclass attributes:

```python
    def test_parses_pass_and_fail_assertions(self, tmp_path):
        ...
        result = run_pass1(corpus_dir)
        assert result.assertions_by_name["connectivity"].verdict == "pass"
        assert result.assertions_by_name["auth_check"].verdict == "fail"
        assert result.all_failed_names == ["auth_check"]

    def test_primary_assertions_passed_excludes_secondary(self, tmp_path):
        ...
        result = run_pass1(corpus_dir)
        assert result.primary_assertions_passed is True
        assert result.all_assertions_passed is False
        assert "latency_secondary" in result.all_failed_names

    def test_all_assertions_passed_true_when_all_pass(self, tmp_path):
        ...
        result = run_pass1(corpus_dir)
        assert result.primary_assertions_passed is True
        assert result.all_assertions_passed is True
        assert result.all_failed_names == []

    def test_primary_assertions_passed_false_when_primary_fails(self, tmp_path):
        ...
        result = run_pass1(corpus_dir)
        assert result.primary_assertions_passed is False

    def test_zero_assertions_is_treated_as_failure(self, tmp_path):
        corpus_dir = self._make_corpus(tmp_path, "hello\nworld\n")
        result = run_pass1(corpus_dir)
        assert result.primary_assertions_passed is False
        assert result.all_assertions_passed is False
        assert "__no_assertions__" in result.all_failed_names
```

For pass2 and pass3 tests that mock `pass1_result` as a dict, change to construct an `AssertionRunResult`. E.g.:

```python
# Before
pass1 = {"assertions": {"connectivity": {"result": "pass", "message": "ok"}},
         "primary_assertions_passed": True, "all_assertions_passed": True,
         "failed_assertion_names": []}
# After
from harness.shared.types import AssertionResult, AssertionRunResult
pass1 = AssertionRunResult(assertions=[
    AssertionResult(name="connectivity", verdict="pass", message="ok"),
])
```

- [ ] **Step 14: Run the full test suite**

```bash
source .venv/bin/activate && pytest tests/ 2>&1 | tail -10
```

Expected: all pass.

- [ ] **Step 15: Commit**

```bash
git add harness/shared/assertion_parser.py harness/shared/types.py \
        harness/runner/scenario_runner.py harness/verify/ \
        harness/agent/loop.py harness/run.py \
        tests/test_assertion_parser.py tests/test_runner.py tests/test_verify.py
git commit -F - <<'EOF'
refactor(verify): consolidate assertion parser into harness/shared/assertion_parser

The same ASSERT regex + zero-assertion guard previously lived in two places
(scenario_runner._parse_assertion_output for the agent loop, and
pass1_functional.run_pass1 for the scorer). Drift between them is what
caused the silent-pass bugs hardened in the previous plan.

Now there's one parser returning AssertionRunResult. run_functional_tests
and run_pass1 both delegate. Downstream (pass2, pass3, scorer dims) reads
typed properties instead of dict keys.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
```

---

## Task 3: Move orphan detection to `write_file` dispatch time

**Files:**
- Modify: `harness/agent/tools.py` `dispatch_file_tool` — orphan check before disk write
- Modify: `harness/agent/loop.py` — pass scenario context (template path) to dispatcher
- Test: `tests/test_agent_loop.py`

### Why

Currently, an agent that writes `deployment/lambda/typo_handler.py` (instead of `handler.py`) succeeds at `write_file`, has the file landed on disk, and only learns 2 turns later — after submit_fix → packaging skip → no_changes — that its edit was orphaned. The previous plan added a warning to surface this, but the fix should happen at the keystroke: refuse the orphan write immediately so the agent can correct the path with the next tool call.

### Behaviour change

`write_file` on `deployment/lambda/<stem>.py` now reads `faulted.yaml` and checks for a matching `S3Key`. If absent, the write is refused with a clear message naming the available stems. Writes outside `deployment/lambda/` (e.g. `faulted.yaml` itself, `deployment/lambda_layer/...`, etc.) are unaffected.

- [ ] **Step 1: Write failing test for orphan write rejection**

Add to `tests/test_agent_loop.py`:

```python
def test_write_file_rejects_orphan_lambda_path(tmp_path):
    from harness.agent.tools import dispatch_file_tool
    scenario = tmp_path
    (scenario / "faulted.yaml").write_text(
        "Resources:\n"
        "  MyFn:\n"
        "    Type: AWS::Lambda::Function\n"
        "    Properties:\n"
        "      Code:\n"
        "        S3Key: handler.zip\n"
    )
    lam = scenario / "deployment" / "lambda"
    lam.mkdir(parents=True)

    result = dispatch_file_tool(
        "write_file",
        {"path": "deployment/lambda/typo_handler.py", "content": "x = 1"},
        str(scenario),
    )
    assert "no matching S3Key" in result
    # The file must NOT have been written to disk:
    assert not (lam / "typo_handler.py").exists()


def test_write_file_accepts_lambda_path_with_matching_s3key(tmp_path):
    from harness.agent.tools import dispatch_file_tool
    scenario = tmp_path
    (scenario / "faulted.yaml").write_text(
        "Resources:\n"
        "  MyFn:\n"
        "    Type: AWS::Lambda::Function\n"
        "    Properties:\n"
        "      Code:\n"
        "        S3Key: handler.zip\n"
    )
    lam = scenario / "deployment" / "lambda"
    lam.mkdir(parents=True)

    result = dispatch_file_tool(
        "write_file",
        {"path": "deployment/lambda/handler.py", "content": "x = 1"},
        str(scenario),
    )
    assert result.startswith("Written ")
    assert (lam / "handler.py").read_text() == "x = 1"


def test_write_file_no_check_outside_lambda_subdir(tmp_path):
    """faulted.yaml writes, and writes to deployment/other/, are not subject to the check."""
    from harness.agent.tools import dispatch_file_tool
    scenario = tmp_path
    (scenario / "faulted.yaml").write_text("# minimal\n")
    (scenario / "deployment").mkdir()
    result = dispatch_file_tool(
        "write_file",
        {"path": "faulted.yaml", "content": "# replaced\n"},
        str(scenario),
    )
    assert result.startswith("Written ")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
source .venv/bin/activate && pytest tests/test_agent_loop.py::test_write_file_rejects_orphan_lambda_path -v
```

Expected: FAIL — current code writes the orphan file.

- [ ] **Step 3: Add the orphan check to `dispatch_file_tool`**

In `harness/agent/tools.py`, add a helper above `dispatch_file_tool`:

```python
def _check_lambda_orphan(rel_path: str, scenario_root: pathlib.Path) -> str | None:
    """If `rel_path` is under deployment/lambda/, ensure its stem matches an
    S3Key in faulted.yaml. Returns an error message if orphaned, None if OK.

    This catches the silent-skip class of bug at write time rather than at
    deploy time (where the agent learns about it only via a confusing
    'no_changes' error 1-2 turns later).
    """
    norm = rel_path.replace("\\", "/")
    if not norm.startswith("deployment/lambda/") or not norm.endswith(".py"):
        return None
    template_path = scenario_root / "faulted.yaml"
    if not template_path.exists():
        return None  # nothing to check against
    template_body = template_path.read_text(encoding="utf-8")
    stem = pathlib.Path(norm).stem
    # Reuse the same matching logic the deployer uses.
    matches = re.findall(r"S3Key:\s*(\S+)\.zip\b", template_body)
    available_stems = {os.path.basename(m) for m in matches}
    if stem in available_stems:
        return None
    return (
        f"Error: deployment/lambda/{stem}.py has no matching S3Key in "
        f"faulted.yaml. The deployer matches Lambda files to template S3Keys "
        f"by filename stem; available stems: {sorted(available_stems)}. "
        f"Either rename your edit to match one of these (e.g. "
        f"deployment/lambda/<stem>.py), or first edit faulted.yaml to add "
        f"an S3Key matching your filename."
    )
```

Add imports at the top of `harness/agent/tools.py`:

```python
import re
```

Then in `dispatch_file_tool`, inside the `if name == "write_file":` branch, after the path-prefix check and `_safe_resolve` but before the existing no-op check, add:

```python
        orphan_err = _check_lambda_orphan(rel, scenario_root)
        if orphan_err is not None:
            return orphan_err
```

The full updated `write_file` branch:

```python
    if name == "write_file":
        rel = inputs.get("path", "")
        content = inputs.get("content", "")
        norm = rel.replace("\\", "/")
        if not (norm.startswith("deployment/") or norm == "faulted.yaml"):
            return f"Error: writing to {rel} is not allowed. Only deployment/ files and faulted.yaml may be modified."
        target = _safe_resolve(rel)
        if target is None:
            return "Error: path traversal not allowed."
        orphan_err = _check_lambda_orphan(rel, scenario_root)
        if orphan_err is not None:
            return orphan_err
        if target.exists() and target.read_text(encoding="utf-8") == content:
            return (
                f"Error: {rel} is unchanged — the content you wrote is identical to the "
                "current file. Your fix had no effect. Re-read the file, identify what "
                "specifically needs to change, and write a corrected version."
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"Written {rel} ({len(content)} chars)."
```

- [ ] **Step 4: Run the new tests; they should pass**

```bash
source .venv/bin/activate && pytest tests/test_agent_loop.py::test_write_file_rejects_orphan_lambda_path tests/test_agent_loop.py::test_write_file_accepts_lambda_path_with_matching_s3key tests/test_agent_loop.py::test_write_file_no_check_outside_lambda_subdir -v
```

Expected: 3 passed.

- [ ] **Step 5: Run the full test suite to confirm no regression**

```bash
source .venv/bin/activate && pytest tests/ 2>&1 | tail -10
```

Expected: all pass. Pay attention to any existing `write_file` test that writes to `deployment/lambda/foo.py` against a template without that S3Key — those tests must either be updated to use a matching stem or add an `S3Key: foo.zip` line to their fixture template.

- [ ] **Step 6: Commit**

```bash
git add harness/agent/tools.py tests/test_agent_loop.py
git commit -F - <<'EOF'
fix(agent): refuse write_file to Lambda paths with no matching S3Key

Previously an agent writing deployment/lambda/typo.py (where typo has no
S3Key in the template) succeeded silently and only learned 2 turns later
that its fix was orphaned. The deployer now reports it on submit_fix, but
the keystroke-time check is strictly better: the agent immediately gets a
list of available stems and can correct the path on the next tool call.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
```

---

## Task 4: Switch functional_test.py contract to JSON output

**Files:**
- Create: `harness/shared/functional_test_helpers.py`
- Create: `tests/test_functional_test_helpers.py`
- Modify: `harness/shared/assertion_parser.py` — read JSON result file when present
- Modify: `harness/runner/scenario_runner.py:run_functional_tests` — pass JSON output path via env
- Modify: `harness/verify/pass1_functional.py:run_pass1` — same
- Modify: `corpus/arch_01_*/functional_test.py` (1 file)
- Modify: `corpus/arch_02_fuzzy_movie_search/functional_test.py`
- Modify: `corpus/arch_08_*/functional_test.py`
- Modify: `corpus/arch_12_*/functional_test.py`

### Why

Parsing assertions from regex-matched stdout is fragile: log lines colliding with the pattern win, test names containing hyphens silently disappear, multi-line messages truncate. A JSON output file is structured, the helper functions make functional tests easier to write, and the helpers can validate the schema (no more wondering whether a test crashed or just emitted nothing).

### Behaviour change

`functional_test.py` files import `from harness.shared.functional_test_helpers import emit_pass, emit_fail, finalize` and call them. The helpers buffer assertions in-memory and write a JSON file at `os.environ["ACE_BENCH_RESULTS_PATH"]` on `finalize()` (or atexit). The parser reads that JSON file if the env var is set and the file exists; otherwise it falls back to the stdout-regex path (no breakage for unmigrated tests).

- [ ] **Step 1: Create `harness/shared/functional_test_helpers.py`**

```python
"""Helpers for corpus functional_test.py files.

Functional tests no longer rely on regex-parsing of stdout. They call
emit_pass()/emit_fail() and finalize() (or rely on atexit), and the harness
reads the resulting JSON file.

Usage in a functional_test.py:

    from harness.shared.functional_test_helpers import emit_pass, emit_fail, finalize

    if check_thing():
        emit_pass("thing_works")
    else:
        emit_fail("thing_works", "thing returned None")
    finalize()  # or rely on atexit

The output file path is controlled by the ACE_BENCH_RESULTS_PATH env var.
If unset, the helpers still print 'ASSERT pass|fail ...' lines for the legacy
stdout-regex fallback.
"""
import atexit
import json
import os
from typing import Literal

_buffer: list[dict] = []
_finalized = False


def emit_pass(name: str, message: str = "") -> None:
    _emit("pass", name, message)


def emit_fail(name: str, message: str = "") -> None:
    _emit("fail", name, message)


def _emit(verdict: Literal["pass", "fail"], name: str, message: str) -> None:
    entry = {"name": name, "verdict": verdict, "message": message}
    _buffer.append(entry)
    # Also write to stdout for backwards-compat with stdout-regex fallback.
    print(f"ASSERT {verdict} {name}: {message}", flush=True)


def finalize() -> None:
    """Write the JSON results file if ACE_BENCH_RESULTS_PATH is set."""
    global _finalized
    if _finalized:
        return
    _finalized = True
    out_path = os.environ.get("ACE_BENCH_RESULTS_PATH")
    if not out_path:
        return
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"assertions": list(_buffer)}, f, indent=2)


# Belt-and-suspenders: if the test forgets to call finalize() but the process
# exits cleanly, write what we have. If it crashes, the file may be missing —
# the parser handles that as a __no_assertions__ failure.
atexit.register(finalize)
```

- [ ] **Step 2: Write tests for the helpers**

Create `tests/test_functional_test_helpers.py`:

```python
import json
import os
import subprocess
import sys
import textwrap

import harness.shared.functional_test_helpers as h


def _reset():
    h._buffer.clear()
    h._finalized = False


def test_emit_pass_buffers_entry(monkeypatch):
    _reset()
    h.emit_pass("a_check", "ok")
    assert h._buffer == [{"name": "a_check", "verdict": "pass", "message": "ok"}]


def test_emit_fail_buffers_entry(monkeypatch):
    _reset()
    h.emit_fail("b_check", "bad value")
    assert h._buffer[0]["verdict"] == "fail"


def test_finalize_writes_json_when_env_set(tmp_path, monkeypatch):
    _reset()
    out = tmp_path / "results.json"
    monkeypatch.setenv("ACE_BENCH_RESULTS_PATH", str(out))
    h.emit_pass("a")
    h.emit_fail("b", "msg")
    h.finalize()
    assert out.exists()
    data = json.loads(out.read_text())
    assert data["assertions"][0] == {"name": "a", "verdict": "pass", "message": ""}
    assert data["assertions"][1] == {"name": "b", "verdict": "fail", "message": "msg"}


def test_finalize_noop_when_env_unset(tmp_path, monkeypatch):
    _reset()
    monkeypatch.delenv("ACE_BENCH_RESULTS_PATH", raising=False)
    h.emit_pass("a")
    h.finalize()  # should not raise; no file written


def test_finalize_idempotent(tmp_path, monkeypatch):
    _reset()
    out = tmp_path / "r.json"
    monkeypatch.setenv("ACE_BENCH_RESULTS_PATH", str(out))
    h.emit_pass("a")
    h.finalize()
    # Mutate buffer post-finalize; a second call should NOT rewrite the file.
    h._buffer.append({"name": "c", "verdict": "pass", "message": ""})
    h.finalize()
    data = json.loads(out.read_text())
    assert len(data["assertions"]) == 1  # original write


def test_atexit_writes_file_when_test_forgets_finalize(tmp_path):
    """Spawn a subprocess that imports the helpers, emits, then exits without
    calling finalize. atexit should fire and write the file."""
    out = tmp_path / "r.json"
    script = tmp_path / "t.py"
    script.write_text(textwrap.dedent("""
        from harness.shared.functional_test_helpers import emit_pass
        emit_pass("a")
        # No finalize call — atexit should handle it.
    """))
    env = {**os.environ, "ACE_BENCH_RESULTS_PATH": str(out)}
    subprocess.run(
        [sys.executable, str(script)],
        env=env,
        check=True,
        cwd=os.path.dirname(os.path.dirname(__file__)),
    )
    data = json.loads(out.read_text())
    assert data["assertions"][0]["name"] == "a"
```

- [ ] **Step 3: Run the helper tests**

```bash
source .venv/bin/activate && pytest tests/test_functional_test_helpers.py -v
```

Expected: 6 passed.

- [ ] **Step 4: Teach `assertion_parser.parse` about the JSON path**

In `harness/shared/assertion_parser.py`, add a new public function `parse_with_json_fallback`:

```python
import json
import os


def parse_from_json_file(path: str) -> AssertionRunResult:
    """Read a structured results file produced by functional_test_helpers."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assertions = [
        AssertionResult(
            name=a["name"],
            verdict=a["verdict"],
            message=a.get("message", ""),
        )
        for a in data.get("assertions", [])
    ]
    return AssertionRunResult(assertions=assertions, returncode=0, crash_reason="")


def parse_with_fallback(
    output: str,
    returncode: int,
    json_path: str | None,
) -> AssertionRunResult:
    """Prefer the JSON results file when available; fall back to stdout regex.

    Crash detection (non-zero returncode, no assertions) is layered on top:
    if a JSON file says all-pass but the test exited non-zero, we still
    synthesize __test_crashed__.
    """
    if json_path and os.path.exists(json_path):
        result = parse_from_json_file(json_path)
        result.returncode = returncode
    else:
        result = parse(output, returncode=returncode)
        # Don't double-add crash markers — parse() already handled them.
        return result

    # Layer crash markers on top of the JSON result.
    if returncode != 0:
        result.assertions.append(AssertionResult(
            name="__test_crashed__",
            verdict="fail",
            message=f"functional_test.py exited with code {returncode}",
        ))
        result.crash_reason = f"exit_code_{returncode}"
    if not result.assertions:
        result.assertions.append(AssertionResult(
            name="__no_assertions__",
            verdict="fail",
            message="functional_test.py emitted no assertions.",
        ))
        result.crash_reason = "no_assertions_emitted"
    return result
```

Add a test for `parse_with_fallback` in `tests/test_assertion_parser.py`:

```python
def test_parse_with_fallback_prefers_json(tmp_path):
    json_path = tmp_path / "r.json"
    json_path.write_text('{"assertions": [{"name": "a", "verdict": "pass", "message": "ok"}]}')
    # stdout has a misleading 'ASSERT fail b' that should be ignored when JSON exists.
    r = parse_with_fallback("ASSERT fail b: bad\n", returncode=0, json_path=str(json_path))
    names = [a.name for a in r.assertions]
    assert names == ["a"]
    assert r.primary_assertions_passed is True


def test_parse_with_fallback_uses_stdout_when_json_absent(tmp_path):
    r = parse_with_fallback("ASSERT pass a: ok\n", returncode=0, json_path=str(tmp_path / "missing.json"))
    assert r.assertions[0].name == "a"


def test_parse_with_fallback_layers_crash_on_json(tmp_path):
    json_path = tmp_path / "r.json"
    json_path.write_text('{"assertions": [{"name": "a", "verdict": "pass", "message": ""}]}')
    r = parse_with_fallback("", returncode=137, json_path=str(json_path))
    names = [a.name for a in r.assertions]
    assert "__test_crashed__" in names
    assert r.crash_reason == "exit_code_137"
```

Update the import in `tests/test_assertion_parser.py`:

```python
from harness.shared.assertion_parser import parse, parse_from_json_file, parse_with_fallback
```

- [ ] **Step 5: Update `run_functional_tests` and `run_pass1` to set the env var and use `parse_with_fallback`**

In `harness/runner/scenario_runner.py:run_functional_tests`:

```python
    def run_functional_tests(self) -> AssertionRunResult:
        corpus_dir = corpus_dir_for_scenario(self.scenario_dir)
        functional_test = corpus_dir / "functional_test.py"
        if not functional_test.exists():
            raise FileNotFoundError(f"functional_test.py not found: {functional_test}")
        # Allocate a unique results path per invocation so concurrent runs don't collide.
        results_path = os.path.join(
            "results", self.run_id, f"functional_results_{int(time.time() * 1000)}.json"
        )
        os.makedirs(os.path.dirname(results_path), exist_ok=True)
        env = {**os.environ, "ACE_BENCH_RESULTS_PATH": os.path.abspath(results_path)}
        proc = subprocess.run(
            [sys.executable, str(functional_test)],
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
        return assertion_parser.parse_with_fallback(
            proc.stdout + "\n" + proc.stderr,
            returncode=proc.returncode,
            json_path=results_path,
        )
```

Similarly in `harness/verify/pass1_functional.py:run_pass1`:

```python
def run_pass1(corpus_dir: str) -> AssertionRunResult:
    functional_test = os.path.join(corpus_dir, "functional_test.py")
    # Allocate a temp results path scoped to this run_pass1 call.
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        results_path = tf.name
    try:
        env = {**os.environ, "ACE_BENCH_RESULTS_PATH": results_path}
        proc = subprocess.run(
            [sys.executable, functional_test],
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
        return assertion_parser.parse_with_fallback(
            proc.stdout + proc.stderr,
            returncode=proc.returncode,
            json_path=results_path,
        )
    finally:
        if os.path.exists(results_path):
            os.unlink(results_path)
```

- [ ] **Step 6: Run the full test suite to confirm nothing breaks**

```bash
source .venv/bin/activate && pytest tests/ 2>&1 | tail -10
```

Expected: all pass. The unmigrated corpus functional_test.py files still emit `ASSERT ...` lines and the fallback path picks them up.

- [ ] **Step 7: Migrate corpus functional_test.py files**

For each of the 4 corpus files, update the assertion-emitting code from `print(f"ASSERT ...")` to `emit_pass`/`emit_fail`.

`corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/functional_test.py`:

```python
# At the top of the file, replace the local `emit` function:
from harness.shared.functional_test_helpers import emit_pass, emit_fail, finalize

# Delete the local `def emit(result, name, message=""):` function.
# Find every call site:
#   emit("pass", "name", "msg")  →  emit_pass("name", "msg")
#   emit("fail", "name", "msg")  →  emit_fail("name", "msg")
# At the very end of the script, before sys.exit(0), call finalize().
```

(Apply the same pattern to all 4 corpus files. The exact line numbers vary; use `sed` or per-file edits.)

A canned migration sequence:

```bash
for f in corpus/arch_*/functional_test.py; do
  # Add the import at the top, after existing imports.
  # Replace local emit() definition.
  # Replace calls.
  # Add finalize() call at end.
  # (Done manually per file because the structure varies.)
  echo "Migrating $f"
done
```

Implementation note: each file has slightly different shape. The migrator MUST:
1. Read the file first.
2. Identify the existing `emit()` helper and delete its definition.
3. Add `from harness.shared.functional_test_helpers import emit_pass, emit_fail, finalize` to imports.
4. Replace every `emit("pass", name, msg)` with `emit_pass(name, msg)`.
5. Replace every `emit("fail", name, msg)` with `emit_fail(name, msg)`.
6. Insert `finalize()` immediately before the trailing `sys.exit(0)` (or at the end if absent).

Do this for each corpus file:
- `corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/functional_test.py`
- `corpus/arch_02_fuzzy_movie_search/functional_test.py`
- `corpus/arch_08_event_driven_architecture_with_sns_fifo_dynamodb_lambda_and_s3/functional_test.py`
- `corpus/arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3/functional_test.py`

- [ ] **Step 8: Verify the migration produced valid JSON output by running one functional test manually**

```bash
source .venv/bin/activate
mkdir -p /tmp/ace-test-results
ACE_BENCH_RESULTS_PATH=/tmp/ace-test-results/r.json python corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/functional_test.py || true
cat /tmp/ace-test-results/r.json | python -m json.tool | head -30
```

Expected: file contains a list of assertion entries with `name`, `verdict`, `message`. (The test will likely fail-fast against an empty LocalStack; that's OK — what we're checking is that `emit_pass`/`emit_fail` calls produce JSON output.)

- [ ] **Step 9: Run the full test suite**

```bash
source .venv/bin/activate && pytest tests/ 2>&1 | tail -10
```

Expected: all pass.

- [ ] **Step 10: Commit**

```bash
git add harness/shared/functional_test_helpers.py harness/shared/assertion_parser.py \
        harness/runner/scenario_runner.py harness/verify/pass1_functional.py \
        tests/test_functional_test_helpers.py tests/test_assertion_parser.py \
        corpus/arch_*/functional_test.py
git commit -F - <<'EOF'
refactor(verify): switch functional_test.py contract to structured JSON output

The stdout-regex protocol was fragile: log lines colliding with the ASSERT
regex won, names with hyphens silently disappeared, multi-line messages
truncated. functional_test.py files now call emit_pass()/emit_fail() and
finalize(); the harness reads a structured JSON results file.

The stdout-regex path remains as a fallback for any unmigrated tests so
this change is non-breaking. All 4 corpus tests migrated in this commit.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
```

---

## Task 5: Unify `deploy()` and introduce `SubmissionState`

**Files:**
- Modify: `harness/shared/types.py` — add `SubmissionState`
- Modify: `harness/runner/scenario_runner.py` — replace 3 deploy methods with 1
- Modify: `harness/run.py` — pass single `deploy()` callback to agent loop
- Modify: `harness/agent/loop.py` — accept single callback signature
- Test: `tests/test_runner.py`, `tests/test_agent_loop.py`

### Why

Today there are three deploy methods on `ScenarioRunner` (`attempt_deployment`, `attempt_redeployment`, `on_model_redeploy`) that are ~90% the same code. Their subtle differences — who sets `self.submitted`, who updates `_last_deployment_outcome` — caused the Task 3 bug from the hardening plan (silent stale-outcome). One method with explicit state on a `SubmissionState` object eliminates the duplication and the drift class.

### Behaviour change

The agent loop now receives `deploy_callback=runner.deploy` and `runner.deploy()` takes one optional argument `is_initial: bool`. The previous `attempt_deployment` (initial) vs `attempt_redeployment` (retry) distinction collapses into `runner.deploy(is_initial=...)`. State is centralised in `runner.submission_state`.

- [ ] **Step 1: Add `SubmissionState` to `harness/shared/types.py`**

```python
@dataclass
class SubmissionState:
    """Persistent state across submit_fix attempts within one scenario run."""
    submitted: bool = False
    last_outcome: DeploymentOutcome = "unknown"
    deploy_attempts: int = 0
```

- [ ] **Step 2: Write failing tests for the unified `deploy()` method**

Add to `tests/test_runner.py`:

```python
def test_deploy_initial_locks_submitted_on_success(tmp_path, mocker):
    mocker.patch("harness.runner.scenario_runner.init_run")
    mocker.patch("harness.runner.scenario_runner.snapshot", return_value={})
    runner = ScenarioRunner(str(tmp_path), "run-1")
    mocker.patch(
        "harness.runner.scenario_runner.handle_submission",
        return_value=DeploymentResult(outcome="deploy_success"),
    )
    r = runner.deploy(is_initial=True)
    assert r.success
    assert runner.submission_state.submitted is True
    assert runner.submission_state.last_outcome == "deploy_success"
    assert runner.submission_state.deploy_attempts == 1


def test_deploy_initial_blocked_after_success(tmp_path, mocker):
    mocker.patch("harness.runner.scenario_runner.init_run")
    mocker.patch("harness.runner.scenario_runner.snapshot", return_value={})
    runner = ScenarioRunner(str(tmp_path), "run-2")
    runner.submission_state.submitted = True
    r = runner.deploy(is_initial=True)
    assert r.success is False
    assert r.outcome == "unknown"
    assert "Already submitted" in r.error


def test_deploy_retry_does_not_check_submitted(tmp_path, mocker):
    mocker.patch("harness.runner.scenario_runner.init_run")
    mocker.patch("harness.runner.scenario_runner.snapshot", return_value={})
    runner = ScenarioRunner(str(tmp_path), "run-3")
    runner.submission_state.submitted = True  # earlier success
    mocker.patch(
        "harness.runner.scenario_runner.handle_submission",
        return_value=DeploymentResult(outcome="deploy_fail"),
    )
    r = runner.deploy(is_initial=False)
    assert r.outcome == "deploy_fail"
    assert runner.submission_state.last_outcome == "deploy_fail"
    assert runner.submission_state.deploy_attempts == 1


def test_deploy_increments_attempt_counter(tmp_path, mocker):
    mocker.patch("harness.runner.scenario_runner.init_run")
    mocker.patch("harness.runner.scenario_runner.snapshot", return_value={})
    runner = ScenarioRunner(str(tmp_path), "run-4")
    mocker.patch(
        "harness.runner.scenario_runner.handle_submission",
        return_value=DeploymentResult(outcome="deploy_fail"),
    )
    runner.deploy(is_initial=True)
    runner.deploy(is_initial=False)
    runner.deploy(is_initial=False)
    assert runner.submission_state.deploy_attempts == 3
```

Run them; expect failure (method doesn't exist).

- [ ] **Step 3: Add `submission_state` and `deploy()` to `ScenarioRunner`**

In `harness/runner/scenario_runner.py`:

a. Add to `__init__`:

```python
        self.submission_state = SubmissionState()
```

(Remove the standalone `self.submitted` and `self._last_deployment_outcome` attributes — they're now derived from `submission_state`. Keep the property aliases below for run.py compat.)

b. Add the unified method (replacing the three older ones):

```python
    def deploy(self, *, is_initial: bool = False) -> DeploymentResult:
        """Single submission entry point — replaces attempt_deployment /
        attempt_redeployment / on_model_redeploy.

        is_initial=True: the first submit of the scenario. Locks submitted on
            success and refuses if already locked.
        is_initial=False: a retry after a failed deploy or failed tests. Does
            not check the submitted lock and does not toggle it.
        """
        with self._lock:
            if is_initial and self.submission_state.submitted:
                return DeploymentResult(outcome="unknown", error="Already submitted (final).")
        result = handle_submission(
            self.scenario_dir, self.run_id,
            self.start_snapshot, self.start_faulted_yaml,
        )
        self.submission_state.deploy_attempts += 1
        self.submission_state.last_outcome = result.outcome
        if is_initial and result.success:
            with self._lock:
                self.submission_state.submitted = True
        return result
```

c. Delete `attempt_deployment`, `attempt_redeployment`, and `on_model_redeploy`.

d. Add backwards-compat properties for any callers that still read `self.submitted` / `self._last_deployment_outcome` (e.g. `run.py` Step 8):

```python
    @property
    def submitted(self) -> bool:
        return self.submission_state.submitted

    @submitted.setter
    def submitted(self, v: bool) -> None:
        self.submission_state.submitted = v

    @property
    def _last_deployment_outcome(self) -> str:
        return self.submission_state.last_outcome

    @_last_deployment_outcome.setter
    def _last_deployment_outcome(self, v: str) -> None:
        self.submission_state.last_outcome = v
```

Add the `SubmissionState` import.

- [ ] **Step 4: Update `harness/run.py` to pass the unified callback**

In `harness/run.py`, replace the `run_agent_loop` invocation arguments:

```python
            asyncio.run(
                run_agent_loop(
                    model=_model,
                    api_key=_api_key,
                    base_url=_base_url,
                    extra_headers=_extra_headers,
                    context=ctx,
                    scenario_dir=scenario_dir,
                    run_id=run_id,
                    harness_api_key=_harness_key,
                    verbose=args.verbose,
                    deploy_callback=lambda: runner.deploy(is_initial=True),
                    redeploy_callback=lambda: runner.deploy(is_initial=False),
                    verify_callback=runner.run_functional_tests,
                    max_test_retries=5,
                )
            )
```

(Two thin lambdas — agent-loop code stays unchanged.)

- [ ] **Step 5: Update existing test_runner.py tests**

Old tests of `attempt_deployment` / `attempt_redeployment` / `on_model_redeploy`:

- `test_submitted_flag_prevents_second_redeployment` — change to `runner.deploy(is_initial=True)` twice.
- `test_attempt_deployment_returns_success_dict` → `test_deploy_initial_returns_success_dict`, call `deploy(is_initial=True)`.
- `test_attempt_deployment_returns_failure_dict_on_no_changes` → similar.
- `test_attempt_deployment_blocked_after_success` → covered by new `test_deploy_initial_blocked_after_success`.
- `test_attempt_redeployment_runs_when_already_submitted` → covered by `test_deploy_retry_does_not_check_submitted`.
- `test_attempt_redeployment_never_sets_submitted_on_success` — covered: after `deploy(is_initial=False)` returning success, `submitted` remains whatever it was.
- `test_attempt_redeployment_returns_failure_dict` → call `deploy(is_initial=False)` against a no_changes mock.
- `test_attempt_redeployment_updates_last_deployment_outcome` → covered.
- `test_attempt_redeployment_updates_outcome_on_success` → covered.

Delete obsolete tests and keep / rename the remaining ones.

- [ ] **Step 6: Run the full test suite**

```bash
source .venv/bin/activate && pytest tests/ 2>&1 | tail -10
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add harness/shared/types.py harness/runner/scenario_runner.py \
        harness/run.py tests/test_runner.py
git commit -F - <<'EOF'
refactor(runner): unify attempt_deployment/attempt_redeployment/on_model_redeploy into deploy()

Three near-identical methods with subtle state-update differences became
one method with an explicit is_initial flag. Submission state is owned
by a SubmissionState dataclass on the runner (replacing the scattered
self.submitted / self._last_deployment_outcome attributes).

Eliminates the class of bug from the previous hardening plan (Task 3)
where one method forgot to update _last_deployment_outcome.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
```

---

## Task 6: Per-submission deployment audit log

**Files:**
- Modify: `harness/shared/result_logger.py` — `log_deployment()`
- Modify: `harness/runner/deployment_handler.py` — call it
- Test: `tests/test_shared.py`

### Why

Today, debugging "why didn't my fix deploy?" requires reading CloudFormation events, listing S3 keys, diffing the filesystem, and reading file_change_log.json. A single `deployment_log.json` recording the `PackagingPlan`, the `DeploymentResult`, and the CFN outcome turns this into "read one file."

### Behaviour change

A new file appears at `results/<run_id>/deployment_log.json` after every submit_fix. Format: array of entries (one per submission attempt) recording packaged files, orphans, CFN outcome, and SHA256 of each uploaded zip. Existing files (tool_call_trace.json, file_change_log.json, verify_result.json, faulted_baseline.json) are unchanged.

- [ ] **Step 1: Add `log_deployment()` to `harness/shared/result_logger.py`**

Read `harness/shared/result_logger.py` first to follow existing patterns.

Append:

```python
from harness.shared.types import DeploymentResult, PackagingPlan


def log_deployment(run_id: str, plan: PackagingPlan, result: DeploymentResult) -> None:
    """Append one entry to results/<run_id>/deployment_log.json."""
    path = os.path.join("results", run_id, "deployment_log.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    entries: list = []
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            try:
                entries = json.load(f)
            except json.JSONDecodeError:
                entries = []
    entry = {
        "outcome": result.outcome,
        "error": result.error,
        "uploads": [
            {
                "rel_path": u.rel_path,
                "stem": u.stem,
                "s3_key_original": u.s3_key_original,
                "s3_key_new": u.s3_key_new,
                "sha256": u.sha256,
                "arcname": u.arcname,
            }
            for u in plan.uploads
        ],
        "orphans": plan.orphans,
        "template_changed": plan.template_changed,
        "packaged_files": result.packaged_files,
        "cfn_events": [
            {"logical_id": e.logical_id, "status": e.status, "reason": e.reason}
            for e in result.cfn_events
        ],
    }
    entries.append(entry)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)
```

- [ ] **Step 2: Write a test for `log_deployment`**

Add to `tests/test_shared.py`:

```python
def test_log_deployment_appends_entries(tmp_path, monkeypatch):
    from harness.shared.result_logger import log_deployment
    from harness.shared.types import DeploymentResult, LambdaUpload, PackagingPlan
    monkeypatch.chdir(tmp_path)

    plan1 = PackagingPlan(uploads=[LambdaUpload(
        rel_path="lambda/h.py", stem="h", s3_key_original="h.zip",
        s3_key_new="lambdas/r/abc/h.zip", sha256="abc", arcname="index.py",
    )])
    log_deployment("run-x", plan1, DeploymentResult(outcome="deploy_success"))
    log_deployment("run-x", PackagingPlan(orphans=["lambda/typo.py"]),
                   DeploymentResult(outcome="no_changes", error="..."))

    import json
    data = json.loads((tmp_path / "results" / "run-x" / "deployment_log.json").read_text())
    assert len(data) == 2
    assert data[0]["outcome"] == "deploy_success"
    assert data[0]["uploads"][0]["sha256"] == "abc"
    assert data[1]["orphans"] == ["lambda/typo.py"]
```

Run; expect failure.

- [ ] **Step 3: Wire `log_deployment` into `handle_submission`**

In `harness/runner/deployment_handler.py`, at the very end of `handle_submission`, before each `return DeploymentResult(...)`, capture the result and call the logger. The cleanest way: refactor so all four branches build the `DeploymentResult` first, log, then return. Use a try/finally pattern:

```python
def handle_submission(scenario_dir: str, run_id: str, start_snapshot: dict, start_faulted_yaml: str = "") -> DeploymentResult:
    plan = PackagingPlan()  # default; updated below
    try:
        # ... existing steps 1-3b build plan and template_body ...
        plan = _build_packaging_plan(diff, template_body, deployment_dir, run_id)
        # ... existing steps 3c, 4 ...
        # Each branch builds `result = DeploymentResult(...)` instead of returning directly.
        # Final: log_deployment(run_id, plan, result) ; return result.
```

Concretely: introduce a local `result = None` at the top, replace every `return DeploymentResult(...)` with `result = DeploymentResult(...)`, and at the bottom:

```python
    log_deployment(run_id, plan, result)
    return result
```

Don't forget the lint_fail branch — it returns before `plan` is built. Just construct an empty plan in that branch:

```python
    if not lint_result["passed"]:
        result = DeploymentResult(
            outcome="lint_fail",
            lint_errors=lint_result["fatal_errors"],
        )
        log_deployment(run_id, plan, result)
        return result
```

Add the import:

```python
from harness.shared.result_logger import log_deployment, log_file_change
```

- [ ] **Step 4: Run the test; should pass**

```bash
source .venv/bin/activate && pytest tests/test_shared.py::test_log_deployment_appends_entries -v
```

- [ ] **Step 5: Run the full suite**

```bash
source .venv/bin/activate && pytest tests/ 2>&1 | tail -10
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add harness/shared/result_logger.py harness/runner/deployment_handler.py tests/test_shared.py
git commit -F - <<'EOF'
feat(runner): log per-submission deployment audit trail

Every submit_fix now appends to results/<run_id>/deployment_log.json the
packaging plan (what zipped, with SHA + arcname + S3Key), orphan files,
CloudFormation outcome, and events. Debugging "why didn't my fix deploy?"
becomes a one-file read instead of cross-referencing CFN events + S3 +
filesystem.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
```

---

## Task 7: Composable verify pipeline

**Files:**
- Create: `harness/verify/pipeline.py` — `VerifyStep` protocol, `run_pipeline()`
- Modify: `harness/verify/verify_loop.py` — orchestrate via pipeline
- Modify: `harness/verify/pass1_functional.py` / `pass2_regression.py` / `pass3_classification.py` / `pass4_concurrency.py` — wrap as `VerifyStep`s
- Test: `tests/test_verify.py`

### Why

Today `run_verify_loop` is a chain of explicit `run_pass1` → `run_pass2` → `run_pass3` → maybe `run_pass4` calls, with the pass4-overrides-pass3 rule applied via direct dict mutation. Adding a new step or changing the override rule requires editing the orchestrator. A `VerifyStep` protocol + a list of steps + named post-processors makes the structure explicit.

### Behaviour change

None. The output of `run_verify_loop` is unchanged. Internally, the orchestrator runs a `list[VerifyStep]` instead of hardcoded function calls.

- [ ] **Step 1: Create `harness/verify/pipeline.py`**

```python
"""Composable verification pipeline.

A VerifyStep is a callable that consumes a VerifyContext and produces a
StepResult. run_pipeline() runs each step in order, threading prior results
through the context. Post-processors run after all steps and may rewrite
their results (e.g. the pass4-fail-downgrades-pass3 rule).
"""
from dataclasses import dataclass, field
from typing import Callable, Protocol

from harness.shared.types import AssertionRunResult


@dataclass
class VerifyContext:
    scenario_dir: str
    run_id: str
    manifest_path: str | None
    corpus_dir: str
    api_endpoint: str
    # Filled in as steps run:
    pass1_result: AssertionRunResult | None = None
    fault_class: str | None = None
    results: dict = field(default_factory=dict)


class VerifyStep(Protocol):
    name: str
    def should_run(self, ctx: VerifyContext) -> bool: ...
    def run(self, ctx: VerifyContext): ...


def run_pipeline(
    ctx: VerifyContext,
    steps: list[VerifyStep],
    postprocessors: list[Callable[[VerifyContext], None]],
) -> dict:
    """Execute steps in order, then run post-processors. Returns ctx.results."""
    for step in steps:
        if not step.should_run(ctx):
            ctx.results[step.name] = None
            continue
        ctx.results[step.name] = step.run(ctx)
    for pp in postprocessors:
        pp(ctx)
    return ctx.results
```

- [ ] **Step 2: Wrap each pass as a `VerifyStep` class**

In `harness/verify/pass1_functional.py`, add (don't replace):

```python
class Pass1Step:
    name = "pass1_functional"

    def should_run(self, ctx) -> bool:
        return True

    def run(self, ctx):
        result = run_pass1(ctx.corpus_dir)
        ctx.pass1_result = result
        return result.to_baseline_dict()
```

In `harness/verify/pass2_regression.py`, add:

```python
class Pass2Step:
    name = "pass2_regression"

    def should_run(self, ctx) -> bool:
        return ctx.pass1_result is not None

    def run(self, ctx):
        return run_pass2(ctx.scenario_dir, ctx.run_id, ctx.pass1_result)
```

In `harness/verify/pass3_classification.py`, add:

```python
class Pass3Step:
    name = "pass3_classification"

    def should_run(self, ctx) -> bool:
        return ctx.pass1_result is not None and bool(ctx.manifest_path)

    def run(self, ctx):
        return run_pass3(ctx.scenario_dir, ctx.run_id, ctx.pass1_result, ctx.manifest_path)
```

In `harness/verify/pass4_concurrency.py`, add:

```python
class Pass4Step:
    name = "pass4_concurrency"

    def should_run(self, ctx) -> bool:
        return ctx.fault_class in {"performance", "reliability"} and bool(ctx.api_endpoint)

    def run(self, ctx):
        return run_pass4(ctx.scenario_dir, ctx.manifest_path, ctx.api_endpoint)
```

- [ ] **Step 3: Move the override rule into a named post-processor**

In `harness/verify/pipeline.py`, append:

```python
def downgrade_pass3_when_pass4_fails(ctx: VerifyContext) -> None:
    """If pass4 ran and failed, pass3's classification drops to 'partial'
    even if pass1's primary assertions passed. Was previously action-at-a-
    distance inside verify_loop; now an explicit named step."""
    pass4 = ctx.results.get("pass4_concurrency")
    pass3 = ctx.results.get("pass3_classification")
    if pass4 is None or pass3 is None:
        return
    if pass4.get("passed"):
        return
    if ctx.pass1_result and ctx.pass1_result.primary_assertions_passed:
        pass3 = dict(pass3)
        pass3["classification"] = "partial"
        pass3["root_cause_addressed"] = False
        ctx.results["pass3_classification"] = pass3
```

- [ ] **Step 4: Rewrite `run_verify_loop` to use the pipeline**

In `harness/verify/verify_loop.py`:

```python
import json
import os

from harness.shared.result_logger import log_verify_result
from harness.verify.pass1_functional import Pass1Step
from harness.verify.pass2_regression import Pass2Step
from harness.verify.pass3_classification import Pass3Step
from harness.verify.pass4_concurrency import Pass4Step
from harness.verify.pipeline import (
    VerifyContext,
    downgrade_pass3_when_pass4_fails,
    run_pipeline,
)


def run_verify_loop(
    scenario_dir: str,
    run_id: str,
    deployment_outcome: str,
    manifest_path: str = None,
    corpus_dir: str = None,
    api_endpoint: str = None,
) -> dict:
    if deployment_outcome != "deploy_success":
        result = {
            "outcome": "did_not_deploy",
            "pass1_functional": None,
            "pass2_regression": None,
            "pass3_classification": None,
            "pass4_concurrency": None,
        }
        log_verify_result(run_id, result)
        return result

    fault_class = None
    if manifest_path and os.path.isfile(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as f:
            fault_class = json.load(f).get("fault_class")

    ctx = VerifyContext(
        scenario_dir=scenario_dir,
        run_id=run_id,
        manifest_path=manifest_path,
        corpus_dir=corpus_dir or scenario_dir,
        api_endpoint=api_endpoint or "",
        fault_class=fault_class,
    )
    results = run_pipeline(
        ctx,
        steps=[Pass1Step(), Pass2Step(), Pass3Step(), Pass4Step()],
        postprocessors=[downgrade_pass3_when_pass4_fails],
    )
    result = {"outcome": "completed", **results}
    log_verify_result(run_id, result)
    return result
```

- [ ] **Step 5: Add a test for `run_pipeline`**

Append to `tests/test_verify.py`:

```python
def test_pipeline_skips_step_when_should_run_false(tmp_path):
    from harness.verify.pipeline import VerifyContext, run_pipeline

    class AlwaysSkipStep:
        name = "skipper"
        def should_run(self, ctx): return False
        def run(self, ctx): raise AssertionError("should not run")

    class AlwaysRunStep:
        name = "runner"
        def should_run(self, ctx): return True
        def run(self, ctx): return {"done": True}

    ctx = VerifyContext(
        scenario_dir="", run_id="", manifest_path=None,
        corpus_dir="", api_endpoint="",
    )
    results = run_pipeline(ctx, steps=[AlwaysSkipStep(), AlwaysRunStep()], postprocessors=[])
    assert results["skipper"] is None
    assert results["runner"] == {"done": True}


def test_pipeline_postprocessor_mutates_results():
    from harness.verify.pipeline import VerifyContext, run_pipeline

    class StepA:
        name = "a"
        def should_run(self, ctx): return True
        def run(self, ctx): return {"x": 1}

    def double_x(ctx):
        ctx.results["a"]["x"] *= 2

    ctx = VerifyContext(
        scenario_dir="", run_id="", manifest_path=None,
        corpus_dir="", api_endpoint="",
    )
    results = run_pipeline(ctx, steps=[StepA()], postprocessors=[double_x])
    assert results["a"]["x"] == 2
```

- [ ] **Step 6: Run the full suite**

```bash
source .venv/bin/activate && pytest tests/ 2>&1 | tail -10
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add harness/verify/ tests/test_verify.py
git commit -F - <<'EOF'
refactor(verify): compose verify pipeline from named steps

run_verify_loop was a chain of hardcoded function calls with a pass4-mutates-
pass3 override applied via direct dict mutation. The override rule is now an
explicit, named post-processor (downgrade_pass3_when_pass4_fails). Each pass
is a VerifyStep class with should_run/run methods. Adding a new step or
changing an override rule no longer requires editing the orchestrator.

Behaviour unchanged: verify_result.json output is identical.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
```

---

## Final Verification

- [ ] **Step 1: Run the full test suite**

```bash
source .venv/bin/activate && pytest tests/ -v 2>&1 | tail -15
```

Expected: 0 failures.

- [ ] **Step 2: Confirm the 7 commits land in order**

```bash
git log --oneline -8
```

Expected: 7 new commits on top of the previous HEAD.

- [ ] **Step 3: Spot-check by running one scenario end-to-end (LocalStack required)**

```bash
localstack start -d
until localstack status services 2>/dev/null | grep -q "running"; do sleep 2; done
HARNESS_API_KEY="test-key" python harness/run.py scenarios/arch01_fault01_connectivity/ \
  --model anthropic/claude-haiku-4-5-20251001 \
  --verbose 2>&1 | tail -50
```

Expected: the agent loop runs, deploys, tests run, and `results/<run_id>/deployment_log.json` exists and is well-formed.

(This step is optional — the test suite covers the contract. Run it only if a final integration check is desired.)

---

## Trade-offs & limitations

- **One big plan, one big PR.** Doing the seven refactors as separate plans would have been safer (smaller blast radius per merge) but would have produced inconsistent code in between: e.g. half the codebase using dataclasses, half using dicts. The dependency graph between cleanups makes the staggered approach awkward.
- **The functional_test.py JSON contract requires a corpus migration.** Task 4 touches every functional_test.py file. If a new corpus is added later, its functional_test.py author must know about the helpers. Mitigated by leaving the stdout-regex fallback in place — unmigrated tests still work, just less robustly.
- **The `to_baseline_dict()` serialization keeps the verify_result.json on-disk schema unchanged** so the scorer dimensions don't need refactoring. This is a small dataclass→dict→dataclass round-trip cost in exchange for not touching the scorer. If the scorer is ever refactored (out of scope here), it should consume `AssertionRunResult` directly.
- **`SubmissionState` lives on the runner; the agent loop keeps its own state (`writes_made`, `writes_since_last_submit`, etc.).** A future plan could unify these into one state object owned by the runner with methods that the agent loop drives. That's a deeper refactor than this plan covers.
- **No worktree.** Each task ends in a committable state, so a reviewer can pause between tasks. If running in a long-lived branch is unacceptable, wrap the plan in `superpowers:using-git-worktrees` per the executing-plans skill.
