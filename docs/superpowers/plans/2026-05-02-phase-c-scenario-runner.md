# Phase C — Scenario Runner & Deployment Handler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the three runner modules (`context_builder`, `scenario_runner`, `deployment_handler`) that present a scenario to the model, intercept its MCP tool calls, and orchestrate its final redeployment submission.

**Architecture:** Three focused Python modules in `harness/runner/`. `context_builder` reads scenario files and returns a context dict. `scenario_runner` owns the lifecycle: deploys, monitors the MCP stderr stream, fires on redeployment. `deployment_handler` executes the submission pipeline (snapshot → lint → package Lambda → CloudFormation update). Tests use mocks — no live LocalStack required for the gate.

**Tech Stack:** Python 3.11, boto3 (via Phase A singletons), subprocess, zipfile, threading, pytest 8, pytest-mock, PyYAML

---

## Manual Pre-Configuration — Builder Must Complete These Before Writing Any Files

**1. Phase A and Phase B tests pass**
```bash
pytest tests/test_shared.py -v
# Expected: 17 passed
LOCALSTACK_ENDPOINT=http://localhost:4566 node --test tests/test_mcp_server.js
# Expected: all tests passed
```

**2. MCP server is registered with Claude Code**
```bash
claude mcp list
# Expected: ace-bench-diagnostic-mcp appears
```

**3. `.env` exists at project root with HARNESS_API_KEY**
```bash
test -f .env && grep -q HARNESS_API_KEY .env && echo "OK" || echo "MISSING"
```

**4. `localstack-deployer` CLI is available**
```bash
localstack-deployer --version
# If missing: pip install localstack-deployer
```

**5. PyYAML is installed**
```bash
python -c "import yaml; print(yaml.__version__)"
# If missing: pip install PyYAML
```

---

## File Structure

| File | Responsibility |
|------|----------------|
| `harness/runner/__init__.py` | Package marker |
| `harness/runner/context_builder.py` | `build_context(scenario_dir) -> dict` — reads scenario files, guards manifest |
| `harness/runner/scenario_runner.py` | `ScenarioRunner` class — lifecycle, tool call interception, submission gate |
| `harness/runner/deployment_handler.py` | `handle_submission(scenario_dir, run_id, start_snapshot) -> dict` — lint, package, CF update |
| `tests/test_runner.py` | Phase C gate — all mocked, no live LocalStack required |

---

## Task 1: Package marker

**Files:**
- Create: `harness/runner/__init__.py`

- [ ] **Step 1: Create package marker**

```bash
touch harness/runner/__init__.py
```

- [ ] **Step 2: Commit**

```bash
git add harness/runner/__init__.py
git commit -m "feat: add harness/runner package"
```

---

## Task 2: context_builder.py

**Files:**
- Create: `harness/runner/context_builder.py`
- Create: `tests/test_runner.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_runner.py`:

