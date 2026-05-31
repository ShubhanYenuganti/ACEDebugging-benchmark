import json
import os
import threading
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError, WaiterError

from harness.runner.context_builder import build_context, corpus_dir_for_scenario
from harness.runner.scenario_runner import ScenarioRunner
from harness.shared.types import DeploymentResult, SubmissionState

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
            "      Handler: index.handler\n"
            "      Code:\n"
            "        S3Bucket: ace-bench-artifacts\n"
            "        S3Key: handler.zip\n"
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
                "files_modified": ["lambda/handler.py"],
                "files_removed": [],
                "total_files_changed": 1,
                "per_file_line_changes": {},
                "total_lines_changed": 0,
            },
        )
        mocker.patch("harness.runner.deployment_handler.log_file_change")
        result = handle_submission(scenario_dir, "run-001", {})
        assert result.outcome == "lint_fail"
        assert len(result.lint_errors) == 1

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
                "files_modified": [os.path.join("lambda", "handler.py")],
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
        assert result.outcome == "deploy_fail"
        assert len(result.cfn_events) > 0




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
    assert result.outcome == "no_changes"
    assert result.error
    assert "No updates" in result.error or "no changes" in result.error.lower()


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
            return_value=DeploymentResult(outcome="deploy_success"),
        )
        runner = ScenarioRunner(scenario_dir, "run-test-1")
        result1 = runner.deploy(is_initial=True)
        result2 = runner.deploy(is_initial=True)
        assert result1.outcome == "deploy_success"
        assert result2.outcome == "unknown"
        assert "Already submitted" in result2.error
        assert mock_handle.call_count == 1


def test_deploy_initial_returns_success(tmp_path, mocker):
    mocker.patch("harness.runner.scenario_runner.init_run")
    mocker.patch("harness.runner.scenario_runner.snapshot", return_value={})
    runner = ScenarioRunner(str(tmp_path), "run-xyz")
    mocker.patch(
        "harness.runner.scenario_runner.handle_submission",
        return_value=DeploymentResult(outcome="deploy_success"),
    )
    result = runner.deploy(is_initial=True)
    assert result.success is True
    assert runner.submitted is True
    assert runner.submission_state.deploy_attempts == 1


def test_deploy_initial_returns_failure_on_no_changes(tmp_path, mocker):
    mocker.patch("harness.runner.scenario_runner.init_run")
    mocker.patch("harness.runner.scenario_runner.snapshot", return_value={})
    runner = ScenarioRunner(str(tmp_path), "run-xyz")
    mocker.patch(
        "harness.runner.scenario_runner.handle_submission",
        return_value=DeploymentResult(outcome="no_changes", error="no changes detected"),
    )
    result = runner.deploy(is_initial=True)
    assert result.success is False
    assert runner.submitted is False
    assert "no changes" in result.error


def test_deploy_initial_locked_after_success(tmp_path, mocker):
    mocker.patch("harness.runner.scenario_runner.init_run")
    mocker.patch("harness.runner.scenario_runner.snapshot", return_value={})
    runner = ScenarioRunner(str(tmp_path), "run-xyz")
    runner.submission_state.submitted = True
    result = runner.deploy(is_initial=True)
    assert result.success is False
    assert "Already submitted" in result.error


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


