import io
import os
import zipfile

from botocore.exceptions import WaiterError

from harness.shared.cfn_lint_runner import run_lint
from harness.shared.file_differ import diff_snapshots, snapshot
from harness.shared.localstack_client import cf_client, s3_client
from harness.shared.result_logger import log_file_change

# Single source of truth — imported by context_builder.py and scenario_runner.py
_STACK_NAME = "ace-bench-stack"
_ARTIFACT_BUCKET = "ace-bench-artifacts"


def _ensure_artifact_bucket() -> None:
    try:
        s3_client.create_bucket(Bucket=_ARTIFACT_BUCKET)
    except Exception:
        pass


def _zip_file(file_path: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(file_path, arcname=os.path.basename(file_path))
    return buf.getvalue()


def handle_submission(scenario_dir: str, run_id: str, start_snapshot: dict) -> dict:
    scenario_dir = os.path.abspath(scenario_dir)
    deployment_dir = os.path.join(scenario_dir, "deployment")
    template_path = os.path.join(scenario_dir, "faulted.yaml")

    # Step 1 — diff snapshots and log
    end_snapshot = snapshot(deployment_dir)
    diff = diff_snapshots(start_snapshot, end_snapshot, deployment_dir)
    log_file_change(run_id, diff)

    # Step 2 — cfn-lint gate
    lint_result = run_lint(template_path)
    if not lint_result["passed"]:
        return {"outcome": "lint_fail", "errors": lint_result["fatal_errors"]}

    # Step 3 — read template body
    with open(template_path, "r", encoding="utf-8") as f:
        template_body = f.read()

    # Step 3b — packaging pre-flight: zip and upload changed Lambda files
    lambda_rel_prefix = os.path.join("deployment", "lambda") + os.sep
    for rel_path in diff["files_modified"] + diff["files_added"]:
        if rel_path.startswith(lambda_rel_prefix) and rel_path.endswith(".py"):
            abs_path = os.path.join(
                deployment_dir, "lambda", os.path.basename(rel_path)
            )
            fn_name = os.path.splitext(os.path.basename(abs_path))[0]
            zip_key = f"lambdas/{run_id}/{fn_name}.zip"
            _ensure_artifact_bucket()
            s3_client.put_object(
                Bucket=_ARTIFACT_BUCKET, Key=zip_key, Body=_zip_file(abs_path)
            )
            template_body = template_body.replace("old-handler.zip", zip_key)

    # Step 4 — CloudFormation update
    cf_client.update_stack(
        StackName=_STACK_NAME,
        TemplateBody=template_body,
        Capabilities=["CAPABILITY_IAM", "CAPABILITY_NAMED_IAM"],
    )

    try:
        waiter = cf_client.get_waiter("stack_update_complete")
        waiter.wait(StackName=_STACK_NAME, WaiterConfig={"Delay": 5, "MaxAttempts": 60})
        return {"outcome": "deploy_success"}
    except WaiterError:
        events_res = cf_client.describe_stack_events(StackName=_STACK_NAME)
        events = [
            {
                "logical_id": e.get("LogicalResourceId"),
                "status": e.get("ResourceStatus"),
                "reason": e.get("ResourceStatusReason"),
            }
            for e in events_res.get("StackEvents", [])
            if e.get("ResourceStatusReason")
        ]
        return {"outcome": "deploy_fail", "events": events}
