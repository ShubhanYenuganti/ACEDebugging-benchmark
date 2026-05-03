"""
E2E integration test for the ACE-Bench harness.

Requirements before running:
- LocalStack running:      localstack start -d
- MCP server registered:   claude mcp list shows ace-bench-diagnostic-mcp
- .env with HARNESS_API_KEY present at project root
- scenarios/arch01_fault01_security/ exists with:
    scenario.md, faulted.yaml, fault_manifest.json (with injected_value and original_value fields),
    deployment/lambda/handler.py

Run:
    pytest tests/test_e2e.py -v -s
"""

import json
import os
import subprocess
import sys
import threading

import pytest

SCENARIO_DIR = os.path.abspath("scenarios/arch01_fault01_security")
MANIFEST_PATH = os.path.join(SCENARIO_DIR, "fault_manifest.json")
RUN_ID = "e2e-test"
RESULTS_DIR = os.path.abspath("results")


@pytest.fixture(scope="module", autouse=True)
def check_prerequisites():
    if not os.path.isdir(SCENARIO_DIR):
        pytest.skip(f"Scenario directory not found: {SCENARIO_DIR}")
    if not os.path.isfile(MANIFEST_PATH):
        pytest.skip(f"fault_manifest.json not found at: {MANIFEST_PATH}")
    ls_result = subprocess.run(
        ["localstack", "status", "services"],
        capture_output=True, text=True,
    )
    if "running" not in ls_result.stdout:
        pytest.skip("LocalStack is not running — start it with: localstack start -d")


def test_e2e_run_exits_0_with_root_cause():
    """Full harness run using stub model — must exit 0, classification=root_cause, regressions=0."""
    harness_cmd = [sys.executable, "harness/run.py", SCENARIO_DIR, "--run-id", RUN_ID]
    stub_cmd = [sys.executable, "tests/stubs/stub_model.py", SCENARIO_DIR, MANIFEST_PATH]

    harness_proc = subprocess.Popen(
        harness_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stub_proc = subprocess.Popen(
        stub_cmd,
        stdin=harness_proc.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    # Release parent's copy of the write-end so stub sees EOF when harness exits.
    harness_proc.stdout.close()

    # Drain harness stderr in background to prevent OS pipe buffer deadlock.
    _harness_stderr: list[str] = []

    def _read_harness_stderr() -> None:
        _harness_stderr.append(harness_proc.stderr.read())

    stderr_thread = threading.Thread(target=_read_harness_stderr, daemon=True)
    stderr_thread.start()

    try:
        stub_stdout, stub_stderr = stub_proc.communicate(timeout=600)
    except subprocess.TimeoutExpired:
        stub_proc.kill()
        harness_proc.kill()
        harness_proc.wait()
        stub_proc.wait()
        stderr_thread.join(timeout=5)
        pytest.fail("E2E test timed out after 600 seconds")

    harness_proc.wait(timeout=30)
    stderr_thread.join(timeout=10)
    harness_stderr = _harness_stderr[0] if _harness_stderr else ""

    print("\n--- harness stderr ---")
    print(harness_stderr)
    print("--- stub stdout ---")
    print(stub_stdout)
    print("--- stub stderr ---")
    print(stub_stderr)

    assert harness_proc.returncode == 0, (
        f"harness/run.py exited with code {harness_proc.returncode}\n"
        f"stderr:\n{harness_stderr}"
    )

    verify_path = os.path.join(RESULTS_DIR, RUN_ID, "verify_result.json")
    assert os.path.isfile(verify_path), f"verify_result.json not written: {verify_path}"

    with open(verify_path) as f:
        verify_result = json.load(f)

    assert verify_result.get("outcome") == "completed", (
        f"Expected outcome=completed, got: {verify_result.get('outcome')}\n"
        f"Full result: {json.dumps(verify_result, indent=2)}"
    )

    classification = (verify_result.get("pass3_classification") or {}).get("classification")
    assert classification == "root_cause", (
        f"Expected classification=root_cause, got: {classification}"
    )

    regression_count = (verify_result.get("pass2_regression") or {}).get("regression_count", -1)
    assert regression_count == 0, (
        f"Expected regression_count=0, got: {regression_count}"
    )