def test_handle_submission_reports_skipped_lambda_files(tmp_path, mocker):
    scenario = tmp_path / "scenario"
    scenario.mkdir()
    # Template references handler.zip only.
    (scenario / "faulted.yaml").write_text(
        "AWSTemplateFormatVersion: '2010-09-09'\n"
        "Resources:\n"
        "  MyFn:\n"
        "    Type: AWS::Lambda::Function\n"
        "    Properties:\n"
        "      Handler: index.handler\n"
        "      Code:\n"
        "        S3Bucket: ace-bench-artifacts\n"
        "        S3Key: handler.zip\n"
    )
    (scenario / "deployment").mkdir()
    lam = scenario / "deployment" / "lambda"
    lam.mkdir()
    (lam / "handler.py").write_text("def handler(e,c): return 200\n")
    # Agent also wrote a NEW file whose stem (typo_handler) does not match any S3Key.
    (lam / "typo_handler.py").write_text("def handler(e,c): return 500\n")

    mocker.patch(
        "harness.runner.deployment_handler.run_lint",
        return_value={"passed": True, "fatal_errors": [], "warnings": []},
    )
    mocker.patch("harness.runner.deployment_handler.snapshot", return_value={})
    mocker.patch(
        "harness.runner.deployment_handler.diff_snapshots",
        return_value={
            "files_added": [os.path.join("lambda", "typo_handler.py")],
            "files_modified": [os.path.join("lambda", "handler.py")],
            "files_removed": [],
            "total_files_changed": 2,
            "per_file_line_changes": {},
            "total_lines_changed": 0,
        },
    )
    mocker.patch("harness.runner.deployment_handler.log_file_change")
    mocker.patch("harness.runner.deployment_handler._ensure_artifact_bucket")
    mocker.patch("harness.runner.deployment_handler.s3_client.put_object")
    cf = mocker.patch("harness.runner.deployment_handler.cf_client")
    cf.update_stack.return_value = {}
    cf.get_waiter.return_value.wait.return_value = None

    result = handle_submission(str(scenario), "run-skip", {})
    assert result.outcome == "deploy_success"
    assert "lambda/typo_handler.py" in result.skipped_lambda_files
    assert "lambda/handler.py" not in result.skipped_lambda_files


def test_run_functional_tests_nonzero_exit_is_failure(tmp_path, mocker):
    mocker.patch("harness.runner.scenario_runner.init_run")
    mocker.patch("harness.runner.scenario_runner.snapshot", return_value={})
    runner = ScenarioRunner(str(tmp_path), "run-test")
    corpus_dir = tmp_path / "corpus" / "arch_01_x"
    corpus_dir.mkdir(parents=True)
    (corpus_dir / "functional_test.py").write_text("raise SystemExit(2)\n")
    mocker.patch(
        "harness.runner.scenario_runner.corpus_dir_for_scenario",
        return_value=corpus_dir,
    )
    mocker.patch(
        "harness.runner.scenario_runner.subprocess.run",
        return_value=MagicMock(
            returncode=2,
            stdout="ASSERT pass test_a: ok\n",
            stderr="some traceback",
        ),
    )
    result = runner.run_functional_tests()
    assert result.primary_assertions_passed is False
    # A test that crashed mid-run shouldn't be credited even if some asserts passed.
    crash_names = [a.name for a in result.failed]
    assert "__test_crashed__" in crash_names

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
            stdout="ASSERT pass test_x: ok\n",
            stderr="",
        ),
    )
    result = runner.run_functional_tests()
    assert result.primary_assertions_passed is True
    assert result.passed[0].name == "test_x"


def test_deploy_retry_ignores_submitted_lock(tmp_path, mocker):
    mocker.patch("harness.runner.scenario_runner.init_run")
    mocker.patch("harness.runner.scenario_runner.snapshot", return_value={})
    runner = ScenarioRunner(str(tmp_path), "run-xyz")
    runner.submission_state.submitted = True
    mocker.patch(
        "harness.runner.scenario_runner.handle_submission",
        return_value=DeploymentResult(outcome="deploy_success"),
    )
    result = runner.deploy(is_initial=False)
    assert result.success is True
    assert runner.submission_state.submitted is True  # unchanged


def test_deploy_retry_does_not_set_submitted_on_success(tmp_path, mocker):
    mocker.patch("harness.runner.scenario_runner.init_run")
    mocker.patch("harness.runner.scenario_runner.snapshot", return_value={})
    runner = ScenarioRunner(str(tmp_path), "run-xyz")
    runner.submission_state.submitted = False
    mocker.patch(
        "harness.runner.scenario_runner.handle_submission",
        return_value=DeploymentResult(outcome="deploy_success"),
    )
    result = runner.deploy(is_initial=False)
    assert result.success is True
    assert runner.submission_state.submitted is False  # not touched by retry


