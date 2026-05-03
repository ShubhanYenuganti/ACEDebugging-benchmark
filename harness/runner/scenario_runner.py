import datetime
import os
import subprocess
import threading

from harness.runner.deployment_handler import _STACK_NAME, handle_submission
from harness.shared.file_differ import snapshot
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

    def start(self) -> None:
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
        result = handle_submission(self.scenario_dir, self.run_id, self.start_snapshot)
        self._last_deployment_outcome = result.get("outcome", "unknown")
        return result
