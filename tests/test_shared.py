import pytest

import harness.shared.localstack_client as lsc
from harness.shared.localstack_client import health_check


class TestHealthCheck:
    def test_raises_runtime_error_when_unreachable(self, mocker):
        mocker.patch.object(
            lsc.cf_client,
            "list_stacks",
            side_effect=Exception("Connection refused"),
        )
        with pytest.raises(RuntimeError, match="LocalStack is not reachable"):
            health_check()

    def test_does_not_raise_when_reachable(self, mocker):
        mocker.patch.object(
            lsc.cf_client,
            "list_stacks",
            return_value={"StackSummaries": []},
        )
        health_check()  # must not raise


from harness.shared.cfn_lint_runner import run_lint

_VALID_TEMPLATE = """\
AWSTemplateFormatVersion: '2010-09-09'
Resources:
  MyBucket:
    Type: AWS::S3::Bucket
"""

_INVALID_TEMPLATE_E_RULE = """\
AWSTemplateFormatVersion: '2010-09-09'
Resources:
  MyBucket:
    Type: AWS::S3::Bucket
    Properties:
      NonExistentProperty: invalid-value
"""


class TestCfnLintRunner:
    def test_passes_on_valid_template(self, tmp_path):
        template = tmp_path / "valid.yaml"
        template.write_text(_VALID_TEMPLATE)
        result = run_lint(str(template))
        assert result["passed"] is True
        assert result["fatal_errors"] == []

    def test_fails_on_e_rule_error(self, tmp_path):
        template = tmp_path / "invalid.yaml"
        template.write_text(_INVALID_TEMPLATE_E_RULE)
        result = run_lint(str(template))
        assert result["passed"] is False
        assert len(result["fatal_errors"]) > 0
        assert result["fatal_errors"][0]["rule"].startswith("E")

    def test_w_rule_warning_does_not_fail(self, tmp_path, mocker):
        mock_output = (
            '[{"Rule": {"Id": "W3010"}, "Message": "some warning",'
            ' "Location": {"Start": {"LineNumber": 3}}}]'
        )
        mocker.patch(
            "subprocess.run",
            return_value=mocker.Mock(stdout=mock_output, stderr="", returncode=2),
        )
        template = tmp_path / "warn.yaml"
        template.write_text(_VALID_TEMPLATE)
        result = run_lint(str(template))
        assert result["passed"] is True
        assert len(result["warnings"]) == 1
        assert result["warnings"][0]["rule"] == "W3010"

    def test_raises_environment_error_when_cfn_lint_missing(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda _: None)
        with pytest.raises(EnvironmentError, match="cfn-lint is not installed"):
            run_lint("any.yaml")


import os

from harness.shared.file_differ import diff_snapshots, snapshot


class TestFileDiffer:
    def test_snapshot_returns_content_for_each_file(self, tmp_path):
        (tmp_path / "a.py").write_text("line1\nline2\n")
        (tmp_path / "b.py").write_text("hello\n")
        result = snapshot(str(tmp_path))
        assert set(result.keys()) == {"a.py", "b.py"}
        assert result["a.py"] == "line1\nline2\n"
        assert result["b.py"] == "hello\n"

    def test_snapshot_uses_relative_paths_for_subdirectories(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "handler.py").write_text("code\n")
        result = snapshot(str(tmp_path))
        expected_key = os.path.join("sub", "handler.py")
        assert expected_key in result

    def test_diff_added_files(self):
        before = {}
        after = {"new.py": "line1\nline2\n"}
        result = diff_snapshots(before, after, "/unused")
        assert result["files_added"] == ["new.py"]
        assert result["files_modified"] == []
        assert result["files_removed"] == []
        assert result["total_files_changed"] == 1
        assert result["per_file_line_changes"]["new.py"]["lines_added"] == 2
        assert result["per_file_line_changes"]["new.py"]["lines_removed"] == 0
        assert result["per_file_line_changes"]["new.py"]["lines_modified"] == 0
        assert result["per_file_line_changes"]["new.py"]["total_lines_changed"] == 2
        assert result["total_lines_changed"] == 2

    def test_diff_removed_files(self):
        before = {"old.py": "a\nb\nc\n"}
        after = {}
        result = diff_snapshots(before, after, "/unused")
        assert result["files_removed"] == ["old.py"]
        assert result["total_files_changed"] == 0  # removed files not counted
        assert result["per_file_line_changes"]["old.py"]["lines_removed"] == 3
        assert result["per_file_line_changes"]["old.py"]["lines_added"] == 0
        assert result["total_lines_changed"] == 3

    def test_diff_modified_files(self):
        before = {"handler.py": "line1\nline2\nline3\n"}
        after = {"handler.py": "line1\nline2_changed\nline3\nline4\n"}
        result = diff_snapshots(before, after, "/unused")
        assert result["files_modified"] == ["handler.py"]
        assert result["total_files_changed"] == 1
        changes = result["per_file_line_changes"]["handler.py"]
        # line2 removed (1 removed); line2_changed + line4 added (2 added)
        assert changes["lines_removed"] == 1
        assert changes["lines_added"] == 2
        assert changes["lines_modified"] == 0
        assert changes["total_lines_changed"] == 3
        assert result["total_lines_changed"] == 3

    def test_diff_unchanged_files_have_no_entry_in_per_file(self):
        before = {"a.py": "same\n", "b.py": "old\n"}
        after = {"a.py": "same\n", "b.py": "new\n"}
        result = diff_snapshots(before, after, "/unused")
        assert "a.py" not in result["per_file_line_changes"]
        assert "b.py" in result["per_file_line_changes"]


