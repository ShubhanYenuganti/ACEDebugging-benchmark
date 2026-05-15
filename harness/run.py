#!/usr/bin/env python3
"""harness/run.py — ACE-Bench evaluation entry point."""

import argparse
import asyncio
import json
import os
import sys
import traceback
import uuid

from dotenv import load_dotenv

from harness.agent.loop import run_agent_loop
from harness.shared.localstack_client import health_check
from harness.shared.result_logger import log_verify_result
from harness.runner.context_builder import build_context
from harness.runner.scenario_runner import ScenarioRunner
from harness.scoring.scorer import score_run
from harness.verify.pass1_functional import run_pass1
from harness.verify.verify_loop import run_verify_loop


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


def _print_score_summary(score: dict) -> None:
    dims = score.get("dimensions", {})
    if not dims:
        gate_str = "FAIL → score zeroed" if not score.get("quality_threshold_met") else "N/A"
        print("── Scoring (Claude Sonnet) ─────────────")
        print(f"Quality gate:     {gate_str}")
        print(f"Zero reason:      {score.get('zero_reason', 'unknown')}")
        print(f"Final score:      {score.get('final_score', 0.0):.4f}")
        print("────────────────────────────────────────")
        print()
        return

    d1 = dims.get("identification", {})
    d2 = dims.get("fix_correctness", {})
    d3 = dims.get("regression_penalty", {})
    d4 = dims.get("efficiency", {})
    d5 = dims.get("quality", {})

    print("── Scoring (Claude Sonnet) ─────────────")
    print(f"Quality gate:     {'PASS' if score.get('quality_threshold_met') else 'FAIL → score zeroed'}")
    print(f"Identification:   {d1.get('score', 0):.2f}  {d1.get('rationale', '')}")
    print(f"Fix correctness:  {d2.get('score', 0):.2f}  {d2.get('rationale', '')}")
    print(f"Regression:      -{d3.get('penalty', 0):.2f}  {d3.get('rationale', '')}")
    print(f"Efficiency:       {d4.get('score', 0):.2f}  {d4.get('rationale', '')}")
    print(f"Quality:          {d5.get('score', 0):.2f}  {d5.get('rationale', '')}")
    print("────────────────────────────────────────")
    print(f"Final score:      {score.get('final_score', 0.0):.4f}")
    print(f"Interpretation:   {score.get('interpretation', '')}")
    print("────────────────────────────────────────")
    print()


_CLOUD_PROVIDER_PREFIXES = (
    "anthropic/",
    "openai/",
    "azure/",
    "gemini/",
    "vertex_ai/",
    "bedrock/",
    "cohere/",
    "groq/",
    "mistral/",
    "deepseek/",
    "xai/",
    "perplexity/",
    "together_ai/",
    "fireworks_ai/",
)


def _resolve_inference_config(
    args: argparse.Namespace,
) -> tuple[str | None, str | None, str | None, dict | None]:
    """Return (model, api_key, base_url, extra_headers) for the active inference backend.

    Resolution order:
      1. --base-url given → VPS mode (explicit override).
      2. --model has a cloud-provider prefix (anthropic/, openai/, …) → cloud mode,
         even if ACE_VPS_ENDPOINT is set in the environment.
      3. ACE_VPS_ENDPOINT set → VPS mode (default model from ACE_VPS_MODEL).
      4. Otherwise → cloud mode using --model / --api-key as-is (LiteLLM picks up
         provider env vars like ANTHROPIC_API_KEY, OPENAI_API_KEY).
    Set ACE_VPS_AUTH_TOKEN for RunPod Secure Cloud pods (adds Authorization header).
    """
    vps_endpoint = os.environ.get("ACE_VPS_ENDPOINT", "").strip()
    model_arg = (args.model or "").strip()
    is_cloud_model = any(model_arg.startswith(p) for p in _CLOUD_PROVIDER_PREFIXES)

    if args.base_url:
        in_vps_mode = True
    elif is_cloud_model:
        in_vps_mode = False
    else:
        in_vps_mode = bool(vps_endpoint)

    if in_vps_mode:
        endpoint = args.base_url or vps_endpoint
        model_name = os.environ.get("ACE_VPS_MODEL", "qwen2.5-coder:32b")
        api_key = os.environ.get("ACE_VPS_API_KEY", "ollama")
        model = args.model or f"ollama/{model_name}"
        auth_token = os.environ.get("ACE_VPS_AUTH_TOKEN", "").strip()
        extra_headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else None
        print(f"[inference] VPS mode → {endpoint}  model={model}"
              + ("  (auth)" if extra_headers else ""))
        return model, api_key, endpoint, extra_headers

    print(f"[inference] cloud mode → model={args.model}")
    return args.model, args.api_key, None, None


