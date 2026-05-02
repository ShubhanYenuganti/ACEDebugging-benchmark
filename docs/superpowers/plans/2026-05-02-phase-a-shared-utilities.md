# Phase A — Shared Utilities Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the four shared modules (`localstack_client`, `cfn_lint_runner`, `file_differ`, `result_logger`) that every other harness phase imports, with full test coverage in `tests/test_shared.py`.

**Architecture:** Four focused modules in `harness/shared/`, each with a single responsibility and no cross-module dependencies. Tests live in a single `tests/test_shared.py` split into four test classes — one per module. Modules are tested in isolation using mocks or hand-crafted fixtures; no live LocalStack required for Phase A tests.

**Tech Stack:** Python 3.11, boto3, cfn-lint (CLI subprocess), pytest 8, pytest-mock, difflib (stdlib), threading (stdlib)

---

## Manual Pre-Configuration — Builder Must Complete These Before Writing Any Files

These steps cannot be scripted in tasks below. Verify each one before starting Task 1.

**1. Python 3.11 is installed and on PATH**
```bash
python3.11 --version
# Expected: Python 3.11.x
```

**2. Docker Desktop is running** — LocalStack requires Docker (used in Phase B+, but install the CLI now)
```bash
docker ps
# Expected: output without error (empty container list is fine)
```

**3. LocalStack CLI is installed**
```bash
localstack --version
# If missing: pip install localstack
```

**4. cfn-lint is installed and on PATH** — `TestCfnLintRunner` calls the real binary; it must exist before running those tests
```bash
cfn-lint --version
# If missing: pip install cfn-lint
# Expected: cfn-lint 1.x.x or similar
```

**5. Git is configured with user name and email**
```bash
git config user.name && git config user.email
# Expected: your name and email on separate lines
```

---

## File Structure

| File | Responsibility |
|------|----------------|
| `requirements.txt` | Python dependencies (boto3, pytest, pytest-mock) |
| `harness/__init__.py` | Package marker |
| `harness/shared/__init__.py` | Package marker |
| `harness/shared/localstack_client.py` | Module-level boto3 singletons + `health_check()` |
| `harness/shared/cfn_lint_runner.py` | `run_lint(path) -> dict` — cfn-lint subprocess wrapper |
| `harness/shared/file_differ.py` | `snapshot(dir)` and `diff_snapshots(before, after, dir)` |
| `harness/shared/result_logger.py` | `init_run`, `log_tool_call`, `log_file_change`, `log_verify_result` |
| `tests/__init__.py` | Package marker |
| `tests/test_shared.py` | All Phase A tests (four test classes) |

**Design decision — `snapshot()` stores content, not hashes:** The spec describes `snapshot` as returning `{relative_path: file_content_hash}`. However, `diff_snapshots` needs actual file content to compute line-level diffs on modified and removed files — content that is no longer on disk by the time the diff runs. Storing content strings enables correct line counting and clean testing with hand-crafted before/after dicts. Change detection (`before[path] != after[path]`) is functionally equivalent to hash comparison.

**Design decision — `log_tool_call` uses threading.Lock + read/write:** The spec says "append without rewriting the whole file." A seek-to-end-of-JSON-array approach is fragile under concurrent writes. A module-level `threading.Lock` serializes writes; each write reads the array, appends, and writes back. The file stays valid JSON at all times.

---

## Task 1: Project Setup

**Files:**
- Create: `requirements.txt`
- Create: `harness/__init__.py`
- Create: `harness/shared/__init__.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p harness/shared tests
```

- [ ] **Step 2: Create `requirements.txt`**

```
boto3>=1.34.0
pytest>=8.0.0
pytest-mock>=3.12.0
```

- [ ] **Step 3: Create package markers**

```bash
touch harness/__init__.py harness/shared/__init__.py tests/__init__.py
```

- [ ] **Step 4: Create and activate virtualenv, install dependencies**

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Confirm:
```bash
pytest --version
# Expected: pytest 8.x.x
python -c "import boto3; print(boto3.__version__)"
# Expected: 1.x.x
```

- [ ] **Step 5: Commit**

```bash
git add requirements.txt harness/__init__.py harness/shared/__init__.py tests/__init__.py
git commit -m "feat: scaffold Phase A project structure"
```

---

## Task 2: localstack_client.py

**Files:**
- Create: `harness/shared/localstack_client.py`
- Create: `tests/test_shared.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_shared.py`:

