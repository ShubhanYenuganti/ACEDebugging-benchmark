# Theme E: I/O Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three I/O safety gaps that can crash or stall the harness: cfn-lint binary absence propagates as an unhandled `EnvironmentError`; `read_file` loads arbitrarily large files into memory without a size cap; `write_file` accepts arbitrarily large content strings without a byte limit.

**Architecture:** Three independent surgical fixes — (1) graceful cfn-lint degradation that returns a structured warning result instead of raising; (2) a `READ_MAX_BYTES` module constant and pre-read size check in `tools.py`; (3) a `WRITE_MAX_BYTES` module constant and pre-write content byte check in `tools.py`. All three changes are purely additive: new constants + early-return guards. No interface changes.

**Tech Stack:** Python 3.11, pytest, pytest-mock (existing tooling).

---

## Background — Three Bugs

### Bug 4.12: cfn-lint binary missing raises `EnvironmentError` into the deployment path

`harness/shared/cfn_lint_runner.py` lines 9–11:

```python
if shutil.which("cfn-lint") is None:
    raise EnvironmentError("cfn-lint is not installed; run 'pip install cfn-lint'")
```

This exception propagates up through `handle_submission` in `deployment_handler.py` and surfaces as a generic crash in the deployment pipeline. The model agent never learns that its template was syntactically valid; the run terminates with a confusing traceback rather than a lint-skipped-but-continued result. In CI environments where cfn-lint is not installed, every scenario fails at this line regardless of the model's fix quality.

### Bug 4.14: `read_file` loads arbitrarily large files into memory

`harness/agent/tools.py` dispatch for `read_file` (around line 153–162):

```python
content = target.read_text(encoding="utf-8")
```

No size check precedes this call. A model that reads a multi-megabyte binary (e.g., a zipped Lambda package accidentally placed in `deployment/`) or a runaway log file could allocate hundreds of megabytes, degrade the process, and produce a response that exceeds the LLM's context window. There is no error feedback — the read either succeeds with an enormous result or raises a `UnicodeDecodeError` that is not caught.

### Bug 4.15: `write_file` accepts arbitrarily large content

`harness/agent/tools.py` dispatch for `write_file` (around line 164–184):

```python
content = inputs.get("content", "")
```

The content string is written to disk with no byte-count validation. A model that emits a multi-megabyte `write_file` call (possibly by including a base64-encoded artifact in the content) could fill the results directory or overwrite a Lambda handler with a file that will be rejected by the packager. The validator that checks S3Key orphans runs before the write, not after, so it cannot prevent oversized content.

---

## File Structure

Changes are confined to two production files and two test files:

- **Modify** `harness/shared/cfn_lint_runner.py` — replace `EnvironmentError` with a structured warning result.
- **Modify** `harness/agent/tools.py` — add `READ_MAX_BYTES` and `WRITE_MAX_BYTES` constants; add size guards.
- **Test** `tests/test_shared.py` — new tests for cfn-lint graceful degradation.
- **Test** `tests/test_agent_loop.py` — new tests for read/write size limits.

No new files. No reorganization.

---

## Task 1: cfn-lint graceful degradation when binary absent (Bug 4.12)

**Files:**
- Modify: `harness/shared/cfn_lint_runner.py`
- Test: `tests/test_shared.py`

### Why

The deployment pipeline should always continue to the submission step. If cfn-lint is absent, the harness should log a structured warning and proceed rather than crashing. The warning is surfaced as a lint result with `passed=True` (no fatal errors found, because no lint ran) and a single warning entry that identifies the missing binary. Callers that check `result["fatal_errors"]` continue to work — the list is empty.

### Behavior change

Replace the `EnvironmentError` raise with an early return:

```python
if shutil.which("cfn-lint") is None:
    return {
        "passed": True,
        "fatal_errors": [],
        "warnings": [{"rule": "HARNESS_WARN_001", "message": "cfn-lint not installed; lint skipped"}],
    }
```

All subsequent logic (subprocess call, output parsing) is unchanged.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_shared.py`:

```python
from harness.shared.cfn_lint_runner import run_lint


class TestCfnLintGracefulDegradation:
    def test_returns_warning_when_cfn_lint_not_installed(self, tmp_path, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda cmd: None)
        template = tmp_path / "template.yaml"
        template.write_text("AWSTemplateFormatVersion: '2010-09-09'\n")
        result = run_lint(str(template))
        assert result["passed"] is True
        assert result["fatal_errors"] == []
        assert len(result["warnings"]) == 1
        assert "cfn-lint" in result["warnings"][0]["message"].lower() or \
               "lint" in result["warnings"][0]["message"].lower()

    def test_does_not_raise_when_cfn_lint_not_installed(self, tmp_path, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda cmd: None)
        template = tmp_path / "template.yaml"
        template.write_text("AWSTemplateFormatVersion: '2010-09-09'\n")
        # Must not raise EnvironmentError.
        try:
            run_lint(str(template))
        except EnvironmentError:
            pytest.fail("run_lint raised EnvironmentError when cfn-lint is absent")
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/test_shared.py::TestCfnLintGracefulDegradation -v
```

Expected: FAIL — `run_lint` raises `EnvironmentError`.

- [ ] **Step 3: Update `cfn_lint_runner.py`**

Replace lines 9–11 (the `EnvironmentError` raise):

```python
if shutil.which("cfn-lint") is None:
    return {
        "passed": True,
        "fatal_errors": [],
        "warnings": [{"rule": "HARNESS_WARN_001", "message": "cfn-lint not installed; lint skipped"}],
    }
