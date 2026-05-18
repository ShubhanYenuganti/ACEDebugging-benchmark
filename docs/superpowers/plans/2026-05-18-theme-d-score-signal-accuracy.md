# Theme D: Score Signal Accuracy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three silent signal failures in the verify pipeline that cause Pass 3 classification to be wrong, Pass 4 to silently skip non-HTTP faults, and the `invalid_patch_detected` heuristic to always fire as False regardless of what the model actually changed.

**Architecture:** Three independent surgical fixes — (1) a `_cfn_normalize()` helper in pass3_classification that converts CloudFormation intrinsic-function dict representations to their YAML-shorthand equivalents before string comparison, making `structural_match` correct for templates that use `!Ref`/`!Sub`/etc.; (2) exposing a proper `{"skipped": true}` result from Pass 4 when it cannot run (rather than silently storing `None`) so scoring can distinguish "not applicable" from "ran and passed"; (3) adding `diff_text` to the `diff_snapshots` return value in file_differ so that the `invalid_patch_detected` signal in Pass 3 can actually function.

**Tech Stack:** Python 3.11, pytest, pytest-mock (existing tooling).

---

## Background — Three Bugs

### Bug 4.9: `structural_match` false-negative with CFN intrinsic functions

`harness/verify/pass3_classification.py` line 89 computes:

```python
structural_match = submitted_value == original_value
```

`submitted_value` comes from `yaml.safe_load` on the submitted template. `original_value` comes from `fault_manifest.json`. The manifest stores intrinsic functions as JSON dicts: `{"Ref": "MyBucket"}`. `yaml.safe_load` on a template that contains `!Ref MyBucket` produces the Python string `"MyBucket"`, not the dict. So `submitted_value == original_value` evaluates `"MyBucket" == {"Ref": "MyBucket"}` → `False`, even though the template is structurally correct. This causes every scenario that uses CFN intrinsics in a fault property to score as `structural_mismatch` regardless of the model's fix.

### Bug 4.11: Pass 4 silently skips non-HTTP scenarios

`harness/verify/pass4_concurrency.py` line 12:

```python
def should_run(self, ctx) -> bool:
    return ctx.fault_class in {"performance", "reliability"} and bool(ctx.api_endpoint)
```

When `ctx.api_endpoint` is empty (e.g., a reliability fault on an SQS pipeline with no HTTP endpoint), `should_run` returns `False`. The pipeline stores `None` for `pass4_concurrency` in `ctx.results`. The scoring dimension for Pass 4 receives `None` and has no way to distinguish "Pass 4 didn't run because it wasn't applicable" from "Pass 4 ran and the result object is broken." An explicit skipped sentinel makes the signal unambiguous.

### Bug 4.20: `diff_text` missing from file_differ, making `invalid_patch_detected` dead

`harness/shared/file_differ.py:diff_snapshots` returns a dict with keys `files_added`, `files_modified`, `files_removed`, `per_file_line_changes`, `total_files_changed`, `total_lines_changed`. There is no `diff_text` key. In `pass3_classification.py` line 97:

```python
diff_text = change_log.get("diff_text", "")
```

This always returns `""`, so:

```python
invalid_patch_detected = len(diff_text) > MAX_DIFF_BYTES
```

is always `False`. The `invalid_patch_detected` field in Pass 3 output is therefore meaningless, and the scoring dimension that uses it cannot penalise oversized patches.

---

## File Structure

Changes are confined to three production files and two test files:

- **Modify** `harness/verify/pass3_classification.py` — add `_cfn_normalize()` helper; apply it in `structural_match` computation.
- **Modify** `harness/verify/pass4_concurrency.py` — move HTTP check from `should_run` into `run_pass4`; return `{"skipped": True, "reason": "no_api_endpoint"}` when endpoint absent.
- **Modify** `harness/shared/file_differ.py` — add `diff_text` key to `diff_snapshots` return value.
- **Test** `tests/test_verify.py` — new tests for all three fixes.

No new files. No reorganization.

---

## Task 1: `_cfn_normalize()` for Pass 3 structural match (Bug 4.9)

**Files:**
- Modify: `harness/verify/pass3_classification.py`
- Test: `tests/test_verify.py`

### Why

The fault manifest stores intrinsic functions as JSON dicts (e.g., `{"Ref": "MyBucket"}`), while `yaml.safe_load` on a CloudFormation template converts `!Ref MyBucket` to the Python string `"MyBucket"`. A direct equality check produces a false-negative for any scenario where the fault property involves an intrinsic function. `_cfn_normalize()` recursively converts the common CFN intrinsic dict forms back to their YAML-shorthand string equivalents so both sides are comparable.

### Behavior change

