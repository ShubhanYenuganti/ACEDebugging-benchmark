# Phase D — Verify Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the four verification passes and the orchestrating `verify_loop.py` that runs them in sequence after a model's fix is deployed, then writes a structured `verify_result.json`.

**Architecture:** Five focused modules in `harness/verify/`. Each pass receives `scenario_dir` and returns a structured dict. `verify_loop.py` orchestrates them in order, applies Pass 4's override rule, and writes the final result via `result_logger`. Tests are fully mocked — subprocess outputs and `fault_manifest.json` are hand-crafted fixtures.

**Tech Stack:** Python 3.11, subprocess, concurrent.futures, requests, PyYAML, pytest 8, pytest-mock

---

## Manual Pre-Configuration — Builder Must Complete These Before Writing Any Files

**1. Phase A and Phase C tests pass**
```bash
pytest tests/test_shared.py tests/test_runner.py -v
# Expected: 25 passed total
```

**2. `requests` library is installed**
```bash
python -c "import requests; print(requests.__version__)"
# If missing: pip install requests && echo "requests>=2.31.0" >> requirements.txt
```

**3. PyYAML is installed**
```bash
python -c "import yaml; print(yaml.__version__)"
# If missing: pip install PyYAML && echo "PyYAML>=6.0" >> requirements.txt
```

---

## File Structure

| File | Responsibility |
|------|----------------|
| `harness/verify/__init__.py` | Package marker |
| `harness/verify/pass1_functional.py` | `run_pass1(corpus_dir) -> dict` — subprocess functional_test.py, parse ASSERT lines |
| `harness/verify/pass2_regression.py` | `run_pass2(scenario_dir, run_id, pass1_result) -> dict` — compare against faulted baseline |
| `harness/verify/pass3_classification.py` | `run_pass3(scenario_dir, run_id, pass1_result, manifest_path) -> dict` — structural diff + semantic check |
| `harness/verify/pass4_concurrency.py` | `run_pass4(scenario_dir, manifest_path, api_endpoint) -> dict` — N concurrent requests, classify responses |
| `harness/verify/verify_loop.py` | `run_verify_loop(scenario_dir, run_id, deployment_outcome, ...) -> dict` — orchestrates all passes |
| `tests/test_verify.py` | Phase D gate |

---

## Task 1: Package marker

**Files:**
- Create: `harness/verify/__init__.py`

- [ ] **Step 1: Create package marker**

```bash
touch harness/verify/__init__.py
```

- [ ] **Step 2: Commit**

```bash
git add harness/verify/__init__.py
git commit -m "feat: add harness/verify package"
```

---

## Task 2: pass1_functional.py

**Files:**
- Create: `harness/verify/pass1_functional.py`
- Create: `tests/test_verify.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_verify.py`:

```python
import json
import os
import pytest
from harness.verify.pass1_functional import run_pass1


class TestPass1Functional:
    def _make_corpus(self, tmp_path, output: str):
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        ft = corpus / "functional_test.py"
        ft.write_text(
            f"import sys\nprint({repr(output)})\nsys.exit(0)\n"
        )
        return str(corpus)

    def test_parses_pass_and_fail_assertions(self, tmp_path):
        output = (
            "ASSERT pass connectivity: connection ok\n"
            "ASSERT fail auth_check: token invalid\n"
            "ASSERT pass data_write: write succeeded\n"
        )
        corpus_dir = self._make_corpus(tmp_path, output)
        result = run_pass1(corpus_dir)
        assert result["assertions"]["connectivity"]["result"] == "pass"
        assert result["assertions"]["auth_check"]["result"] == "fail"
        assert result["assertions"]["data_write"]["result"] == "pass"
        assert result["failed_assertion_names"] == ["auth_check"]

    def test_primary_assertions_passed_excludes_secondary(self, tmp_path):
        output = (
            "ASSERT pass main_check: ok\n"
            "ASSERT fail latency_secondary: too slow\n"
        )
        corpus_dir = self._make_corpus(tmp_path, output)
        result = run_pass1(corpus_dir)
        assert result["primary_assertions_passed"] is True
        assert result["all_assertions_passed"] is False
        assert "latency_secondary" in result["failed_assertion_names"]

    def test_all_assertions_passed_true_when_all_pass(self, tmp_path):
        output = "ASSERT pass check_a: ok\nASSERT pass check_b: ok\n"
        corpus_dir = self._make_corpus(tmp_path, output)
        result = run_pass1(corpus_dir)
        assert result["primary_assertions_passed"] is True
        assert result["all_assertions_passed"] is True
        assert result["failed_assertion_names"] == []

    def test_primary_assertions_passed_false_when_primary_fails(self, tmp_path):
        output = (
            "ASSERT fail main_check: broken\n"
            "ASSERT pass side_check_secondary: ok\n"
        )
        corpus_dir = self._make_corpus(tmp_path, output)
        result = run_pass1(corpus_dir)
        assert result["primary_assertions_passed"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_verify.py::TestPass1Functional -v
```