```python
import pytest
import harness.shared.localstack_client as lsc
from harness.shared.localstack_client import health_check


class TestHealthCheck:
    def test_raises_runtime_error_when_unreachable(self, mocker):
        mocker.patch.object(
            lsc.cf_client,
            "list_stacks",
            side_effect=Exception("Connection refused"),
        )
        with pytest.raises(RuntimeError, match="LocalStack is not reachable"):
            health_check()

    def test_does_not_raise_when_reachable(self, mocker):
        mocker.patch.object(
            lsc.cf_client,
            "list_stacks",
            return_value={"StackSummaries": []},
        )
        health_check()  # must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_shared.py::TestHealthCheck -v
```
Expected: `ModuleNotFoundError: No module named 'harness.shared.localstack_client'`

- [ ] **Step 3: Create `harness/shared/localstack_client.py`**

```python
import boto3

_ENDPOINT = "http://localhost:4566"
_CREDS = {
    "aws_access_key_id": "test",
    "aws_secret_access_key": "test",
    "region_name": "us-east-1",
}


def _client(service: str):
    return boto3.client(service, endpoint_url=_ENDPOINT, **_CREDS)


cf_client = _client("cloudformation")
lambda_client = _client("lambda")
s3_client = _client("s3")
sqs_client = _client("sqs")
iam_client = _client("iam")
logs_client = _client("logs")
apigateway_client = _client("apigateway")


def health_check() -> None:
    try:
        cf_client.list_stacks()
    except Exception as exc:
        raise RuntimeError(
            f"LocalStack is not reachable at {_ENDPOINT}: {exc}"
        ) from exc
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_shared.py::TestHealthCheck -v
```
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add harness/shared/localstack_client.py tests/test_shared.py
git commit -m "feat: add localstack_client with boto3 singletons and health_check"
```

---

## Task 3: cfn_lint_runner.py

**Files:**
- Create: `harness/shared/cfn_lint_runner.py`
- Modify: `tests/test_shared.py` (append `TestCfnLintRunner` class)

- [ ] **Step 1: Append failing tests to `tests/test_shared.py`**

Append after the closing `}` of `TestHealthCheck`:

```python
from harness.shared.cfn_lint_runner import run_lint


_VALID_TEMPLATE = """\
AWSTemplateFormatVersion: '2010-09-09'
Resources:
  MyBucket:
    Type: AWS::S3::Bucket
"""

_INVALID_TEMPLATE_E_RULE = """\
AWSTemplateFormatVersion: '2010-09-09'
Resources:
  MyBucket:
    Type: AWS::S3::Bucket
    Properties:
      NonExistentProperty: invalid-value
"""


class TestCfnLintRunner:
    def test_passes_on_valid_template(self, tmp_path):
        template = tmp_path / "valid.yaml"
        template.write_text(_VALID_TEMPLATE)
        result = run_lint(str(template))
        assert result["passed"] is True
        assert result["fatal_errors"] == []

    def test_fails_on_e_rule_error(self, tmp_path):
        template = tmp_path / "invalid.yaml"
        template.write_text(_INVALID_TEMPLATE_E_RULE)
        result = run_lint(str(template))
        assert result["passed"] is False
        assert len(result["fatal_errors"]) > 0
        assert result["fatal_errors"][0]["rule"].startswith("E")

    def test_w_rule_warning_does_not_fail(self, tmp_path, mocker):
        mock_output = (
            '[{"Rule": {"Id": "W3010"}, "Message": "some warning",'
            ' "Location": {"Start": {"LineNumber": 3}}}]'
        )
        mocker.patch(
            "subprocess.run",
            return_value=mocker.Mock(stdout=mock_output, stderr="", returncode=2),
        )
        template = tmp_path / "warn.yaml"
        template.write_text(_VALID_TEMPLATE)
        result = run_lint(str(template))
        assert result["passed"] is True
        assert len(result["warnings"]) == 1
        assert result["warnings"][0]["rule"] == "W3010"

    def test_raises_environment_error_when_cfn_lint_missing(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda _: None)
        with pytest.raises(EnvironmentError, match="cfn-lint is not installed"):
            run_lint("any.yaml")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_shared.py::TestCfnLintRunner -v
```
Expected: `ModuleNotFoundError: No module named 'harness.shared.cfn_lint_runner'`

- [ ] **Step 3: Create `harness/shared/cfn_lint_runner.py`**

```python
import json
import shutil
import subprocess


