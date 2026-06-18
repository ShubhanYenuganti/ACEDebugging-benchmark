#!/usr/bin/env python3
"""
Deploy a corpus architecture to LocalStack using known_good.yaml + deployment/lambda/.
Usage: python corpus/validate_deploy.py <arch_dir>
Output: JSON to stdout with "outcome" key:
  success        — stack reached CREATE_COMPLETE
  lint_fail      — cfn-lint found errors
  deploy_fail    — CloudFormation deployment failed
  localstack_unreachable — LocalStack not responding
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import boto3
import requests
from botocore.exceptions import ClientError

ENDPOINT = "http://localhost:4566"
REGION = "us-east-1"
CREDS = {"aws_access_key_id": "test", "aws_secret_access_key": "test"}
STACK_NAME = "ace-bench-stack"
ARTIFACT_BUCKET = "ace-bench-artifacts"


def _result(outcome, **kwargs):
    print(json.dumps({"outcome": outcome, **kwargs}))
    sys.exit(0)


def _check_localstack():
    try:
        resp = requests.get(f"{ENDPOINT}/_localstack/health", timeout=5)
        return resp.status_code == 200
    except Exception:
        return False


def _cfn_lint(yaml_path):
    proc = subprocess.run(
        ["cfn-lint", str(yaml_path), "--format", "json"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        try:
            return json.loads(proc.stdout)
        except Exception:
            return [{"message": proc.stdout or proc.stderr}]
    return None


def _ensure_bucket(s3):
    try:
        s3.head_bucket(Bucket=ARTIFACT_BUCKET)
    except ClientError:
        s3.create_bucket(Bucket=ARTIFACT_BUCKET)


def _zip_and_upload(arch_dir, s3):
    lambda_dir = Path(arch_dir) / "deployment" / "lambda"
    if not lambda_dir.exists():
        return []
    uploaded = []
    for fn_dir in sorted(lambda_dir.iterdir()):
        if not fn_dir.is_dir():
            continue
        # Skip utility dirs that are not Lambda function packages
        if fn_dir.name.startswith("_"):
            continue
        index_file = fn_dir / "index.py"
        if not index_file.exists():
            continue
        zip_key = f"{fn_dir.name}.zip"
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
                # Walk the entire handler dir so vendored packages
                # (e.g. aws_xray_sdk, xray_instrument.py) are included.
                for file_path in sorted(fn_dir.rglob("*")):
                    if file_path.is_file():
                        arcname = file_path.relative_to(fn_dir)
                        zf.write(file_path, arcname)
            with open(tmp_path, "rb") as f:
                s3.put_object(Bucket=ARTIFACT_BUCKET, Key=zip_key, Body=f.read())
            uploaded.append(zip_key)
        finally:
            os.unlink(tmp_path)
    return uploaded


def _delete_stack_if_exists(cfn):
    try:
        stacks = cfn.describe_stacks(StackName=STACK_NAME)["Stacks"]
        if stacks and stacks[0]["StackStatus"] != "DELETE_COMPLETE":
            cfn.delete_stack(StackName=STACK_NAME)
            _wait_stack(cfn, "DELETE_COMPLETE", timeout=180)
    except ClientError as exc:
        if "does not exist" not in str(exc):
            raise


def _wait_stack(cfn, target, timeout=300):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            status = cfn.describe_stacks(StackName=STACK_NAME)["Stacks"][0]["StackStatus"]
        except ClientError as exc:
            if "does not exist" in str(exc) and target == "DELETE_COMPLETE":
                return "DELETE_COMPLETE"
            raise
        if status == target:
            return status
        if "FAILED" in status or ("ROLLBACK" in status and target == "CREATE_COMPLETE"):
            return status
        time.sleep(5)
    raise TimeoutError(f"Stack did not reach {target} in {timeout}s")


def _get_failure_events(cfn):
    try:
        events = cfn.describe_stack_events(StackName=STACK_NAME)["StackEvents"]
        return [
            {
                "resource": e.get("LogicalResourceId"),
                "status": e.get("ResourceStatus"),
                "reason": e.get("ResourceStatusReason"),
            }
            for e in events
            if "FAILED" in e.get("ResourceStatus", "")
        ]
    except Exception:
        return []


def main():
    if len(sys.argv) < 2:
        _result("error", message="Usage: validate_deploy.py <arch_dir>")

    arch_dir = Path(sys.argv[1]).resolve()
    yaml_path = arch_dir / "known_good.yaml"

    if not yaml_path.exists():
        _result("error", message=f"No known_good.yaml at {yaml_path}")

    if not _check_localstack():
        _result("localstack_unreachable", message="LocalStack not responding at http://localhost:4566")

    lint_findings = _cfn_lint(yaml_path)
    if lint_findings:
        _result("lint_fail", findings=lint_findings)

    s3 = boto3.client("s3", endpoint_url=ENDPOINT, region_name=REGION, **CREDS)
    cfn = boto3.client("cloudformation", endpoint_url=ENDPOINT, region_name=REGION, **CREDS)

    _ensure_bucket(s3)
    uploaded = _zip_and_upload(arch_dir, s3)

    _delete_stack_if_exists(cfn)

    template_body = yaml_path.read_text()
    cfn.create_stack(
        StackName=STACK_NAME,
        TemplateBody=template_body,
        Capabilities=["CAPABILITY_IAM", "CAPABILITY_NAMED_IAM"],
        OnFailure="DO_NOTHING",
    )

    final_status = _wait_stack(cfn, "CREATE_COMPLETE", timeout=300)
    if final_status == "CREATE_COMPLETE":
        _result("success", stack=STACK_NAME, uploaded_zips=uploaded)
    else:
        _result(
            "deploy_fail",
            stack_status=final_status,
            failures=_get_failure_events(cfn),
        )


if __name__ == "__main__":
    main()
