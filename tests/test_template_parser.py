import textwrap
import pytest
from harness.shared.template_parser import extract_s3key_stems


def _write_template(tmp_path, body: str) -> str:
    p = tmp_path / "faulted.yaml"
    p.write_text(textwrap.dedent(body))
    return str(p)


def test_plain_s3key(tmp_path):
    path = _write_template(tmp_path, """
        Resources:
          Fn:
            Type: AWS::Lambda::Function
            Properties:
              Code:
                S3Bucket: bucket
                S3Key: handler.zip
    """)
    assert extract_s3key_stems(path) == {"handler": "handler.zip"}


def test_path_prefixed_s3key(tmp_path):
    path = _write_template(tmp_path, """
        Resources:
          Fn:
            Type: AWS::Lambda::Function
            Properties:
              Code:
                S3Key: lambdas/handler.zip
    """)
    assert extract_s3key_stems(path) == {"handler": "lambdas/handler.zip"}


def test_quoted_s3key(tmp_path):
    path = _write_template(tmp_path, """
        Resources:
          Fn:
            Type: AWS::Lambda::Function
            Properties:
              Code:
                S3Key: "lambdas/handler.zip"
    """)
    assert extract_s3key_stems(path) == {"handler": "lambdas/handler.zip"}


def test_sub_intrinsic_s3key(tmp_path):
    path = _write_template(tmp_path, """
        Resources:
          Fn:
            Type: AWS::Lambda::Function
            Properties:
              Code:
                S3Key: !Sub "${Bucket}/handler.zip"
    """)
    result = extract_s3key_stems(path)
    assert "handler" in result


def test_multiple_lambdas(tmp_path):
    path = _write_template(tmp_path, """
        Resources:
          FnA:
            Type: AWS::Lambda::Function
            Properties:
              Code:
                S3Key: alpha.zip
          FnB:
            Type: AWS::Lambda::Function
            Properties:
              Code:
                S3Key: beta.zip
    """)
    assert extract_s3key_stems(path) == {"alpha": "alpha.zip", "beta": "beta.zip"}


def test_non_zip_s3key_ignored(tmp_path):
    path = _write_template(tmp_path, """
        Resources:
          Fn:
            Type: AWS::Lambda::Function
            Properties:
              Code:
                S3Key: handler.jar
    """)
    assert extract_s3key_stems(path) == {}


def test_missing_file_returns_empty():
    assert extract_s3key_stems("/does/not/exist.yaml") == {}


def test_malformed_yaml_returns_empty(tmp_path):
    path = tmp_path / "faulted.yaml"
    path.write_text(": this is not valid yaml: [\n")
    assert extract_s3key_stems(str(path)) == {}