def run_lint(template_path: str) -> dict:
    if shutil.which("cfn-lint") is None:
        raise EnvironmentError(
            "cfn-lint is not installed. Install it with: pip install cfn-lint"
        )

    result = subprocess.run(
        ["cfn-lint", "--format", "json", template_path],
        capture_output=True,
        text=True,
    )

    fatal_errors = []
    warnings = []

    output = result.stdout.strip()
    if output:
        try:
            matches = json.loads(output)
            for match in matches:
                rule_id = match.get("Rule", {}).get("Id", "")
                message = match.get("Message", "")
                start = match.get("Location", {}).get("Start", {})
                location = f"line {start.get('LineNumber', '?')}"
                entry = {"rule": rule_id, "message": message, "location": location}
                if rule_id.startswith("E"):
                    fatal_errors.append(entry)
                elif rule_id.startswith("W"):
                    warnings.append(entry)
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass

    return {
        "passed": len(fatal_errors) == 0,
        "fatal_errors": fatal_errors,
        "warnings": warnings,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_shared.py::TestCfnLintRunner -v
```
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add harness/shared/cfn_lint_runner.py tests/test_shared.py
git commit -m "feat: add cfn_lint_runner subprocess wrapper"
```

---

## Task 4: file_differ.py

**Files:**
- Create: `harness/shared/file_differ.py`
- Modify: `tests/test_shared.py` (append `TestFileDiffer` class)

- [ ] **Step 1: Append failing tests to `tests/test_shared.py`**

Append after `TestCfnLintRunner`:

```python
import os
from harness.shared.file_differ import snapshot, diff_snapshots


class TestFileDiffer:
    def test_snapshot_returns_content_for_each_file(self, tmp_path):
        (tmp_path / "a.py").write_text("line1\nline2\n")
        (tmp_path / "b.py").write_text("hello\n")
        result = snapshot(str(tmp_path))
        assert set(result.keys()) == {"a.py", "b.py"}
        assert result["a.py"] == "line1\nline2\n"
        assert result["b.py"] == "hello\n"

    def test_snapshot_uses_relative_paths_for_subdirectories(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "handler.py").write_text("code\n")
        result = snapshot(str(tmp_path))
        expected_key = os.path.join("sub", "handler.py")
        assert expected_key in result

    def test_diff_added_files(self):
        before = {}
        after = {"new.py": "line1\nline2\n"}
        result = diff_snapshots(before, after, "/unused")
        assert result["files_added"] == ["new.py"]
        assert result["files_modified"] == []
        assert result["files_removed"] == []
        assert result["total_files_changed"] == 1
        assert result["per_file_line_changes"]["new.py"]["lines_added"] == 2
        assert result["per_file_line_changes"]["new.py"]["lines_removed"] == 0
        assert result["per_file_line_changes"]["new.py"]["lines_modified"] == 0
        assert result["per_file_line_changes"]["new.py"]["total_lines_changed"] == 2
        assert result["total_lines_changed"] == 2

    def test_diff_removed_files(self):
        before = {"old.py": "a\nb\nc\n"}
        after = {}
        result = diff_snapshots(before, after, "/unused")
        assert result["files_removed"] == ["old.py"]
        assert result["total_files_changed"] == 0  # removed files not counted
        assert result["per_file_line_changes"]["old.py"]["lines_removed"] == 3
        assert result["per_file_line_changes"]["old.py"]["lines_added"] == 0
        assert result["total_lines_changed"] == 3

    def test_diff_modified_files(self):
        before = {"handler.py": "line1\nline2\nline3\n"}
        after = {"handler.py": "line1\nline2_changed\nline3\nline4\n"}
        result = diff_snapshots(before, after, "/unused")
        assert result["files_modified"] == ["handler.py"]
        assert result["total_files_changed"] == 1
        changes = result["per_file_line_changes"]["handler.py"]
        # line2 removed (1 removed); line2_changed + line4 added (2 added)
        assert changes["lines_removed"] == 1
        assert changes["lines_added"] == 2
        assert changes["lines_modified"] == 0
        assert changes["total_lines_changed"] == 3
        assert result["total_lines_changed"] == 3

    def test_diff_unchanged_files_have_no_entry_in_per_file(self):
        before = {"a.py": "same\n", "b.py": "old\n"}
        after = {"a.py": "same\n", "b.py": "new\n"}
        result = diff_snapshots(before, after, "/unused")
        assert "a.py" not in result["per_file_line_changes"]
        assert "b.py" in result["per_file_line_changes"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_shared.py::TestFileDiffer -v
```
Expected: `ModuleNotFoundError: No module named 'harness.shared.file_differ'`

- [ ] **Step 3: Create `harness/shared/file_differ.py`**

```python
import difflib
import os
from typing import Dict


def snapshot(directory: str) -> Dict[str, str]:
    result = {}
    for root, _, files in os.walk(directory):
        for fname in sorted(files):
            full_path = os.path.join(root, fname)
            rel_path = os.path.relpath(full_path, directory)
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                result[rel_path] = f.read()
    return result


def diff_snapshots(
    before: Dict[str, str], after: Dict[str, str], directory: str
) -> dict:
    before_keys = set(before)
    after_keys = set(after)

    files_added = sorted(after_keys - before_keys)
    files_removed = sorted(before_keys - after_keys)
    files_modified = sorted(
        p for p in before_keys & after_keys if before[p] != after[p]
    )

    per_file_line_changes: dict = {}

    for path in files_added:
        n = len(after[path].splitlines())
        per_file_line_changes[path] = {
            "lines_added": n,
            "lines_modified": 0,
            "lines_removed": 0,
            "total_lines_changed": n,
        }

    for path in files_removed:
        n = len(before[path].splitlines())
        per_file_line_changes[path] = {
            "lines_added": 0,
            "lines_modified": 0,
            "lines_removed": n,
            "total_lines_changed": n,
        }

    for path in files_modified:
        added = removed = 0
        for line in difflib.unified_diff(
            before[path].splitlines(), after[path].splitlines()
        ):
            if line.startswith("+") and not line.startswith("+++"):
                added += 1
            elif line.startswith("-") and not line.startswith("---"):
                removed += 1
        per_file_line_changes[path] = {
            "lines_added": added,
            "lines_modified": 0,
            "lines_removed": removed,
            "total_lines_changed": added + removed,
        }

    total = sum(v["total_lines_changed"] for v in per_file_line_changes.values())

    return {
        "files_added": files_added,
        "files_modified": files_modified,
        "files_removed": files_removed,
        "total_files_changed": len(files_added) + len(files_modified),
        "per_file_line_changes": per_file_line_changes,
        "total_lines_changed": total,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_shared.py::TestFileDiffer -v
```
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add harness/shared/file_differ.py tests/test_shared.py
git commit -m "feat: add file_differ with snapshot and diff_snapshots"
```

---

## Task 5: result_logger.py

**Files:**
- Create: `harness/shared/result_logger.py`
- Modify: `tests/test_shared.py` (append `TestResultLogger` class)

- [ ] **Step 1: Append failing tests to `tests/test_shared.py`**

Append after `TestFileDiffer`:

```python
import concurrent.futures
import json
from pathlib import Path
import harness.shared.result_logger as rl
from harness.shared.result_logger import (
    init_run,
    log_tool_call,
    log_file_change,
    log_verify_result,
)


class TestResultLogger:
    def test_init_run_creates_directory_and_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rl, "RESULTS_DIR", str(tmp_path))
        init_run("run-001", "arch01_fault01")
        run_dir = tmp_path / "run-001"
        assert run_dir.is_dir()
        assert (run_dir / "scenario_id.txt").read_text() == "arch01_fault01"
        assert json.loads((run_dir / "tool_call_trace.json").read_text()) == []

    def test_log_tool_call_appends_entries_in_order(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rl, "RESULTS_DIR", str(tmp_path))
        init_run("run-002", "arch01_fault01")
        log_tool_call(
            "run-002", 1, "ace_invoke_lambda",
            {"fn": "MyFunc"}, {"status": 200}, "2026-01-01T00:00:00Z",
        )
        log_tool_call(
            "run-002", 2, "ace_get_log_tail",
            {"fn": "MyFunc"}, {"logs": []}, "2026-01-01T00:00:01Z",
        )
        data = json.loads(
            (tmp_path / "run-002" / "tool_call_trace.json").read_text()
        )
        assert len(data) == 2
        assert data[0]["tool"] == "ace_invoke_lambda"
        assert data[0]["turn"] == 1
        assert data[1]["tool"] == "ace_get_log_tail"
        assert data[1]["turn"] == 2

    def test_log_tool_call_concurrent_no_corruption(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rl, "RESULTS_DIR", str(tmp_path))
        init_run("run-003", "arch01_fault01")

        def write_entry(i: int) -> None:
            log_tool_call(
                "run-003", i, f"tool_{i}",
                {"i": i}, {"ok": True}, f"2026-01-01T00:00:{i:02d}Z",
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(write_entry, i) for i in range(20)]
            concurrent.futures.wait(futures)

        data = json.loads(
            (tmp_path / "run-003" / "tool_call_trace.json").read_text()
        )
        assert len(data) == 20

    def test_log_file_change_writes_diff_dict(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rl, "RESULTS_DIR", str(tmp_path))
        init_run("run-004", "arch01_fault01")
        diff = {
            "files_added": ["new.py"],
            "files_modified": [],
            "files_removed": [],
            "total_files_changed": 1,
            "per_file_line_changes": {
                "new.py": {
                    "lines_added": 5,
                    "lines_modified": 0,
                    "lines_removed": 0,
                    "total_lines_changed": 5,
                }
            },
            "total_lines_changed": 5,
        }
        log_file_change("run-004", diff)
        written = json.loads(
            (tmp_path / "run-004" / "file_change_log.json").read_text()
        )
        assert written == diff

    def test_log_verify_result_writes_result_dict(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rl, "RESULTS_DIR", str(tmp_path))
        init_run("run-005", "arch01_fault01")
        result = {
            "outcome": "completed",
            "pass1_functional": {"all_assertions_passed": True},
        }
        log_verify_result("run-005", result)
        written = json.loads(
            (tmp_path / "run-005" / "verify_result.json").read_text()
        )
        assert written == result
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_shared.py::TestResultLogger -v
```
Expected: `ModuleNotFoundError: No module named 'harness.shared.result_logger'`

- [ ] **Step 3: Create `harness/shared/result_logger.py`**

```python
import json
import threading
from pathlib import Path

