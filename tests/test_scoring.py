import pytest
from unittest.mock import patch, MagicMock


def make_mock_response(text: str):
    msg = MagicMock()
    msg.content = [MagicMock(text=text)]
    return msg


def test_call_scoring_agent_returns_text():
    with patch("harness.scoring.agent.client") as mock_client:
        mock_client.messages.create.return_value = make_mock_response('{"score": 1.0}')
        from harness.scoring.agent import call_scoring_agent
        result = call_scoring_agent("sys", "user")
        assert result == '{"score": 1.0}'


def test_call_scoring_agent_propagates_api_error():
    with patch("harness.scoring.agent.client") as mock_client:
        mock_client.messages.create.side_effect = Exception("API error")
        from harness.scoring.agent import call_scoring_agent
        with pytest.raises(Exception, match="API error"):
            call_scoring_agent("sys", "user")


import json

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
    with patch("harness.scoring.dimensions.fix_correctness.call_scoring_agent") as mock_agent:
        mock_agent.return_value = '{"rationale": "ok"}'
        from harness.scoring.dimensions.fix_correctness import score

        # 1.0 — all passed
        r = score(_make_verify(True, True, [], 2), SAMPLE_MANIFEST, KNOWN_GOOD_YAML, TRAFFIC_FLOW_MD)
        assert r["score"] == 1.0

        # 0.6 — primary passed, secondaries failed
        r = score(_make_verify(False, True, ["b"], 2), SAMPLE_MANIFEST, KNOWN_GOOD_YAML, TRAFFIC_FLOW_MD)
        assert r["score"] == 0.6

        # 0.3 — partial (some passed, primary failed)
        r = score(_make_verify(False, False, ["a"], 2), SAMPLE_MANIFEST, KNOWN_GOOD_YAML, TRAFFIC_FLOW_MD)
        assert r["score"] == 0.3

        # 0.0 — nothing passed
        r = score(_make_verify(False, False, ["a", "b"], 2), SAMPLE_MANIFEST, KNOWN_GOOD_YAML, TRAFFIC_FLOW_MD)
        assert r["score"] == 0.0


def test_fix_correctness_calls_agent_for_rationale():
    with patch("harness.scoring.dimensions.fix_correctness.call_scoring_agent") as mock_agent:
        mock_agent.return_value = '{"rationale": "all assertions passed cleanly"}'
        from harness.scoring.dimensions.fix_correctness import score
        r = score(_make_verify(True, True, [], 2), SAMPLE_MANIFEST, KNOWN_GOOD_YAML, TRAFFIC_FLOW_MD)

    mock_agent.assert_called_once()
    assert r["rationale"] == "all assertions passed cleanly"


def manifest_resource_in_prompt(prompt, manifest):
    return manifest["target_resource"] in prompt


def test_fix_correctness_prompt_includes_context():
    with patch("harness.scoring.dimensions.fix_correctness.call_scoring_agent") as mock_agent:
        mock_agent.return_value = '{"rationale": "ok"}'
        from harness.scoring.dimensions.fix_correctness import score
        score(_make_verify(True, True, [], 2), SAMPLE_MANIFEST, KNOWN_GOOD_YAML, TRAFFIC_FLOW_MD)
        prompt = mock_agent.call_args[0][1]

    assert "known_good.yaml" in prompt.lower() or "ProcessorLambdaESM" in prompt
    assert "traffic_flow" in prompt.lower() or "Hop 4" in prompt
    assert manifest_resource_in_prompt(prompt, SAMPLE_MANIFEST)


def test_regression_penalties_deterministic():
    with patch("harness.scoring.dimensions.regression.call_scoring_agent") as mock_agent:
        mock_agent.return_value = '{"rationale": "ok"}'
        from harness.scoring.dimensions.regression import compute

        cases = [
            (_make_verify(True, True, [], 2, 0, 0), 0.00),
            (_make_verify(True, True, [], 2, 0, 1), 0.08),
            (_make_verify(True, True, [], 2, 1, 0), 0.18),
            (_make_verify(True, True, [], 2, 1, 1), 0.28),
            (_make_verify(True, True, [], 2, 2, 0), 0.28),
        ]
        for verify, expected_penalty in cases:
            r = compute(verify, SAMPLE_MANIFEST, KNOWN_GOOD_YAML, TRAFFIC_FLOW_MD)
            assert r["penalty"] == expected_penalty, f"expected {expected_penalty}, got {r['penalty']}"


def test_regression_calls_agent_for_rationale():
    with patch("harness.scoring.dimensions.regression.call_scoring_agent") as mock_agent:
        mock_agent.return_value = '{"rationale": "one critical regression on hop 4"}'
        from harness.scoring.dimensions.regression import compute
        r = compute(_make_verify(True, True, [], 2, 1, 0), SAMPLE_MANIFEST, KNOWN_GOOD_YAML, TRAFFIC_FLOW_MD)

    mock_agent.assert_called_once()
    assert r["rationale"] == "one critical regression on hop 4"


def test_regression_no_agent_call_when_no_regressions():
    """When penalty is 0.0, we still call agent but it returns a no-regression rationale."""
    with patch("harness.scoring.dimensions.regression.call_scoring_agent") as mock_agent:
        mock_agent.return_value = '{"rationale": "no regressions"}'
        from harness.scoring.dimensions.regression import compute
        r = compute(_make_verify(True, True, [], 2, 0, 0), SAMPLE_MANIFEST, KNOWN_GOOD_YAML, TRAFFIC_FLOW_MD)

    mock_agent.assert_called_once()
    assert r["penalty"] == 0.0
    assert r["rationale"] == "no regressions"


def test_regression_prompt_includes_context():
    with patch("harness.scoring.dimensions.regression.call_scoring_agent") as mock_agent:
        mock_agent.return_value = '{"rationale": "ok"}'
        from harness.scoring.dimensions.regression import compute
        compute(_make_verify(True, True, [], 2, 1, 0), SAMPLE_MANIFEST, KNOWN_GOOD_YAML, TRAFFIC_FLOW_MD)
        prompt = mock_agent.call_args[0][1]

    assert "known_good.yaml" in prompt.lower() or "ProcessorLambdaESM" in prompt
    assert "traffic_flow" in prompt.lower() or "Hop 4" in prompt
