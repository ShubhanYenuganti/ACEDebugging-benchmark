# Track B — Deployment Retry Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the fire-and-forget signal-file deployment mechanism with a synchronous callback so the agent loop can read LocalStack errors, revise its fix with `write_file`, and retry up to 5 times before giving up.

**Architecture:** `run_agent_loop` gains a `deploy_callback` parameter. When `submit_fix` is called, instead of writing a signal file the loop calls `deploy_callback()` synchronously (via `run_in_executor`). On failure the loop injects the error message back as a tool result and continues. `ScenarioRunner` gets a new `attempt_deployment()` method that only marks `submitted=True` on success. `deployment_handler` catches the "No updates are to be performed" `ClientError` and returns a structured dict. `run.py` wires the callback and removes signal-file polling. `tools.py` removes the signal-file write from `submit_fix`.

**Tech Stack:** Python 3.11, asyncio, boto3, LiteLLM

---

## File Structure

| File | Change |
|------|--------|
| `harness/runner/deployment_handler.py` | Catch "No updates" ClientError; add `error` key to all failure return dicts |
| `harness/runner/scenario_runner.py` | Add `attempt_deployment()` method |
| `harness/agent/tools.py` | Remove signal-file write from `submit_fix` handler; return empty string |
| `harness/agent/loop.py` | Add `deploy_callback` + `max_deploy_retries` params; replace `submitted` break with retry logic |
| `harness/run.py` | Pass `deploy_callback=runner.attempt_deployment`; remove signal-file polling loop |
| `tests/test_agent_loop.py` | Update submit_fix tests; add retry scenario tests |

---

### Task 1: Catch "No updates" in `deployment_handler.py`

**Files:**
- Modify: `harness/runner/deployment_handler.py`

- [ ] **Step 1: Write a failing test**

In `tests/test_runner.py` (or a new `tests/test_deployment_handler.py`), add:

```python
from unittest.mock import patch, MagicMock
from botocore.exceptions import ClientError
from harness.runner.deployment_handler import handle_submission

def test_handle_submission_returns_no_changes_on_no_updates_error(tmp_path):
    # minimal faulted.yaml so lint passes
    (tmp_path / "faulted.yaml").write_text(
        "AWSTemplateFormatVersion: '2010-09-09'\nResources:\n  Dummy:\n    Type: AWS::CloudFormation::WaitConditionHandle\n"
    )
    err = ClientError(
        {"Error": {"Code": "ValidationError", "Message": "No updates are to be performed."}},
        "UpdateStack",
    )
    with patch("harness.runner.deployment_handler.cf_client") as mock_cf:
        mock_cf.update_stack.side_effect = err
        result = handle_submission(str(tmp_path), "run-test", {})
    assert result["outcome"] == "no_changes"
    assert "error" in result
    assert "No updates" in result["error"] or "no changes" in result["error"].lower()
```

- [ ] **Step 2: Run to confirm it fails**

```bash
pytest tests/test_runner.py::test_handle_submission_returns_no_changes_on_no_updates_error -v
```

Expected: FAIL — `ClientError` propagates instead of returning `{"outcome": "no_changes", ...}`.

- [ ] **Step 3: Implement the fix in `deployment_handler.py`**

Find the `cf_client.update_stack(...)` call (currently raises on "No updates"). Wrap it:

```python
try:
    cf_client.update_stack(
        StackName=_STACK_NAME,
        TemplateBody=template_body,
        Capabilities=["CAPABILITY_IAM", "CAPABILITY_NAMED_IAM"],
    )
except ClientError as e:
    msg = str(e)
    if "No updates are to be performed" in msg:
        return {
            "outcome": "no_changes",
            "error": (
                "CloudFormation rejected the update: no changes detected in the template. "
                "Your file edit did not modify any CloudFormation resource properties. "
                "Check that you edited faulted.yaml (not just Lambda handler code) "
                "if the fault is in a resource configuration."
            ),
        }
    raise
```