Expected: `ModuleNotFoundError: No module named 'harness.verify.pass1_functional'`

- [ ] **Step 3: Create `harness/verify/pass1_functional.py`**

```python
import os
import re
import subprocess


def run_pass1(corpus_dir: str) -> dict:
    functional_test = os.path.join(corpus_dir, "functional_test.py")
    result = subprocess.run(
        ["python", functional_test],
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr

    assertions = {}
    for line in output.splitlines():
        m = re.match(r"ASSERT\s+(pass|fail)\s+(\w+):\s*(.*)", line.strip())
        if m:
            verdict, name, message = m.group(1), m.group(2), m.group(3)
            assertions[name] = {"result": verdict, "message": message}

    failed = [n for n, v in assertions.items() if v["result"] == "fail"]
    primary_failed = [n for n in failed if "_secondary" not in n]

    return {
        "assertions": assertions,
        "primary_assertions_passed": len(primary_failed) == 0,
        "all_assertions_passed": len(failed) == 0,
        "failed_assertion_names": failed,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_verify.py::TestPass1Functional -v
```

Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add harness/verify/pass1_functional.py tests/test_verify.py
git commit -m "feat: add pass1_functional — parse ASSERT lines from functional_test.py"
```

---

## Task 3: pass2_regression.py

**Files:**
- Create: `harness/verify/pass2_regression.py`
- Modify: `tests/test_verify.py` (append `TestPass2Regression` class)

- [ ] **Step 1: Append failing tests to `tests/test_verify.py`**

```python
import json
from pathlib import Path
from harness.verify.pass2_regression import run_pass2
import harness.verify.pass2_regression as p2mod


