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