Also ensure every other early-return dict in `handle_submission` includes an `"error"` key (e.g., `lint_fail` returns `{"outcome": "lint_fail", "errors": [...]}` — add `"error": "; ".join(str(e) for e in errors)` so callers can surface it uniformly).

- [ ] **Step 4: Run the test to confirm it passes**

```bash
pytest tests/test_runner.py::test_handle_submission_returns_no_changes_on_no_updates_error -v
```

Expected: PASS.

- [ ] **Step 5: Run full Python test suite**

```bash
pytest tests/
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add harness/runner/deployment_handler.py tests/test_runner.py
git commit -m "fix(runner): catch 'No updates' ClientError in deployment_handler"
```

---

### Task 2: Add `attempt_deployment()` to `ScenarioRunner`

**Files:**
- Modify: `harness/runner/scenario_runner.py`

- [ ] **Step 1: Write a failing test**

In `tests/test_runner.py`, add:

```python
from harness.runner.scenario_runner import ScenarioRunner

def test_attempt_deployment_returns_success_dict(tmp_path, mocker):
    runner = ScenarioRunner(str(tmp_path), "run-xyz")
    mocker.patch(
        "harness.runner.scenario_runner.handle_submission",
        return_value={"outcome": "deploy_success"},
    )
    result = runner.attempt_deployment()
    assert result["success"] is True
    assert runner.submitted is True

def test_attempt_deployment_returns_failure_dict_on_no_changes(tmp_path, mocker):
    runner = ScenarioRunner(str(tmp_path), "run-xyz")
    mocker.patch(
        "harness.runner.scenario_runner.handle_submission",
        return_value={"outcome": "no_changes", "error": "no changes detected"},
    )
    result = runner.attempt_deployment()
    assert result["success"] is False
    assert runner.submitted is False
    assert "no changes" in result["error"]

def test_attempt_deployment_blocked_after_success(tmp_path, mocker):
    runner = ScenarioRunner(str(tmp_path), "run-xyz")
    mocker.patch(
        "harness.runner.scenario_runner.handle_submission",
        return_value={"outcome": "deploy_success"},
    )
    runner.attempt_deployment()
    result = runner.attempt_deployment()
    assert result["success"] is False
    assert "Already submitted" in result["error"]
```

- [ ] **Step 2: Run to confirm tests fail**

```bash
pytest tests/test_runner.py -k "attempt_deployment" -v
```

Expected: `AttributeError: 'ScenarioRunner' object has no attribute 'attempt_deployment'`.

- [ ] **Step 3: Implement `attempt_deployment()` in `scenario_runner.py`**

Add this method to the `ScenarioRunner` class (after `on_model_redeploy` or at the end of the class):

```python
def attempt_deployment(self) -> dict:
    with self._lock:
        if self.submitted:
            return {"success": False, "error": "Already submitted (final)."}
    result = handle_submission(self.scenario_dir, self.run_id, self.start_snapshot)
    outcome = result.get("outcome", "unknown")
    success = outcome == "deploy_success"
    if success:
        with self._lock:
            self.submitted = True
        self._last_deployment_outcome = outcome
    return {
        "success": success,
        "error": result.get("error", outcome),
        "result": result,
    }
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_runner.py -k "attempt_deployment" -v
```

Expected: all three tests pass.

- [ ] **Step 5: Run full test suite**

```bash
pytest tests/
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add harness/runner/scenario_runner.py tests/test_runner.py
git commit -m "feat(runner): add attempt_deployment() for synchronous retry support"
```

---

### Task 3: Remove signal-file write from `tools.py`

**Files:**
- Modify: `harness/agent/tools.py`

- [ ] **Step 1: Locate submit_fix handler**

In `tools.py`, find the `submit_fix` branch inside `dispatch_file_tool`. It currently writes `/tmp/ace-bench-update.json`. The entire write should be removed. The handler should return an empty string (the loop now owns deployment logic).