New `_cfn_normalize(value)` function. Applied to both `submitted_value` and `original_value` before the equality check on line 89. Handles `{"Ref": str}`, `{"Fn::Sub": str}`, `{"Fn::ImportValue": str}`, `{"Fn::GetAtt": list}`, and other single-key `Fn::*` dicts. For values that are not intrinsic dicts, returns the value unchanged.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_verify.py`:

```python
from harness.verify.pass3_classification import _cfn_normalize


class TestCfnNormalize:
    def test_ref_dict_becomes_string(self):
        assert _cfn_normalize({"Ref": "MyBucket"}) == "MyBucket"

    def test_fn_sub_dict_becomes_string(self):
        assert _cfn_normalize({"Fn::Sub": "arn:aws:s3:::${MyBucket}"}) == "arn:aws:s3:::${MyBucket}"

    def test_fn_getatt_dict_stays_as_list_joined(self):
        result = _cfn_normalize({"Fn::GetAtt": ["MyFunction", "Arn"]})
        assert result == "MyFunction.Arn"

    def test_fn_importvalue_dict_becomes_string(self):
        assert _cfn_normalize({"Fn::ImportValue": "SharedBucketName"}) == "SharedBucketName"

    def test_plain_string_unchanged(self):
        assert _cfn_normalize("us-east-1") == "us-east-1"

    def test_plain_int_unchanged(self):
        assert _cfn_normalize(512) == 512

    def test_plain_none_unchanged(self):
        assert _cfn_normalize(None) is None

    def test_non_intrinsic_dict_unchanged(self):
        d = {"key": "value", "other": "thing"}
        assert _cfn_normalize(d) == d

    def test_structural_match_with_ref_intrinsic(self, tmp_path, monkeypatch):
        """structural_match must be True when submitted uses !Ref matching manifest {"Ref":...}."""
        import json, yaml
        monkeypatch.setenv("RESULTS_DIR", str(tmp_path))
        manifest = {
            "fault_id": "arch01_fault01",
            "fault_class": "connectivity",
            "fault_property": "Handler",
            "original_value": {"Ref": "HandlerFunction"},
            "faulted_value": "wrong.handler",
        }
        (tmp_path / "fault_manifest.json").write_text(json.dumps(manifest))
        submitted_template = "AWSTemplateFormatVersion: '2010-09-09'\nHandler: !Ref HandlerFunction\n"
        (tmp_path / "submitted.yaml").write_text(submitted_template)
        run_dir = tmp_path / "results" / "run-xyz"
        run_dir.mkdir(parents=True)
        # run_pass3 reads submitted.yaml from results/<run_id>/submitted.yaml
        (run_dir / "submitted.yaml").write_text(submitted_template)
        (run_dir / "file_change_log.json").write_text(json.dumps({}))
        from harness.verify.pass3_classification import run_pass3
        result = run_pass3(str(tmp_path), str(tmp_path / "fault_manifest.json"), "run-xyz")
        assert result["structural_match"] is True
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/test_verify.py::TestCfnNormalize -v
```

Expected: `ImportError` — `_cfn_normalize` does not yet exist.

- [ ] **Step 3: Add `_cfn_normalize` to `pass3_classification.py`**

Add the function near the top of `harness/verify/pass3_classification.py` (before `run_pass3`):

```python
_CFN_INTRINSIC_TAGS = {
    "Ref",
    "Fn::Sub",
    "Fn::ImportValue",
    "Fn::Select",
    "Fn::If",
    "Fn::Join",
    "Fn::Split",
    "Fn::FindInMap",
    "Fn::Base64",
    "Fn::Cidr",
    "Fn::Transform",
}


def _cfn_normalize(value):
    """Normalise a CloudFormation intrinsic-function dict to a comparable primitive.

    yaml.safe_load converts '!Ref Foo' to the string 'Foo', while fault manifests
    (stored as JSON) represent the same as {"Ref": "Foo"}. Without this normalisation,
    structural_match is always False for templates that use intrinsic functions.
    """
    if not isinstance(value, dict) or len(value) != 1:
        return value
    (key, val) = next(iter(value.items()))
    if key == "Ref":
        return val
    if key == "Fn::GetAtt":
        if isinstance(val, list) and len(val) == 2:
            return f"{val[0]}.{val[1]}"
        return val
    if key in _CFN_INTRINSIC_TAGS:
        if isinstance(val, str):
            return val
    return value