RESULTS_DIR = "results"

_trace_lock = threading.Lock()


def init_run(run_id: str, scenario_id: str) -> None:
    run_dir = Path(RESULTS_DIR) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "scenario_id.txt").write_text(scenario_id)
    (run_dir / "tool_call_trace.json").write_text("[]")


def log_tool_call(
    run_id: str,
    turn: int,
    tool: str,
    input: dict,
    output: dict,
    timestamp: str,
) -> None:
    path = Path(RESULTS_DIR) / run_id / "tool_call_trace.json"
    entry = {
        "turn": turn,
        "tool": tool,
        "input": input,
        "output": output,
        "timestamp": timestamp,
    }
    with _trace_lock:
        data = json.loads(path.read_text())
        data.append(entry)
        path.write_text(json.dumps(data, indent=2))


def log_file_change(run_id: str, diff: dict) -> None:
    path = Path(RESULTS_DIR) / run_id / "file_change_log.json"
    path.write_text(json.dumps(diff, indent=2))


def log_verify_result(run_id: str, result: dict) -> None:
    path = Path(RESULTS_DIR) / run_id / "verify_result.json"
    path.write_text(json.dumps(result, indent=2))
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_shared.py::TestResultLogger -v
```
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add harness/shared/result_logger.py tests/test_shared.py
git commit -m "feat: add result_logger with thread-safe tool call logging"
```

