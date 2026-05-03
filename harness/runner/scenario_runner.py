import datetime
import io
import os
import re
import subprocess
import threading
import zipfile

from harness.runner.deployment_handler import (
    _ARTIFACT_BUCKET,
    _STACK_NAME,
    _ensure_artifact_bucket,
    handle_submission,
)
from harness.shared.file_differ import snapshot
from harness.shared.localstack_client import s3_client
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

        _ensure_artifact_bucket()
        for s3_key in s3_keys:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for py_path in py_files:
                    zf.write(py_path, arcname=os.path.basename(py_path))
            s3_client.put_object(
                Bucket=_ARTIFACT_BUCKET, Key=s3_key, Body=buf.getvalue()
            )

    def start(self) -> None:
        self._upload_initial_lambda_zips()
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
