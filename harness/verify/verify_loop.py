import json
import os

from harness.shared.result_logger import log_verify_result
from harness.verify.pass1_functional import run_pass1
from harness.verify.pass2_regression import run_pass2
from harness.verify.pass3_classification import run_pass3
from harness.verify.pass4_concurrency import run_pass4

RESULTS_DIR = "results"

_CONCURRENCY_CLASSES = {"performance", "reliability"}


def run_verify_loop(
    scenario_dir: str,
    run_id: str,
    deployment_outcome: str,
    manifest_path: str = None,
    corpus_dir: str = None,
    api_endpoint: str = None,
) -> dict:
    if deployment_outcome != "deploy_success":
        result = {
            "outcome": "did_not_deploy",
            "pass1_functional": None,
            "pass2_regression": None,
            "pass3_classification": None,
            "pass4_concurrency": None,
        }
        log_verify_result(run_id, result)
        return result

    pass1 = run_pass1(corpus_dir or scenario_dir)
    pass2 = run_pass2(scenario_dir, run_id, pass1)
    pass3 = run_pass3(scenario_dir, run_id, pass1, manifest_path)

    pass4 = None
    if manifest_path and os.path.isfile(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        if manifest.get("fault_class") in _CONCURRENCY_CLASSES:
            pass4 = run_pass4(scenario_dir, manifest_path, api_endpoint or "")
            # Override rule: Pass 4 failure downgrades Pass 3 to "partial"
            if not pass4["passed"] and pass1["primary_assertions_passed"]:
                pass3 = dict(pass3)
                pass3["classification"] = "partial"
                pass3["root_cause_addressed"] = False

    result = {
        "outcome": "completed",
        "pass1_functional": pass1,
        "pass2_regression": pass2,
        "pass3_classification": pass3,
        "pass4_concurrency": pass4,
    }
    log_verify_result(run_id, result)
    return result
