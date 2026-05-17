import hashlib
import io
import os
import re
import zipfile

from botocore.exceptions import ClientError, WaiterError

from harness.shared.cfn_lint_runner import run_lint
from harness.shared.file_differ import diff_snapshots, extract_line_changes, snapshot
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


def handler_to_arcname(handler: str) -> str:
    """Derive the in-zip filename Lambda will look for from a Handler value.

    Lambda's Handler format is '<module>.<function>', so the module portion
    becomes '<module>.py' inside the zip. Falls back to 'index.py'.
    """
    if not handler:
        return "index.py"
    parts = handler.rsplit(".", 1)
    if len(parts) == 2 and parts[0]:
        return os.path.basename(parts[0]) + ".py"
    return "index.py"


def find_handler_for_s3key(template_body: str, s3_key: str) -> str | None:
    """Return the Handler value for the Lambda whose Code.S3Key is `s3_key`.

    Walks the template, pairing each S3Key with the nearest preceding Handler.
    """
    s3_pattern = re.compile(r"S3Key:\s*" + re.escape(s3_key) + r"(?=\s|$)")
    s3_match = s3_pattern.search(template_body)
    if not s3_match:
        return None
    nearest = None
    for m in re.finditer(r"Handler:\s*(\S+)", template_body[: s3_match.start()]):
        nearest = m
    return nearest.group(1) if nearest else None


def find_s3key_for_stem(template_body: str, stem: str) -> str | None:
    """Return the S3Key whose basename (without .zip) matches `stem`."""
    for m in re.finditer(r"S3Key:\s*(\S+)\.zip\b", template_body):
        key = m.group(1) + ".zip"
        if os.path.splitext(os.path.basename(key))[0] == stem:
            return key
    return None


def _zip_file(file_path: str, arcname: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(file_path, arcname=arcname)
    return buf.getvalue()


def handle_submission(scenario_dir: str, run_id: str, start_snapshot: dict, start_faulted_yaml: str = "") -> dict:
    scenario_dir = os.path.abspath(scenario_dir)
    deployment_dir = os.path.join(scenario_dir, "deployment")
    template_path = os.path.join(scenario_dir, "faulted.yaml")

    # Step 1 — diff snapshots and log
    end_snapshot = snapshot(deployment_dir)
    diff = diff_snapshots(start_snapshot, end_snapshot, deployment_dir)

    # Also track faulted.yaml if initial content was provided
    if start_faulted_yaml:
        with open(template_path, "r", encoding="utf-8") as f:
            current_faulted = f.read()
        if current_faulted != start_faulted_yaml:
            changes = extract_line_changes(
                start_faulted_yaml.splitlines(), current_faulted.splitlines()
            )
            added = sum(1 for c in changes if c["type"] == "added")
            removed = sum(1 for c in changes if c["type"] == "removed")
            diff["per_file_line_changes"]["faulted.yaml"] = {
                "lines_added": added,
                "lines_modified": 0,
                "lines_removed": removed,
                "total_lines_changed": added + removed,
                "changes": changes,
            }
            diff["files_modified"] = ["faulted.yaml"] + diff["files_modified"]
            diff["total_files_changed"] += 1
            diff["total_lines_changed"] += added + removed

    log_file_change(run_id, diff)

    # Step 2 — cfn-lint gate
    lint_result = run_lint(template_path)
    if not lint_result["passed"]:
        return {
            "outcome": "lint_fail",
            "errors": lint_result["fatal_errors"],
            "skipped_lambda_files": [],
        }

    # Step 3 — read template body
    with open(template_path, "r", encoding="utf-8") as f:
        template_body = f.read()

    # Step 3b — packaging pre-flight: zip and upload changed Lambda files.
    # For each changed .py file under deployment/lambda/:
    #   1. Locate the matching CFN S3Key by stem (e.g. foo.py -> S3Key: foo.zip).
    #   2. Derive the in-zip filename from that Lambda's Handler so the runtime
    #      can find the module (avoids the index.py vs <name>.py mismatch).
    #   3. Upload to a content-hashed S3Key so retries with new content produce
    #      a new S3Key (and therefore a real CloudFormation diff); retries with
    #      identical bytes correctly produce the same key.
    lambda_rel_prefix = "lambda" + os.sep
    skipped_lambda_files: list[str] = []
    for rel_path in diff["files_modified"] + diff["files_added"]:
        if not (rel_path.startswith(lambda_rel_prefix) and rel_path.endswith(".py")):
            continue
        abs_path = os.path.join(deployment_dir, "lambda", os.path.basename(rel_path))
        fn_stem = os.path.splitext(os.path.basename(abs_path))[0]

        original_s3_key = find_s3key_for_stem(template_body, fn_stem)
        if original_s3_key is None:
            # No Lambda in the template uses this file — record it so the
            # agent can correct the filename (or template S3Key) on retry.
            # Previously this was a silent skip and the agent had no signal
            # that its edit never reached the live Lambda.
            skipped_lambda_files.append(rel_path.replace(os.sep, "/"))
            continue
        handler = find_handler_for_s3key(template_body, original_s3_key)
        arcname = handler_to_arcname(handler)

        zip_bytes = _zip_file(abs_path, arcname=arcname)
        content_hash = hashlib.sha256(zip_bytes).hexdigest()[:12]
        new_s3_key = f"lambdas/{run_id}/{content_hash}/{fn_stem}.zip"

        _ensure_artifact_bucket()
        s3_client.put_object(Bucket=_ARTIFACT_BUCKET, Key=new_s3_key, Body=zip_bytes)

        # Replace the specific S3Key value, not a regex over the whole template.
        template_body = re.sub(
            r"(S3Key:\s*)" + re.escape(original_s3_key) + r"(?=\s|$)",
            r"\g<1>" + new_s3_key,
            template_body,
        )

    # Step 4 — CloudFormation update
    try:
        cf_client.update_stack(
            StackName=_STACK_NAME,
            TemplateBody=template_body,
            Capabilities=["CAPABILITY_IAM", "CAPABILITY_NAMED_IAM"],
        )
    except ClientError as e:
        msg = str(e)
        if "No updates are to be performed" in msg:
            extra = ""
            if skipped_lambda_files:
                extra = (
                    " Note: the following modified Lambda file(s) had no "
                    f"matching S3Key in the template and were not deployed: "
                    f"{skipped_lambda_files}. Rename the file to match an "
                    "S3Key stem in faulted.yaml, or edit the template's "
                    "S3Key to match your filename."
                )
            return {
                "outcome": "no_changes",
                "error": (
                    "CloudFormation rejected the update: no changes detected. "
                    "Your edits did not produce any diff in the deployed template or Lambda code. "
                    "Verify your write_file call changed the correct file and property." + extra
                ),
                "skipped_lambda_files": skipped_lambda_files,
            }
        raise

    try:
        waiter = cf_client.get_waiter("stack_update_complete")
        waiter.wait(StackName=_STACK_NAME, WaiterConfig={"Delay": 5, "MaxAttempts": 60})
        return {
            "outcome": "deploy_success",
            "skipped_lambda_files": skipped_lambda_files,
        }
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
        return {
            "outcome": "deploy_fail",
            "events": events,
            "skipped_lambda_files": skipped_lambda_files,
        }
