"""Phase G — agent-managed memory layer (MemoryStore + dispatch + teardown)."""
import os

import pytest

from harness.agent.memory import (
    CONTENT_MAX_BYTES,
    MAX_ROWS,
    NS_KEY_MAX_CHARS,
    MemoryStore,
    teardown_memory,
)


def _store(tmp_path, run_id="run-aaa", enable_fts=True):
    return MemoryStore(str(tmp_path / "agent_memory.db"), run_id, enable_fts=enable_fts)


# ── write / read ──────────────────────────────────────────────────────────────

def test_write_and_read_roundtrip(tmp_path):
    s = _store(tmp_path)
    msg = s.write("tried_fixes", "attempt-1", "Removed FilterCriteria; test still failed.")
    assert "Error" not in msg
    rows = s.read("tried_fixes")
    assert len(rows) == 1
    assert rows[0]["namespace"] == "tried_fixes"
    assert rows[0]["key"] == "attempt-1"
    assert "FilterCriteria" in rows[0]["content"]
    s.close()


def test_run_id_is_stamped(tmp_path):
    s = _store(tmp_path, run_id="deadbeef")
    s.write("obs", "k", "v")
    assert s.read("obs")[0]["run_id"] == "deadbeef"
    s.close()


def test_upsert_replaces_content_single_row(tmp_path):
    s = _store(tmp_path)
    s.write("ns", "k", "first")
    s.write("ns", "k", "second")
    rows = s.read("ns")
    assert len(rows) == 1
    assert rows[0]["content"] == "second"
    s.close()


def test_namespace_isolation(tmp_path):
    s = _store(tmp_path)
    s.write("a", "k1", "alpha")
    s.write("b", "k2", "beta")
    rows = s.read("a")
    assert len(rows) == 1
    assert rows[0]["content"] == "alpha"
    s.close()


def test_read_none_lists_namespaces_with_counts(tmp_path):
    s = _store(tmp_path)
    s.write("a", "k1", "x")
    s.write("a", "k2", "y")
    s.write("b", "k3", "z")
    summary = {r["namespace"]: r["count"] for r in s.read(None)}
    assert summary == {"a": 2, "b": 1}
    s.close()


def test_read_newest_first(tmp_path):
    s = _store(tmp_path)
    s.write("ns", "old", "1")
    s.write("ns", "new", "2")
    rows = s.read("ns")
    assert [r["key"] for r in rows] == ["new", "old"]
    s.close()


def test_read_unknown_namespace_returns_empty(tmp_path):
    s = _store(tmp_path)
    assert s.read("nope") == []
    s.close()


# ── search ────────────────────────────────────────────────────────────────────

def test_search_hit_and_miss(tmp_path):
    s = _store(tmp_path)
    s.write("obs", "k1", "IAM role missing dynamodb:PutItem permission")
    s.write("obs", "k2", "queue depth nominal")
    hits = s.search("dynamodb")
    assert any("PutItem" in r["content"] for r in hits)
    assert s.search("kinesis") == []
    s.close()


def test_search_namespace_filter(tmp_path):
    s = _store(tmp_path)
    s.write("a", "k1", "throttle detected")
    s.write("b", "k2", "throttle detected")
    hits = s.search("throttle", namespace="a")
    assert len(hits) == 1
    assert hits[0]["namespace"] == "a"
    s.close()


def test_search_like_fallback_returns_results(tmp_path):
    s = _store(tmp_path, enable_fts=False)
    assert s.fts_enabled is False
    s.write("obs", "k", "EventSourceMapping disabled")
    hits = s.search("EventSourceMapping")
    assert len(hits) == 1
    s.close()


def test_search_fts_path_when_available(tmp_path):
    s = _store(tmp_path, enable_fts=True)
    s.write("obs", "k", "lambda concurrency limit reached")
    hits = s.search("concurrency")
    assert len(hits) == 1
    s.close()


# ── validation / limits ───────────────────────────────────────────────────────

def test_write_rejects_empty_namespace_or_key(tmp_path):
    s = _store(tmp_path)
    assert "Error" in s.write("", "k", "v")
    assert "Error" in s.write("ns", "", "v")
    assert s.read(None) == []
    s.close()


