import json
import os
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


def log_text_mode_failure(run_id: str, turn: int, raw: str, error: str) -> None:
    """Append one text-mode parse failure record to results/<run_id>/text_mode_failures.json."""
    path = Path(RESULTS_DIR) / run_id / "text_mode_failures.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"turn": turn, "raw_preview": (raw or "")[:300], "error": error}
    existing: list = []
    if path.is_file():
        try:
            existing = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            existing = []
    existing.append(entry)
    path.write_text(json.dumps(existing, indent=2))


def log_file_change(run_id: str, diff: dict) -> None:
    path = Path(RESULTS_DIR) / run_id / "file_change_log.json"
    path.write_text(json.dumps(diff, indent=2))


def log_verify_result(run_id: str, result: dict) -> None:
    path = Path(RESULTS_DIR) / run_id / "verify_result.json"
    path.write_text(json.dumps(result, indent=2))


def log_deployment(run_id: str, plan, result) -> None:
    """Append one entry to results/<run_id>/deployment_log.json."""
    from harness.shared.types import DeploymentResult, PackagingPlan  # avoid circular at module load
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
