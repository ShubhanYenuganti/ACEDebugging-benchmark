import json
import pathlib

import pytest
from unittest.mock import patch, MagicMock


def make_mock_response(text: str):
    choice = MagicMock()
    choice.message.content = text
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def test_call_scoring_agent_returns_text():
    with patch("harness.scoring.agent.litellm.completion") as mock_completion:
        mock_completion.return_value = make_mock_response('{"score": 1.0}')
        from harness.scoring.agent import call_scoring_agent
        result = call_scoring_agent("sys", "user")
        assert result == '{"score": 1.0}'


def test_call_scoring_agent_propagates_api_error():
    with patch("harness.scoring.agent.litellm.completion") as mock_completion:
        mock_completion.side_effect = Exception("API error")
        from harness.scoring.agent import call_scoring_agent
        with pytest.raises(Exception, match="API error"):
            call_scoring_agent("sys", "user")


SAMPLE_MANIFEST = {
    "target_resource": "ProcessorLambdaESM",
    "target_property": "Enabled",
    "original_value": True,
    "injected_value": False,
    "optimal_tool_calls": 5,
    "optimal_files_changed": 1,
    "optimal_lines_changed": 3,
    "valid_fixes": [{"Enabled": True}],
    "invalid_patches": [],
    "fault_class": "config",
}

SAMPLE_VERIFY = {
    "outcome": "completed",
    "pass1_functional": {
        "all_assertions_passed": True,
        "primary_assertions_passed": True,
        "failed_assertion_names": [],
        "assertions": ["a", "b"],
    },
    "pass2_regression": {
        "critical_regression_count": 0,
        "non_critical_regression_count": 0,
        "regression_count": 0,
    },
    "pass3_classification": {
        "classification": "root_cause",
        "structural_match": True,
        "invalid_patch_detected": False,
    },
    "pass4_concurrency": None,
}

TRACE_WITH_TARGET = [
    {"turn": 1, "tool": "ace_get_event_source", "input": {"function_name": "ace-bench-processor"}, "output": "{}"},
    {"turn": 2, "tool": "ace_update_template", "input": {}, "output": "{}"},
]

TRACE_WITHOUT_TARGET = [
    {"turn": 1, "tool": "ace_invoke_endpoint", "input": {}, "output": "{}"},
    {"turn": 2, "tool": "ace_update_template", "input": {}, "output": "{}"},
]

KNOWN_GOOD_YAML = "Resources:\n  ProcessorLambdaESM:\n    Type: AWS::Lambda::EventSourceMapping"
TRAFFIC_FLOW_MD = "Hop 4: SQS triggers Processor Lambda via EventSourceMapping."


def test_identification_prompt_differs_by_trace():
    with patch("harness.scoring.dimensions.identification.call_scoring_agent") as mock_agent:
        mock_agent.return_value = '{"score": 0.5, "rationale": "test"}'
        from harness.scoring.dimensions.identification import score

        score(TRACE_WITH_TARGET, SAMPLE_MANIFEST, SAMPLE_VERIFY, KNOWN_GOOD_YAML, TRAFFIC_FLOW_MD)
        prompt_with = mock_agent.call_args[0][1]

        mock_agent.return_value = '{"score": 0.0, "rationale": "test"}'
        score(TRACE_WITHOUT_TARGET, SAMPLE_MANIFEST, SAMPLE_VERIFY, KNOWN_GOOD_YAML, TRAFFIC_FLOW_MD)
        prompt_without = mock_agent.call_args[0][1]

    assert prompt_with != prompt_without


def test_identification_parses_agent_response():
    with patch("harness.scoring.dimensions.identification.call_scoring_agent") as mock_agent:
        mock_agent.return_value = '{"score": 0.5, "rationale": "correct resource found"}'
        from harness.scoring.dimensions.identification import score
        result = score(TRACE_WITH_TARGET, SAMPLE_MANIFEST, SAMPLE_VERIFY, KNOWN_GOOD_YAML, TRAFFIC_FLOW_MD)

    assert result["score"] == 0.5
    assert result["rationale"] == "correct resource found"


def test_identification_known_good_in_prompt():
    with patch("harness.scoring.dimensions.identification.call_scoring_agent") as mock_agent:
        mock_agent.return_value = '{"score": 1.0, "rationale": "ok"}'
        from harness.scoring.dimensions.identification import score
        score(TRACE_WITH_TARGET, SAMPLE_MANIFEST, SAMPLE_VERIFY, KNOWN_GOOD_YAML, TRAFFIC_FLOW_MD)
        prompt = mock_agent.call_args[0][1]

    assert "known_good.yaml" in prompt.lower() or "ProcessorLambdaESM" in prompt
    assert "traffic_flow" in prompt.lower() or "Hop 4" in prompt


