import pytest

from harness.shared import iam_enforcement


def test_assert_raises_when_enforcement_off(monkeypatch):
    monkeypatch.setattr(iam_enforcement, "iam_enforcement_active", lambda: False)
    with pytest.raises(RuntimeError, match="IAM enforcement"):
        iam_enforcement.assert_iam_enforcement()


def test_assert_passes_when_enforcement_on(monkeypatch):
    monkeypatch.setattr(iam_enforcement, "iam_enforcement_active", lambda: True)
    iam_enforcement.assert_iam_enforcement()  # must not raise
