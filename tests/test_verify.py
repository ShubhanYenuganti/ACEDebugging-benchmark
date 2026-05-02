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