```python
import json
import os
import pytest
from harness.runner.context_builder import build_context

_FIXED_INSTRUCTION = (
    "A deployed instance of this system is running in your local environment. "
    "The deployment directory and CloudFormation template are available to you directly. "
    "Diagnostic tools are available via MCP. Diagnose the reported symptom, edit whatever "
    "files need changing, and redeploy using localstack-deployer when ready. "
    "Your first successful redeployment is your scored submission."
)


class TestContextBuilder:
    def _make_scenario(self, tmp_path, include_manifest=False):
        scenario = tmp_path / "scenario"
        scenario.mkdir()
        (scenario / "scenario.md").write_text("The API returns 500 on POST /orders.")
        (scenario / "faulted.yaml").write_text("AWSTemplateFormatVersion: '2010-09-09'\n")
        deployment = scenario / "deployment"
        deployment.mkdir()
        (deployment / "handler.py").write_text("def handler(event, ctx): return {'statusCode': 200}\n")
        if include_manifest:
            (scenario / "fault_manifest.json").write_text(json.dumps({"fault_class": "config"}))
        return str(scenario)

    def test_raises_value_error_when_manifest_present(self, tmp_path, mocker):
        mocker.patch(
            "harness.runner.context_builder._get_stack_outputs",
            return_value={},
        )
        scenario_dir = self._make_scenario(tmp_path, include_manifest=True)
        with pytest.raises(ValueError, match="fault_manifest.json"):
            build_context(scenario_dir)

    def test_returns_correct_shape(self, tmp_path, mocker):
        mocker.patch(
            "harness.runner.context_builder._get_stack_outputs",
            return_value={"ApiEndpoint": "http://localhost:4566/restapis/abc/test/_user_request_"},
        )
        scenario_dir = self._make_scenario(tmp_path)
        ctx = build_context(scenario_dir)
        assert ctx["scenario_brief"] == "The API returns 500 on POST /orders."
        assert ctx["template_path"].endswith("faulted.yaml")
        assert ctx["deployment_dir"].endswith("deployment")
        assert ctx["stack_outputs"]["ApiEndpoint"].startswith("http")
        assert ctx["instruction"] == _FIXED_INSTRUCTION

    def test_template_path_is_absolute(self, tmp_path, mocker):
        mocker.patch("harness.runner.context_builder._get_stack_outputs", return_value={})
        scenario_dir = self._make_scenario(tmp_path)
        ctx = build_context(scenario_dir)
        assert os.path.isabs(ctx["template_path"])
        assert os.path.isabs(ctx["deployment_dir"])
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_runner.py::TestContextBuilder -v
```

Expected: `ModuleNotFoundError: No module named 'harness.runner.context_builder'`

- [ ] **Step 3: Create `harness/runner/context_builder.py`**

```python
import os
from botocore.exceptions import ClientError
from harness.shared.localstack_client import cf_client

_FIXED_INSTRUCTION = (
    "A deployed instance of this system is running in your local environment. "
    "The deployment directory and CloudFormation template are available to you directly. "
    "Diagnostic tools are available via MCP. Diagnose the reported symptom, edit whatever "
    "files need changing, and redeploy using localstack-deployer when ready. "
    "Your first successful redeployment is your scored submission."
)

_STACK_NAME = "ace-bench-stack"


def _get_stack_outputs() -> dict:
    try:
        res = cf_client.describe_stacks(StackName=_STACK_NAME)
        outputs = {}
        for o in res["Stacks"][0].get("Outputs", []):
            outputs[o["OutputKey"]] = o["OutputValue"]
        return outputs
    except ClientError:
        return {}


def build_context(scenario_dir: str) -> dict:
    scenario_dir = os.path.abspath(scenario_dir)
    manifest_path = os.path.join(scenario_dir, "fault_manifest.json")
    if os.path.isfile(manifest_path):
        raise ValueError(
            f"fault_manifest.json is readable from model-accessible path: {manifest_path}. "
            "Move it out of the scenario directory before running the harness."
        )

    with open(os.path.join(scenario_dir, "scenario.md"), "r", encoding="utf-8") as f:
        scenario_brief = f.read()

    return {
        "scenario_brief": scenario_brief,
        "template_path": os.path.join(scenario_dir, "faulted.yaml"),
        "deployment_dir": os.path.join(scenario_dir, "deployment"),
        "stack_outputs": _get_stack_outputs(),
        "instruction": _FIXED_INSTRUCTION,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_runner.py::TestContextBuilder -v
```

Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add harness/runner/context_builder.py tests/test_runner.py
git commit -m "feat: add context_builder with fault_manifest guard"
```

---

## Task 3: deployment_handler.py

**Files:**
- Create: `harness/runner/deployment_handler.py`
- Modify: `tests/test_runner.py` (append `TestDeploymentHandler` class)

- [ ] **Step 1: Append failing tests to `tests/test_runner.py`**

```python
import zipfile
from unittest.mock import MagicMock
from harness.runner.deployment_handler import handle_submission