Current code (approximately):

```python
elif name == "submit_fix":
    import json, pathlib
    pathlib.Path("/tmp/ace-bench-update.json").write_text(
        json.dumps({"scenario_dir": scenario_dir, "run_id": run_id})
    )
    return ""
```

- [ ] **Step 2: Write a regression test confirming no file is written**

In `tests/test_agent_loop.py`, add:

```python
import pathlib

def test_submit_fix_does_not_write_signal_file(tmp_path):
    from harness.agent.tools import dispatch_file_tool
    signal = pathlib.Path("/tmp/ace-bench-update.json")
    if signal.exists():
        signal.unlink()
    dispatch_file_tool("submit_fix", {}, str(tmp_path))
    assert not signal.exists(), "submit_fix must not write signal file when deploy_callback is used"
```

- [ ] **Step 3: Run to confirm test fails (signal file still written)**

```bash
pytest tests/test_agent_loop.py::test_submit_fix_does_not_write_signal_file -v
```

Expected: FAIL — signal file is created.

- [ ] **Step 4: Remove signal-file write from `tools.py`**

Replace the `submit_fix` branch so it is a no-op:

```python
elif name == "submit_fix":
    return ""
```

The loop in `loop.py` now handles all deployment logic via `deploy_callback`.

- [ ] **Step 5: Run the regression test**

```bash
pytest tests/test_agent_loop.py::test_submit_fix_does_not_write_signal_file -v
```

Expected: PASS.

- [ ] **Step 6: Run full test suite**

```bash
pytest tests/
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add harness/agent/tools.py tests/test_agent_loop.py
git commit -m "refactor(agent): remove signal-file write from submit_fix; loop owns deployment"
```

---

### Task 4: Add retry logic to `loop.py`

**Files:**
- Modify: `harness/agent/loop.py`

- [ ] **Step 1: Write failing tests for retry behavior**

In `tests/test_agent_loop.py`, add:

```python
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

@pytest.mark.asyncio
async def test_loop_retries_on_deploy_failure(mock_client):
    """Agent retries after deploy_callback returns failure."""
    deploy_results = [
        {"success": False, "error": "no changes detected"},
        {"success": True, "error": None},
    ]
    deploy_callback = MagicMock(side_effect=deploy_results)

    responses = [
        # turn 1: write_file then submit_fix
        MagicMock(choices=[MagicMock(message=MagicMock(
            content=None,
            tool_calls=[
                MagicMock(id="c1", function=MagicMock(name="write_file",
                    arguments='{"path":"faulted.yaml","content":"AWSTemplateFormatVersion: \'2010-09-09\'\nResources:\n  X:\n    Type: AWS::CloudFormation::WaitConditionHandle\n"}')),
            ]
        ))]),
        MagicMock(choices=[MagicMock(message=MagicMock(
            content=None,
            tool_calls=[
                MagicMock(id="c2", function=MagicMock(name="submit_fix", arguments="{}")),
            ]
        ))]),
        # turn 2 (after deploy failure): write_file again then submit_fix
        MagicMock(choices=[MagicMock(message=MagicMock(
            content=None,
            tool_calls=[
                MagicMock(id="c3", function=MagicMock(name="write_file",
                    arguments='{"path":"faulted.yaml","content":"AWSTemplateFormatVersion: \'2010-09-09\'\nResources:\n  Y:\n    Type: AWS::CloudFormation::WaitConditionHandle\n"}')),
            ]
        ))]),
        MagicMock(choices=[MagicMock(message=MagicMock(
            content=None,
            tool_calls=[
                MagicMock(id="c4", function=MagicMock(name="submit_fix", arguments="{}")),
            ]
        ))]),
    ]
    mock_client.chat.completions.create = AsyncMock(side_effect=responses)

    scenario_dir = ... # use a tmp_path with faulted.yaml and deployment/ dir
    await run_agent_loop(
        client=mock_client,
        model="test-model",
        context={...},
        mcp_tools=[],
        scenario_dir=str(scenario_dir),
        deploy_callback=deploy_callback,
        max_deploy_retries=5,
    )
    assert deploy_callback.call_count == 2


@pytest.mark.asyncio
async def test_loop_exits_after_max_retries(mock_client):
    """Loop exits after max_deploy_retries failed attempts."""
    deploy_callback = MagicMock(return_value={"success": False, "error": "persistent error"})
    # provide enough responses: write_file + submit_fix repeated 6 times (1 initial + 5 retries)
    # ... (abbreviated — actual test must supply N pairs of write+submit responses)
    await run_agent_loop(
        ...,
        deploy_callback=deploy_callback,
        max_deploy_retries=5,
    )
    assert deploy_callback.call_count == 6  # 1 initial + 5 retries


@pytest.mark.asyncio
async def test_loop_rejects_submit_without_new_write_after_failure(mock_client):
    """submit_fix is refused if no write_file since last failed attempt."""
    deploy_callback = MagicMock(return_value={"success": False, "error": "no changes"})
    # response: write_file, submit_fix (fail), submit_fix again (no write in between)
    # second submit_fix must get error message without calling deploy_callback again
    ...
```