```

- [ ] **Step 4: Run the new tests to verify they pass**

```bash
pytest tests/test_shared.py::TestCfnLintGracefulDegradation -v
```

Expected: 2 passed.

- [ ] **Step 5: Run the full shared test suite**

```bash
pytest tests/test_shared.py -v
```

Expected: all PASS.

---

## Task 2: `read_file` 1 MB size guard (Bug 4.14)

**Files:**
- Modify: `harness/agent/tools.py`
- Test: `tests/test_agent_loop.py`

### Why

A 1 MB cap is well above any realistic CloudFormation template or Lambda handler file (templates rarely exceed 50 KB; handlers rarely exceed 200 KB). It prevents the model from accidentally loading binary artifacts or runaway logs that would corrupt the LLM context. The error message returned to the model must include the actual file size so the model can choose a different approach rather than retrying blindly.

### Behavior change

New module constant `READ_MAX_BYTES = 1_048_576`. Before calling `target.read_text()`, check `target.stat().st_size`. If the file exceeds the limit, return an error string to the model (do not raise). The check uses `st_size` (bytes) for efficiency — no need to read the file to discover it is too large.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_agent_loop.py`:

```python
from harness.agent.tools import dispatch_file_tool, READ_MAX_BYTES


class TestReadFileSizeLimit:
    def test_read_file_returns_error_when_file_too_large(self, tmp_path):
        large_file = tmp_path / "large.py"
        large_file.write_bytes(b"x" * (READ_MAX_BYTES + 1))
        result = dispatch_file_tool(
            "read_file", {"path": str(large_file)}, str(tmp_path)
        )
        assert "too large" in result.lower() or "exceeds" in result.lower() or "limit" in result.lower()

    def test_read_file_succeeds_at_limit_boundary(self, tmp_path):
        boundary_file = tmp_path / "boundary.py"
        boundary_file.write_bytes(b"a" * READ_MAX_BYTES)
        result = dispatch_file_tool(
            "read_file", {"path": str(boundary_file)}, str(tmp_path)
        )
        # Must not be an error — should return file contents.
        assert "too large" not in result.lower()
        assert len(result) == READ_MAX_BYTES

    def test_read_file_succeeds_for_normal_file(self, tmp_path):
        f = tmp_path / "handler.py"
        f.write_text("def handler(event, context): return {}\n")
        result = dispatch_file_tool(
            "read_file", {"path": str(f)}, str(tmp_path)
        )
        assert "handler" in result
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/test_agent_loop.py::TestReadFileSizeLimit -v
```

Expected: `ImportError` (constant absent) or assertion failure (no size check today).

- [ ] **Step 3: Add `READ_MAX_BYTES` and size guard to `tools.py`**

Add near the top of `harness/agent/tools.py` (with other module constants):

```python
READ_MAX_BYTES = 1_048_576   # 1 MiB
```

In the `read_file` branch of `dispatch_file_tool`, before `target.read_text(...)`:

```python
file_size = target.stat().st_size
if file_size > READ_MAX_BYTES:
    return (
        f"Error: file is too large to read ({file_size:,} bytes). "
        f"Maximum allowed size is {READ_MAX_BYTES:,} bytes (1 MiB). "
        "Choose a smaller file or read a specific section."
    )
```

- [ ] **Step 4: Run the new tests to verify they pass**

```bash
pytest tests/test_agent_loop.py::TestReadFileSizeLimit -v
```

Expected: all PASS.

- [ ] **Step 5: Run the full agent loop test suite**

```bash
pytest tests/test_agent_loop.py -v
```

Expected: all PASS.

---

## Task 3: `write_file` 512 KB content size guard (Bug 4.15)

**Files:**
- Modify: `harness/agent/tools.py`
- Test: `tests/test_agent_loop.py`

### Why

512 KB is a generous cap for any Lambda handler. AWS Lambda itself imposes a 250 MB deployment package limit, but inline Lambda handlers (`.py` files, not zips) should never exceed a few hundred KB of source code. Encoding the check as a byte count on the UTF-8-encoded content (not the Python string length) is correct because multi-byte characters would otherwise under-count. The check is inserted before the path-permission and orphan checks to fail fast.

### Behavior change

New module constant `WRITE_MAX_BYTES = 524_288`. At the top of the `write_file` branch in `dispatch_file_tool`, before any path resolution, encode the content to UTF-8 and check its byte length. If exceeded, return an error string to the model. The error must include the actual byte count.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_agent_loop.py`:

```python
from harness.agent.tools import dispatch_file_tool, WRITE_MAX_BYTES