```

Then update line 89 in `run_pass3`:

```python
structural_match = _cfn_normalize(submitted_value) == _cfn_normalize(original_value)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
pytest tests/test_verify.py::TestCfnNormalize -v
```

Expected: all PASS.

- [ ] **Step 5: Run the full verify test suite**

```bash
pytest tests/test_verify.py -v
```

Expected: all PASS.

---

## Task 2: Pass 4 explicit skipped result (Bug 4.11)

**Files:**
- Modify: `harness/verify/pass4_concurrency.py`
- Test: `tests/test_verify.py`

### Why

When `should_run` returns `False`, the pipeline stores `None`. Downstream scoring code receiving `None` for `pass4_concurrency` must guess whether Pass 4 was skipped intentionally or the result is missing due to a bug. An explicit `{"skipped": True, "reason": "no_api_endpoint"}` return is unambiguous and allows the scoring dimension to weight the pass correctly (skip = not applicable, not a pass or a fail).

The fix moves the `api_endpoint` guard out of `should_run` and into `run_pass4` itself, so `should_run` only gates on `fault_class`. The step always runs for relevant fault classes and returns a skipped result when there is no endpoint to probe.

### Behavior change

`should_run` checks `fault_class` only. `run_pass4` returns `{"skipped": True, "reason": "no_api_endpoint"}` immediately when `api_endpoint` is falsy. All other behaviour is unchanged.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_verify.py`:

```python
from harness.verify.pass4_concurrency import run_pass4


class TestPass4Skipped:
    def test_run_pass4_returns_skipped_when_no_endpoint(self, tmp_path):
        manifest_path = tmp_path / "fault_manifest.json"
        manifest_path.write_text('{"fault_class": "reliability", "concurrency_probe_n": 5}')
        result = run_pass4(str(tmp_path), str(manifest_path), api_endpoint="")
        assert result.get("skipped") is True
        assert "no_api_endpoint" in result.get("reason", "")

    def test_run_pass4_returns_skipped_when_endpoint_none(self, tmp_path):
        manifest_path = tmp_path / "fault_manifest.json"
        manifest_path.write_text('{"fault_class": "performance", "concurrency_probe_n": 3}')
        result = run_pass4(str(tmp_path), str(manifest_path), api_endpoint=None)
        assert result.get("skipped") is True

    def test_pass4_step_should_run_true_for_reliability_with_empty_endpoint(self, tmp_path):
        """should_run must return True on fault_class alone; api_endpoint check is in run_pass4."""
        from harness.verify.pass4_concurrency import Pass4Step
        from harness.verify.pipeline import VerifyContext
        ctx = VerifyContext(
            scenario_dir=str(tmp_path),
            run_id="run-xyz",
            manifest_path=None,
            corpus_dir=str(tmp_path),
            api_endpoint="",
            fault_class="reliability",
        )
        step = Pass4Step()
        assert step.should_run(ctx) is True
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/test_verify.py::TestPass4Skipped -v
```

Expected: FAIL — `run_pass4` currently raises an exception on missing endpoint (tries to open manifest then connect), and `should_run` returns False for empty endpoint.

- [ ] **Step 3: Update `pass4_concurrency.py`**

Change `should_run` to remove the `api_endpoint` guard:

```python
def should_run(self, ctx) -> bool:
    return ctx.fault_class in {"performance", "reliability"}
```

At the top of `run_pass4`, add the early-exit guard:

```python
def run_pass4(scenario_dir: str, manifest_path: str, api_endpoint: str) -> dict:
    if not api_endpoint:
        return {"skipped": True, "reason": "no_api_endpoint"}

    with open(manifest_path, "r", encoding="utf-8") as f:
        ...
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
pytest tests/test_verify.py::TestPass4Skipped -v
```

Expected: all PASS.

- [ ] **Step 5: Run the full verify test suite**

```bash
pytest tests/test_verify.py -v
```

Expected: all PASS.

---

## Task 3: Add `diff_text` to `diff_snapshots` (Bug 4.20)

**Files:**
- Modify: `harness/shared/file_differ.py`
- Test: `tests/test_verify.py` (and/or `tests/test_shared.py`)

### Why

`pass3_classification.py` reads `change_log.get("diff_text", "")` to compute `invalid_patch_detected = len(diff_text) > MAX_DIFF_BYTES`. Since `diff_snapshots` never includes `diff_text`, this is always `False`. The `invalid_patch_detected` field in every Pass 3 result is therefore garbage, and the efficiency scoring dimension that penalises oversized edits cannot work. Adding `diff_text` as a unified diff of all modified files closes this gap.

### Behavior change

`diff_snapshots` gains a new `"diff_text"` key whose value is a UTF-8 string containing the concatenated `difflib.unified_diff` output for all modified files. The value is empty when no files changed. All existing keys remain unchanged — this is purely additive.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_verify.py` (or `tests/test_shared.py`):

```python
from harness.shared.file_differ import diff_snapshots, snapshot


