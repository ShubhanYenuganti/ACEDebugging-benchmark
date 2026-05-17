import json
import os

from harness.shared.result_logger import log_verify_result
from harness.verify.pass1_functional import Pass1Step
from harness.verify.pass2_regression import Pass2Step
from harness.verify.pass3_classification import Pass3Step
from harness.verify.pass4_concurrency import Pass4Step
from harness.verify.pipeline import (
    VerifyContext,
    downgrade_pass3_when_pass4_fails,
    run_pipeline,
)


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

    fault_class = None
    if manifest_path and os.path.isfile(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as f:
            fault_class = json.load(f).get("fault_class")

    ctx = VerifyContext(
        scenario_dir=scenario_dir,
        run_id=run_id,
        manifest_path=manifest_path,
        corpus_dir=corpus_dir or scenario_dir,
        api_endpoint=api_endpoint or "",
        fault_class=fault_class,
    )
    results = run_pipeline(
        ctx,
        steps=[Pass1Step(), Pass2Step(), Pass3Step(), Pass4Step()],
        postprocessors=[downgrade_pass3_when_pass4_fails],
    )
    result = {"outcome": "completed", **results}
    log_verify_result(run_id, result)
    return result
