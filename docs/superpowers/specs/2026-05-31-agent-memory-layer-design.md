# Agent-Managed Memory Layer — Design Spec

**Date:** 2026-05-31
**Status:** Approved (brainstorming complete)
**Scope:** Phase G inline agent. Provider-agnostic (LiteLLM). No SDK changes.

---

## 1. Problem & Goal

The Phase G agent retains full history *within* a single `run_agent_loop`
invocation (the `messages` array), but that history is raw and unstructured:
verbose MCP JSON blobs and repeated file contents. A weaker or smaller-context
model loses signal in the noise and repeats itself — it re-derives facts already
present in context, or forgets which fix it already tried.

**Goal:** *reduce signal loss* by giving the agent a deliberate, structured,
queryable memory it maintains itself. This is explicitly **not** an attempt to
*neutralize* context-window size as a benchmark variable (that would require
capping the working context for all models — out of scope). Larger-context
models keep their passive advantage; this layer lifts the floor for everyone and
rewards models that take disciplined notes.

### Design philosophy — agent autonomy, zero harness curation

The harness provides a **blank** store plus three tools. It never writes rows
itself and never injects "facts" into the agent's context. The agent alone
decides what is worth remembering and what to retrieve. Memory usage is **not
scored directly** — it is an *enabler*. Agents that keep good notes perform
better; agents that ignore it are unaffected. That autonomy is the signal we
want: it measures whether a model can manage its own working memory, the way a
real engineer keeps a debugging notebook.

---

## 2. Storage backend

A single SQLite file per scenario evaluation. One primary table:

```sql
CREATE TABLE memory (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    namespace TEXT NOT NULL,
    key       TEXT NOT NULL,
    content   TEXT NOT NULL,
    run_id    TEXT NOT NULL,
    ts        TEXT NOT NULL,
    UNIQUE(namespace, key)        -- write upserts on (namespace, key)
);
```

- **`run_id` is stamped by the harness automatically** (metadata, not curation).
  The agent never sets it. If the benchmark later adopts a multi-run model, the
  agent can tell which attempt a note came from — for now every row carries the
  current run's id.
- **Search** uses a parallel FTS5 table `memory_fts(namespace, key, content)`
  kept in sync on every write (rowid = `memory.id`). If the host SQLite is
  compiled without FTS5, the store transparently falls back to `LIKE` substring
  matching. Detected once at store construction.

