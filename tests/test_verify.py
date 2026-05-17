import json
import os

import pytest

from harness.shared.types import AssertionResult, AssertionRunResult
from harness.verify.pass1_functional import run_pass1


class TestPass1Functional:
    def _make_corpus(self, tmp_path, output: str):
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        ft = corpus / "functional_test.py"
        ft.write_text(f"import sys\nprint({repr(output)})\nsys.exit(0)\n")
        return str(corpus)

    def test_parses_pass_and_fail_assertions(self, tmp_path):
        output = (
            "ASSERT pass connectivity: connection ok\n"
            "ASSERT fail auth_check: token invalid\n"
            "ASSERT pass data_write: write succeeded\n"
        )
        corpus_dir = self._make_corpus(tmp_path, output)
        result = run_pass1(corpus_dir)
        assert result.assertions_by_name["connectivity"].verdict == "pass"
        assert result.assertions_by_name["auth_check"].verdict == "fail"
        assert result.assertions_by_name["data_write"].verdict == "pass"
        assert result.all_failed_names == ["auth_check"]

    def test_primary_assertions_passed_excludes_secondary(self, tmp_path):
        output = "ASSERT pass main_check: ok\nASSERT fail latency_secondary: too slow\n"
        corpus_dir = self._make_corpus(tmp_path, output)
        result = run_pass1(corpus_dir)
        assert result.primary_assertions_passed is True
        assert result.all_assertions_passed is False
        assert "latency_secondary" in result.all_failed_names

    def test_all_assertions_passed_true_when_all_pass(self, tmp_path):
        output = "ASSERT pass check_a: ok\nASSERT pass check_b: ok\n"
        corpus_dir = self._make_corpus(tmp_path, output)
        result = run_pass1(corpus_dir)
        assert result.primary_assertions_passed is True
        assert result.all_assertions_passed is True
        assert result.all_failed_names == []

    def test_primary_assertions_passed_false_when_primary_fails(self, tmp_path):
        output = (
            "ASSERT fail main_check: broken\nASSERT pass side_check_secondary: ok\n"
        )
        corpus_dir = self._make_corpus(tmp_path, output)
        result = run_pass1(corpus_dir)
        assert result.primary_assertions_passed is False

    def test_zero_assertions_is_treated_as_failure(self, tmp_path):
        # Test crashed / mis-configured: no ASSERT lines emitted at all.
        # Must be treated as failure so scorer doesn't credit a non-run.
        corpus_dir = self._make_corpus(tmp_path, "hello\nworld\n")
        result = run_pass1(corpus_dir)
        assert result.primary_assertions_passed is False
        assert result.all_assertions_passed is False
        assert "__no_assertions__" in result.all_failed_names
        assert result.assertions_by_name["__no_assertions__"].verdict == "fail"


from pathlib import Path

import harness.verify.pass2_regression as p2mod
from harness.verify.pass2_regression import run_pass2


class TestPass2Regression:
    def _write_baseline(self, tmp_path, run_id, assertions: dict, results_dir: str):
        run_dir = Path(results_dir) / run_id
        run_dir.mkdir(parents=True)
        baseline = {
            "assertions": {
                name: {"result": verdict, "message": ""}
                for name, verdict in assertions.items()
            }
        }
        (run_dir / "faulted_baseline.json").write_text(json.dumps(baseline))

    def test_detects_regression_from_pass_to_fail(self, tmp_path, monkeypatch):
        results_dir = str(tmp_path / "results")
        self._write_baseline(
            tmp_path,
            "run-r1",
            {"check_a": "pass", "check_b": "pass", "check_c": "fail"},
            results_dir,
        )
        monkeypatch.setattr(p2mod, "RESULTS_DIR", results_dir)
        pass1_result = AssertionRunResult(assertions=[
            AssertionResult(name="check_a", verdict="pass", message=""),
            AssertionResult(name="check_b", verdict="fail", message="broke"),
            AssertionResult(name="check_c", verdict="fail", message="still broken"),
        ])
        result = run_pass2("scenario", "run-r1", pass1_result)
        assert result["regression_count"] == 1
        assert result["regressions"][0]["assertion"] == "check_b"
        assert result["regressions"][0]["severity"] == "critical"

    def test_secondary_assertion_regression_is_non_critical(
        self, tmp_path, monkeypatch
    ):
        results_dir = str(tmp_path / "results")
        self._write_baseline(
            tmp_path, "run-r2", {"check_secondary": "pass"}, results_dir
        )
        monkeypatch.setattr(p2mod, "RESULTS_DIR", results_dir)
        pass1_result = AssertionRunResult(assertions=[
            AssertionResult(name="check_secondary", verdict="fail", message=""),
        ])
        result = run_pass2("scenario", "run-r2", pass1_result)
        assert result["critical_regression_count"] == 0
        assert result["non_critical_regression_count"] == 1

    def test_no_regressions_when_all_stable(self, tmp_path, monkeypatch):
        results_dir = str(tmp_path / "results")
        self._write_baseline(
            tmp_path, "run-r3", {"check_a": "fail", "check_b": "pass"}, results_dir
        )
        monkeypatch.setattr(p2mod, "RESULTS_DIR", results_dir)
        pass1_result = AssertionRunResult(assertions=[
            AssertionResult(name="check_a", verdict="pass", message="fixed"),
            AssertionResult(name="check_b", verdict="pass", message="still ok"),
        ])
        result = run_pass2("scenario", "run-r3", pass1_result)
        assert result["regression_count"] == 0
        assert result["regressions"] == []