def test_write_rejects_oversize_content(tmp_path):
    s = _store(tmp_path)
    big = "x" * (CONTENT_MAX_BYTES + 1)
    assert "Error" in s.write("ns", "k", big)
    assert s.read("ns") == []
    s.close()


def test_write_rejects_oversize_key(tmp_path):
    s = _store(tmp_path)
    assert "Error" in s.write("ns", "k" * (NS_KEY_MAX_CHARS + 1), "v")
    s.close()


def test_read_and_search_cap_at_max_rows(tmp_path):
    s = _store(tmp_path)
    for i in range(MAX_ROWS + 10):
        s.write("ns", f"k{i:03d}", "needle payload")
    assert len(s.read("ns")) == MAX_ROWS
    assert len(s.search("needle")) == MAX_ROWS
    s.close()


# ── teardown ──────────────────────────────────────────────────────────────────

def test_teardown_removes_files_and_is_idempotent(tmp_path):
    path = str(tmp_path / "agent_memory.db")
    s = MemoryStore(path, "run-x")
    s.write("ns", "k", "v")
    s.close()
    assert os.path.exists(path)
    teardown_memory(path)
    assert not os.path.exists(path)
    assert not os.path.exists(path + "-wal")
    assert not os.path.exists(path + "-shm")
    teardown_memory(path)  # idempotent, no raise


# ── dispatch_memory_tool ──────────────────────────────────────────────────────

def _dispatch(tmp_path):
    from harness.agent.tools import dispatch_memory_tool
    s = _store(tmp_path)
    return dispatch_memory_tool, s


def test_dispatch_write_then_read(tmp_path):
    d, s = _dispatch(tmp_path)
    msg = d("memory_write", {"namespace": "obs", "key": "k1", "content": "throttling seen"}, s)
    assert "Saved" in msg
    out = d("memory_read", {"namespace": "obs"}, s)
    assert "throttling seen" in out
    assert "[obs/k1]" in out
    s.close()


def test_dispatch_write_stamps_run_id(tmp_path):
    from harness.agent.tools import dispatch_memory_tool
    s = _store(tmp_path, run_id="run-zzz")
    dispatch_memory_tool("memory_write", {"namespace": "n", "key": "k", "content": "c"}, s)
    assert s.read("n")[0]["run_id"] == "run-zzz"
    s.close()


def test_dispatch_read_all_lists_namespaces(tmp_path):
    d, s = _dispatch(tmp_path)
    d("memory_write", {"namespace": "a", "key": "k", "content": "x"}, s)
    out = d("memory_read", {}, s)
    assert "Namespaces:" in out and "a (1)" in out
    s.close()


def test_dispatch_read_empty(tmp_path):
    d, s = _dispatch(tmp_path)
    assert d("memory_read", {}, s) == "(memory is empty)"
    s.close()


def test_dispatch_search_hit_and_miss(tmp_path):
    d, s = _dispatch(tmp_path)
    d("memory_write", {"namespace": "obs", "key": "k", "content": "DLQ depth rising"}, s)
    assert "DLQ depth rising" in d("memory_search", {"query": "DLQ"}, s)
    assert d("memory_search", {"query": "kinesis"}, s) == "(no matching entries)"
    s.close()


def test_dispatch_bad_input_returns_error_not_raise(tmp_path):
    d, s = _dispatch(tmp_path)
    assert "Error" in d("memory_write", {"namespace": "", "key": "k", "content": "c"}, s)
    assert "Error" in d("memory_search", {}, s)
    assert "Error" in d("nonexistent_memory_tool", {}, s)
    s.close()


def test_memory_tool_definitions_shape():
    from harness.agent.tools import MEMORY_TOOL_DEFINITIONS
    names = [t["function"]["name"] for t in MEMORY_TOOL_DEFINITIONS]
    assert set(names) == {"memory_write", "memory_read", "memory_search"}
    for t in MEMORY_TOOL_DEFINITIONS:
        assert t["type"] == "function"
        assert "parameters" in t["function"]
