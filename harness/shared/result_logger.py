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