import harness.verify.pass3_classification as p3mod
from harness.verify.pass3_classification import run_pass3


class TestPass3Classification:
    def _setup(
        self, tmp_path, run_id, manifest, faulted_yaml, submitted_yaml, diff_text
    ):
        scenario = tmp_path / "scenario"
        scenario.mkdir(exist_ok=True)
        (scenario / "faulted.yaml").write_text(faulted_yaml)
        manifest_path = tmp_path / f"manifest_{run_id}.json"
        manifest_path.write_text(json.dumps(manifest))
        results_dir = str(tmp_path / "results")
        run_dir = Path(results_dir) / run_id
        run_dir.mkdir(parents=True)
        (run_dir / "submitted.yaml").write_text(submitted_yaml)
        (run_dir / "file_change_log.json").write_text(
            json.dumps({"diff_text": diff_text})
        )
        return str(scenario), str(manifest_path), results_dir

    def test_root_cause_when_structural_match_and_no_invalid_patch(
        self, tmp_path, monkeypatch
    ):
        faulted_yaml = "Resources:\n  MyFn:\n    Properties:\n      Timeout: 3\n"
        submitted_yaml = "Resources:\n  MyFn:\n    Properties:\n      Timeout: 30\n"
        manifest = {
            "target_resource": "MyFn",
            "target_property": "Properties.Timeout",
            "original_value": 30,
            "invalid_patches": ["env_var_workaround"],
            "valid_fixes": [],
        }
        scenario_dir, manifest_path, results_dir = self._setup(
            tmp_path,
            "run-p3-1",
            manifest,
            faulted_yaml,
            submitted_yaml,
            "-      Timeout: 3\n+      Timeout: 30\n",
        )
        monkeypatch.setattr(p3mod, "RESULTS_DIR", results_dir)
        pass1_result = AssertionRunResult(assertions=[
            AssertionResult(name="check_a", verdict="pass", message="ok"),
        ])
        result = run_pass3(scenario_dir, "run-p3-1", pass1_result, manifest_path)
        assert result["classification"] == "root_cause"
        assert result["structural_match"] is True
        assert result["invalid_patch_detected"] is False
        assert result["root_cause_addressed"] is True

    def test_workaround_when_assertions_pass_but_structural_mismatch(
        self, tmp_path, monkeypatch
    ):
        faulted_yaml = "Resources:\n  MyFn:\n    Properties:\n      Timeout: 3\n"
        submitted_yaml = "Resources:\n  MyFn:\n    Properties:\n      Timeout: 3\n"
        manifest = {
            "target_resource": "MyFn",
            "target_property": "Properties.Timeout",
            "original_value": 30,
            "invalid_patches": [],
            "valid_fixes": [],
        }
        scenario_dir, manifest_path, results_dir = self._setup(
            tmp_path,
            "run-p3-2",
            manifest,
            faulted_yaml,
            submitted_yaml,
            "+  ENV_VAR: override\n",
        )
        monkeypatch.setattr(p3mod, "RESULTS_DIR", results_dir)
        pass1_result = AssertionRunResult(assertions=[
            AssertionResult(name="check_a", verdict="pass", message="ok"),
        ])
        result = run_pass3(scenario_dir, "run-p3-2", pass1_result, manifest_path)
        assert result["classification"] == "workaround"
        assert result["structural_match"] is False

    def test_invalid_patch_string_in_diff_detected(self, tmp_path, monkeypatch):
        faulted_yaml = "Resources:\n  MyFn:\n    Properties:\n      Timeout: 3\n"
        submitted_yaml = "Resources:\n  MyFn:\n    Properties:\n      Timeout: 30\n"
        manifest = {
            "target_resource": "MyFn",
            "target_property": "Properties.Timeout",
            "original_value": 30,
            "invalid_patches": ["env_var_workaround"],
            "valid_fixes": [],
        }
        diff_text = "+      Timeout: 30\n+      env_var_workaround: true\n"
        scenario_dir, manifest_path, results_dir = self._setup(
            tmp_path, "run-p3-3", manifest, faulted_yaml, submitted_yaml, diff_text
        )
        monkeypatch.setattr(p3mod, "RESULTS_DIR", results_dir)
        pass1_result = AssertionRunResult(assertions=[
            AssertionResult(name="check_a", verdict="pass", message="ok"),
        ])
        result = run_pass3(scenario_dir, "run-p3-3", pass1_result, manifest_path)
        assert result["invalid_patch_detected"] is True
        assert result["classification"] != "root_cause"

    def test_partial_when_no_primary_assertions_pass(self, tmp_path, monkeypatch):
        faulted_yaml = "Resources:\n  MyFn:\n    Properties:\n      Timeout: 3\n"
        submitted_yaml = faulted_yaml
        manifest = {
            "target_resource": "MyFn",
            "target_property": "Properties.Timeout",
            "original_value": 30,
            "invalid_patches": [],
            "valid_fixes": [],
        }
        scenario_dir, manifest_path, results_dir = self._setup(
            tmp_path, "run-p3-4", manifest, faulted_yaml, submitted_yaml, ""
        )
        monkeypatch.setattr(p3mod, "RESULTS_DIR", results_dir)
        pass1_result = AssertionRunResult(assertions=[
            AssertionResult(name="check_a", verdict="fail", message="still broken"),
        ])
        result = run_pass3(scenario_dir, "run-p3-4", pass1_result, manifest_path)
        assert result["classification"] in ("partial", "none")

    def test_structural_match_with_list_indexed_path(self, tmp_path, monkeypatch):
        # target_property uses list-index syntax like "Foo[0].Bar[1].Baz" — the
        # navigator must descend into list elements, not just dict keys.
        faulted_yaml = (
            "Resources:\n"
            "  Role:\n"
            "    Properties:\n"
            "      Policies:\n"
            "        - PolicyName: P\n"
            "          PolicyDocument:\n"
            "            Statement:\n"
            "              - Effect: Allow\n"
            "                Action:\n"
            "                  - dynamodb:GetItem\n"
        )
        submitted_yaml = (
            "Resources:\n"
            "  Role:\n"
            "    Properties:\n"
            "      Policies:\n"
            "        - PolicyName: P\n"
            "          PolicyDocument:\n"
            "            Statement:\n"
            "              - Effect: Allow\n"
            "                Action:\n"
            "                  - dynamodb:GetItem\n"
            "                  - dynamodb:PutItem\n"
        )
        manifest = {
            "target_resource": "Role",
            "target_property": (
                "Properties.Policies[0].PolicyDocument.Statement[0].Action"
            ),
            "original_value": ["dynamodb:GetItem", "dynamodb:PutItem"],
            "invalid_patches": [],
            "valid_fixes": [],
        }
        scenario_dir, manifest_path, results_dir = self._setup(
            tmp_path,
            "run-p3-5",
            manifest,
            faulted_yaml,
            submitted_yaml,
            "+                  - dynamodb:PutItem\n",
        )
        monkeypatch.setattr(p3mod, "RESULTS_DIR", results_dir)
        pass1_result = AssertionRunResult(assertions=[
            AssertionResult(name="check_a", verdict="pass", message="ok"),
        ])
        result = run_pass3(scenario_dir, "run-p3-5", pass1_result, manifest_path)
        assert result["structural_match"] is True
        assert result["classification"] == "root_cause"

    def test_structural_match_false_when_list_index_value_differs(
        self, tmp_path, monkeypatch
    ):
        faulted_yaml = (
            "Resources:\n"
            "  Role:\n"
            "    Properties:\n"
            "      Policies:\n"
            "        - PolicyName: P\n"
            "          PolicyDocument:\n"
            "            Statement:\n"
            "              - Effect: Allow\n"
            "                Action:\n"
            "                  - dynamodb:GetItem\n"
        )
        submitted_yaml = faulted_yaml  # no fix applied
        manifest = {
            "target_resource": "Role",
            "target_property": (
                "Properties.Policies[0].PolicyDocument.Statement[0].Action"
            ),
            "original_value": ["dynamodb:GetItem", "dynamodb:PutItem"],
            "invalid_patches": [],
            "valid_fixes": [],
        }
        scenario_dir, manifest_path, results_dir = self._setup(
            tmp_path, "run-p3-6", manifest, faulted_yaml, submitted_yaml, ""
        )
        monkeypatch.setattr(p3mod, "RESULTS_DIR", results_dir)
        pass1_result = AssertionRunResult(assertions=[
            AssertionResult(name="check_a", verdict="fail", message=""),
        ])
        result = run_pass3(scenario_dir, "run-p3-6", pass1_result, manifest_path)
        assert result["structural_match"] is False