> Note: these are integration tests on `run_agent_loop`. Fill in the `scenario_dir` and `context` fixtures from existing test helpers in the file. The intent is clear — implement the actual test body to match the helpers already present in `test_agent_loop.py`.

- [ ] **Step 2: Run to confirm tests fail**

```bash
pytest tests/test_agent_loop.py -k "retry" -v
```

Expected: FAIL — `run_agent_loop` has no `deploy_callback` parameter.

- [ ] **Step 3: Update `run_agent_loop` signature in `loop.py`**

Change the function signature:

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
):
```

- [ ] **Step 4: Add retry counters after `writes_made = 0`**

```python
writes_made = 0
retry_count = 0
writes_since_last_submit = 0
```

- [ ] **Step 5: Increment `writes_since_last_submit` alongside `writes_made`**

Find where `writes_made` is incremented (after successful `write_file` dispatch). Add:

```python
writes_made += 1
writes_since_last_submit += 1
```

- [ ] **Step 6: Replace the submit_fix handling block**

Find the existing submit_fix handling (around `if writes_made == 0: content = "Error: submit_fix refused..."` and `else: submitted = True`). Replace the `else` branch with:

```python
elif deploy_callback is None:
    # fallback: no callback provided — old signal-file path (no retry)
    import json, pathlib
    pathlib.Path("/tmp/ace-bench-update.json").write_text(
        json.dumps({"scenario_dir": scenario_dir})
    )
    submitted = True
    content = "Fix submitted."
elif retry_count > 0 and writes_since_last_submit == 0:
    content = (
        "Error: no new file changes since last failed attempt. "
        "Revise your fix with write_file before calling submit_fix again."
    )
else:
    loop = asyncio.get_event_loop()
    deploy_result = await loop.run_in_executor(None, deploy_callback)
    if deploy_result["success"]:
        submitted = True
        content = "Fix deployed successfully."
    elif retry_count >= max_deploy_retries:
        submitted = True
        content = (
            f"Maximum retries ({max_deploy_retries}) reached. "
            f"Last error: {deploy_result.get('error', 'unknown')}. Exiting."
        )
    else:
        retry_count += 1
        writes_since_last_submit = 0
        content = (
            f"Deployment failed (attempt {retry_count}/{max_deploy_retries}): "
            f"{deploy_result.get('error', 'unknown')}. "
            "Read the error carefully, revise your fix with write_file, "
            "then call submit_fix again."
        )
