import json
import os

from harness.shared.types import AssertionRunResult

RESULTS_DIR = "results"


class Pass2Step:
    name = "pass2_regression"

    def should_run(self, ctx) -> bool:
        return ctx.pass1_result is not None

    def run(self, ctx):
        return run_pass2(ctx.scenario_dir, ctx.run_id, ctx.pass1_result)


def run_pass2(scenario_dir: str, run_id: str, pass1_result: AssertionRunResult) -> dict:
    baseline_path = os.path.join(RESULTS_DIR, run_id, "faulted_baseline.json")
    with open(baseline_path, "r", encoding="utf-8") as f:
        faulted_baseline = json.load(f)

    current_by_name = pass1_result.assertions_by_name
    regressions = []
    for name, baseline_entry in faulted_baseline["assertions"].items():
        if baseline_entry["result"] != "pass":
            continue
        cur = current_by_name.get(name)
        if cur and cur.verdict == "fail":
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