from unittest.mock import MagicMock, patch

from harness.verify.pass4_concurrency import run_pass4


class TestPass4Concurrency:
    def _make_manifest(self, tmp_path, run_id, manifest: dict):
        path = tmp_path / f"manifest_{run_id}.json"
        path.write_text(json.dumps(manifest))
        return str(path)

    def _mock_post(self, status_code):
        r = MagicMock()
        r.status_code = status_code
        return r

    def test_all_success_returns_passed_true(self, tmp_path):
        manifest_path = self._make_manifest(
            tmp_path, "p4-1", {"fault_class": "reliability", "concurrency_probe_n": 5}
        )
        with patch(
            "harness.verify.pass4_concurrency.requests.post",
            return_value=self._mock_post(200),
        ):
            result = run_pass4("scenario", manifest_path, "http://localhost:4566/test")
        assert result["requests_sent"] == 5
        assert result["success_count"] == 5
        assert result["throttled_count"] == 0
        assert result["timeout_count"] == 0
        assert result["passed"] is True

    def test_throttled_response_sets_passed_false(self, tmp_path):
        manifest_path = self._make_manifest(
            tmp_path, "p4-2", {"fault_class": "performance", "concurrency_probe_n": 4}
        )
        responses = [200, 200, 429, 200]
        idx = [0]

        def mock_post(*a, **kw):
            r = MagicMock()
            r.status_code = responses[idx[0] % len(responses)]
            idx[0] += 1
            return r

        with patch(
            "harness.verify.pass4_concurrency.requests.post", side_effect=mock_post
        ):
            result = run_pass4("scenario", manifest_path, "http://localhost:4566/test")
        assert result["throttled_count"] == 1
        assert result["passed"] is False

    def test_timeout_response_sets_passed_false(self, tmp_path):
        manifest_path = self._make_manifest(
            tmp_path, "p4-3", {"fault_class": "reliability", "concurrency_probe_n": 3}
        )
        with patch(
            "harness.verify.pass4_concurrency.requests.post",
            return_value=self._mock_post(504),
        ):
            result = run_pass4("scenario", manifest_path, "http://localhost:4566/test")
        assert result["timeout_count"] == 3
        assert result["passed"] is False

    def test_uses_default_n_10_when_field_absent(self, tmp_path):
        manifest_path = self._make_manifest(
            tmp_path, "p4-4", {"fault_class": "reliability"}
        )
        with patch(
            "harness.verify.pass4_concurrency.requests.post",
            return_value=self._mock_post(200),
        ):
            result = run_pass4("scenario", manifest_path, "http://localhost:4566/test")
        assert result["requests_sent"] == 10


