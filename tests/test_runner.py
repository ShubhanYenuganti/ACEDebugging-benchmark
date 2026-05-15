import json
import os
import threading
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError, WaiterError

from harness.runner.context_builder import build_context, corpus_dir_for_scenario
from harness.runner.scenario_runner import ScenarioRunner, _parse_pytest_output

_FIXED_INSTRUCTION = (
    "A deployed instance of this system is running in your local environment. "
    "Inspect the deployment directory and CloudFormation template with read_file "
    "and list_directory. Diagnostic tools are available via MCP to probe the "
    "running system. Diagnose the reported symptom, edit any broken files with "
    "write_file (deployment/ files or faulted.yaml), then call submit_fix to "
    "redeploy. submit_fix will refuse if no write_file has occurred — you must "
    "edit at least one file before submitting. Your first submit_fix is your "
    "scored submission."
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


from harness.runner.deployment_handler import handle_submission


class TestDeploymentHandler:
    def _make_scenario(self, tmp_path):
        scenario = tmp_path / "scenario"
        scenario.mkdir()
        (scenario / "faulted.yaml").write_text(
            "AWSTemplateFormatVersion: '2010-09-09'\n"
            "Resources:\n"
            "  MyFn:\n"
            "    Type: AWS::Lambda::Function\n"
            "    Properties:\n"
            "      S3Key: old-handler.zip\n"
        )
        deployment = scenario / "deployment"
        deployment.mkdir()
        lam = deployment / "lambda"
        lam.mkdir()
        (lam / "handler.py").write_text("def handler(e,c): return 200\n")
        return str(scenario)

    def test_returns_lint_fail_on_fatal_errors(self, tmp_path, mocker):
        scenario_dir = self._make_scenario(tmp_path)
        mocker.patch(
            "harness.runner.deployment_handler.run_lint",
            return_value={
                "passed": False,
                "fatal_errors": [
                    {"rule": "E3001", "message": "bad", "location": "line 1"}
                ],
            },
        )
        mocker.patch("harness.runner.deployment_handler.snapshot", return_value={})
        mocker.patch(
            "harness.runner.deployment_handler.diff_snapshots",
            return_value={
                "files_added": [],
                "files_modified": ["deployment/lambda/handler.py"],
                "files_removed": [],
                "total_files_changed": 1,
                "per_file_line_changes": {},
                "total_lines_changed": 0,
            },
        )
        mocker.patch("harness.runner.deployment_handler.log_file_change")
        result = handle_submission(scenario_dir, "run-001", {})
        assert result["outcome"] == "lint_fail"
        assert len(result["errors"]) == 1

    def test_packaging_preflight_zips_and_uploads_lambda(self, tmp_path, mocker):
        scenario_dir = self._make_scenario(tmp_path)
        mocker.patch(
            "harness.runner.deployment_handler.run_lint",
            return_value={"passed": True, "fatal_errors": [], "warnings": []},
        )
        mocker.patch("harness.runner.deployment_handler.snapshot", return_value={})
        mocker.patch(
            "harness.runner.deployment_handler.diff_snapshots",
            return_value={
                "files_added": [],
                "files_modified": [os.path.join("deployment", "lambda", "handler.py")],
                "files_removed": [],
                "total_files_changed": 1,
                "per_file_line_changes": {},
                "total_lines_changed": 0,
            },
        )
        mocker.patch("harness.runner.deployment_handler.log_file_change")
        mock_s3 = MagicMock()
        mock_cf = MagicMock()
        mock_cf.update_stack.return_value = {}
        mock_cf.describe_stack_events.return_value = {"StackEvents": []}
        mock_waiter = MagicMock()
        mock_cf.get_waiter.return_value = mock_waiter
        mocker.patch("harness.runner.deployment_handler.s3_client", mock_s3)
        mocker.patch("harness.runner.deployment_handler.cf_client", mock_cf)
        handle_submission(scenario_dir, "run-002", {})
        assert mock_s3.put_object.called
        call_kwargs = mock_s3.put_object.call_args.kwargs
        assert call_kwargs["Bucket"] == "ace-bench-artifacts"
        assert call_kwargs["Key"].endswith(".zip")

    def test_deploy_fail_returns_rollback_outcome(self, tmp_path, mocker):
        scenario_dir = self._make_scenario(tmp_path)
        mocker.patch(
            "harness.runner.deployment_handler.run_lint",
            return_value={"passed": True, "fatal_errors": [], "warnings": []},
        )
        mocker.patch("harness.runner.deployment_handler.snapshot", return_value={})
        mocker.patch(
            "harness.runner.deployment_handler.diff_snapshots",
            return_value={
                "files_added": [],
                "files_modified": [],
                "files_removed": [],
                "total_files_changed": 0,
                "per_file_line_changes": {},
                "total_lines_changed": 0,
            },
        )
        mocker.patch("harness.runner.deployment_handler.log_file_change")
        mock_cf = MagicMock()
        mock_cf.update_stack.return_value = {}
        mock_waiter = MagicMock()
        mock_waiter.wait.side_effect = WaiterError(
            "update_complete", "Waiter failed", None
        )
        mock_cf.get_waiter.return_value = mock_waiter
        mock_cf.describe_stack_events.return_value = {
            "StackEvents": [
                {
                    "LogicalResourceId": "MyFn",
                    "ResourceStatus": "UPDATE_ROLLBACK_COMPLETE",
                    "ResourceStatusReason": "Resource creation cancelled",
                }
            ]
        }
        mocker.patch("harness.runner.deployment_handler.cf_client", mock_cf)
        mocker.patch("harness.runner.deployment_handler.s3_client", MagicMock())
        result = handle_submission(scenario_dir, "run-003", {})
        assert result["outcome"] == "deploy_fail"
        assert len(result["events"]) > 0




def test_handle_submission_returns_no_changes_on_no_updates_error(tmp_path):
    (tmp_path / "faulted.yaml").write_text(
        "AWSTemplateFormatVersion: '2010-09-09'\nResources:\n  Dummy:\n    Type: AWS::CloudFormation::WaitConditionHandle\n"
    )
    err = ClientError(
        {"Error": {"Code": "ValidationError", "Message": "No updates are to be performed."}},
        "UpdateStack",
    )
    with patch("harness.runner.deployment_handler.cf_client") as mock_cf, \
         patch("harness.runner.deployment_handler.snapshot", return_value={}), \
         patch("harness.runner.deployment_handler.diff_snapshots", return_value={
             "files_added": [], "files_modified": [], "files_removed": [],
             "total_files_changed": 0, "per_file_line_changes": {}, "total_lines_changed": 0,
         }), \
         patch("harness.runner.deployment_handler.log_file_change"), \
         patch("harness.runner.deployment_handler.run_lint", return_value={"passed": True, "fatal_errors": [], "warnings": []}):
        mock_cf.update_stack.side_effect = err
        result = handle_submission(str(tmp_path), "run-test", {})
    assert result["outcome"] == "no_changes"
    assert "error" in result
    assert "No updates" in result["error"] or "no changes" in result["error"].lower()


import threading


class TestScenarioRunner:
    def _make_scenario(self, tmp_path):
        scenario = tmp_path / "scenario"
        scenario.mkdir()
        (scenario / "scenario.md").write_text("Symptom: orders fail.")
        (scenario / "faulted.yaml").write_text(
            "AWSTemplateFormatVersion: '2010-09-09'\n"
        )
        deployment = scenario / "deployment"
        deployment.mkdir()
        (deployment / "handler.py").write_text("def handler(e,c): pass\n")
        return str(scenario)

    def test_submitted_flag_prevents_second_redeployment(self, tmp_path, mocker):
        scenario_dir = self._make_scenario(tmp_path)
        mocker.patch("harness.runner.scenario_runner.init_run")
        mocker.patch("harness.runner.scenario_runner.snapshot", return_value={})
        mock_handle = mocker.patch(
            "harness.runner.scenario_runner.handle_submission",
            return_value={"outcome": "deploy_success"},
        )
        runner = ScenarioRunner(scenario_dir, "run-test-1")
        result1 = runner.on_model_redeploy()
        result2 = runner.on_model_redeploy()
        assert result1["outcome"] == "deploy_success"
        assert result2["outcome"] == "already_submitted"
        assert mock_handle.call_count == 1

    def test_intercept_tool_call_increments_count_and_logs(self, tmp_path, mocker):
        scenario_dir = self._make_scenario(tmp_path)
        mocker.patch("harness.runner.scenario_runner.init_run")
        mocker.patch("harness.runner.scenario_runner.snapshot", return_value={})
        mock_log = mocker.patch("harness.runner.scenario_runner.log_tool_call")
        runner = ScenarioRunner(scenario_dir, "run-test-2")
        runner.intercept_tool_call("ace_invoke_lambda", {"fn": "MyFn"}, {"status": 200})
        runner.intercept_tool_call("ace_get_log_tail", {"fn": "MyFn"}, {"logs": []})
        assert runner.tool_call_count == 2
        assert mock_log.call_count == 2
        first_call_args = mock_log.call_args_list[0]
        assert first_call_args.args[2] == "ace_invoke_lambda"


def test_attempt_deployment_returns_success_dict(tmp_path, mocker):
    mocker.patch("harness.runner.scenario_runner.init_run")
    mocker.patch("harness.runner.scenario_runner.snapshot", return_value={})
    runner = ScenarioRunner(str(tmp_path), "run-xyz")
    mocker.patch(
        "harness.runner.scenario_runner.handle_submission",
        return_value={"outcome": "deploy_success"},
    )
    result = runner.attempt_deployment()
    assert result["success"] is True
    assert runner.submitted is True


def test_attempt_deployment_returns_failure_dict_on_no_changes(tmp_path, mocker):
    mocker.patch("harness.runner.scenario_runner.init_run")
    mocker.patch("harness.runner.scenario_runner.snapshot", return_value={})
    runner = ScenarioRunner(str(tmp_path), "run-xyz")
    mocker.patch(
        "harness.runner.scenario_runner.handle_submission",
        return_value={"outcome": "no_changes", "error": "no changes detected"},
    )
    result = runner.attempt_deployment()
    assert result["success"] is False
    assert runner.submitted is False
    assert "no changes" in result["error"]


def test_attempt_deployment_blocked_after_success(tmp_path, mocker):
    mocker.patch("harness.runner.scenario_runner.init_run")
    mocker.patch("harness.runner.scenario_runner.snapshot", return_value={})
    runner = ScenarioRunner(str(tmp_path), "run-xyz")
    mocker.patch(
        "harness.runner.scenario_runner.handle_submission",
        return_value={"outcome": "deploy_success"},
    )
    runner.attempt_deployment()
    result = runner.attempt_deployment()
    assert result["success"] is False
    assert "Already submitted" in result["error"]


def test_corpus_dir_for_scenario_resolves_arch01(tmp_path):
    corpus = tmp_path / "corpus" / "arch_01_serverless_microservices"
    corpus.mkdir(parents=True)
    (tmp_path / "scenarios" / "arch01_fault07_data_correctness").mkdir(parents=True)
    scenario = tmp_path / "scenarios" / "arch01_fault07_data_correctness"
    result = corpus_dir_for_scenario(scenario, corpus_root=tmp_path / "corpus")
    assert result == corpus

def test_corpus_dir_for_scenario_raises_on_unknown_arch(tmp_path):
    (tmp_path / "scenarios" / "arch99_fault01_connectivity").mkdir(parents=True)
    scenario = tmp_path / "scenarios" / "arch99_fault01_connectivity"
    with pytest.raises(FileNotFoundError):
        corpus_dir_for_scenario(scenario, corpus_root=tmp_path / "corpus")


def test_corpus_dir_for_scenario_raises_on_bad_name(tmp_path):
    bad = tmp_path / "scenarios" / "noarch_fault01"
    bad.mkdir(parents=True)
    (tmp_path / "corpus").mkdir(parents=True)
    with pytest.raises(ValueError, match="Cannot parse arch number"):
        corpus_dir_for_scenario(bad, corpus_root=tmp_path / "corpus")


def test_parse_pytest_output_all_passing():
    output = (
        "PASSED functional_test.py::test_friend_request\n"
        "PASSED functional_test.py::test_read_list\n"
        "2 passed in 1.23s\n"
    )
    result = _parse_pytest_output(output)
    assert result["all_passed"] is True
    assert len(result["passed"]) == 2
    assert len(result["failed"]) == 0
    assert result["passed"][0]["name"] == "test_friend_request"

def test_parse_pytest_output_with_failures():
    output = (
        "PASSED functional_test.py::test_friend_request\n"
        "FAILED functional_test.py::test_accept_state\n"
        "E   AssertionError: state 'Requested' != 'Friends'\n"
        "1 passed, 1 failed in 2.00s\n"
    )
    result = _parse_pytest_output(output)
    assert result["all_passed"] is False
    assert len(result["passed"]) == 1
    assert len(result["failed"]) == 1
    assert result["failed"][0]["name"] == "test_accept_state"
    assert "Requested" in result["failed"][0]["short_error"]

def test_run_functional_tests_calls_subprocess(tmp_path, mocker):
    mocker.patch("harness.runner.scenario_runner.init_run")
    mocker.patch("harness.runner.scenario_runner.snapshot", return_value={})
    runner = ScenarioRunner(str(tmp_path), "run-test")
    corpus_dir = tmp_path / "corpus" / "arch_01_x"
    corpus_dir.mkdir(parents=True)
    (corpus_dir / "functional_test.py").write_text("def test_x(): pass\n")
    mocker.patch(
        "harness.runner.scenario_runner.corpus_dir_for_scenario",
        return_value=corpus_dir,
    )
    mocker.patch(
        "harness.runner.scenario_runner.subprocess.run",
        return_value=MagicMock(
            returncode=0,
            stdout="PASSED functional_test.py::test_x\n1 passed in 0.5s\n",
            stderr="",
        ),
    )
    result = runner.run_functional_tests()
    assert result["all_passed"] is True
    assert result["passed"][0]["name"] == "test_x"


def test_attempt_redeployment_runs_when_already_submitted(tmp_path, mocker):
    mocker.patch("harness.runner.scenario_runner.init_run")
    mocker.patch("harness.runner.scenario_runner.snapshot", return_value={})
    runner = ScenarioRunner(str(tmp_path), "run-xyz")
    runner.submitted = True
    mocker.patch(
        "harness.runner.scenario_runner.handle_submission",
        return_value={"outcome": "deploy_success"},
    )
    result = runner.attempt_redeployment()
    assert result["success"] is True
    assert runner.submitted is True  # unchanged

def test_attempt_redeployment_never_sets_submitted_on_success(tmp_path, mocker):
    mocker.patch("harness.runner.scenario_runner.init_run")
    mocker.patch("harness.runner.scenario_runner.snapshot", return_value={})
    runner = ScenarioRunner(str(tmp_path), "run-xyz")
    runner.submitted = False
    mocker.patch(
        "harness.runner.scenario_runner.handle_submission",
        return_value={"outcome": "deploy_success"},
    )
    result = runner.attempt_redeployment()
    assert result["success"] is True
    assert runner.submitted is False  # not touched

def test_attempt_redeployment_returns_failure_dict(tmp_path, mocker):
    mocker.patch("harness.runner.scenario_runner.init_run")
    mocker.patch("harness.runner.scenario_runner.snapshot", return_value={})
    runner = ScenarioRunner(str(tmp_path), "run-xyz")
    mocker.patch(
        "harness.runner.scenario_runner.handle_submission",
        return_value={"outcome": "no_changes", "error": "no changes detected"},
    )
    result = runner.attempt_redeployment()
    assert result["success"] is False
    assert "no changes" in result["error"]