def test_deploy_retry_returns_failure_on_no_changes(tmp_path, mocker):
    mocker.patch("harness.runner.scenario_runner.init_run")
    mocker.patch("harness.runner.scenario_runner.snapshot", return_value={})
    runner = ScenarioRunner(str(tmp_path), "run-xyz")
    mocker.patch(
        "harness.runner.scenario_runner.handle_submission",
        return_value=DeploymentResult(outcome="no_changes", error="no changes detected"),
    )
    result = runner.deploy(is_initial=False)
    assert result.success is False
    assert "no changes" in result.error


def test_deploy_updates_last_outcome(tmp_path, mocker):
    mocker.patch("harness.runner.scenario_runner.init_run")
    mocker.patch("harness.runner.scenario_runner.snapshot", return_value={})
    runner = ScenarioRunner(str(tmp_path), "run-xyz")
    runner.submission_state.last_outcome = "deploy_success"
    mocker.patch(
        "harness.runner.scenario_runner.handle_submission",
        return_value=DeploymentResult(outcome="deploy_fail"),
    )
    runner.deploy(is_initial=False)
    assert runner._last_deployment_outcome == "deploy_fail"


def test_deploy_initial_locks_submitted_on_success(tmp_path, mocker):
    mocker.patch("harness.runner.scenario_runner.init_run")
    mocker.patch("harness.runner.scenario_runner.snapshot", return_value={})
    runner = ScenarioRunner(str(tmp_path), "run-1")
    mocker.patch(
        "harness.runner.scenario_runner.handle_submission",
        return_value=DeploymentResult(outcome="deploy_success"),
    )
    r = runner.deploy(is_initial=True)
    assert r.success
    assert runner.submission_state.submitted is True
    assert runner.submission_state.last_outcome == "deploy_success"
    assert runner.submission_state.deploy_attempts == 1


def test_deploy_initial_blocked_after_success(tmp_path, mocker):
    mocker.patch("harness.runner.scenario_runner.init_run")
    mocker.patch("harness.runner.scenario_runner.snapshot", return_value={})
    runner = ScenarioRunner(str(tmp_path), "run-2")
    runner.submission_state.submitted = True
    r = runner.deploy(is_initial=True)
    assert r.success is False
    assert r.outcome == "unknown"
    assert "Already submitted" in r.error


def test_deploy_retry_does_not_check_submitted(tmp_path, mocker):
    mocker.patch("harness.runner.scenario_runner.init_run")
    mocker.patch("harness.runner.scenario_runner.snapshot", return_value={})
    runner = ScenarioRunner(str(tmp_path), "run-3")
    runner.submission_state.submitted = True  # earlier success
    mocker.patch(
        "harness.runner.scenario_runner.handle_submission",
        return_value=DeploymentResult(outcome="deploy_fail"),
    )
    r = runner.deploy(is_initial=False)
    assert r.outcome == "deploy_fail"
    assert runner.submission_state.last_outcome == "deploy_fail"
    assert runner.submission_state.deploy_attempts == 1


def test_deploy_retry_snapshots_submitted_on_success(tmp_path, mocker):
    """Fix #1A: every successful (re)deploy re-snapshots submitted.yaml so Pass 3
    scores the template that is actually live, not a stale first attempt."""
    mocker.patch("harness.runner.scenario_runner.init_run")
    mocker.patch("harness.runner.scenario_runner.snapshot", return_value={})
    runner = ScenarioRunner(str(tmp_path), "run-snap-1")
    mocker.patch(
        "harness.runner.scenario_runner.handle_submission",
        return_value=DeploymentResult(outcome="deploy_success"),
    )
    spy = mocker.patch.object(runner, "_write_submitted_yaml")
    runner.deploy(is_initial=False)
    spy.assert_called_once()


def test_deploy_retry_no_snapshot_on_failure(tmp_path, mocker):
    """A failed retry must NOT overwrite the last good submitted.yaml snapshot."""
    mocker.patch("harness.runner.scenario_runner.init_run")
    mocker.patch("harness.runner.scenario_runner.snapshot", return_value={})
    runner = ScenarioRunner(str(tmp_path), "run-snap-2")
    mocker.patch(
        "harness.runner.scenario_runner.handle_submission",
        return_value=DeploymentResult(outcome="deploy_fail", error="boom"),
    )
    spy = mocker.patch.object(runner, "_write_submitted_yaml")
    runner.deploy(is_initial=False)
    spy.assert_not_called()


