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
