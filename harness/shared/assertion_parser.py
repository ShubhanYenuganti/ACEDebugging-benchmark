"""Parse functional_test.py output into an AssertionRunResult.

Single source of truth for both the agent retry loop and the scorer pipeline.
The output protocol is one line per assertion:

    ASSERT pass|fail <name>: <message>

Primary assertions: names without '_secondary' suffix; their failure fails the run.
Secondary assertions: tracked for regression analysis, do not fail the run.
"""
import json
import os
import re

from harness.shared.types import AssertionResult, AssertionRunResult

_ASSERT_LINE = re.compile(r"ASSERT\s+(pass|fail)\s+(\w+):\s*(.*)")


def parse(output: str, returncode: int = 0) -> AssertionRunResult:
    """Parse functional_test.py output into an AssertionRunResult.

    A zero-assertion run is treated as a synthetic failure (`__no_assertions__`)
    because functional_test.py emitting nothing almost always means it crashed
    before any assertion ran. A non-zero returncode appends `__test_crashed__`.
    """
    assertions: list[AssertionResult] = []
    for line in output.splitlines():
        m = _ASSERT_LINE.match(line.strip())
        if not m:
            continue
        verdict, name, message = m.group(1), m.group(2), m.group(3)
        assertions.append(AssertionResult(name=name, verdict=verdict, message=message))

    crash_reason = ""
    if not assertions:
        assertions.append(AssertionResult(
            name="__no_assertions__",
            verdict="fail",
            message=(
                "functional_test.py produced no ASSERT lines (likely crashed "
                "before any assertion ran — import error, missing dependency, "
                "network error in setup, etc.)."
            ),
        ))
        crash_reason = "no_assertions_emitted"

    if returncode != 0:
        assertions.append(AssertionResult(
            name="__test_crashed__",
            verdict="fail",
            message=f"functional_test.py exited with code {returncode}",
        ))
        if not crash_reason:
            crash_reason = f"exit_code_{returncode}"

    return AssertionRunResult(
        assertions=assertions,
        returncode=returncode,
        crash_reason=crash_reason,
    )


def parse_from_json_file(path: str) -> AssertionRunResult:
    """Read a structured results file produced by functional_test_helpers."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assertions = [
        AssertionResult(
            name=a["name"],
            verdict=a["verdict"],
            message=a.get("message", ""),
        )
        for a in data.get("assertions", [])
    ]
    return AssertionRunResult(assertions=assertions, returncode=0, crash_reason="")


def parse_with_fallback(
    output: str,
    returncode: int,
    json_path: str | None,
) -> AssertionRunResult:
    """Prefer the JSON results file when present; fall back to stdout regex.

    Crash detection (non-zero returncode, no assertions) is layered on top
    of the JSON result when a JSON file is available.
    """
    if json_path and os.path.exists(json_path) and os.path.getsize(json_path) > 0:
        result = parse_from_json_file(json_path)
        result.returncode = returncode
        if returncode != 0:
            result.assertions.append(AssertionResult(
                name="__test_crashed__",
                verdict="fail",
                message=f"functional_test.py exited with code {returncode}",
            ))
            result.crash_reason = f"exit_code_{returncode}"
        if not result.assertions:
            result.assertions.append(AssertionResult(
                name="__no_assertions__",
                verdict="fail",
                message="functional_test.py emitted no assertions.",
            ))
            result.crash_reason = "no_assertions_emitted"
        return result
    return parse(output, returncode=returncode)