def test_deploy_increments_attempt_counter(tmp_path, mocker):
    mocker.patch("harness.runner.scenario_runner.init_run")
    mocker.patch("harness.runner.scenario_runner.snapshot", return_value={})
    runner = ScenarioRunner(str(tmp_path), "run-4")
    mocker.patch(
        "harness.runner.scenario_runner.handle_submission",
        return_value=DeploymentResult(outcome="deploy_fail"),
    )
    runner.deploy(is_initial=True)
    runner.deploy(is_initial=False)
    runner.deploy(is_initial=False)
    assert runner.submission_state.deploy_attempts == 3


# ---------------------------------------------------------------------------
# template_parser integration — find_source_for_stem, _zip_dir, packaging plan
# ---------------------------------------------------------------------------

from harness.runner.deployment_handler import find_source_for_stem, _zip_dir


def test_find_source_for_stem_prefers_directory(tmp_path):
    d = tmp_path / "lambda" / "handler"
    d.mkdir(parents=True)
    (d / "handler.py").write_text("# handler")
    (tmp_path / "lambda" / "handler.py").write_text("# flat")
    path, is_dir = find_source_for_stem(str(tmp_path), "handler")
    assert is_dir is True
    assert path == str(d)


def test_find_source_for_stem_flat_fallback(tmp_path):
    (tmp_path / "lambda").mkdir()
    flat = tmp_path / "lambda" / "handler.py"
    flat.write_text("# flat handler")
    path, is_dir = find_source_for_stem(str(tmp_path), "handler")
    assert is_dir is False
    assert path == str(flat)


def test_find_source_for_stem_returns_none_when_missing(tmp_path):
    path, is_dir = find_source_for_stem(str(tmp_path), "nonexistent")
    assert path is None
    assert is_dir is False


def test_find_source_for_stem_non_lambda_subdir(tmp_path):
    d = tmp_path / "glue" / "etl"
    d.mkdir(parents=True)
    (d / "etl.py").write_text("# glue job")
    path, is_dir = find_source_for_stem(str(tmp_path), "etl")
    assert is_dir is True
    assert path == str(d)


def test_zip_dir_includes_all_files(tmp_path):
    import io as _io
    d = tmp_path / "handler"
    d.mkdir()
    (d / "handler.py").write_text("# main")
    (d / "utils.py").write_text("# helper")
    sub = d / "models"
    sub.mkdir()
    (sub / "schema.py").write_text("# schema")
    zip_bytes = _zip_dir(str(d))
    with zipfile.ZipFile(_io.BytesIO(zip_bytes)) as archive:
        names = archive.namelist()
    assert "handler.py" in names
    assert "utils.py" in names
    assert "models/schema.py" in names


def test_build_packaging_plan_uses_dir_source(tmp_path):
    import textwrap
    from harness.runner.deployment_handler import _build_packaging_plan

    template = tmp_path / "faulted.yaml"
    template.write_text(textwrap.dedent("""
        Resources:
          Fn:
            Type: AWS::Lambda::Function
            Properties:
              Handler: handler.lambda_handler
              Code:
                S3Key: handler.zip
    """))
    d = tmp_path / "lambda" / "handler"
    d.mkdir(parents=True)
    (d / "handler.py").write_text("# main")
    diff = {
        "files_modified": [os.path.join("lambda", "handler", "handler.py")],
        "files_added": [],
        "per_file_line_changes": {},
    }
    plan = _build_packaging_plan(diff, str(template), str(tmp_path), "run-test")
    assert len(plan.uploads) == 1
    assert plan.uploads[0].is_dir is True
    assert plan.uploads[0].stem == "handler"
    assert plan.uploads[0].source_path == str(d)


