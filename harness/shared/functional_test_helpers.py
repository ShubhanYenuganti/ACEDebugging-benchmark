"""Helpers for corpus functional_test.py files.

Functional tests no longer rely on regex-parsing of stdout. They call
emit_pass()/emit_fail() and finalize() (or rely on atexit), and the harness
reads the resulting JSON file.

Usage in a functional_test.py:

    from harness.shared.functional_test_helpers import emit_pass, emit_fail, finalize

    if check_thing():
        emit_pass("thing_works")
    else:
        emit_fail("thing_works", "thing returned None")
    finalize()  # or rely on atexit
"""
import atexit
import json
import os
from typing import Literal

_buffer: list[dict] = []
_finalized = False


def emit_pass(name: str, message: str = "") -> None:
    _emit("pass", name, message)


def emit_fail(name: str, message: str = "") -> None:
    _emit("fail", name, message)


def _emit(verdict: Literal["pass", "fail"], name: str, message: str) -> None:
    entry = {"name": name, "verdict": verdict, "message": message}
    _buffer.append(entry)
    print(f"ASSERT {verdict} {name}: {message}", flush=True)


def finalize() -> None:
    """Write the JSON results file if ACE_BENCH_RESULTS_PATH is set."""
    global _finalized
    if _finalized:
        return
    _finalized = True
    out_path = os.environ.get("ACE_BENCH_RESULTS_PATH")
    if not out_path:
        return
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"assertions": list(_buffer)}, f, indent=2)


atexit.register(finalize)
