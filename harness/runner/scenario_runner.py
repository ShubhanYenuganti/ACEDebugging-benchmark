import os
import subprocess
import sys
import threading
import time

from harness.runner.deployment_handler import (
    _ARTIFACT_BUCKET,
    _STACK_NAME,
    _ensure_artifact_bucket,
    find_handler_for_s3key,
    handle_submission,
    handler_to_arcname,
)
from harness.runner.context_builder import corpus_dir_for_scenario
from harness.shared import assertion_parser
from harness.shared.file_differ import snapshot
from harness.shared.localstack_client import cf_client, s3_client
from harness.shared.result_logger import init_run, log_tool_call
from harness.shared.types import AssertionRunResult, DeploymentResult, SubmissionState


class ScenarioRunner:
    def __init__(self, scenario_dir: str, run_id: str):
        self.scenario_dir = os.path.abspath(scenario_dir)
        self.run_id = run_id
        self.deployment_dir = os.path.join(self.scenario_dir, "deployment")
        self.submission_state = SubmissionState()
        self._lock = threading.Lock()

        scenario_id = os.path.basename(self.scenario_dir)
        init_run(run_id, scenario_id)
        self.start_snapshot = snapshot(self.deployment_dir)
        template_path = os.path.join(self.scenario_dir, "faulted.yaml")
        if os.path.exists(template_path):
            with open(template_path, "r", encoding="utf-8") as _f:
                self.start_faulted_yaml = _f.read()
        else:
            self.start_faulted_yaml = ""

    def _upload_initial_lambda_zips(self) -> None:
        from harness.runner.deployment_handler import (
            _zip_dir, _zip_file, find_source_for_stem,
        )
        from harness.shared.template_parser import extract_s3key_stems

        template_path = os.path.join(self.scenario_dir, "faulted.yaml")
        stems = extract_s3key_stems(template_path)
        if not stems:
            return

        with open(template_path, "r", encoding="utf-8") as f:
            template_body = f.read()

        _ensure_artifact_bucket()
        for stem, s3_key in stems.items():
            source_path, is_dir = find_source_for_stem(self.deployment_dir, stem)
            if source_path is None:
                continue
            if is_dir:
                zip_bytes = _zip_dir(source_path)
            else:
                handler = find_handler_for_s3key(template_body, s3_key)
                arcname = handler_to_arcname(handler)
                zip_bytes = _zip_file(source_path, arcname=arcname)
            s3_client.put_object(Bucket=_ARTIFACT_BUCKET, Key=s3_key, Body=zip_bytes)

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

    def run_functional_tests(self) -> AssertionRunResult:
        corpus_dir = corpus_dir_for_scenario(self.scenario_dir)
        functional_test = corpus_dir / "functional_test.py"
        if not functional_test.exists():
            raise FileNotFoundError(f"functional_test.py not found: {functional_test}")
        results_path = os.path.join(
            "results", self.run_id, f"functional_results_{int(time.time() * 1000)}.json"
        )
        os.makedirs(os.path.dirname(results_path), exist_ok=True)
        env = {**os.environ, "ACE_BENCH_RESULTS_PATH": os.path.abspath(results_path)}
        proc = subprocess.run(
            [sys.executable, str(functional_test)],
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
        return assertion_parser.parse_with_fallback(
            proc.stdout + "\n" + proc.stderr,
            returncode=proc.returncode,
            json_path=results_path,
        )

    def deploy(self, *, is_initial: bool = False) -> DeploymentResult:
        """Single submission entry point — replaces attempt_deployment /
        attempt_redeployment / on_model_redeploy.

        is_initial=True: the first submit of the scenario. Locks submitted on
            success and refuses if already locked.
        is_initial=False: a retry after a failed deploy or failed tests. Does
            not check the submitted lock and does not toggle it.
        """
        with self._lock:
            if is_initial and self.submission_state.submitted:
                return DeploymentResult(outcome="unknown", error="Already submitted (final).")
        result = handle_submission(
            self.scenario_dir, self.run_id,
            self.start_snapshot, self.start_faulted_yaml,
        )
        self.submission_state.deploy_attempts += 1
        self.submission_state.last_outcome = result.outcome
        if is_initial and result.success and self.submission_state.initial_deployment_outcome == "unknown":
            self.submission_state.initial_deployment_outcome = result.outcome
        if is_initial and result.success:
            with self._lock:
                self.submission_state.submitted = True
            self._write_submitted_yaml()
        return result

    def _write_submitted_yaml(self) -> None:
        """Snapshot faulted.yaml → results/<run_id>/submitted.yaml for Pass 3."""
        src = os.path.join(self.scenario_dir, "faulted.yaml")
        dst_dir = os.path.join("results", self.run_id)
        os.makedirs(dst_dir, exist_ok=True)
        dst = os.path.join(dst_dir, "submitted.yaml")
        if os.path.isfile(src):
            with open(src, "r", encoding="utf-8") as f:
                content = f.read()
            with open(dst, "w", encoding="utf-8") as f:
                f.write(content)

    @property
    def submitted(self) -> bool:
        return self.submission_state.submitted

    @submitted.setter
    def submitted(self, v: bool) -> None:
        self.submission_state.submitted = v

    @property
    def _last_deployment_outcome(self) -> str:
        return self.submission_state.last_outcome

    @_last_deployment_outcome.setter
    def _last_deployment_outcome(self, v: str) -> None:
        self.submission_state.last_outcome = v

    @property
    def initial_deployment_outcome(self) -> str:
        return self.submission_state.initial_deployment_outcome
