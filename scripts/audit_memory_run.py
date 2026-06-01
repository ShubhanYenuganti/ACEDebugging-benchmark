#!/usr/bin/env python3
"""Audit a completed ACE-Bench run for memory-layer + pipeline correctness.

Verifies the invariants the memory layer must uphold using only the durable
artifacts a run leaves behind (the SQLite store is torn down after scoring, so
its *absence* is itself evidence). Intended to run after a live
`python harness/run.py --model ... --run-id <id>` invocation.

Usage:
    python scripts/audit_memory_run.py <run_id> [--require-memory]

Exit code 0 if every HARD invariant holds, 1 otherwise.
"""
import argparse
import json
import os
import sys

RESULTS_DIR = "results"


def _load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def audit(run_id: str, require_memory: bool) -> bool:
    run_dir = os.path.join(RESULTS_DIR, run_id)
    if not os.path.isdir(run_dir):
        print(f"FAIL: results dir not found: {run_dir}", file=sys.stderr)
        return False

    hard_failures: list[str] = []
    info: list[str] = []

    def hard(ok: bool, label: str, detail: str = "") -> None:
        tag = "PASS" if ok else "FAIL"
        line = f"  [{tag}] {label}"
        if detail:
            line += f" — {detail}"
        print(line)
        if not ok:
            hard_failures.append(label)

    db_path = os.path.join(run_dir, "agent_memory.db")
    tool_trace_path = os.path.join(run_dir, "tool_call_trace.json")
    mem_trace_path = os.path.join(run_dir, "memory_trace.json")
    edit_trace_path = os.path.join(run_dir, "edit_trace.json")
    score_path = os.path.join(run_dir, "score.json")

    print(f"Auditing run: {run_id}")
    print("─ HARD invariants ─────────────────────────────────────────")

    # 1. Teardown: the SQLite store (and WAL/SHM sidecars) must be gone.
    leftover = [
        p for p in (db_path, db_path + "-wal", db_path + "-shm") if os.path.exists(p)
    ]
    hard(not leftover, "agent_memory.db torn down after scoring",
         "" if not leftover else f"leftover: {leftover}")

    # 2. memory_trace.json present and a list.
    mem_trace = None
    if os.path.isfile(mem_trace_path):
        try:
            mem_trace = _load_json(mem_trace_path)
        except json.JSONDecodeError:
            mem_trace = None
    hard(isinstance(mem_trace, list), "memory_trace.json present and is a JSON list")

    # 3. Isolation: no memory_* op may appear in the diagnostic tool-call trace.
    tool_trace = []
    if os.path.isfile(tool_trace_path):
        try:
            tool_trace = _load_json(tool_trace_path)
        except json.JSONDecodeError:
            tool_trace = []
    mem_in_tooltrace = [e for e in tool_trace if str(e.get("tool", "")).startswith("memory_")]
    hard(not mem_in_tooltrace,
         "no memory_* ops leaked into tool_call_trace.json",
         "" if not mem_in_tooltrace else f"{len(mem_in_tooltrace)} leaked")

    # 4. Pipeline reached scoring.
    hard(os.path.isfile(score_path), "score.json produced (pipeline completed)")

    # 5. edit_trace present (fix-boundary record the scorer relies on).
    hard(os.path.isfile(edit_trace_path), "edit_trace.json present")

    print("─ INFO ────────────────────────────────────────────────────")
    if isinstance(mem_trace, list):
        ops = {}
        for e in mem_trace:
            ops[e.get("op", "?")] = ops.get(e.get("op", "?"), 0) + 1
        total = len(mem_trace)
        info.append(f"memory operations: {total} ({ops or 'none'})")
        namespaces = sorted({e.get("namespace", "") for e in mem_trace if e.get("namespace")})
        if namespaces:
            info.append(f"namespaces used by the agent: {namespaces}")
        if os.path.isfile(score_path):
            try:
                info.append(f"final_score: {_load_json(score_path).get('final_score')}")
            except json.JSONDecodeError:
                pass
        for line in info:
            print(f"  · {line}")

        # Optional strict gate: the model must have actually written memory.
        writes = sum(1 for e in mem_trace if e.get("op") == "write")
        if require_memory:
            hard(writes >= 1, "[--require-memory] agent wrote at least one note",
                 f"{writes} writes")
        elif writes == 0:
            print("  ! WARN: agent made zero memory writes this run "
                  "(layer is correct, but unexercised).")

    print("────────────────────────────────────────────────────────────")
    if hard_failures:
        print(f"RESULT: FAIL — {len(hard_failures)} hard invariant(s) violated: "
              f"{hard_failures}")
        return False
    print("RESULT: PASS — all hard invariants hold.")
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description="Audit a run for memory-layer correctness.")
    ap.add_argument("run_id", help="run id under results/")
    ap.add_argument("--require-memory", action="store_true",
                    help="hard-fail if the agent made zero memory writes")
    args = ap.parse_args()
    ok = audit(args.run_id, args.require_memory)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