import harness.verify.pass1_functional as p1mod
import harness.verify.pass2_regression as p2mod
import harness.verify.pass3_classification as p3mod
import harness.verify.pass4_concurrency as p4mod
import harness.verify.verify_loop as vlmod
from harness.verify.verify_loop import run_verify_loop


class TestVerifyLoop:
    def _write_baseline(self, results_dir, run_id, assertions):
        run_dir = Path(results_dir) / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        baseline = {
            "assertions": {
                n: {"result": v, "message": ""} for n, v in assertions.items()
            }
        }
        (run_dir / "faulted_baseline.json").write_text(json.dumps(baseline))

    def test_did_not_deploy_skips_all_passes(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vlmod, "log_verify_result", lambda *a, **kw: None)
        result = run_verify_loop("scenario", "run-v1", deployment_outcome="lint_fail")
        assert result["outcome"] == "did_not_deploy"
        assert result["pass1_functional"] is None
        assert result["pass2_regression"] is None
        assert result["pass3_classification"] is None
        assert result["pass4_concurrency"] is None

    def test_pass4_skipped_for_non_performance_fault_class(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vlmod, "log_verify_result", lambda *a, **kw: None)
        pass1 = AssertionRunResult(assertions=[
            AssertionResult(name="check_a", verdict="pass", message=""),
        ])
        pass2 = {
            "regression_count": 0,
            "regressions": [],
            "critical_regression_count": 0,
            "non_critical_regression_count": 0,
        }
        pass3 = {
            "structural_match": True,
            "invalid_patch_detected": False,
            "classification": "root_cause",
            "root_cause_addressed": True,
        }
        monkeypatch.setattr(p1mod, "run_pass1", lambda *a, **kw: pass1)
        monkeypatch.setattr(p2mod, "run_pass2", lambda *a, **kw: pass2)
        monkeypatch.setattr(p3mod, "run_pass3", lambda *a, **kw: pass3)
        manifest_path = str(tmp_path / "manifest.json")
        Path(manifest_path).write_text(json.dumps({"fault_class": "config"}))
        result = run_verify_loop(
            "scenario",
            "run-v2",
            deployment_outcome="deploy_success",
            manifest_path=manifest_path,
            corpus_dir="corpus",
            api_endpoint="http://localhost:4566",
        )
        assert result["pass4_concurrency"] is None

    def test_pass4_failure_overrides_pass3_to_partial(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vlmod, "log_verify_result", lambda *a, **kw: None)
        pass1 = AssertionRunResult(assertions=[
            AssertionResult(name="check_a", verdict="pass", message=""),
        ])
        pass2 = {
            "regression_count": 0,
            "regressions": [],
            "critical_regression_count": 0,
            "non_critical_regression_count": 0,
        }
        pass3 = {
            "structural_match": True,
            "invalid_patch_detected": False,
            "classification": "root_cause",
            "root_cause_addressed": True,
        }
        pass4 = {
            "requests_sent": 10,
            "success_count": 7,
            "throttled_count": 3,
            "timeout_count": 0,
            "error_count": 0,
            "passed": False,
        }
        monkeypatch.setattr(p1mod, "run_pass1", lambda *a, **kw: pass1)
        monkeypatch.setattr(p2mod, "run_pass2", lambda *a, **kw: pass2)
        monkeypatch.setattr(p3mod, "run_pass3", lambda *a, **kw: pass3)
        monkeypatch.setattr(p4mod, "run_pass4", lambda *a, **kw: pass4)
        manifest_path = str(tmp_path / "manifest.json")
        Path(manifest_path).write_text(
            json.dumps({"fault_class": "reliability", "concurrency_probe_n": 10})
        )
        result = run_verify_loop(
            "scenario",
            "run-v3",
            deployment_outcome="deploy_success",
            manifest_path=manifest_path,
            corpus_dir="corpus",
            api_endpoint="http://localhost:4566",
        )
        assert result["pass3_classification"]["classification"] == "partial"
        assert result["pass4_concurrency"]["passed"] is False


