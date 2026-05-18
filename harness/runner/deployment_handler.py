import hashlib
import io
import os
import re
import zipfile

from botocore.exceptions import ClientError, WaiterError

from harness.shared.cfn_lint_runner import run_lint
from harness.shared.template_parser import extract_s3key_stems
from harness.shared.file_differ import diff_snapshots, extract_line_changes, snapshot
from harness.shared.localstack_client import cf_client, s3_client
from harness.shared.result_logger import log_deployment, log_file_change
from harness.shared.types import (
    CfnEvent,
    DeploymentResult,
    LambdaUpload,
    PackagingPlan,
)

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


def _zip_dir(dir_path: str) -> bytes:
    """Zip all files under dir_path, preserving relative paths within the dir."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, _dirnames, filenames in os.walk(dir_path):
            for filename in sorted(filenames):
                abs_file = os.path.join(dirpath, filename)
                arcname = os.path.relpath(abs_file, dir_path)
                zf.write(abs_file, arcname=arcname)
    return buf.getvalue()


def find_source_for_stem(deployment_dir: str, stem: str) -> tuple[str | None, bool]:
    """Return (abs_path, is_dir) for the Lambda source matching `stem`.

    Searches deployment_dir recursively. Prefers a subdirectory named `stem`
    (directory-based packaging) over a flat `stem.py` file (legacy layout).
    Returns (None, False) if no match is found.
    """
    deployment_dir = os.path.abspath(deployment_dir)
    for dirpath, dirnames, _filenames in os.walk(deployment_dir):
        for d in dirnames:
            if d == stem:
                return os.path.join(dirpath, d), True
    for dirpath, _dirnames, filenames in os.walk(deployment_dir):
        for f in filenames:
            if f == stem + ".py":
                return os.path.join(dirpath, f), False
    return None, False


def _path_to_package_stem(rel_path: str, known_stems: set[str]) -> str | None:
    """Return the package stem for a changed file path, or None if not in known_stems.

    Handles flat layout ('lambda/handler.py' → 'handler') and directory layout
    ('lambda/handler/utils.py' → 'handler').
    """
    import pathlib as _pathlib
    norm = rel_path.replace("\\", "/")
    parts = _pathlib.PurePosixPath(norm).parts
    for part in parts:
        candidate = _pathlib.PurePosixPath(part).stem if part.endswith(".py") else part
        if candidate in known_stems:
            return candidate
    return None


def _build_packaging_plan(diff: dict, template_path: str, deployment_dir: str, run_id: str) -> PackagingPlan:
    """Compute what to upload from a deployment diff.

    Uses YAML-parsed S3Key stems so quoted values and CFN intrinsics are handled
    correctly. Locates source packages (directory or flat .py) by stem under the
    full deployment_dir tree, not just deployment/lambda/.
    """
    stems = extract_s3key_stems(template_path)
    known_stems = set(stems.keys())

    plan = PackagingPlan()
    affected_stems: set[str] = set()
    for rel_path in diff["files_modified"] + diff["files_added"]:
        norm = rel_path.replace("\\", "/")
        stem = _path_to_package_stem(rel_path, known_stems)
        if stem:
            affected_stems.add(stem)
        elif norm.endswith(".py"):
            plan.orphans.append(norm)
    seen_source_paths: set[str] = set()

    with open(template_path, "r", encoding="utf-8") as _f:
        template_body_for_handler = _f.read()

    for stem in sorted(affected_stems):
        s3key_original = stems[stem]
        source_path, is_dir = find_source_for_stem(deployment_dir, stem)
        if source_path is None:
            plan.orphans.append(stem)
            continue

        abs_source = os.path.abspath(source_path)
        if abs_source in seen_source_paths:
            raise ValueError(
                f"Stem collision: '{stem}' resolves to source '{source_path}' "
                "which is already claimed by another stem."
            )
        seen_source_paths.add(abs_source)

        if is_dir:
            zip_bytes = _zip_dir(source_path)
            arcname = ""
        else:
            handler = find_handler_for_s3key(template_body_for_handler, s3key_original)
            arcname = handler_to_arcname(handler)
            zip_bytes = _zip_file(source_path, arcname=arcname)

        sha = hashlib.sha256(zip_bytes).hexdigest()[:12]
        rel_display = os.path.relpath(source_path, deployment_dir)
        plan.uploads.append(LambdaUpload(
            rel_path=rel_display.replace(os.sep, "/"),
            stem=stem,
            s3_key_original=s3key_original,
            s3_key_new=f"lambdas/{run_id}/{sha}/{stem}.zip",
            sha256=sha,
            arcname=arcname,
            source_path=source_path,
            is_dir=is_dir,
        ))

    if diff.get("per_file_line_changes", {}).get("faulted.yaml"):
        plan.template_changed = True

    return plan


def handle_submission(scenario_dir: str, run_id: str, start_snapshot: dict, start_faulted_yaml: str = "") -> DeploymentResult:
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

    plan = PackagingPlan()

    # Step 2 — cfn-lint gate
    lint_result = run_lint(template_path)
    if not lint_result["passed"]:
        result = DeploymentResult(
            outcome="lint_fail",
            lint_errors=lint_result["fatal_errors"],
        )
        log_deployment(run_id, plan, result)
        return result

    # Step 3 — read template body
    with open(template_path, "r", encoding="utf-8") as f:
        template_body = f.read()

    # Step 3b — build packaging plan from diff + template
    plan = _build_packaging_plan(diff, template_path, deployment_dir, run_id)

    # Step 3c — execute the plan: upload zips, mutate template body
    for upload in plan.uploads:
        _ensure_artifact_bucket()
        if upload.is_dir:
            zip_bytes = _zip_dir(upload.source_path)
        else:
            zip_bytes = _zip_file(upload.source_path, arcname=upload.arcname)
        s3_client.put_object(Bucket=_ARTIFACT_BUCKET, Key=upload.s3_key_new, Body=zip_bytes)
        template_body = re.sub(
            r"(S3Key:\s*)" + re.escape(upload.s3_key_original) + r"(?=\s|$)",
            r"\g<1>" + upload.s3_key_new,
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
        if "No updates are to be performed" in str(e):
            extra = ""
            if plan.has_orphans:
                extra = (
                    " Note: the following modified Lambda file(s) had no "
                    f"matching S3Key in the template and were not deployed: "
                    f"{plan.orphans}. Rename the file to match an S3Key stem "
                    "in faulted.yaml, or edit the template's S3Key to match "
                    "your filename."
                )
            result = DeploymentResult(
                outcome="no_changes",
                error=(
                    "CloudFormation rejected the update: no changes detected. "
                    "Your edits did not produce any diff in the deployed template "
                    "or Lambda code. Verify your write_file call changed the "
                    "correct file and property." + extra
                ),
                skipped_lambda_files=plan.orphans,
                packaged_files=[u.rel_path for u in plan.uploads],
            )
            log_deployment(run_id, plan, result)
            return result
        raise

    try:
        waiter = cf_client.get_waiter("stack_update_complete")
        waiter.wait(StackName=_STACK_NAME, WaiterConfig={"Delay": 5, "MaxAttempts": 60})
        result = DeploymentResult(
            outcome="deploy_success",
            skipped_lambda_files=plan.orphans,
            packaged_files=[u.rel_path for u in plan.uploads],
        )
        log_deployment(run_id, plan, result)
        return result
    except WaiterError:
        events_res = cf_client.describe_stack_events(StackName=_STACK_NAME)
        events = [
            CfnEvent(
                logical_id=e.get("LogicalResourceId"),
                status=e.get("ResourceStatus"),
                reason=e.get("ResourceStatusReason"),
            )
            for e in events_res.get("StackEvents", [])
            if e.get("ResourceStatusReason")
        ]
        result = DeploymentResult(
            outcome="deploy_fail",
            cfn_events=events,
            skipped_lambda_files=plan.orphans,
            packaged_files=[u.rel_path for u in plan.uploads],
        )
        log_deployment(run_id, plan, result)
        return result
