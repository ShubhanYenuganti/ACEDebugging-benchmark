#!/usr/bin/env python3
"""harness/run.py — ACE-Bench evaluation entry point."""

import argparse
import json
import os
import sys
import time
import uuid

from dotenv import load_dotenv

from harness.shared.localstack_client import health_check
from harness.shared.result_logger import log_verify_result
from harness.runner.context_builder import build_context
from harness.runner.scenario_runner import ScenarioRunner
from harness.verify.verify_loop import run_verify_loop

_TIMEOUT_SECONDS = 30 * 60  # 30 minutes


def _validate_scenario(scenario_dir: str) -> None:
    for item in ["scenario.md", "faulted.yaml", "fault_manifest.json", "deployment"]:
        if not os.path.exists(os.path.join(scenario_dir, item)):
            print(f"ERROR: Missing required item in scenario_dir: {item}", file=sys.stderr)
            sys.exit(1)


def _print_context(ctx: dict) -> None:
    print("=" * 60)
    print("SCENARIO BRIEF")
    print("=" * 60)
    print(ctx["scenario_brief"])
    print()
    print("TEMPLATE:", ctx["template_path"])
    print("DEPLOYMENT DIR:", ctx["deployment_dir"])
    print()
    if ctx["stack_outputs"]:
        print("STACK OUTPUTS:")
        for k, v in ctx["stack_outputs"].items():
            print(f"  {k}: {v}")
        print()
    print("INSTRUCTION:")
    print(ctx["instruction"])
    print("=" * 60)
    sys.stdout.flush()


def _print_summary(run_id: str, scenario_id: str, verify_result: dict, runner: "ScenarioRunner") -> None:
    p1 = verify_result.get("pass1_functional") or {}
    p2 = verify_result.get("pass2_regression") or {}
    p3 = verify_result.get("pass3_classification") or {}
    p4 = verify_result.get("pass4_concurrency")
    outcome = verify_result.get("outcome", "unknown")

    deployment_status = "PASS" if outcome == "completed" else "FAIL"

    if p1:
        if p1.get("all_assertions_passed"):
            functional_status = "PASS"
        elif p1.get("primary_assertions_passed"):
            functional_status = "PARTIAL"
        else:
            functional_status = "FAIL"
    else:
        functional_status = "SKIPPED"

    reg_count = p2.get("regression_count", 0)
    crit = p2.get("critical_regression_count", 0)
    noncrit = p2.get("non_critical_regression_count", 0)
    regressions_str = "none" if reg_count == 0 else f"{crit} critical, {noncrit} non-critical"

    classification = p3.get("classification", "n/a") if p3 else "n/a"

    if p4 is None:
        concurrency_str = "SKIPPED"
    elif p4.get("passed"):
        concurrency_str = "PASS"
    else:
        concurrency_str = "FAIL"

    file_change_path = os.path.join("results", run_id, "file_change_log.json")
    files_changed = lines_changed = 0
    if os.path.isfile(file_change_path):
        with open(file_change_path) as f:
            fc = json.load(f)
        files_changed = fc.get("total_files_changed", 0)
        lines_changed = fc.get("total_lines_changed", 0)

    print()
    print("═" * 39)
    print(f"ACE-Bench Run: {run_id}")
    print(f"Scenario: {scenario_id}")
    print("═" * 39)
    print()
    print(f"Deployment:       {deployment_status}")
    print(f"Functional test:  {functional_status}")
    print(f"Regressions:      {regressions_str}")
    print(f"Classification:   {classification}")
    print(f"Concurrency:      {concurrency_str}")
    print()
    print(f"Tool calls made:  {runner.tool_call_count}")
    print(f"Files changed:    {files_changed}")
    print(f"Lines changed:    {lines_changed}")
    print()
    print(f"Full results:     results/{run_id}/")
    print("═" * 39)
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="ACE-Bench evaluation harness")
    parser.add_argument("scenario_dir", help="Path to scenario directory")
    parser.add_argument("--run-id", default=None, help="Run identifier (auto-generated if omitted)")
    args = parser.parse_args()

    load_dotenv()

    scenario_dir = os.path.abspath(args.scenario_dir)
    run_id = args.run_id or uuid.uuid4().hex[:8]
    scenario_id = os.path.basename(scenario_dir)

    # Step 2 — health check
    try:
        health_check()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    # Step 3 — validate scenario directory structure
    _validate_scenario(scenario_dir)

    manifest_path = os.path.join(scenario_dir, "fault_manifest.json")

    # Derive corpus_dir from scenario_id (e.g. arch01_fault01_security -> corpus/arch_01_default)
    parts = scenario_id.split("_")
    arch_prefix = "_".join(parts[:2]) if len(parts) >= 2 else parts[0]
    corpus_dir = os.path.join(os.path.dirname(scenario_dir), "..", "corpus",
                              f"arch_{arch_prefix[len('arch'):]}_default")
    corpus_dir = os.path.abspath(corpus_dir)
    if not os.path.isdir(corpus_dir):
        # Fallback: functional_test.py may be co-located in scenario_dir
        corpus_dir = scenario_dir

    # Step 4 — ScenarioRunner.__init__ calls init_run and takes start_snapshot.
    # init_run is NOT called here separately — ScenarioRunner handles it.
    runner = ScenarioRunner(scenario_dir, run_id)

    # Step 5 — deploy faulted template, capture baseline
    try:
        runner.start()
    except RuntimeError as e:
        print(f"ERROR: Scenario deployment failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Step 6 — build context (raises ValueError if fault_manifest.json is readable in scenario_dir)
    # Temporarily rename manifest so build_context does not raise.
    manifest_hidden = manifest_path + ".hidden"
    manifest_was_present = os.path.isfile(manifest_path)
    if manifest_was_present:
        os.rename(manifest_path, manifest_hidden)
    try:
        ctx = build_context(scenario_dir)
    finally:
        if manifest_was_present:
            os.rename(manifest_hidden, manifest_path)

    # Step 7 — hand off to model
    _print_context(ctx)

    # Step 8 — block until submission complete or 30-minute timeout.
    # THREADING NOTE: runner.submitted is set True BEFORE handle_submission() completes
    # (double-submission guard). We must also wait for _last_deployment_outcome != "unknown"
    # as the real completion signal.
    deadline = time.monotonic() + _TIMEOUT_SECONDS
    while not runner.submitted or runner._last_deployment_outcome == "unknown":
        if time.monotonic() > deadline:
            log_verify_result(run_id, {"outcome": "timed_out"})
            print("ERROR: Timed out waiting for model redeployment.", file=sys.stderr)
            sys.exit(1)
        time.sleep(1)

    # Step 9 — verify loop
    verify_result = run_verify_loop(
        scenario_dir=scenario_dir,
        run_id=run_id,
        deployment_outcome=runner._last_deployment_outcome,
        manifest_path=manifest_path,
        corpus_dir=corpus_dir,
        api_endpoint=ctx["stack_outputs"].get("ApiEndpoint", ""),
    )

    # Step 10 — print human-readable summary
    _print_summary(run_id, scenario_id, verify_result, runner)

    # Step 11 — exit code
    sys.exit(0 if verify_result.get("outcome") == "completed" else 1)


if __name__ == "__main__":
    main()