def _make_verify(all_passed, primary_passed, failed_names, total_count, crit_reg=0, noncrit_reg=0):
    return {
        "outcome": "completed",
        "pass1_functional": {
            "all_assertions_passed": all_passed,
            "primary_assertions_passed": primary_passed,
            "failed_assertion_names": failed_names,
            "assertions": [f"a{i}" for i in range(total_count)],
        },
        "pass2_regression": {
            "critical_regression_count": crit_reg,
            "non_critical_regression_count": noncrit_reg,
            "regression_count": crit_reg + noncrit_reg,
        },
        "pass3_classification": {
            "classification": "root_cause",
            "structural_match": True,
            "invalid_patch_detected": False,
        },
        "pass4_concurrency": None,
    }


def test_fix_correctness_scores_deterministic():
    from harness.scoring.dimensions.fix_correctness import score

    assert score(_make_verify(True, True, [], 2))["score"] == 1.0
    assert score(_make_verify(False, True, ["b"], 2))["score"] == 0.6
    assert score(_make_verify(False, False, ["a"], 2))["score"] == 0.3
    assert score(_make_verify(False, False, ["a", "b"], 2))["score"] == 0.0


def test_fix_correctness_rationale_is_string():
    from harness.scoring.dimensions.fix_correctness import score
    r = score(_make_verify(True, True, [], 2))
    assert isinstance(r["rationale"], str) and len(r["rationale"]) > 0


def test_regression_penalties_deterministic():
    from harness.scoring.dimensions.regression import compute

    cases = [
        (_make_verify(True, True, [], 2, 0, 0), 0.00),
        (_make_verify(True, True, [], 2, 0, 1), 0.08),
        (_make_verify(True, True, [], 2, 1, 0), 0.18),
        (_make_verify(True, True, [], 2, 1, 1), 0.28),
        (_make_verify(True, True, [], 2, 2, 0), 0.28),
    ]
    for verify, expected_penalty in cases:
        r = compute(verify)
        assert r["penalty"] == expected_penalty, f"expected {expected_penalty}, got {r['penalty']}"


def test_regression_rationale_is_string():
    from harness.scoring.dimensions.regression import compute
    r = compute(_make_verify(True, True, [], 2, 1, 0))
    assert isinstance(r["rationale"], str) and len(r["rationale"]) > 0


SAMPLE_FILE_LOG = {
    "total_files_changed": 1,
    "total_lines_changed": 3,
    "files_modified": ["harness/scenarios/arch_01/known_good.yaml"],
    "files_added": [],
    "per_file_line_changes": {"harness/scenarios/arch_01/known_good.yaml": 3},
}

SAMPLE_TRACE = [
    {"turn": i, "tool": f"tool_{i}", "input": {}, "output": "{}"} for i in range(6)
]


def test_threshold_score_curve():
    from harness.scoring.dimensions.efficiency import threshold_score
    assert threshold_score(5, 5) == 1.0                        # ratio 1.0
    assert threshold_score(7, 5) == 1.0                        # ratio 1.4 <= 1.5
    assert threshold_score(10, 5) == pytest.approx(0.8, abs=0.001)   # ratio 2.0
    assert threshold_score(12, 5) == pytest.approx(0.64, abs=0.001)  # ratio 2.4
    assert threshold_score(15, 5) == pytest.approx(0.4, abs=0.001)   # ratio 3.0
    assert threshold_score(20, 5) == pytest.approx(0.0, abs=0.001)   # ratio 4.0
    assert threshold_score(25, 5) == 0.0                       # ratio 5.0


def test_efficiency_combined_score():
    with patch("harness.scoring.dimensions.efficiency.call_scoring_agent") as mock_agent:
        mock_agent.return_value = '{"rationale": "efficient"}'
        from harness.scoring.dimensions.efficiency import score

        # actual == optimal across the board -> all sub-scores 1.0 -> combined 1.0
        manifest = dict(SAMPLE_MANIFEST)
        manifest["optimal_tool_calls"] = len(SAMPLE_TRACE)
        manifest["optimal_files_changed"] = SAMPLE_FILE_LOG["total_files_changed"]
        manifest["optimal_lines_changed"] = SAMPLE_FILE_LOG["total_lines_changed"]

        r = score(SAMPLE_TRACE, SAMPLE_FILE_LOG, manifest, KNOWN_GOOD_YAML, TRAFFIC_FLOW_MD)
        assert r["score"] == 1.0
        assert "tool_calls" in r
        assert "files_changed" in r
        assert "lines_changed" in r


