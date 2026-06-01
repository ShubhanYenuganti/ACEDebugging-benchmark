# Agent Memory Layer — Implementation Plan

Companion to `2026-05-31-agent-memory-layer-design.md`. TDD per step; run the
relevant suite after each. Memory is gated behind `run_agent_loop`'s new
`memory_db_path` param (default `None` = off) so existing loop tests are
unaffected; `run.py` always passes a path in production.

## Step 1 — `harness/agent/memory.py` (storage core)  [test_memory.py]
- `MemoryStore(db_path, run_id)`: opens SQLite, creates `memory` table +
  `memory_fts` FTS5 table (detect FTS5 at init; fall back to LIKE).
- `write(namespace, key, content) -> str`: validate (non-empty ns/key ≤256,
  content ≤64KB), upsert on `(namespace,key)`, stamp `run_id`+`ts`, sync FTS.
- `read(namespace=None) -> list[dict]`: namespace rows newest-first (≤50), or
  namespace summary when `None`.
- `search(query, namespace=None) -> list[dict]`: FTS bm25 rank or LIKE recency,
  ≤50 rows.
- `close()`; module fn `teardown_memory(db_path)` removes db/-wal/-shm, idempotent.
- Write tests first, then implement until green.

## Step 2 — tool surface in `harness/agent/tools.py`  [test_agent_loop.py]
- `MEMORY_TOOL_DEFINITIONS` (memory_write / memory_read / memory_search schemas).
- `_MEMORY_TOOL_NAMES` set.
- `dispatch_memory_tool(name, inputs, store) -> str`: format rows into readable
  text; bad input → `Error: ...` string (never raise); truncate content to 4KB
  and rows to 50 in output.
- Tests: dispatch round-trip, run_id stamping, error strings, definition shape.

## Step 3 — trace logging in `harness/shared/result_logger.py`  [test_runner.py]
- `log_memory_event(run_id, turn, op, namespace, key)` → append to
  `results/<run_id>/memory_trace.json` (mirror `log_edit_event`).
- `init_run` writes `memory_trace.json = "[]"`.
- Test: events appended; file initialised.

## Step 4 — loop wiring in `harness/agent/loop.py`  [test_agent_loop.py]
- New param `memory_db_path: str | None = None`.
- When set: `store = MemoryStore(memory_db_path, run_id)`; tools list gains
  `MEMORY_TOOL_DEFINITIONS`; route `_MEMORY_TOOL_NAMES` → `dispatch_memory_tool`;
  `log_memory_event(...)`; `store.close()` in `finally`. Memory calls do NOT
  touch `writes_made` / `writes_since_last_submit` and are NOT logged to
  `tool_call_trace.json`.
- `_build_system` gains the under-prescriptive memory paragraph (only when
  memory enabled).
- Test: model-issued `memory_write` → absent from `tool_call_trace.json`,
  present in `memory_trace.json`, write counter untouched.

## Step 5 — run.py lifecycle  [manual + existing suite]
- Compute `memory_db_path = results/<run_id>/agent_memory.db`; pass to
  `run_agent_loop`; after `score_run(...)` call `teardown_memory(memory_db_path)`.

## Step 6 — full suite green
- `pytest tests/` (Python). Node MCP tests untouched.