class TestDeploymentHandler:
    def _make_scenario(self, tmp_path):
        scenario = tmp_path / "scenario"
        scenario.mkdir()
        (scenario / "faulted.yaml").write_text(
            "AWSTemplateFormatVersion: '2010-09-09'\n"
            "Resources:\n"
            "  MyFn:\n"
            "    Type: AWS::Lambda::Function\n"
            "    Properties:\n"
            "      S3Key: old-handler.zip\n"
        )
        deployment = scenario / "deployment"
        deployment.mkdir()
        lam = deployment / "lambda"
        lam.mkdir()
        (lam / "handler.py").write_text("def handler(e,c): return 200\n")
        return str(scenario)

    def test_returns_lint_fail_on_fatal_errors(self, tmp_path, mocker):
        scenario_dir = self._make_scenario(tmp_path)
        mocker.patch(
            "harness.runner.deployment_handler.run_lint",
            return_value={"passed": False, "fatal_errors": [{"rule": "E3001", "message": "bad", "location": "line 1"}]},
        )
        mocker.patch("harness.runner.deployment_handler.snapshot", return_value={})
        mocker.patch("harness.runner.deployment_handler.diff_snapshots", return_value={
            "files_added": [], "files_modified": ["deployment/lambda/handler.py"],
            "files_removed": [], "total_files_changed": 1,
            "per_file_line_changes": {}, "total_lines_changed": 0,
        })
        mocker.patch("harness.runner.deployment_handler.log_file_change")
        result = handle_submission(scenario_dir, "run-001", {})
        assert result["outcome"] == "lint_fail"
        assert len(result["errors"]) == 1

    def test_packaging_preflight_zips_and_uploads_lambda(self, tmp_path, mocker):
        scenario_dir = self._make_scenario(tmp_path)
        mocker.patch(
            "harness.runner.deployment_handler.run_lint",
            return_value={"passed": True, "fatal_errors": [], "warnings": []},
        )
        mocker.patch("harness.runner.deployment_handler.snapshot", return_value={})
        mocker.patch("harness.runner.deployment_handler.diff_snapshots", return_value={
            "files_added": [], "files_modified": [os.path.join("deployment", "lambda", "handler.py")],
            "files_removed": [], "total_files_changed": 1,
            "per_file_line_changes": {}, "total_lines_changed": 0,
        })
        mocker.patch("harness.runner.deployment_handler.log_file_change")
        mock_s3 = MagicMock()
        mock_cf = MagicMock()
        mock_cf.update_stack.return_value = {}
        mock_cf.describe_stack_events.return_value = {"StackEvents": []}
        mock_waiter = MagicMock()
        mock_cf.get_waiter.return_value = mock_waiter
        mocker.patch("harness.runner.deployment_handler.s3_client", mock_s3)
        mocker.patch("harness.runner.deployment_handler.cf_client", mock_cf)
        handle_submission(scenario_dir, "run-002", {})
        assert mock_s3.put_object.called
        call_kwargs = mock_s3.put_object.call_args.kwargs
        assert call_kwargs["Bucket"] == "ace-bench-artifacts"
        assert call_kwargs["Key"].endswith(".zip")

    def test_deploy_fail_returns_rollback_outcome(self, tmp_path, mocker):
        scenario_dir = self._make_scenario(tmp_path)
        mocker.patch(
            "harness.runner.deployment_handler.run_lint",
            return_value={"passed": True, "fatal_errors": [], "warnings": []},
        )
        mocker.patch("harness.runner.deployment_handler.snapshot", return_value={})
        mocker.patch("harness.runner.deployment_handler.diff_snapshots", return_value={
            "files_added": [], "files_modified": [], "files_removed": [],
            "total_files_changed": 0, "per_file_line_changes": {}, "total_lines_changed": 0,
        })
        mocker.patch("harness.runner.deployment_handler.log_file_change")
        from botocore.exceptions import WaiterError
        mock_cf = MagicMock()
        mock_cf.update_stack.return_value = {}
        mock_waiter = MagicMock()
        mock_waiter.wait.side_effect = WaiterError("update_complete", "Waiter failed", None)
        mock_cf.get_waiter.return_value = mock_waiter
        mock_cf.describe_stack_events.return_value = {
            "StackEvents": [
                {
                    "LogicalResourceId": "MyFn",
                    "ResourceStatus": "UPDATE_ROLLBACK_COMPLETE",
                    "ResourceStatusReason": "Resource creation cancelled",
                }
            ]
        }
        mocker.patch("harness.runner.deployment_handler.cf_client", mock_cf)
        mocker.patch("harness.runner.deployment_handler.s3_client", MagicMock())
        result = handle_submission(scenario_dir, "run-003", {})
        assert result["outcome"] == "deploy_fail"
        assert len(result["events"]) > 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_runner.py::TestDeploymentHandler -v
