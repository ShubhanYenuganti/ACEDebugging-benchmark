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