def main() -> None:
    parser = argparse.ArgumentParser(description="ACE-Bench evaluation harness")
    parser.add_argument("scenario_dir", help="Path to scenario directory")
    parser.add_argument("--run-id", default=None, help="Run identifier (auto-generated if omitted)")
    parser.add_argument(
        "--model",
        default=None,
        metavar="PROVIDER/MODEL",
        help=(
            "LiteLLM model string to use as the evaluated agent "
            "(e.g. anthropic/claude-sonnet-4-6, openai/gpt-4o, "
            "gemini/gemini-1.5-pro, ollama/qwen2.5). "
            "If omitted, the harness waits for an external agent to write the signal file."
        ),
    )
    parser.add_argument(
        "--api-key",
        default=None,
        metavar="KEY",
        help=(
            "API key for the model provider. "
            "Falls back to the provider-specific env var "
            "(ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY, etc.). "
            "Not required for local Ollama."
        ),
    )
    parser.add_argument(
        "--base-url",
        default=None,
        metavar="URL",
        help=(
            "Custom API base URL (e.g. http://localhost:11434 for Ollama, "
            "or a self-hosted OpenAI-compatible endpoint)."
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print the model's reasoning text and each tool call as it executes.",
    )
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

    # Derive corpus_dir from manifest architecture field
    with open(manifest_path) as f:
        _manifest_data = json.load(f)
    _arch_name = _manifest_data.get("architecture", "")
    corpus_dir = os.path.join(os.path.dirname(scenario_dir), "..", "corpus", _arch_name)
    corpus_dir = os.path.abspath(corpus_dir)
    if not _arch_name or not os.path.isdir(corpus_dir):
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

    # Write faulted_baseline.json — Pass 2 regression check uses this to find assertions
    # that were passing on the faulted deployment but fail after the model's fix.
    _baseline = run_pass1(corpus_dir)
    _baseline_path = os.path.join("results", run_id, "faulted_baseline.json")
    with open(_baseline_path, "w") as _f:
        json.dump(_baseline, _f, indent=2)

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

    _model, _api_key, _base_url, _extra_headers = _resolve_inference_config(args)

    if _model:
        _harness_key = os.environ.get("HARNESS_API_KEY", "")
        if not _harness_key:
            print("ERROR: HARNESS_API_KEY env var is required when --model is used", file=sys.stderr)
            sys.exit(1)

        try:
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
                    deploy_callback=runner.attempt_deployment,
                )
            )
        except BaseException as exc:
            log_verify_result(run_id, {"outcome": "agent_error"})
            print(f"ERROR: Agent crashed: {exc}", file=sys.stderr)
            traceback.print_exc()
            sys.exit(1)

    # Step 8 — ensure deployment outcome is recorded
    # (attempt_deployment sets _last_deployment_outcome on success;
    #  if loop exited without a successful deploy, record it as no_submission)
    if runner._last_deployment_outcome == "unknown":
        runner._last_deployment_outcome = "no_submission"

    # Step 9 — verify loop
    verify_result = run_verify_loop(
        scenario_dir=scenario_dir,
        run_id=run_id,
        deployment_outcome=runner._last_deployment_outcome,
        manifest_path=manifest_path,
        corpus_dir=corpus_dir,
        api_endpoint=ctx["stack_outputs"].get("ApiEndpoint", ""),
    )

    # Step 10 — autonomous scoring
    base_dir = os.path.dirname(os.path.dirname(scenario_dir))
    print("[scorer] Running scoring agent (Claude Sonnet)...")
    score_result = score_run(run_id, base_dir)

    # Step 11 — print human-readable summary (stdout may be closed if piped to stub)
    try:
        _print_summary(run_id, scenario_id, verify_result, runner)
        _print_score_summary(score_result)
        sys.stdout.flush()
    except BrokenPipeError:
        # Pipe consumer closed early (e.g. stub_model finished reading); summary is
        # cosmetic — verify_result.json is the authoritative record. Redirect
        # stdout to /dev/null so the interpreter shutdown's flush does not raise
        # again (which would force exit code 120).
        try:
            devnull_fd = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull_fd, sys.stdout.fileno())
            os.close(devnull_fd)
        except OSError:
            pass

    # Step 12 — exit code
    sys.exit(0 if verify_result.get("outcome") == "completed" else 1)


if __name__ == "__main__":
    main()
