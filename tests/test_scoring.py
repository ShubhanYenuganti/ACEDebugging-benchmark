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
