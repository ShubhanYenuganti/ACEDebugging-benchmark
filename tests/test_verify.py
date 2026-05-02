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