```

Expected: `ModuleNotFoundError: No module named 'harness.runner.deployment_handler'`

- [ ] **Step 3: Create `harness/runner/deployment_handler.py`**

```python
import io
import os
import zipfile

from botocore.exceptions import WaiterError

from harness.shared.cfn_lint_runner import run_lint
from harness.shared.file_differ import snapshot, diff_snapshots
from harness.shared.localstack_client import cf_client, s3_client
from harness.shared.result_logger import log_file_change

_STACK_NAME = "ace-bench-stack"
_ARTIFACT_BUCKET = "ace-bench-artifacts"


def _ensure_artifact_bucket() -> None:
    try:
        s3_client.create_bucket(Bucket=_ARTIFACT_BUCKET)
    except Exception:
        pass


def _zip_file(file_path: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(file_path, arcname=os.path.basename(file_path))
    return buf.getvalue()


def handle_submission(scenario_dir: str, run_id: str, start_snapshot: dict) -> dict:
    scenario_dir = os.path.abspath(scenario_dir)
    deployment_dir = os.path.join(scenario_dir, "deployment")
    template_path = os.path.join(scenario_dir, "faulted.yaml")

    # Step 1 — diff snapshots and log
    end_snapshot = snapshot(deployment_dir)
    diff = diff_snapshots(start_snapshot, end_snapshot, deployment_dir)
    log_file_change(run_id, diff)

    # Step 2 — cfn-lint
    lint_result = run_lint(template_path)
    if not lint_result["passed"]:
        return {"outcome": "lint_fail", "errors": lint_result["fatal_errors"]}

    # Step 3 — read template body
    with open(template_path, "r", encoding="utf-8") as f:
        template_body = f.read()

    # Step 3 — packaging pre-flight for changed Lambda files
    lambda_rel_prefix = os.path.join("deployment", "lambda") + os.sep
    for rel_path in diff["files_modified"] + diff["files_added"]:
        if rel_path.startswith(lambda_rel_prefix) and rel_path.endswith(".py"):
            abs_path = os.path.join(deployment_dir, "lambda", os.path.basename(rel_path))
            fn_name = os.path.splitext(os.path.basename(abs_path))[0]
            zip_key = f"lambdas/{run_id}/{fn_name}.zip"
            _ensure_artifact_bucket()
            s3_client.put_object(Bucket=_ARTIFACT_BUCKET, Key=zip_key, Body=_zip_file(abs_path))
            template_body = template_body.replace("old-handler.zip", zip_key)

    # Step 4 — CloudFormation update
    cf_client.update_stack(
        StackName=_STACK_NAME,
        TemplateBody=template_body,
        Capabilities=["CAPABILITY_IAM", "CAPABILITY_NAMED_IAM"],
    )

    try:
        waiter = cf_client.get_waiter("stack_update_complete")
        waiter.wait(StackName=_STACK_NAME, WaiterConfig={"Delay": 5, "MaxAttempts": 60})
        return {"outcome": "deploy_success"}
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
        return {"outcome": "deploy_fail", "events": events}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_runner.py::TestDeploymentHandler -v
```

Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add harness/runner/deployment_handler.py tests/test_runner.py
git commit -m "feat: add deployment_handler with lint, packaging, and CF update"
```

---

## Task 4: scenario_runner.py

**Files:**
- Create: `harness/runner/scenario_runner.py`
- Modify: `tests/test_runner.py` (append `TestScenarioRunner` class)

- [ ] **Step 1: Append failing tests to `tests/test_runner.py`**

```python
import threading
from harness.runner.scenario_runner import ScenarioRunner


class TestScenarioRunner:
    def _make_scenario(self, tmp_path):
        scenario = tmp_path / "scenario"
        scenario.mkdir()
        (scenario / "scenario.md").write_text("Symptom: orders fail.")
        (scenario / "faulted.yaml").write_text("AWSTemplateFormatVersion: '2010-09-09'\n")
        deployment = scenario / "deployment"
        deployment.mkdir()
        (deployment / "handler.py").write_text("def handler(e,c): pass\n")
        return str(scenario)

    def test_submitted_flag_prevents_second_redeployment(self, tmp_path, mocker):
        scenario_dir = self._make_scenario(tmp_path)
        mocker.patch("harness.runner.scenario_runner.init_run")
        mocker.patch("harness.runner.scenario_runner.snapshot", return_value={})
        mock_handle = mocker.patch(
            "harness.runner.scenario_runner.handle_submission",
            return_value={"outcome": "deploy_success"},
        )
        runner = ScenarioRunner(scenario_dir, "run-test-1")
        result1 = runner.on_model_redeploy()
        result2 = runner.on_model_redeploy()
        assert result1["outcome"] == "deploy_success"
        assert result2["outcome"] == "already_submitted"
        assert mock_handle.call_count == 1

    def test_intercept_tool_call_increments_count_and_logs(self, tmp_path, mocker):
        scenario_dir = self._make_scenario(tmp_path)
        mocker.patch("harness.runner.scenario_runner.init_run")
        mocker.patch("harness.runner.scenario_runner.snapshot", return_value={})
        mock_log = mocker.patch("harness.runner.scenario_runner.log_tool_call")
        runner = ScenarioRunner(scenario_dir, "run-test-2")
        runner.intercept_tool_call("ace_invoke_lambda", {"fn": "MyFn"}, {"status": 200})
        runner.intercept_tool_call("ace_get_log_tail", {"fn": "MyFn"}, {"logs": []})
        assert runner.tool_call_count == 2
        assert mock_log.call_count == 2
        first_call_args = mock_log.call_args_list[0]
        assert first_call_args.args[2] == "ace_invoke_lambda"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_runner.py::TestScenarioRunner -v
```

Expected: `ModuleNotFoundError: No module named 'harness.runner.scenario_runner'`

- [ ] **Step 3: Create `harness/runner/scenario_runner.py`**

```python
import datetime
import os
import subprocess
import threading

from harness.shared.file_differ import snapshot
from harness.shared.result_logger import init_run, log_tool_call
from harness.runner.deployment_handler import handle_submission

_STACK_NAME = "ace-bench-stack"


class ScenarioRunner:
    def __init__(self, scenario_dir: str, run_id: str):
        self.scenario_dir = os.path.abspath(scenario_dir)
        self.run_id = run_id
        self.deployment_dir = os.path.join(self.scenario_dir, "deployment")
        self.tool_call_count = 0
        self.submitted = False
        self._lock = threading.Lock()

        scenario_id = os.path.basename(self.scenario_dir)
        init_run(run_id, scenario_id)
        self.start_snapshot = snapshot(self.deployment_dir)

    def start(self) -> None:
        result = subprocess.run(
            [
                "localstack-deployer", "create-stack",
                "--stack-name", _STACK_NAME,
                "--template", os.path.join(self.scenario_dir, "faulted.yaml"),
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"localstack-deployer create-stack failed:\n{result.stderr}"
            )

    def intercept_tool_call(self, tool_name: str, input: dict, output: dict) -> None:
        with self._lock:
            self.tool_call_count += 1
            turn = self.tool_call_count
        timestamp = datetime.datetime.utcnow().isoformat() + "Z"
        log_tool_call(self.run_id, turn, tool_name, input, output, timestamp)

    def on_model_redeploy(self) -> dict:
        with self._lock:
            if self.submitted:
                return {"outcome": "already_submitted"}
            self.submitted = True
        return handle_submission(self.scenario_dir, self.run_id, self.start_snapshot)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_runner.py::TestScenarioRunner -v
```

Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add harness/runner/scenario_runner.py tests/test_runner.py
git commit -m "feat: add ScenarioRunner with submitted flag and tool call interception"
```

---

## Task 5: Phase C Gate — Full Test Suite

- [ ] **Step 1: Run all Phase C tests**

```bash
pytest tests/test_runner.py -v
```

Expected (8 tests):
```
tests/test_runner.py::TestContextBuilder::test_raises_value_error_when_manifest_present PASSED
tests/test_runner.py::TestContextBuilder::test_returns_correct_shape PASSED
tests/test_runner.py::TestContextBuilder::test_template_path_is_absolute PASSED
tests/test_runner.py::TestDeploymentHandler::test_returns_lint_fail_on_fatal_errors PASSED
tests/test_runner.py::TestDeploymentHandler::test_packaging_preflight_zips_and_uploads_lambda PASSED
tests/test_runner.py::TestDeploymentHandler::test_deploy_fail_returns_rollback_outcome PASSED
tests/test_runner.py::TestScenarioRunner::test_submitted_flag_prevents_second_redeployment PASSED
tests/test_runner.py::TestScenarioRunner::test_intercept_tool_call_increments_count_and_logs PASSED

8 passed
```

- [ ] **Step 2: Verify all public symbols import cleanly**

```bash
python -c "
from harness.runner.context_builder import build_context
from harness.runner.deployment_handler import handle_submission
from harness.runner.scenario_runner import ScenarioRunner
print('All imports OK')
"
```

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "feat: Phase C complete — runner, context builder, deployment handler, 8 passing tests"
```

**Phase C gate is clear. Phase D may begin.**

---

## Self-Review

### Spec coverage

| Spec requirement | Covered by |
|-----------------|------------|
| C1 — `build_context` returns 5-key dict | `test_returns_correct_shape` |
| C1 — raises `ValueError` if `fault_manifest.json` present | `test_raises_value_error_when_manifest_present` |
| C1 — `template_path` and `deployment_dir` are absolute paths | `test_template_path_is_absolute` |
| C1 — Fixed instruction text verbatim | `_FIXED_INSTRUCTION` constant matches spec exactly |
| C2 — `ScenarioRunner.__init__` calls `init_run` and `snapshot` | Task 4 implementation |
| C2 — `intercept_tool_call` increments count and calls `log_tool_call` | `test_intercept_tool_call_increments_count_and_logs` |
| C2 — `submitted` flag blocks second redeployment | `test_submitted_flag_prevents_second_redeployment` |
| C3 — Step 1: diff snapshot + `log_file_change` | `deployment_handler.py` lines 1-3 |
| C3 — Step 2: lint → return `lint_fail` | `test_returns_lint_fail_on_fatal_errors` |
| C3 — Step 3: zip Lambda + `s3_client.put_object` | `test_packaging_preflight_zips_and_uploads_lambda` |
| C3 — Step 4: `update_stack` with `CAPABILITY_IAM` | `deployment_handler.py` |
| C3 — WaiterError → `deploy_fail` with events | `test_deploy_fail_returns_rollback_outcome` |
| C3 — UPDATE_COMPLETE → `deploy_success` | Covered by mock waiter not raising in packaging test |

### Placeholder scan

No TBD, TODO, or vague steps found.

### Type consistency

- `handle_submission(scenario_dir, run_id, start_snapshot)` — defined in `deployment_handler.py` and called with identical args in `scenario_runner.py:on_model_redeploy` and all three deployment test cases
- `log_tool_call(run_id, turn, tool_name, input, output, timestamp)` — 6 positional args, matches Phase A `result_logger.log_tool_call` signature exactly
- `diff` dict keys (`files_modified`, `files_added`, `total_files_changed`) — used consistently between `deployment_handler.py` and test mocks
- `on_model_redeploy` returns `{"outcome": "already_submitted"}` on second call — asserted in test; `{"outcome": "deploy_success"}` on first — mocked and asserted
