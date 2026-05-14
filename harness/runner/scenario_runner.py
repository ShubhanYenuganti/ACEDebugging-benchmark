import datetime
import io
import os
import re
import subprocess
import threading
import time
import zipfile

from harness.runner.deployment_handler import (
    _ARTIFACT_BUCKET,
    _STACK_NAME,
    _ensure_artifact_bucket,
    handle_submission,
)
from harness.shared.file_differ import snapshot
from harness.shared.localstack_client import cf_client, s3_client
from harness.shared.result_logger import init_run, log_tool_call


class ScenarioRunner:
    def __init__(self, scenario_dir: str, run_id: str):
        self.scenario_dir = os.path.abspath(scenario_dir)
        self.run_id = run_id
        self.deployment_dir = os.path.join(self.scenario_dir, "deployment")
        self.tool_call_count = 0
        self.submitted = False
        self._last_deployment_outcome: str = "unknown"
        self._lock = threading.Lock()

        scenario_id = os.path.basename(self.scenario_dir)
        init_run(run_id, scenario_id)
        self.start_snapshot = snapshot(self.deployment_dir)

    def _upload_initial_lambda_zips(self) -> None:
        template_path = os.path.join(self.scenario_dir, "faulted.yaml")
        lambda_dir = os.path.join(self.deployment_dir, "lambda")

        with open(template_path, "r", encoding="utf-8") as f:
            template_body = f.read()

        s3_keys = re.findall(r"S3Key:\s*(\S+)", template_body)

        py_files = []
        if os.path.isdir(lambda_dir):
            py_files = sorted(
                os.path.join(lambda_dir, fn)
                for fn in os.listdir(lambda_dir)
                if fn.endswith(".py")
            )

        if not py_files or not s3_keys:
            return

        py_by_stem = {os.path.splitext(os.path.basename(p))[0]: p for p in py_files}

        _ensure_artifact_bucket()
        for s3_key in s3_keys:
            key_stem = os.path.splitext(os.path.basename(s3_key))[0]
            py_path = py_by_stem.get(key_stem) or next(iter(py_by_stem.values()))
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.write(py_path, arcname="index.py")
            s3_client.put_object(
                Bucket=_ARTIFACT_BUCKET, Key=s3_key, Body=buf.getvalue()
            )

    def _delete_existing_stack(self) -> None:
        # Delete any existing stack so the bucket is removed before we re-upload.
        # The ArtifactBucket is a CF resource — deleting the stack deletes the bucket.
        # We must upload zips AFTER deletion so the new create-stack finds them.
        try:
            status = cf_client.describe_stacks(StackName=_STACK_NAME)["Stacks"][0]["StackStatus"]
        except Exception as e:
            if "does not exist" in str(e):
                return  # nothing to delete
            raise
        if status == "DELETE_COMPLETE":
            return
        cf_client.delete_stack(StackName=_STACK_NAME)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            time.sleep(3)
            try:
                s = cf_client.describe_stacks(StackName=_STACK_NAME)["Stacks"][0]["StackStatus"]
                if s == "DELETE_COMPLETE":
                    break
            except Exception as e:
                if "does not exist" in str(e):
                    break
                raise

    def start(self) -> None:
        # 1. Delete any existing stack (removes the CF-managed ArtifactBucket)
        self._delete_existing_stack()
        # 2. Upload Lambda zips now that the bucket has been deleted by stack deletion
        self._upload_initial_lambda_zips()
        # 3. Create the stack — localstack-deployer will skip deletion (stack is gone)
        result = subprocess.run(
            [
                "localstack-deployer",
                "create-stack",
                "--stack-name",
                _STACK_NAME,
                "--template",
                os.path.join(self.scenario_dir, "faulted.yaml"),
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"localstack-deployer create-stack failed:\n{result.stderr}"
            )

    def intercept_tool_call(self, tool_name: str, input: dict, output: dict) -> None:
        with self._lock:
            self.tool_call_count += 1
            turn = self.tool_call_count
        timestamp = datetime.datetime.utcnow().isoformat() + "Z"
        log_tool_call(self.run_id, turn, tool_name, input, output, timestamp)

    def on_model_redeploy(self) -> dict:
        with self._lock:
            if self.submitted:
                return {"outcome": "already_submitted"}
            self.submitted = True
        try:
            result = handle_submission(self.scenario_dir, self.run_id, self.start_snapshot)
        except Exception:
            self._last_deployment_outcome = "error"
            raise
        self._last_deployment_outcome = result.get("outcome", "unknown")
        return result
