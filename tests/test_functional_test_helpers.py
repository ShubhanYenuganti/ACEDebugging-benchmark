import json
import os
import subprocess
import sys
import textwrap

import harness.shared.functional_test_helpers as h


def _reset():
    h._buffer.clear()
    h._finalized = False


def test_emit_pass_buffers_entry():
    _reset()
    h.emit_pass("a_check", "ok")
    assert h._buffer == [{"name": "a_check", "verdict": "pass", "message": "ok"}]


def test_emit_fail_buffers_entry():
    _reset()
    h.emit_fail("b_check", "bad value")
    assert h._buffer[0]["verdict"] == "fail"


def test_finalize_writes_json_when_env_set(tmp_path, monkeypatch):
    _reset()
    out = tmp_path / "results.json"
    monkeypatch.setenv("ACE_BENCH_RESULTS_PATH", str(out))
    h.emit_pass("a")
    h.emit_fail("b", "msg")
    h.finalize()
    assert out.exists()
    data = json.loads(out.read_text())
    assert data["assertions"][0] == {"name": "a", "verdict": "pass", "message": ""}
    assert data["assertions"][1] == {"name": "b", "verdict": "fail", "message": "msg"}


def test_finalize_noop_when_env_unset(tmp_path, monkeypatch):
    _reset()
    monkeypatch.delenv("ACE_BENCH_RESULTS_PATH", raising=False)
    h.emit_pass("a")
    h.finalize()


def test_finalize_idempotent(tmp_path, monkeypatch):
    _reset()
    out = tmp_path / "r.json"
    monkeypatch.setenv("ACE_BENCH_RESULTS_PATH", str(out))
    h.emit_pass("a")
    h.finalize()
    h._buffer.append({"name": "c", "verdict": "pass", "message": ""})
    h.finalize()
    data = json.loads(out.read_text())
    assert len(data["assertions"]) == 1


def test_atexit_writes_file_when_test_forgets_finalize(tmp_path):
    """atexit should fire and write the JSON file even without an explicit finalize()."""
    out = tmp_path / "r.json"
    script = tmp_path / "t.py"
    script.write_text(textwrap.dedent("""
        from harness.shared.functional_test_helpers import emit_pass
        emit_pass("a")
    """))
    env = {**os.environ, "ACE_BENCH_RESULTS_PATH": str(out)}
    subprocess.run(
        [sys.executable, str(script)],
        env=env,
        check=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    data = json.loads(out.read_text())
    assert data["assertions"][0]["name"] == "a"