def test_pipeline_skips_step_when_should_run_false():
    from harness.verify.pipeline import VerifyContext, run_pipeline

    class AlwaysSkipStep:
        name = "skipper"
        def should_run(self, ctx): return False
        def run(self, ctx): raise AssertionError("should not run")

    class AlwaysRunStep:
        name = "runner"
        def should_run(self, ctx): return True
        def run(self, ctx): return {"done": True}

    ctx = VerifyContext(
        scenario_dir="", run_id="", manifest_path=None,
        corpus_dir="", api_endpoint="",
    )
    results = run_pipeline(ctx, steps=[AlwaysSkipStep(), AlwaysRunStep()], postprocessors=[])
    assert results["skipper"] is None
    assert results["runner"] == {"done": True}


def test_pipeline_postprocessor_mutates_results():
    from harness.verify.pipeline import VerifyContext, run_pipeline

    class StepA:
        name = "a"
        def should_run(self, ctx): return True
        def run(self, ctx): return {"x": 1}

    def double_x(ctx):
        ctx.results["a"]["x"] *= 2

    ctx = VerifyContext(
        scenario_dir="", run_id="", manifest_path=None,
        corpus_dir="", api_endpoint="",
    )
    results = run_pipeline(ctx, steps=[StepA()], postprocessors=[double_x])
    assert results["a"]["x"] == 2
