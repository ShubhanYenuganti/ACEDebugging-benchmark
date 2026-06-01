"""Deterministic tests for scripts/audit_memory_run.py (the live-run auditor).

Builds synthetic run directories so the auditor's PASS/FAIL logic is verified
without a live model or LocalStack.
"""
import importlib.util
import json
import pathlib

import pytest

_SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "audit_memory_run.py"


@pytest.fixture
def auditor(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("audit_memory_run", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "RESULTS_DIR", str(tmp_path))
    return mod, tmp_path


def _make_run(
    base,
    run_id="r1",
    *,
    db=False,
    memory_trace=None,
    tool_trace=None,
    score=True,
    edit_trace=True,
):
    d = pathlib.Path(base) / run_id
    d.mkdir(parents=True, exist_ok=True)
    if db:
        (d / "agent_memory.db").write_bytes(b"SQLite")
    (d / "memory_trace.json").write_text(json.dumps(
        memory_trace if memory_trace is not None else []
    ))
    (d / "tool_call_trace.json").write_text(json.dumps(tool_trace or []))
    if score:
        (d / "score.json").write_text(json.dumps({"final_score": 0.8}))
    if edit_trace:
        (d / "edit_trace.json").write_text(json.dumps([]))
    return run_id


def test_clean_run_passes(auditor):
    mod, base = auditor
    rid = _make_run(
        base,
        memory_trace=[{"turn": 1, "op": "write", "namespace": "obs", "key": "k"}],
        tool_trace=[{"turn": 2, "tool": "ace_invoke_lambda"}],
    )
    assert mod.audit(rid, require_memory=True) is True


def test_leftover_db_fails(auditor):
    mod, base = auditor
    rid = _make_run(base, db=True,
                    memory_trace=[{"turn": 1, "op": "write", "namespace": "n", "key": "k"}])
    assert mod.audit(rid, require_memory=False) is False


def test_memory_leak_into_tool_trace_fails(auditor):
    mod, base = auditor
    rid = _make_run(
        base,
        memory_trace=[{"turn": 1, "op": "write", "namespace": "n", "key": "k"}],
        tool_trace=[{"turn": 1, "tool": "memory_write"}],  # must never happen
    )
    assert mod.audit(rid, require_memory=False) is False


def test_missing_score_fails(auditor):
    mod, base = auditor
    rid = _make_run(base, score=False,
                    memory_trace=[{"turn": 1, "op": "write", "namespace": "n", "key": "k"}])
    assert mod.audit(rid, require_memory=False) is False


def test_require_memory_fails_when_no_writes(auditor):
    mod, base = auditor
    rid = _make_run(base, memory_trace=[{"turn": 1, "op": "read", "namespace": "", "key": ""}])
    assert mod.audit(rid, require_memory=True) is False


def test_zero_writes_warns_but_passes_without_strict(auditor):
    mod, base = auditor
    rid = _make_run(base, memory_trace=[])
    assert mod.audit(rid, require_memory=False) is True


def test_missing_run_dir_fails(auditor):
    mod, _ = auditor
    assert mod.audit("does-not-exist", require_memory=False) is False