def test_build_packaging_plan_stem_collision_raises(tmp_path):
    import textwrap
    import unittest.mock as mock
    from harness.runner.deployment_handler import _build_packaging_plan

    template = tmp_path / "faulted.yaml"
    template.write_text(textwrap.dedent("""
        Resources:
          FnA:
            Type: AWS::Lambda::Function
            Properties:
              Code:
                S3Key: handler.zip
    """))
    shared_path = str(tmp_path / "lambda" / "shared")
    # Patch both extract_s3key_stems and find_source_for_stem so that two
    # distinct stems both resolve to the exact same source path, triggering
    # the collision guard in _build_packaging_plan.
    with mock.patch(
        "harness.runner.deployment_handler.extract_s3key_stems",
        return_value={"handler": "handler.zip", "worker": "worker.zip"},
    ), mock.patch(
        "harness.runner.deployment_handler.find_source_for_stem",
        return_value=(shared_path, True),
    ):
        diff2 = {
            "files_modified": [
                os.path.join("lambda", "shared", "handler.py"),
                os.path.join("lambda", "shared", "worker.py"),
            ],
            "files_added": [],
            "per_file_line_changes": {},
        }
        with pytest.raises(ValueError, match="Stem collision"):
            _build_packaging_plan(diff2, str(template), str(tmp_path), "run-col")


def test_build_packaging_plan_glue_subdir(tmp_path):
    import textwrap
    from harness.runner.deployment_handler import _build_packaging_plan

    template = tmp_path / "faulted.yaml"
    template.write_text(textwrap.dedent("""
        Resources:
          GlueJob:
            Type: AWS::Glue::Job
            Properties:
              Code:
                S3Key: etl.zip
    """))
    d = tmp_path / "glue" / "etl"
    d.mkdir(parents=True)
    (d / "etl.py").write_text("# glue")
    diff = {
        "files_modified": [os.path.join("glue", "etl", "etl.py")],
        "files_added": [],
        "per_file_line_changes": {},
    }
    plan = _build_packaging_plan(diff, str(template), str(tmp_path), "run-glue")
    assert len(plan.uploads) == 1
    assert plan.uploads[0].stem == "etl"


def test_upload_initial_lambda_zips_uses_directory_source(tmp_path, mocker):
    """ScenarioRunner._upload_initial_lambda_zips() uploads a dir-based Lambda zip."""
    import textwrap, io as _io, zipfile as _zf

    scenario_dir = tmp_path / "scenario"
    scenario_dir.mkdir()
    (scenario_dir / "scenario.md").write_text("symptom")
    (scenario_dir / "fault_manifest.json").write_text('{"architecture": "arch_01_test"}')
    template = scenario_dir / "faulted.yaml"
    template.write_text(textwrap.dedent("""
        AWSTemplateFormatVersion: '2010-09-09'
        Resources:
          Fn:
            Type: AWS::Lambda::Function
            Properties:
              Handler: handler.lambda_handler
              Code:
                S3Bucket: ace-bench-artifacts
                S3Key: handler.zip
    """))
    deployment = scenario_dir / "deployment"
    handler_dir = deployment / "lambda" / "handler"
    handler_dir.mkdir(parents=True)
    (handler_dir / "handler.py").write_text("def lambda_handler(e, c): return {}")
    (handler_dir / "utils.py").write_text("# helper")

    mock_s3 = MagicMock()
    mocker.patch("harness.runner.scenario_runner.s3_client", mock_s3)
    mocker.patch("harness.runner.scenario_runner.cf_client", MagicMock())
    mocker.patch("harness.runner.scenario_runner._ensure_artifact_bucket")
    mocker.patch("harness.runner.scenario_runner.init_run")

    runner = ScenarioRunner(str(scenario_dir), "run-init-test")
    runner._upload_initial_lambda_zips()

    assert mock_s3.put_object.called
    call_kwargs = mock_s3.put_object.call_args.kwargs
    assert call_kwargs["Key"] == "handler.zip"

    zip_bytes = call_kwargs["Body"]
    with _zf.ZipFile(_io.BytesIO(zip_bytes)) as archive:
        names = archive.namelist()
    assert "handler.py" in names
    assert "utils.py" in names