class TestPass2Regression:
    def _write_baseline(self, tmp_path, run_id, assertions: dict, results_dir: str):
        run_dir = Path(results_dir) / run_id
        run_dir.mkdir(parents=True)
        baseline = {
            "assertions": {
                name: {"result": verdict, "message": ""}
                for name, verdict in assertions.items()
            }
        }
        (run_dir / "faulted_baseline.json").write_text(json.dumps(baseline))

    def test_detects_regression_from_pass_to_fail(self, tmp_path, monkeypatch):
        results_dir = str(tmp_path / "results")
        self._write_baseline(tmp_path, "run-r1", {
            "check_a": "pass", "check_b": "pass", "check_c": "fail",
        }, results_dir)
        monkeypatch.setattr(p2mod, "RESULTS_DIR", results_dir)
        pass1_result = {
            "assertions": {
                "check_a": {"result": "pass", "message": ""},
                "check_b": {"result": "fail", "message": "broke"},
                "check_c": {"result": "fail", "message": "still broken"},
            }
        }
        result = run_pass2("scenario", "run-r1", pass1_result)
        assert result["regression_count"] == 1
        assert result["regressions"][0]["assertion"] == "check_b"
        assert result["regressions"][0]["severity"] == "critical"

    def test_secondary_assertion_regression_is_non_critical(self, tmp_path, monkeypatch):
        results_dir = str(tmp_path / "results")
        self._write_baseline(tmp_path, "run-r2", {"check_secondary": "pass"}, results_dir)
        monkeypatch.setattr(p2mod, "RESULTS_DIR", results_dir)
        pass1_result = {"assertions": {"check_secondary": {"result": "fail", "message": ""}}}
        result = run_pass2("scenario", "run-r2", pass1_result)
        assert result["critical_regression_count"] == 0
        assert result["non_critical_regression_count"] == 1

    def test_no_regressions_when_all_stable(self, tmp_path, monkeypatch):
        results_dir = str(tmp_path / "results")
        self._write_baseline(tmp_path, "run-r3", {
            "check_a": "fail", "check_b": "pass",
        }, results_dir)
        monkeypatch.setattr(p2mod, "RESULTS_DIR", results_dir)
        pass1_result = {
            "assertions": {
                "check_a": {"result": "pass", "message": "fixed"},
                "check_b": {"result": "pass", "message": "still ok"},
            }
        }
        result = run_pass2("scenario", "run-r3", pass1_result)
        assert result["regression_count"] == 0
        assert result["regressions"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_verify.py::TestPass2Regression -v
```

Expected: `ModuleNotFoundError: No module named 'harness.verify.pass2_regression'`

- [ ] **Step 3: Create `harness/verify/pass2_regression.py`**

```python
import json
import os

RESULTS_DIR = "results"


def run_pass2(scenario_dir: str, run_id: str, pass1_result: dict) -> dict:
    baseline_path = os.path.join(RESULTS_DIR, run_id, "faulted_baseline.json")
    with open(baseline_path, "r", encoding="utf-8") as f:
        faulted_baseline = json.load(f)

    regressions = []
    for name, baseline_entry in faulted_baseline["assertions"].items():
        if baseline_entry["result"] == "pass":
            current = pass1_result["assertions"].get(name)
            if current and current["result"] == "fail":
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

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_verify.py::TestPass2Regression -v
```

Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add harness/verify/pass2_regression.py tests/test_verify.py
git commit -m "feat: add pass2_regression — detect newly-failing assertions vs faulted baseline"
```

---

## Task 4: pass3_classification.py

**Files:**
- Create: `harness/verify/pass3_classification.py`
- Modify: `tests/test_verify.py` (append `TestPass3Classification` class)

- [ ] **Step 1: Append failing tests to `tests/test_verify.py`**

```python
import json
from pathlib import Path
from harness.verify.pass3_classification import run_pass3
import harness.verify.pass3_classification as p3mod


class TestPass3Classification:
    def _setup(self, tmp_path, run_id, manifest, faulted_yaml, submitted_yaml, diff_text):
        scenario = tmp_path / "scenario"
        scenario.mkdir(exist_ok=True)
        (scenario / "faulted.yaml").write_text(faulted_yaml)
        manifest_path = tmp_path / f"manifest_{run_id}.json"
        manifest_path.write_text(json.dumps(manifest))
        results_dir = str(tmp_path / "results")
        run_dir = Path(results_dir) / run_id
        run_dir.mkdir(parents=True)
        (run_dir / "submitted.yaml").write_text(submitted_yaml)
        (run_dir / "file_change_log.json").write_text(json.dumps({"diff_text": diff_text}))
        return str(scenario), str(manifest_path), results_dir

    def test_root_cause_when_structural_match_and_no_invalid_patch(self, tmp_path, monkeypatch):
        faulted_yaml = "Resources:\n  MyFn:\n    Properties:\n      Timeout: 3\n"
        submitted_yaml = "Resources:\n  MyFn:\n    Properties:\n      Timeout: 30\n"
        manifest = {
            "target_resource": "MyFn", "target_property": "Properties.Timeout",
            "original_value": 30, "invalid_patches": ["env_var_workaround"], "valid_fixes": [],
        }
        scenario_dir, manifest_path, results_dir = self._setup(
            tmp_path, "run-p3-1", manifest, faulted_yaml, submitted_yaml,
            "-      Timeout: 3\n+      Timeout: 30\n"
        )
        monkeypatch.setattr(p3mod, "RESULTS_DIR", results_dir)
        pass1_result = {"primary_assertions_passed": True,
                        "assertions": {"check_a": {"result": "pass", "message": "ok"}}}
        result = run_pass3(scenario_dir, "run-p3-1", pass1_result, manifest_path)
        assert result["classification"] == "root_cause"
        assert result["structural_match"] is True
        assert result["invalid_patch_detected"] is False
        assert result["root_cause_addressed"] is True

    def test_workaround_when_assertions_pass_but_structural_mismatch(self, tmp_path, monkeypatch):
        faulted_yaml = "Resources:\n  MyFn:\n    Properties:\n      Timeout: 3\n"
        submitted_yaml = "Resources:\n  MyFn:\n    Properties:\n      Timeout: 3\n"
        manifest = {
            "target_resource": "MyFn", "target_property": "Properties.Timeout",
            "original_value": 30, "invalid_patches": [], "valid_fixes": [],
        }
        scenario_dir, manifest_path, results_dir = self._setup(
            tmp_path, "run-p3-2", manifest, faulted_yaml, submitted_yaml, "+  ENV_VAR: override\n"
        )
        monkeypatch.setattr(p3mod, "RESULTS_DIR", results_dir)
        pass1_result = {"primary_assertions_passed": True,
                        "assertions": {"check_a": {"result": "pass", "message": "ok"}}}
        result = run_pass3(scenario_dir, "run-p3-2", pass1_result, manifest_path)
        assert result["classification"] == "workaround"
        assert result["structural_match"] is False

    def test_invalid_patch_string_in_diff_detected(self, tmp_path, monkeypatch):
        faulted_yaml = "Resources:\n  MyFn:\n    Properties:\n      Timeout: 3\n"
        submitted_yaml = "Resources:\n  MyFn:\n    Properties:\n      Timeout: 30\n"
        manifest = {
            "target_resource": "MyFn", "target_property": "Properties.Timeout",
            "original_value": 30, "invalid_patches": ["env_var_workaround"], "valid_fixes": [],
        }
        diff_text = "+      Timeout: 30\n+      env_var_workaround: true\n"
        scenario_dir, manifest_path, results_dir = self._setup(
            tmp_path, "run-p3-3", manifest, faulted_yaml, submitted_yaml, diff_text
        )
        monkeypatch.setattr(p3mod, "RESULTS_DIR", results_dir)
        pass1_result = {"primary_assertions_passed": True,
                        "assertions": {"check_a": {"result": "pass", "message": "ok"}}}
        result = run_pass3(scenario_dir, "run-p3-3", pass1_result, manifest_path)
        assert result["invalid_patch_detected"] is True
        assert result["classification"] != "root_cause"

    def test_partial_when_no_primary_assertions_pass(self, tmp_path, monkeypatch):
        faulted_yaml = "Resources:\n  MyFn:\n    Properties:\n      Timeout: 3\n"
        submitted_yaml = faulted_yaml
        manifest = {
            "target_resource": "MyFn", "target_property": "Properties.Timeout",
            "original_value": 30, "invalid_patches": [], "valid_fixes": [],
        }
        scenario_dir, manifest_path, results_dir = self._setup(
            tmp_path, "run-p3-4", manifest, faulted_yaml, submitted_yaml, ""
        )
        monkeypatch.setattr(p3mod, "RESULTS_DIR", results_dir)
        pass1_result = {"primary_assertions_passed": False,
                        "assertions": {"check_a": {"result": "fail", "message": "still broken"}}}
        result = run_pass3(scenario_dir, "run-p3-4", pass1_result, manifest_path)
        assert result["classification"] in ("partial", "none")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_verify.py::TestPass3Classification -v
```

Expected: `ModuleNotFoundError: No module named 'harness.verify.pass3_classification'`

- [ ] **Step 3: Create `harness/verify/pass3_classification.py`**

```python
import json
import os

import yaml

RESULTS_DIR = "results"


def _navigate(data: dict, dot_path: str):
    node = data
    for part in dot_path.split("."):
        if isinstance(node, dict):
            node = node.get(part)
        else:
            return None
    return node


def run_pass3(scenario_dir: str, run_id: str, pass1_result: dict, manifest_path: str) -> dict:
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    with open(os.path.join(scenario_dir, "faulted.yaml"), "r", encoding="utf-8") as f:
        faulted_doc = yaml.safe_load(f)

    submitted_path = os.path.join(RESULTS_DIR, run_id, "submitted.yaml")
    if os.path.isfile(submitted_path):
        with open(submitted_path, "r", encoding="utf-8") as f:
            submitted_doc = yaml.safe_load(f)
    else:
        submitted_doc = faulted_doc

    target_resource = manifest.get("target_resource", "")
    target_property = manifest.get("target_property", "")
    original_value = manifest.get("original_value")
    invalid_patches = manifest.get("invalid_patches", [])

    # Signal 1 — structural diff
    submitted_resources = (submitted_doc or {}).get("Resources", {})
    resource_node = submitted_resources.get(target_resource, {})
    submitted_value = _navigate(resource_node, target_property)
    structural_match = submitted_value == original_value

    # Signal 2 — invalid patch substring in diff text
    change_log_path = os.path.join(RESULTS_DIR, run_id, "file_change_log.json")
    diff_text = ""
    if os.path.isfile(change_log_path):
        with open(change_log_path, "r", encoding="utf-8") as f:
            change_log = json.load(f)
        diff_text = change_log.get("diff_text", "")

    invalid_patch_detected = any(p in diff_text for p in invalid_patches)

    primary_passed = pass1_result.get("primary_assertions_passed", False)
    assertions = pass1_result.get("assertions", {})

    if structural_match and not invalid_patch_detected:
        classification = "root_cause"
    elif primary_passed and not structural_match:
        classification = "workaround"
    elif not primary_passed:
        any_improvement = any(v["result"] == "pass" for v in assertions.values())
        classification = "partial" if any_improvement else "none"
    else:
        classification = "none"

    return {
        "structural_match": structural_match,
        "invalid_patch_detected": invalid_patch_detected,
        "classification": classification,
        "root_cause_addressed": classification == "root_cause",
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_verify.py::TestPass3Classification -v
```

Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add harness/verify/pass3_classification.py tests/test_verify.py
git commit -m "feat: add pass3_classification — structural diff + semantic invalid patch detection"
```

---

## Task 5: pass4_concurrency.py

**Files:**
- Create: `harness/verify/pass4_concurrency.py`
- Modify: `tests/test_verify.py` (append `TestPass4Concurrency` class)

- [ ] **Step 1: Append failing tests to `tests/test_verify.py`**

```python
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
from harness.verify.pass4_concurrency import run_pass4


class TestPass4Concurrency:
    def _make_manifest(self, tmp_path, run_id, manifest: dict):
        path = tmp_path / f"manifest_{run_id}.json"
        path.write_text(json.dumps(manifest))
        return str(path)

    def _mock_post(self, status_code):
        r = MagicMock()
        r.status_code = status_code
        return r

    def test_all_success_returns_passed_true(self, tmp_path):
        manifest_path = self._make_manifest(tmp_path, "p4-1",
                                            {"fault_class": "reliability", "concurrency_probe_n": 5})
        with patch("harness.verify.pass4_concurrency.requests.post",
                   return_value=self._mock_post(200)):
            result = run_pass4("scenario", manifest_path, "http://localhost:4566/test")
        assert result["requests_sent"] == 5
        assert result["success_count"] == 5
        assert result["throttled_count"] == 0
        assert result["timeout_count"] == 0
        assert result["passed"] is True

    def test_throttled_response_sets_passed_false(self, tmp_path):
        manifest_path = self._make_manifest(tmp_path, "p4-2",
                                            {"fault_class": "performance", "concurrency_probe_n": 4})
        responses = [200, 200, 429, 200]
        idx = [0]
        def mock_post(*a, **kw):
            r = MagicMock()
            r.status_code = responses[idx[0] % len(responses)]
            idx[0] += 1
            return r
        with patch("harness.verify.pass4_concurrency.requests.post", side_effect=mock_post):
            result = run_pass4("scenario", manifest_path, "http://localhost:4566/test")
        assert result["throttled_count"] == 1
        assert result["passed"] is False

    def test_timeout_response_sets_passed_false(self, tmp_path):
        manifest_path = self._make_manifest(tmp_path, "p4-3",
                                            {"fault_class": "reliability", "concurrency_probe_n": 3})
        with patch("harness.verify.pass4_concurrency.requests.post",
                   return_value=self._mock_post(504)):
            result = run_pass4("scenario", manifest_path, "http://localhost:4566/test")
        assert result["timeout_count"] == 3
        assert result["passed"] is False

    def test_uses_default_n_10_when_field_absent(self, tmp_path):
        manifest_path = self._make_manifest(tmp_path, "p4-4", {"fault_class": "reliability"})
        with patch("harness.verify.pass4_concurrency.requests.post",
                   return_value=self._mock_post(200)):
            result = run_pass4("scenario", manifest_path, "http://localhost:4566/test")
        assert result["requests_sent"] == 10
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_verify.py::TestPass4Concurrency -v
```

Expected: `ModuleNotFoundError: No module named 'harness.verify.pass4_concurrency'`

- [ ] **Step 3: Create `harness/verify/pass4_concurrency.py`**

```python
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests


def run_pass4(scenario_dir: str, manifest_path: str, api_endpoint: str) -> dict:
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    n = manifest.get("concurrency_probe_n", 10)
    counts = {"success": 0, "throttled": 0, "timeout": 0, "error": 0}

    def send(_):
        try:
            resp = requests.post(api_endpoint, json={}, timeout=10)
            if resp.status_code == 200:
                return "success"
            elif resp.status_code == 429:
                return "throttled"
            elif resp.status_code == 504:
                return "timeout"
            else:
                return "error"
        except Exception:
            return "error"

    with ThreadPoolExecutor(max_workers=n) as executor:
        for future in as_completed([executor.submit(send, i) for i in range(n)]):
            counts[future.result()] += 1

    return {
        "requests_sent": n,
        "success_count": counts["success"],
        "throttled_count": counts["throttled"],
        "timeout_count": counts["timeout"],
        "error_count": counts["error"],
        "passed": counts["throttled"] == 0 and counts["timeout"] == 0,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_verify.py::TestPass4Concurrency -v
```

Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add harness/verify/pass4_concurrency.py tests/test_verify.py
git commit -m "feat: add pass4_concurrency — N concurrent requests classified by status code"
```

---

## Task 6: verify_loop.py orchestrator

**Files:**
- Create: `harness/verify/verify_loop.py`
- Modify: `tests/test_verify.py` (append `TestVerifyLoop` class)

- [ ] **Step 1: Append failing tests to `tests/test_verify.py`**

```python
import json
from pathlib import Path
from harness.verify.verify_loop import run_verify_loop
import harness.verify.verify_loop as vlmod


class TestVerifyLoop:
    def _write_baseline(self, results_dir, run_id, assertions):
        run_dir = Path(results_dir) / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        baseline = {"assertions": {n: {"result": v, "message": ""} for n, v in assertions.items()}}
        (run_dir / "faulted_baseline.json").write_text(json.dumps(baseline))

    def test_did_not_deploy_skips_all_passes(self, tmp_path, monkeypatch):
        results_dir = str(tmp_path / "results")
        self._write_baseline(results_dir, "run-v1", {})
        monkeypatch.setattr(vlmod, "RESULTS_DIR", results_dir)
        monkeypatch.setattr(vlmod, "log_verify_result", lambda *a, **kw: None)
        result = run_verify_loop("scenario", "run-v1", deployment_outcome="lint_fail")
        assert result["outcome"] == "did_not_deploy"
        assert result["pass1_functional"] is None
        assert result["pass2_regression"] is None
        assert result["pass3_classification"] is None
        assert result["pass4_concurrency"] is None

    def test_pass4_skipped_for_non_performance_fault_class(self, tmp_path, monkeypatch):
        results_dir = str(tmp_path / "results")
        self._write_baseline(results_dir, "run-v2", {"check_a": "fail"})
        monkeypatch.setattr(vlmod, "RESULTS_DIR", results_dir)
        monkeypatch.setattr(vlmod, "log_verify_result", lambda *a, **kw: None)
        pass1 = {"assertions": {"check_a": {"result": "pass", "message": ""}},
                 "primary_assertions_passed": True, "all_assertions_passed": True, "failed_assertion_names": []}
        pass2 = {"regression_count": 0, "regressions": [],
                 "critical_regression_count": 0, "non_critical_regression_count": 0}
        pass3 = {"structural_match": True, "invalid_patch_detected": False,
                 "classification": "root_cause", "root_cause_addressed": True}
        monkeypatch.setattr(vlmod, "run_pass1", lambda *a, **kw: pass1)
        monkeypatch.setattr(vlmod, "run_pass2", lambda *a, **kw: pass2)
        monkeypatch.setattr(vlmod, "run_pass3", lambda *a, **kw: pass3)
        manifest_path = str(tmp_path / "manifest.json")
        Path(manifest_path).write_text(json.dumps({"fault_class": "config"}))
        result = run_verify_loop("scenario", "run-v2", deployment_outcome="deploy_success",
                                 manifest_path=manifest_path, corpus_dir="corpus",
                                 api_endpoint="http://localhost:4566")
        assert result["pass4_concurrency"] is None

    def test_pass4_failure_overrides_pass3_to_partial(self, tmp_path, monkeypatch):
        results_dir = str(tmp_path / "results")
        self._write_baseline(results_dir, "run-v3", {"check_a": "fail"})
        monkeypatch.setattr(vlmod, "RESULTS_DIR", results_dir)
        monkeypatch.setattr(vlmod, "log_verify_result", lambda *a, **kw: None)
        pass1 = {"assertions": {"check_a": {"result": "pass", "message": ""}},
                 "primary_assertions_passed": True, "all_assertions_passed": True, "failed_assertion_names": []}
        pass2 = {"regression_count": 0, "regressions": [],
                 "critical_regression_count": 0, "non_critical_regression_count": 0}
        pass3 = {"structural_match": True, "invalid_patch_detected": False,
                 "classification": "root_cause", "root_cause_addressed": True}
        pass4 = {"requests_sent": 10, "success_count": 7, "throttled_count": 3,
                 "timeout_count": 0, "error_count": 0, "passed": False}
        monkeypatch.setattr(vlmod, "run_pass1", lambda *a, **kw: pass1)
        monkeypatch.setattr(vlmod, "run_pass2", lambda *a, **kw: pass2)
        monkeypatch.setattr(vlmod, "run_pass3", lambda *a, **kw: pass3)
        monkeypatch.setattr(vlmod, "run_pass4", lambda *a, **kw: pass4)
        manifest_path = str(tmp_path / "manifest.json")
        Path(manifest_path).write_text(json.dumps({"fault_class": "reliability", "concurrency_probe_n": 10}))
        result = run_verify_loop("scenario", "run-v3", deployment_outcome="deploy_success",
                                 manifest_path=manifest_path, corpus_dir="corpus",
                                 api_endpoint="http://localhost:4566")
        assert result["pass3_classification"]["classification"] == "partial"
        assert result["pass4_concurrency"]["passed"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_verify.py::TestVerifyLoop -v
```

Expected: `ModuleNotFoundError: No module named 'harness.verify.verify_loop'`

- [ ] **Step 3: Create `harness/verify/verify_loop.py`**

```python
import json
import os

from harness.shared.result_logger import log_verify_result
from harness.verify.pass1_functional import run_pass1
from harness.verify.pass2_regression import run_pass2
from harness.verify.pass3_classification import run_pass3
from harness.verify.pass4_concurrency import run_pass4

RESULTS_DIR = "results"

_CONCURRENCY_CLASSES = {"performance", "reliability"}


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

    pass1 = run_pass1(corpus_dir or scenario_dir)
    pass2 = run_pass2(scenario_dir, run_id, pass1)
    pass3 = run_pass3(scenario_dir, run_id, pass1, manifest_path)

    pass4 = None
    if manifest_path and os.path.isfile(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        if manifest.get("fault_class") in _CONCURRENCY_CLASSES:
            pass4 = run_pass4(scenario_dir, manifest_path, api_endpoint or "")
            if not pass4["passed"] and pass1["primary_assertions_passed"]:
                pass3 = dict(pass3)
                pass3["classification"] = "partial"
                pass3["root_cause_addressed"] = False

    result = {
        "outcome": "completed",
        "pass1_functional": pass1,
        "pass2_regression": pass2,
        "pass3_classification": pass3,
        "pass4_concurrency": pass4,
    }
    log_verify_result(run_id, result)
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_verify.py::TestVerifyLoop -v
```

Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add harness/verify/verify_loop.py tests/test_verify.py
git commit -m "feat: add verify_loop orchestrator with Pass 4 override rule"
```

---

## Task 7: Phase D Gate — Full Test Suite

- [ ] **Step 1: Run all Phase D tests**

```bash
pytest tests/test_verify.py -v
```

Expected (18 tests):
```
tests/test_verify.py::TestPass1Functional::test_parses_pass_and_fail_assertions PASSED
tests/test_verify.py::TestPass1Functional::test_primary_assertions_passed_excludes_secondary PASSED
tests/test_verify.py::TestPass1Functional::test_all_assertions_passed_true_when_all_pass PASSED
tests/test_verify.py::TestPass1Functional::test_primary_assertions_passed_false_when_primary_fails PASSED
tests/test_verify.py::TestPass2Regression::test_detects_regression_from_pass_to_fail PASSED
tests/test_verify.py::TestPass2Regression::test_secondary_assertion_regression_is_non_critical PASSED
tests/test_verify.py::TestPass2Regression::test_no_regressions_when_all_stable PASSED
tests/test_verify.py::TestPass3Classification::test_root_cause_when_structural_match_and_no_invalid_patch PASSED
tests/test_verify.py::TestPass3Classification::test_workaround_when_assertions_pass_but_structural_mismatch PASSED
tests/test_verify.py::TestPass3Classification::test_invalid_patch_string_in_diff_detected PASSED
tests/test_verify.py::TestPass3Classification::test_partial_when_no_primary_assertions_pass PASSED
tests/test_verify.py::TestPass4Concurrency::test_all_success_returns_passed_true PASSED
tests/test_verify.py::TestPass4Concurrency::test_throttled_response_sets_passed_false PASSED
tests/test_verify.py::TestPass4Concurrency::test_timeout_response_sets_passed_false PASSED
tests/test_verify.py::TestPass4Concurrency::test_uses_default_n_10_when_field_absent PASSED
tests/test_verify.py::TestVerifyLoop::test_did_not_deploy_skips_all_passes PASSED
tests/test_verify.py::TestVerifyLoop::test_pass4_skipped_for_non_performance_fault_class PASSED
tests/test_verify.py::TestVerifyLoop::test_pass4_failure_overrides_pass3_to_partial PASSED

18 passed
```

- [ ] **Step 2: Verify all public symbols import cleanly**

```bash
python -c "
from harness.verify.pass1_functional import run_pass1
from harness.verify.pass2_regression import run_pass2
from harness.verify.pass3_classification import run_pass3
from harness.verify.pass4_concurrency import run_pass4
from harness.verify.verify_loop import run_verify_loop
print('All imports OK')
"
```

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "feat: Phase D complete — verify loop with 4 passes, 18 passing tests"
```

**Phase D gate is clear. Phase E may begin.**

---

## Self-Review

### Spec coverage

| Spec requirement | Covered by |
|-----------------|------------|
| D1 — `run_verify_loop` orchestrates all four passes in order | Task 6 |
| D1 — Pass 2 runs even if Pass 1 fails | `run_pass2` always called in `verify_loop.py` |
| D1 — Pass 4 only runs for performance/reliability | `test_pass4_skipped_for_non_performance_fault_class` |
| D1 — `did_not_deploy` skips all passes | `test_did_not_deploy_skips_all_passes` |
| D1 — Writes `verify_result.json` via `log_verify_result` | `verify_loop.py` calls `log_verify_result(run_id, result)` |
| D2 — Parse `ASSERT pass/fail name: msg` lines | `test_parses_pass_and_fail_assertions` |
| D2 — `primary_assertions_passed` excludes `_secondary` names | `test_primary_assertions_passed_excludes_secondary` |
| D2 — `all_assertions_passed` and `failed_assertion_names` | `test_all_assertions_passed_true_when_all_pass` |
| D3 — Detect assertions that flipped pass → fail vs baseline | `test_detects_regression_from_pass_to_fail` |
| D3 — `_secondary` regressions are `non_critical` | `test_secondary_assertion_regression_is_non_critical` |
| D3 — No regressions when assertions are stable | `test_no_regressions_when_all_stable` |
| D4 — `root_cause` when structural match + no invalid patch | `test_root_cause_when_structural_match_and_no_invalid_patch` |
| D4 — `workaround` when assertions pass but no structural match | `test_workaround_when_assertions_pass_but_structural_mismatch` |
| D4 — Invalid patch substring in diff detected | `test_invalid_patch_string_in_diff_detected` |
| D4 — `partial`/`none` when primary assertions fail | `test_partial_when_no_primary_assertions_pass` |
| D5 — N concurrent requests, 429=throttled, 504=timeout | Tasks 5 tests |
| D5 — Default N=10 when `concurrency_probe_n` absent | `test_uses_default_n_10_when_field_absent` |
| D5 — Pass 4 fail + Pass 1 pass → override Pass 3 to `partial` | `test_pass4_failure_overrides_pass3_to_partial` |

### Placeholder scan

No TBD, TODO, or vague steps found.

### Type consistency

- `run_pass1(corpus_dir)` — 1 param, called identically in `verify_loop.py` and tests
- `run_pass2(scenario_dir, run_id, pass1_result)` — 3 params, consistent across definition, `verify_loop.py`, and tests
- `run_pass3(scenario_dir, run_id, pass1_result, manifest_path)` — 4 params, consistent
- `run_pass4(scenario_dir, manifest_path, api_endpoint)` — 3 params, consistent
- `RESULTS_DIR` — module-level string in both `pass2_regression.py` and `verify_loop.py`; patched via `monkeypatch.setattr(mod, "RESULTS_DIR", ...)` in all tests — matches Phase A pattern
- `log_verify_result` — called as `log_verify_result(run_id, result)` in `verify_loop.py`; matches Phase A `result_logger.log_verify_result(run_id: str, result: dict)` signature