def test_efficiency_agent_rationale_called_once():
    with patch("harness.scoring.dimensions.efficiency.call_scoring_agent") as mock_agent:
        mock_agent.return_value = '{"rationale": "some explanation"}'
        from harness.scoring.dimensions.efficiency import score
        r = score(SAMPLE_TRACE, SAMPLE_FILE_LOG, SAMPLE_MANIFEST, KNOWN_GOOD_YAML, TRAFFIC_FLOW_MD)

    mock_agent.assert_called_once()
    assert r["rationale"] == "some explanation"


def test_efficiency_prompt_includes_context():
    with patch("harness.scoring.dimensions.efficiency.call_scoring_agent") as mock_agent:
        mock_agent.return_value = '{"rationale": "ok"}'
        from harness.scoring.dimensions.efficiency import score
        score(SAMPLE_TRACE, SAMPLE_FILE_LOG, SAMPLE_MANIFEST, KNOWN_GOOD_YAML, TRAFFIC_FLOW_MD)
        prompt = mock_agent.call_args[0][1]

    assert "known_good.yaml" in prompt.lower() or "ProcessorLambdaESM" in prompt
    assert "traffic_flow" in prompt.lower() or "Hop 4" in prompt


def _make_verify_for_gate(classification, primary_passed, regression_count):
    return {
        "outcome": "completed",
        "pass1_functional": {
            "all_assertions_passed": primary_passed and regression_count == 0,
            "primary_assertions_passed": primary_passed,
            "failed_assertion_names": [],
            "assertions": ["a"],
        },
        "pass2_regression": {
            "critical_regression_count": regression_count,
            "non_critical_regression_count": 0,
            "regression_count": regression_count,
        },
        "pass3_classification": {
            "classification": classification,
            "structural_match": classification == "root_cause",
            "invalid_patch_detected": classification not in ("root_cause", "workaround"),
        },
        "pass4_concurrency": None,
    }


def test_check_gate_passes_for_clean_root_cause():
    from harness.scoring.dimensions.quality import check_gate
    v = _make_verify_for_gate("root_cause", True, 0)
    assert check_gate(v) is True


def test_check_gate_fails_bad_classification():
    from harness.scoring.dimensions.quality import check_gate
    for cls in ("partial", "none", "unknown"):
        v = _make_verify_for_gate(cls, True, 0)
        assert check_gate(v) is False, f"expected False for classification={cls}"


def test_check_gate_fails_primary_not_passed():
    from harness.scoring.dimensions.quality import check_gate
    v = _make_verify_for_gate("root_cause", False, 0)
    assert check_gate(v) is False


def test_check_gate_fails_with_regression():
    from harness.scoring.dimensions.quality import check_gate
    v = _make_verify_for_gate("root_cause", True, 1)
    assert check_gate(v) is False


def test_quality_score_parses_agent_response():
    with patch("harness.scoring.dimensions.quality.call_scoring_agent") as mock_agent:
        mock_agent.return_value = '{"score": 0.85, "classification": "root_cause", "rationale": "clean fix"}'
        from harness.scoring.dimensions.quality import score
        r = score(SAMPLE_VERIFY, SAMPLE_MANIFEST, SAMPLE_FILE_LOG, KNOWN_GOOD_YAML, TRAFFIC_FLOW_MD)

    assert r["score"] == 0.85
    assert r["classification"] == "root_cause"
    assert r["rationale"] == "clean fix"


def test_quality_prompt_includes_context():
    with patch("harness.scoring.dimensions.quality.call_scoring_agent") as mock_agent:
        mock_agent.return_value = '{"score": 1.0, "classification": "root_cause", "rationale": "ok"}'
        from harness.scoring.dimensions.quality import score
        score(SAMPLE_VERIFY, SAMPLE_MANIFEST, SAMPLE_FILE_LOG, KNOWN_GOOD_YAML, TRAFFIC_FLOW_MD)
        prompt = mock_agent.call_args[0][1]

    assert "known_good.yaml" in prompt.lower() or "ProcessorLambdaESM" in prompt
    assert "traffic_flow" in prompt.lower() or "Hop 4" in prompt
    assert "valid_fixes" in prompt or str(SAMPLE_MANIFEST["valid_fixes"]) in prompt