import concurrent.futures
import json
from pathlib import Path

import harness.shared.result_logger as rl
from harness.shared.result_logger import (
    init_run,
    log_file_change,
    log_tool_call,
    log_verify_result,
)


class TestResultLogger:
    def test_init_run_creates_directory_and_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rl, "RESULTS_DIR", str(tmp_path))
        init_run("run-001", "arch01_fault01")
        run_dir = tmp_path / "run-001"
        assert run_dir.is_dir()
        assert (run_dir / "scenario_id.txt").read_text() == "arch01_fault01"
        assert json.loads((run_dir / "tool_call_trace.json").read_text()) == []

    def test_log_tool_call_appends_entries_in_order(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rl, "RESULTS_DIR", str(tmp_path))
        init_run("run-002", "arch01_fault01")
        log_tool_call(
            "run-002",
            1,
            "ace_invoke_lambda",
            {"fn": "MyFunc"},
            {"status": 200},
            "2026-01-01T00:00:00Z",
        )
        log_tool_call(
            "run-002",
            2,
            "ace_get_log_tail",
            {"fn": "MyFunc"},
            {"logs": []},
            "2026-01-01T00:00:01Z",
        )
        data = json.loads((tmp_path / "run-002" / "tool_call_trace.json").read_text())
        assert len(data) == 2
        assert data[0]["tool"] == "ace_invoke_lambda"
        assert data[0]["turn"] == 1
        assert data[1]["tool"] == "ace_get_log_tail"
        assert data[1]["turn"] == 2

    def test_log_tool_call_concurrent_no_corruption(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rl, "RESULTS_DIR", str(tmp_path))
        init_run("run-003", "arch01_fault01")

        def write_entry(i: int) -> None:
            log_tool_call(
                "run-003",
                i,
                f"tool_{i}",
                {"i": i},
                {"ok": True},
                f"2026-01-01T00:00:{i:02d}Z",
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(write_entry, i) for i in range(20)]
            concurrent.futures.wait(futures)

        data = json.loads((tmp_path / "run-003" / "tool_call_trace.json").read_text())
        assert len(data) == 20

    def test_log_file_change_writes_diff_dict(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rl, "RESULTS_DIR", str(tmp_path))
        init_run("run-004", "arch01_fault01")
        diff = {
            "files_added": ["new.py"],
            "files_modified": [],
            "files_removed": [],
            "total_files_changed": 1,
            "per_file_line_changes": {
                "new.py": {
                    "lines_added": 5,
                    "lines_modified": 0,
                    "lines_removed": 0,
                    "total_lines_changed": 5,
                }
            },
            "total_lines_changed": 5,
        }
        log_file_change("run-004", diff)
        written = json.loads(
            (tmp_path / "run-004" / "file_change_log.json").read_text()
        )
        assert written == diff

    def test_log_verify_result_writes_result_dict(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rl, "RESULTS_DIR", str(tmp_path))
        init_run("run-005", "arch01_fault01")
        result = {
            "outcome": "completed",
            "pass1_functional": {"all_assertions_passed": True},
        }
        log_verify_result("run-005", result)
        written = json.loads((tmp_path / "run-005" / "verify_result.json").read_text())
        assert written == result


def test_log_deployment_appends_entries(tmp_path, monkeypatch):
    import json
    from harness.shared.result_logger import log_deployment
    from harness.shared.types import DeploymentResult, LambdaUpload, PackagingPlan
    monkeypatch.chdir(tmp_path)

    plan1 = PackagingPlan(uploads=[LambdaUpload(
        rel_path="lambda/h.py", stem="h", s3_key_original="h.zip",
        s3_key_new="lambdas/r/abc/h.zip", sha256="abc", arcname="index.py",
    )])
    log_deployment("run-x", plan1, DeploymentResult(outcome="deploy_success"))
    log_deployment("run-x", PackagingPlan(orphans=["lambda/typo.py"]),
                   DeploymentResult(outcome="no_changes", error="no changes"))

    data = json.loads((tmp_path / "results" / "run-x" / "deployment_log.json").read_text())
    assert len(data) == 2
    assert data[0]["outcome"] == "deploy_success"
    assert data[0]["uploads"][0]["sha256"] == "abc"
    assert data[1]["orphans"] == ["lambda/typo.py"]
