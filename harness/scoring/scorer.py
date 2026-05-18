import json
import pathlib
from harness.runner.context_builder import corpus_dir_for_scenario
from harness.scoring.agent import SCORING_MODEL
from harness.scoring import gate
from harness.scoring.dimensions import (
    identification,
    fix_correctness,
    regression,
    efficiency,
    quality,
)


def _load_text(path: pathlib.Path) -> str:
    return path.read_text()


def _load_json(path: pathlib.Path) -> dict:
    return json.loads(path.read_text())


def _resolve_corpus_dir(scenario_dir: pathlib.Path, base: pathlib.Path) -> pathlib.Path:
    """Locate the corpus directory for a scenario.

    Scenario IDs look like 'arch12_fault07_data_correctness' but corpus
    directories are named like 'arch_12_event_driven_architecture_...'.
    `corpus_dir_for_scenario` extracts the arch number and finds the
    matching corpus dir by 'arch_<NN>_' prefix.
    """
    arch_id_file = scenario_dir / "arch_id.txt"
    if arch_id_file.exists():
        return base / "corpus" / arch_id_file.read_text().strip()
    return corpus_dir_for_scenario(scenario_dir, corpus_root=base / "corpus")


def _write_zero(run_dir: pathlib.Path, run_id: str, scenario_id: str, reason: str, quality_gate_met: bool = True) -> dict:
    result = {
        "run_id": run_id,
        "scenario_id": scenario_id,
        "scored_by": SCORING_MODEL,
        "quality_threshold_met": quality_gate_met,
        "zero_reason": reason,
        "dimensions": {},
        "weighted": 0.0,
        "composite": 0.0,
        "final_score": 0.0,
        "interpretation": "Failed quality gate or no improvement",
    }
    (run_dir / "score.json").write_text(json.dumps(result, indent=2))
    return result


def score_run(run_id: str, base_dir: str) -> dict:
    base = pathlib.Path(base_dir)
    run_dir = base / "results" / run_id

    scenario_id = (run_dir / "scenario_id.txt").read_text().strip()
    scenario_dir = base / "scenarios" / scenario_id
    corpus_dir = _resolve_corpus_dir(scenario_dir, base)

    required = [
        run_dir / "verify_result.json",
        run_dir / "tool_call_trace.json",
        run_dir / "file_change_log.json",
        scenario_dir / "fault_manifest.json",
        scenario_dir / "faulted.yaml",
        corpus_dir / "known_good.yaml",
        corpus_dir / "traffic_flow.md",
    ]
    for f in required:
        if not f.exists():
            return _write_zero(run_dir, run_id, scenario_id, "missing_artifacts")

    verify_result = _load_json(run_dir / "verify_result.json")
    tool_trace = _load_json(run_dir / "tool_call_trace.json")
    file_log = _load_json(run_dir / "file_change_log.json")
    manifest = _load_json(scenario_dir / "fault_manifest.json")
    known_good = _load_text(corpus_dir / "known_good.yaml")
    traffic_flow = _load_text(corpus_dir / "traffic_flow.md")

    if verify_result.get("outcome") != "completed":
        return _write_zero(run_dir, run_id, scenario_id, verify_result.get("outcome", "unknown_outcome"))

    if not gate.check_quality_gate(verify_result):
        return _write_zero(run_dir, run_id, scenario_id, "quality_gate_failed", quality_gate_met=False)

    d1 = identification.score(tool_trace, manifest, verify_result, known_good, traffic_flow)
    d2 = fix_correctness.score(verify_result)
    d3 = regression.compute(verify_result)
    d4 = efficiency.score(tool_trace, file_log, manifest, known_good, traffic_flow)
    d5 = quality.score(verify_result, manifest, file_log, known_good, traffic_flow)

    weighted = (d1["score"] * 0.20) + (d2["score"] * 0.25) \
        + (d4["score"] * 0.15) + (d5["score"] * 0.40)
    composite = max(0.0, round(weighted - d3["penalty"], 4))

    def interpret(s: float) -> str:
        if s >= 0.90:
            return "Root cause identified, clean fix, no regressions, efficient"
        if s >= 0.75:
            return "Correct fix with minor inefficiency or implementation concern"
        if s >= 0.50:
            return "Fix works but via workaround, or has regressions"
        if s >= 0.25:
            return "Partial resolution, significant issues"
        return "Failed quality gate or no improvement"

    result = {
        "run_id": run_id,
        "scenario_id": scenario_id,
        "scored_by": SCORING_MODEL,
        "quality_threshold_met": True,
        "zero_reason": None,
        "dimensions": {
            "identification": d1,
            "fix_correctness": d2,
            "regression_penalty": d3,
            "efficiency": d4,
            "quality": d5,
        },
        "weighted": round(weighted, 4),
        "composite": composite,
        "final_score": composite,
        "interpretation": interpret(composite),
    }
    (run_dir / "score.json").write_text(json.dumps(result, indent=2))
    return result