### Limits (abuse / context-bloat guards)
- `content` capped at 64 KB per write (rejected with an error string, mirrors
  `write_file`'s size guard).
- `namespace` and `key` must be non-empty strings ≤ 256 chars.
- `memory_read` / `memory_search` return at most 50 rows; content of each row
  truncated to 4 KB in the returned text (full content stays in the DB).

---

## 3. The three tools

Added to the model's tool list alongside the existing file tools. All
provider-agnostic via LiteLLM.

| Tool | Signature | Behaviour |
|------|-----------|-----------|
| `memory_write` | `(namespace, key, content)` | Upsert. Agent invents namespaces (`tried_fixes`, `ruled_out`, `observations`, …). Returns confirmation. |
| `memory_read`  | `(namespace=None)` | If `namespace` given: all rows in it (newest first). If omitted: the list of namespaces with row counts. |
| `memory_search`| `(query, namespace=None)` | Rows matching `query`, ranked (bm25 under FTS5; recency under LIKE). Optional namespace filter. |

Returned strings are human-readable and parseable, e.g.:

```
[tried_fixes/attempt-1] (run a1b2c3d4, 2026-05-31T20:14:02Z)
Removed FilterCriteria from EventSourceMapping. Test inventory_records_written
still FAILED — root cause is elsewhere.
```

---

## 4. Module layout & seams

| File | Change |
|------|--------|
| `harness/agent/memory.py` *(new)* | `MemoryStore` class: open/init schema, FTS detection, `write`, `read`, `search`, `close`. Plus `teardown_memory(db_path)` helper (removes `.db`, `-wal`, `-shm`). Pure storage — unit-testable with no loop. |
| `harness/agent/tools.py` | Add `MEMORY_TOOL_DEFINITIONS` (3 schemas), `_MEMORY_TOOL_NAMES`, and `dispatch_memory_tool(name, inputs, store)`. Mirrors `dispatch_file_tool` style (returns a string). |
| `harness/shared/result_logger.py` | Add `log_memory_event(run_id, turn, op, namespace, key)` → appends to `results/<run_id>/memory_trace.json`. Mirrors `log_edit_event`. Initialise `memory_trace.json` to `[]` in `init_run`. |
| `harness/agent/loop.py` | New param `memory_db_path: str \| None`. Construct `MemoryStore(memory_db_path, run_id)` when provided; add `MEMORY_TOOL_DEFINITIONS` to model tools; route `_MEMORY_TOOL_NAMES` to `dispatch_memory_tool`; log each call via `log_memory_event`; `store.close()` in a `finally`. Extend `_build_system` with a short, **under-prescriptive** memory paragraph. |
| `harness/run.py` | Compute `memory_db_path = results/<run_id>/agent_memory.db`; pass to `run_agent_loop`; after `score_run(...)` returns, call `teardown_memory(memory_db_path)`. |

### Key invariants preserved
- **Not MCP calls.** Memory tools dispatch locally; they never round-trip
  through MCP and therefore **never appear in `tool_call_trace.json`** → they do
  **not** count against the efficiency / diagnostic-call scoring dimension.
  Memory bookkeeping is free.
- They are logged only to `memory_trace.json`, purely for post-hoc analysis of
  how much each model leaned on memory. The scorer ignores this file.
- They do **not** increment `writes_made` / `writes_since_last_submit` (those
  gate `submit_fix`); a `memory_write` is not a code edit.
- `fault_manifest.json` / `known_good.yaml` exposure rules are untouched —
  memory tools read/write only the SQLite store, never scenario files.

---

## 5. Lifecycle / teardown

1. DB file created on `MemoryStore` construction at loop start (empty schema).
2. Lives through the agent loop, verify, and scoring.
3. **Dropped after `score.json` is written** in `run.py` via
   `teardown_memory(memory_db_path)`. The next scenario starts blank.

Because the path is computed in `run.py` and *passed in* (not hard-coded from
`run_id` inside the loop), moving to a scenario-scoped / cross-run store later is
a one-line path change — no redesign. For now `results/<run_id>/agent_memory.db`
is the path (single-run model: one run_id == one scenario evaluation).

---

## 6. System-prompt addition (under-prescriptive on purpose)

A short paragraph appended in `_build_system`, e.g.:

> You have a private persistent memory store (tools `memory_write`,
> `memory_read`, `memory_search`) that survives across your fix attempts in this
> scenario and starts empty. Use it however you find useful — for example to
> record what each fix attempt changed and how the tests responded, hypotheses
> you have ruled out, or key diagnostic findings — so you do not repeat work.
> Memory calls are free and never count against you.

We deliberately do **not** prescribe a schema or namespaces — discovering a
useful structure is part of the signal.

---

## 7. Testing — `tests/test_memory.py` (+ loop assertions)

`MemoryStore` unit tests (no loop, temp file):
- write → read round-trip; newest-first ordering.
- upsert: second write to same `(namespace, key)` replaces content, keeps one row.
- namespace isolation: `read(ns_a)` never returns `ns_b` rows.
- `read(None)` lists namespaces with counts.
- search hit/miss; namespace-filtered search.
- FTS path *and* forced LIKE-fallback path both return correct results
  (monkeypatch FTS availability off).
- content-size, namespace/key validation, and row/truncation limits.
- `teardown_memory` removes `.db` (+ `-wal`/`-shm`) and is idempotent.

`dispatch_memory_tool` tests:
- each op returns a sensible string; bad inputs return an `Error:` string
  (never raise).
- `run_id` is stamped on writes.

Loop-level test (extends `tests/test_agent_loop.py`):
- a `memory_write` issued by the model dispatches locally, **does not** appear
  in `tool_call_trace.json`, appears in `memory_trace.json`, and does not toggle
  the `submit_fix`-gating write counter.

---

## 8. Explicitly NOT doing (YAGNI)

- No context capping / window equalization.
- No harness-populated facts or auto-summarisation.
- No new scoring dimension; memory is invisible to the score.
- No provider-native SDKs.
- No multi-run orchestration (only leaving the path seam open for it).