---

## Task 6: Phase A Gate — Full Test Suite

- [ ] **Step 1: Run all Phase A tests**

```bash
pytest tests/test_shared.py -v
```

Expected — all 17 tests pass:
```
tests/test_shared.py::TestHealthCheck::test_raises_runtime_error_when_unreachable PASSED
tests/test_shared.py::TestHealthCheck::test_does_not_raise_when_reachable PASSED
tests/test_shared.py::TestCfnLintRunner::test_passes_on_valid_template PASSED
tests/test_shared.py::TestCfnLintRunner::test_fails_on_e_rule_error PASSED
tests/test_shared.py::TestCfnLintRunner::test_w_rule_warning_does_not_fail PASSED
tests/test_shared.py::TestCfnLintRunner::test_raises_environment_error_when_cfn_lint_missing PASSED
tests/test_shared.py::TestFileDiffer::test_snapshot_returns_content_for_each_file PASSED
tests/test_shared.py::TestFileDiffer::test_snapshot_uses_relative_paths_for_subdirectories PASSED
tests/test_shared.py::TestFileDiffer::test_diff_added_files PASSED
tests/test_shared.py::TestFileDiffer::test_diff_removed_files PASSED
tests/test_shared.py::TestFileDiffer::test_diff_modified_files PASSED
tests/test_shared.py::TestFileDiffer::test_diff_unchanged_files_have_no_entry_in_per_file PASSED
tests/test_shared.py::TestResultLogger::test_init_run_creates_directory_and_files PASSED
tests/test_shared.py::TestResultLogger::test_log_tool_call_appends_entries_in_order PASSED
tests/test_shared.py::TestResultLogger::test_log_tool_call_concurrent_no_corruption PASSED
tests/test_shared.py::TestResultLogger::test_log_file_change_writes_diff_dict PASSED
tests/test_shared.py::TestResultLogger::test_log_verify_result_writes_result_dict PASSED

17 passed
```

