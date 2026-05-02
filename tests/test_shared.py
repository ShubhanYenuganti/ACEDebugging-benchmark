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