class TestWriteFileSizeLimit:
    def test_write_file_returns_error_when_content_too_large(self, tmp_path):
        # Create a writable deployment path.
        deploy = tmp_path / "deployment" / "lambda"
        deploy.mkdir(parents=True)
        oversized = "x" * (WRITE_MAX_BYTES + 1)
        result = dispatch_file_tool(
            "write_file",
            {"path": str(deploy / "handler.py"), "content": oversized},
            str(tmp_path),
        )
        assert "too large" in result.lower() or "exceeds" in result.lower() or "limit" in result.lower()
        # File must not have been written.
        assert not (deploy / "handler.py").exists()

    def test_write_file_succeeds_at_limit_boundary(self, tmp_path):
        deploy = tmp_path / "deployment" / "lambda"
        deploy.mkdir(parents=True)
        # Provide a faulted.yaml so the runner can resolve S3Key.
        (tmp_path / "faulted.yaml").write_text(
            "Resources:\n  Fn:\n    Type: AWS::Lambda::Function\n"
            "    Properties:\n      Code:\n        S3Key: handler.zip\n"
        )
        at_limit = "a" * WRITE_MAX_BYTES
        result = dispatch_file_tool(
            "write_file",
            {"path": str(deploy / "handler.py"), "content": at_limit},
            str(tmp_path),
        )
        # Should either succeed or fail for a non-size reason (e.g., orphan check).
        assert "too large" not in result.lower()

    def test_write_file_succeeds_for_normal_content(self, tmp_path):
        deploy = tmp_path / "deployment" / "lambda"
        deploy.mkdir(parents=True)
        (tmp_path / "faulted.yaml").write_text(
            "Resources:\n  Fn:\n    Type: AWS::Lambda::Function\n"
            "    Properties:\n      Code:\n        S3Key: handler.zip\n"
        )
        result = dispatch_file_tool(
            "write_file",
            {"path": str(deploy / "handler.py"), "content": "def handler(e, c): return {}\n"},
            str(tmp_path),
        )
        assert "too large" not in result.lower()
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/test_agent_loop.py::TestWriteFileSizeLimit -v
```

Expected: `ImportError` (constant absent) or assertion failure (no size check today).

- [ ] **Step 3: Add `WRITE_MAX_BYTES` and content size guard to `tools.py`**

Add near the top of `harness/agent/tools.py` alongside `READ_MAX_BYTES`:

```python
WRITE_MAX_BYTES = 524_288   # 512 KiB
```

At the very start of the `write_file` branch in `dispatch_file_tool` (before path resolution):

```python
content = inputs.get("content", "")
content_bytes = len(content.encode("utf-8"))
if content_bytes > WRITE_MAX_BYTES:
    return (
        f"Error: content is too large to write ({content_bytes:,} bytes). "
        f"Maximum allowed size is {WRITE_MAX_BYTES:,} bytes (512 KiB). "
        "Split the file or reduce content size."
    )
```

- [ ] **Step 4: Run the new tests to verify they pass**

```bash
pytest tests/test_agent_loop.py::TestWriteFileSizeLimit -v
```

Expected: all PASS.

- [ ] **Step 5: Run the full agent loop test suite**

```bash
pytest tests/test_agent_loop.py -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add harness/shared/cfn_lint_runner.py harness/agent/tools.py tests/test_shared.py tests/test_agent_loop.py
git commit -m "$(cat <<'EOF'
fix(io): I/O safety — cfn-lint graceful degradation, read/write size guards

Three fixes to prevent I/O-related crashes and memory exhaustion:

1. cfn-lint graceful degradation: run_lint() now returns a structured
   warning result instead of raising EnvironmentError when cfn-lint is
   not installed. Deployment pipeline continues; lint result has
   passed=True with a HARNESS_WARN_001 warning entry.

2. read_file 1 MiB guard: READ_MAX_BYTES = 1_048_576 constant added to
   tools.py. dispatch_file_tool checks st_size before read_text() and
   returns a descriptive error string to the model without raising.

3. write_file 512 KiB guard: WRITE_MAX_BYTES = 524_288 constant added.
   dispatch_file_tool encodes content to UTF-8 and checks byte length
   before any path resolution. Oversized content returns an error and
   the file is never written.

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
| 4.12 | cfn-lint absent raises `EnvironmentError` into deployment path | `cfn_lint_runner.py` lines 9–11 | Return `{"passed": True, "warnings": [...]}` instead of raising |
| 4.14 | `read_file` has no size limit | `tools.py:dispatch_file_tool` read branch | `READ_MAX_BYTES = 1_048_576`; check `st_size` before `read_text` |
| 4.15 | `write_file` has no content size limit | `tools.py:dispatch_file_tool` write branch | `WRITE_MAX_BYTES = 524_288`; check `len(content.encode())` before write |
