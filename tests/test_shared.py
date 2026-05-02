import harness.shared.localstack_client as lsc
import pytest
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
