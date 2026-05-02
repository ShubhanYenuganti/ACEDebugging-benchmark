import json
import os

import pytest
from harness.runner.context_builder import build_context

_FIXED_INSTRUCTION = (
    "A deployed instance of this system is running in your local environment. "
    "The deployment directory and CloudFormation template are available to you directly. "
    "Diagnostic tools are available via MCP. Diagnose the reported symptom, edit whatever "
    "files need changing, and redeploy using localstack-deployer when ready. "
    "Your first successful redeployment is your scored submission."
)


class TestContextBuilder:
    def _make_scenario(self, tmp_path, include_manifest=False):
        scenario = tmp_path / "scenario"
        scenario.mkdir()
        (scenario / "scenario.md").write_text("The API returns 500 on POST /orders.")
        (scenario / "faulted.yaml").write_text(
            "AWSTemplateFormatVersion: '2010-09-09'\n"
        )
        deployment = scenario / "deployment"
        deployment.mkdir()
        (deployment / "handler.py").write_text(
            "def handler(event, ctx): return {'statusCode': 200}\n"
        )
        if include_manifest:
            (scenario / "fault_manifest.json").write_text(
                json.dumps({"fault_class": "config"})
            )
        return str(scenario)

    def test_raises_value_error_when_manifest_present(self, tmp_path, mocker):
        mocker.patch(
            "harness.runner.context_builder._get_stack_outputs",
            return_value={},
        )
        scenario_dir = self._make_scenario(tmp_path, include_manifest=True)
        with pytest.raises(ValueError, match="fault_manifest.json"):
            build_context(scenario_dir)

    def test_returns_correct_shape(self, tmp_path, mocker):
        mocker.patch(
            "harness.runner.context_builder._get_stack_outputs",
            return_value={
                "ApiEndpoint": "http://localhost:4566/restapis/abc/test/_user_request_"
            },
        )
        scenario_dir = self._make_scenario(tmp_path)
        ctx = build_context(scenario_dir)
        assert ctx["scenario_brief"] == "The API returns 500 on POST /orders."
        assert ctx["template_path"].endswith("faulted.yaml")
        assert ctx["deployment_dir"].endswith("deployment")
        assert ctx["stack_outputs"]["ApiEndpoint"].startswith("http")
        assert ctx["instruction"] == _FIXED_INSTRUCTION

    def test_template_path_is_absolute(self, tmp_path, mocker):
        mocker.patch(
            "harness.runner.context_builder._get_stack_outputs", return_value={}
        )
        scenario_dir = self._make_scenario(tmp_path)
        ctx = build_context(scenario_dir)
        assert os.path.isabs(ctx["template_path"])
        assert os.path.isabs(ctx["deployment_dir"])