```

- [ ] **Step 7: Confirm loop exit condition**

The existing `if submitted: break` at the end of the turn loop remains unchanged. `submitted` is only set True on success or when max retries is reached — so the loop naturally continues on failure.

- [ ] **Step 8: Run retry tests**

```bash
pytest tests/test_agent_loop.py -k "retry" -v
```

Expected: all retry tests pass.

- [ ] **Step 9: Run full test suite**

```bash
pytest tests/
```

Expected: all tests pass.

- [ ] **Step 10: Commit**

```bash
git add harness/agent/loop.py tests/test_agent_loop.py
git commit -m "feat(agent): add deploy_callback retry loop (up to 5 retries) to run_agent_loop"
```

---

### Task 5: Wire callback in `run.py` and remove signal-file polling

**Files:**
- Modify: `harness/run.py`

- [ ] **Step 1: Locate signal-file polling in `run.py`**

Find the block that polls `/tmp/ace-bench-update.json` every 1s and spawns `_redeploy_thread`. This is roughly:

```python
while not runner.submitted or runner._last_deployment_outcome == "unknown":
    time.sleep(1)
    if pathlib.Path("/tmp/ace-bench-update.json").exists():
        ...
```

- [ ] **Step 2: Pass `deploy_callback` to `run_agent_loop`**

Change the `asyncio.run(run_agent_loop(...))` call to:

```python
asyncio.run(
    run_agent_loop(
        client=client,
        model=model,
        context=context,
        mcp_tools=mcp_tools,
        scenario_dir=scenario_dir,
        verbose=verbose,
        deploy_callback=runner.attempt_deployment,
        max_deploy_retries=5,
    )
)
```

- [ ] **Step 3: Remove signal-file polling loop**

Delete the while loop that polls for the signal file and spawns the deployment thread. The `asyncio.run(...)` call is now blocking and completes only after the agent loop exits (success or max retries). After it returns, the deployment outcome is already recorded on `runner`.

- [ ] **Step 4: Verify final outcome handling**

After `asyncio.run(...)`, `runner.submitted` is True and `runner._last_deployment_outcome` is set (or was set by `attempt_deployment`). The rest of `run.py` that reads these values (result logging, printing score, etc.) should work unchanged.

- [ ] **Step 5: Run a smoke test**

```bash
pytest tests/test_agent_loop.py tests/test_runner.py -v
```

Expected: all pass. (Full end-to-end with LocalStack is out of scope for unit tests.)

- [ ] **Step 6: Commit**

```bash
git add harness/run.py
git commit -m "feat(run): wire deploy_callback, remove signal-file polling loop"
```

---

## Self-Review

**Spec coverage:**
- "No updates are to be performed" caught → structured error: Task 1 ✓
- `attempt_deployment()` only sets submitted on success: Task 2 ✓
- Signal-file write removed from tools.py: Task 3 ✓
- `deploy_callback` + `max_deploy_retries` params in loop: Task 4 ✓
- `writes_since_last_submit` resets to 0 on each retry: Task 4 Step 6 ✓
- Guard against submit_fix with no new writes after failure: Task 4 Step 6 (`elif retry_count > 0 and writes_since_last_submit == 0`) ✓
- Max retries exit sets `submitted=True` to break loop: Task 4 Step 6 ✓
- run.py wired: Task 5 ✓
- Fallback path (no deploy_callback) writes signal file: Task 4 Step 6 ✓

**Placeholder scan:** Task 4 Step 1 tests are marked as abbreviated with `...`. Implementer must fill in `scenario_dir`, `context`, and the multi-turn response mocks using helpers already in `test_agent_loop.py`. This is acknowledged — the test structure and assertions are exact; only fixture wiring is left to implementer judgment.

**Type consistency:**
- `deploy_callback` is typed as callable returning `{"success": bool, "error": str|None}` — matches `attempt_deployment()` return in Task 2 Step 3 ✓
- `retry_count`, `writes_since_last_submit` are int counters — both initialized at 0 ✓
- `run_in_executor(None, deploy_callback)` — synchronous callable, no args; `attempt_deployment` takes no args ✓