def _write_file(path: pathlib.Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _make_run_dir(base: pathlib.Path, run_id: str, scenario_id: str, verify: dict, trace: list, file_log: dict) -> pathlib.Path:
    run_dir = base / "results" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "scenario_id.txt").write_text(scenario_id)
    (run_dir / "verify_result.json").write_text(json.dumps(verify))
    (run_dir / "tool_call_trace.json").write_text(json.dumps(trace))
    (run_dir / "file_change_log.json").write_text(json.dumps(file_log))
    return run_dir


def _make_scenario_dir(base: pathlib.Path, scenario_id: str, manifest: dict, faulted_yaml: str) -> pathlib.Path:
    s_dir = base / "scenarios" / scenario_id
    s_dir.mkdir(parents=True)
    (s_dir / "fault_manifest.json").write_text(json.dumps(manifest))
    (s_dir / "faulted.yaml").write_text(faulted_yaml)
    return s_dir


def _make_corpus_dir(base: pathlib.Path, arch_id: str, known_good: str, traffic_flow: str) -> pathlib.Path:
    c_dir = base / "corpus" / arch_id
    c_dir.mkdir(parents=True)
    (c_dir / "known_good.yaml").write_text(known_good)
    (c_dir / "traffic_flow.md").write_text(traffic_flow)
    return c_dir


@patch("harness.scoring.dimensions.identification.call_scoring_agent")
@patch("harness.scoring.dimensions.efficiency.call_scoring_agent")
@patch("harness.scoring.dimensions.quality.call_scoring_agent")
def test_scorer_writes_score_json(mock_q, mock_e, mock_id, tmp_path):
    mock_id.return_value = '{"score": 1.0, "rationale": "identified"}'
    mock_e.return_value = '{"rationale": "efficient"}'
    mock_q.return_value = '{"score": 1.0, "classification": "root_cause", "rationale": "clean"}'

    scenario_id = "arch_01_order_processing_fault_01"
    arch_id = "arch_01_order_processing"
    run_id = "test-run-001"

    _make_run_dir(tmp_path, run_id, scenario_id, SAMPLE_VERIFY, SAMPLE_TRACE, SAMPLE_FILE_LOG)
    _make_scenario_dir(tmp_path, scenario_id, SAMPLE_MANIFEST, "faulted: yaml")
    _make_corpus_dir(tmp_path, arch_id, KNOWN_GOOD_YAML, TRAFFIC_FLOW_MD)

    from harness.scoring.scorer import score_run
    score_run(run_id, str(tmp_path))

    score_path = tmp_path / "results" / run_id / "score.json"
    assert score_path.exists(), "score.json was not written"

    written = json.loads(score_path.read_text())
    assert written["run_id"] == run_id
    assert written["final_score"] >= 0.0
    assert "dimensions" in written
    assert "identification" in written["dimensions"]
    assert "fix_correctness" in written["dimensions"]
    assert "regression_penalty" in written["dimensions"]
    assert "efficiency" in written["dimensions"]
    assert "quality" in written["dimensions"]


@patch("harness.scoring.dimensions.identification.call_scoring_agent")
@patch("harness.scoring.dimensions.efficiency.call_scoring_agent")
@patch("harness.scoring.dimensions.quality.call_scoring_agent")
def test_scorer_zero_on_did_not_deploy(mock_q, mock_e, mock_id, tmp_path):
    scenario_id = "arch_01_order_processing_fault_01"
    arch_id = "arch_01_order_processing"
    run_id = "test-run-002"
    verify_failed = dict(SAMPLE_VERIFY, outcome="did_not_deploy")

    _make_run_dir(tmp_path, run_id, scenario_id, verify_failed, SAMPLE_TRACE, SAMPLE_FILE_LOG)
    _make_scenario_dir(tmp_path, scenario_id, SAMPLE_MANIFEST, "faulted: yaml")
    _make_corpus_dir(tmp_path, arch_id, KNOWN_GOOD_YAML, TRAFFIC_FLOW_MD)

    from harness.scoring.scorer import score_run
    result = score_run(run_id, str(tmp_path))

    mock_id.assert_not_called()
    assert result["final_score"] == 0.0
    assert result["zero_reason"] == "did_not_deploy"