class TestDiffText:
    def test_diff_text_key_present_when_no_changes(self, tmp_path):
        d = tmp_path / "deploy"
        d.mkdir()
        (d / "handler.py").write_text("def handler(): pass\n")
        s = snapshot(str(d))
        result = diff_snapshots(s, s, str(d))
        assert "diff_text" in result
        assert result["diff_text"] == ""

    def test_diff_text_contains_unified_diff_on_modification(self, tmp_path):
        d = tmp_path / "deploy"
        d.mkdir()
        f = d / "handler.py"
        f.write_text("def handler(): return 1\n")
        before = snapshot(str(d))
        f.write_text("def handler(): return 2\n")
        after = snapshot(str(d))
        result = diff_snapshots(before, after, str(d))
        assert "diff_text" in result
        assert "-def handler(): return 1" in result["diff_text"]
        assert "+def handler(): return 2" in result["diff_text"]

    def test_diff_text_empty_when_only_file_added(self, tmp_path):
        d = tmp_path / "deploy"
        d.mkdir()
        before = snapshot(str(d))
        (d / "new_file.py").write_text("# new\n")
        after = snapshot(str(d))
        result = diff_snapshots(before, after, str(d))
        # Added files have no before-content to diff — diff_text may be empty or contain the new lines.
        assert "diff_text" in result
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/test_verify.py::TestDiffText -v
```

Expected: `KeyError` or `AssertionError` — `diff_text` key absent from result.

- [ ] **Step 3: Add `diff_text` to `diff_snapshots`**

In `harness/shared/file_differ.py`, add `import difflib` at the top if not present.

Then inside `diff_snapshots`, after computing the existing return dict, add the diff_text computation before the `return` statement:

```python
    # Build unified diff for all modified files so callers can detect oversized patches.
    diff_lines = []
    for rel_path in per_file:
        abs_path = os.path.join(deployment_dir, rel_path)
        before_lines = before.get(rel_path, {}).get("lines", [])
        if os.path.isfile(abs_path):
            try:
                after_lines = open(abs_path, encoding="utf-8", errors="replace").readlines()
            except OSError:
                after_lines = []
        else:
            after_lines = []
        diff_lines.extend(
            difflib.unified_diff(
                before_lines,
                after_lines,
                fromfile=f"a/{rel_path}",
                tofile=f"b/{rel_path}",
            )
        )
    diff_text = "".join(diff_lines)

    return {
        "files_added": ...,
        ...
        "diff_text": diff_text,
    }
```

Note: the `snapshot()` function must store raw lines for the diff to work. If `snapshot()` currently stores only a hash, update it to also store `"lines"` per file (or read the file again from disk for the before snapshot). Check `file_differ.py` for the exact snapshot structure and adapt accordingly — the key invariant is that `diff_text` must be present in the return value.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
pytest tests/test_verify.py::TestDiffText -v
```

Expected: all PASS.

- [ ] **Step 5: Run the full shared and verify test suites**

```bash
pytest tests/test_shared.py tests/test_verify.py -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add harness/verify/pass3_classification.py harness/verify/pass4_concurrency.py harness/shared/file_differ.py tests/test_verify.py
git commit -m "$(cat <<'EOF'
fix(verify): score signal accuracy — cfn_normalize, pass4 skip sentinel, diff_text

Three fixes to silent signal failures in the verify pipeline:

1. _cfn_normalize(): pass3_classification now normalises CFN intrinsic
   dicts ({"Ref": "Foo"}, {"Fn::Sub": ...}) before structural_match
   comparison. yaml.safe_load converts !Ref Foo -> "Foo" while fault
   manifests store {"Ref": "Foo"}, causing false-negative matches.

2. Pass 4 explicit skip: should_run() now gates on fault_class only.
   run_pass4() returns {"skipped": True, "reason": "no_api_endpoint"}
   when api_endpoint is absent, replacing the silent None that made
   scoring unable to distinguish not-applicable from broken.

3. diff_text in diff_snapshots: file_differ now includes a unified diff
   string in its return value, unblocking the invalid_patch_detected
   heuristic in Pass 3 that always evaluated to False.

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
| 4.9 | `structural_match` false-negative with CFN intrinsics | `pass3_classification.py` line 89 | `_cfn_normalize()` on both sides of equality check |
| 4.11 | Pass 4 silently skips non-HTTP reliability faults | `pass4_concurrency.py:should_run` line 12 | Move endpoint guard into `run_pass4`; return `{"skipped": True}` |
| 4.20 | `diff_text` absent from `diff_snapshots` | `file_differ.py:diff_snapshots` | Add `difflib.unified_diff` accumulation; return as `"diff_text"` key |