- [ ] **Step 2: Verify all public symbols import cleanly**

```bash
python -c "
from harness.shared.localstack_client import (
    cf_client, lambda_client, s3_client, sqs_client,
    iam_client, logs_client, apigateway_client, health_check,
)
from harness.shared.cfn_lint_runner import run_lint
from harness.shared.file_differ import snapshot, diff_snapshots
from harness.shared.result_logger import init_run, log_tool_call, log_file_change, log_verify_result
print('All imports OK')
"
```
Expected: `All imports OK`

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "feat: Phase A complete — shared utilities with 17 passing tests"
```

**Phase A gate is clear. Phase B may begin.**

---

## Self-Review

### Spec coverage

| Spec requirement | Covered by |
|------------------|------------|
| A1 — singletons for CF, Lambda, S3, SQS, IAM, Logs, API GW | Task 2: all seven clients in `localstack_client.py` |
| A1 — `health_check()` raises `RuntimeError` when unreachable | `TestHealthCheck::test_raises_runtime_error_when_unreachable` |
| A1 — `health_check()` does not raise when reachable | `TestHealthCheck::test_does_not_raise_when_reachable` |
| A2 — `passed: True` on valid template | `TestCfnLintRunner::test_passes_on_valid_template` |
| A2 — `passed: False` on E-rule error | `TestCfnLintRunner::test_fails_on_e_rule_error` |
| A2 — W rules recorded but do not fail | `TestCfnLintRunner::test_w_rule_warning_does_not_fail` |
| A2 — `EnvironmentError` if cfn-lint not installed | `TestCfnLintRunner::test_raises_environment_error_when_cfn_lint_missing` |
| A3 — `snapshot()` walks directory, returns per-file data | `TestFileDiffer::test_snapshot_*` (2 tests) |
| A3 — `diff_snapshots` counts added, modified, removed files and lines | `TestFileDiffer::test_diff_*` (4 tests) |
| A3 — `total_files_changed = len(added) + len(modified)` (removed not counted) | `test_diff_removed_files` asserts `total_files_changed == 0` |
| A4 — `init_run` creates dir, writes `scenario_id.txt`, initialises trace file | `TestResultLogger::test_init_run_creates_directory_and_files` |
| A4 — `log_tool_call` appends to JSON array | `test_log_tool_call_appends_entries_in_order` |
| A4 — No file corruption under concurrent calls | `test_log_tool_call_concurrent_no_corruption` |
| A4 — `log_file_change` writes diff dict | `test_log_file_change_writes_diff_dict` |
| A4 — `log_verify_result` writes result dict | `test_log_verify_result_writes_result_dict` |

**Gaps:** None.

### Placeholder scan

No TBD, TODO, "implement later", "similar to", or steps without code blocks found.

### Type consistency

- `health_check()` — defined and called consistently across Task 2 test and implementation
- `run_lint(template_path: str) -> dict` — keys `passed`, `fatal_errors`, `warnings` used consistently across Task 3 test and implementation
- `snapshot(directory: str) -> Dict[str, str]` — values are content strings; `diff_snapshots` accesses them as `before[path]` / `after[path]` consistently in Task 4
- `diff_snapshots` return keys — `files_added`, `files_modified`, `files_removed`, `total_files_changed`, `per_file_line_changes`, `total_lines_changed` — asserted identically in tests and returned by implementation
- `RESULTS_DIR` — patched as `rl.RESULTS_DIR` (module attribute) in all five `TestResultLogger` tests; accessed as global `RESULTS_DIR` inside all four logger functions — consistent
