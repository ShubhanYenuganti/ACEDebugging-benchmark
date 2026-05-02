import json
import os

import pytest

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
        assert result["assertions"]["connectivity"]["result"] == "pass"
        assert result["assertions"]["auth_check"]["result"] == "fail"
        assert result["assertions"]["data_write"]["result"] == "pass"
        assert result["failed_assertion_names"] == ["auth_check"]

    def test_primary_assertions_passed_excludes_secondary(self, tmp_path):
        output = "ASSERT pass main_check: ok\nASSERT fail latency_secondary: too slow\n"
        corpus_dir = self._make_corpus(tmp_path, output)
        result = run_pass1(corpus_dir)
        assert result["primary_assertions_passed"] is True
        assert result["all_assertions_passed"] is False
        assert "latency_secondary" in result["failed_assertion_names"]

    def test_all_assertions_passed_true_when_all_pass(self, tmp_path):
        output = "ASSERT pass check_a: ok\nASSERT pass check_b: ok\n"
        corpus_dir = self._make_corpus(tmp_path, output)
        result = run_pass1(corpus_dir)
        assert result["primary_assertions_passed"] is True
        assert result["all_assertions_passed"] is True
        assert result["failed_assertion_names"] == []

    def test_primary_assertions_passed_false_when_primary_fails(self, tmp_path):
        output = (
            "ASSERT fail main_check: broken\nASSERT pass side_check_secondary: ok\n"
        )
        corpus_dir = self._make_corpus(tmp_path, output)
        result = run_pass1(corpus_dir)
        assert result["primary_assertions_passed"] is False


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
        pass1_result = {
            "assertions": {
                "check_a": {"result": "pass", "message": ""},
                "check_b": {"result": "fail", "message": "broke"},
                "check_c": {"result": "fail", "message": "still broken"},
            }
        }
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
        pass1_result = {
            "assertions": {"check_secondary": {"result": "fail", "message": ""}}
        }
        result = run_pass2("scenario", "run-r2", pass1_result)
        assert result["critical_regression_count"] == 0
        assert result["non_critical_regression_count"] == 1

    def test_no_regressions_when_all_stable(self, tmp_path, monkeypatch):
        results_dir = str(tmp_path / "results")
        self._write_baseline(
            tmp_path, "run-r3", {"check_a": "fail", "check_b": "pass"}, results_dir
        )
        monkeypatch.setattr(p2mod, "RESULTS_DIR", results_dir)
        pass1_result = {
            "assertions": {
                "check_a": {"result": "pass", "message": "fixed"},
                "check_b": {"result": "pass", "message": "still ok"},
            }
        }
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
        pass1_result = {
            "primary_assertions_passed": True,
            "assertions": {"check_a": {"result": "pass", "message": "ok"}},
        }
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
        pass1_result = {
            "primary_assertions_passed": True,
            "assertions": {"check_a": {"result": "pass", "message": "ok"}},
        }
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
        pass1_result = {
            "primary_assertions_passed": True,
            "assertions": {"check_a": {"result": "pass", "message": "ok"}},
        }
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
        pass1_result = {
            "primary_assertions_passed": False,
            "assertions": {"check_a": {"result": "fail", "message": "still broken"}},
        }
        result = run_pass3(scenario_dir, "run-p3-4", pass1_result, manifest_path)
        assert result["classification"] in ("partial", "none")
