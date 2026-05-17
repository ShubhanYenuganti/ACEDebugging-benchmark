import os
import re
import subprocess
import sys


def run_pass1(corpus_dir: str) -> dict:
    functional_test = os.path.join(corpus_dir, "functional_test.py")
    result = subprocess.run(
        [sys.executable, functional_test],
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr

    assertions = {}
    for line in output.splitlines():
        m = re.match(r"ASSERT\s+(pass|fail)\s+(\w+):\s*(.*)", line.strip())
        if m:
            verdict, name, message = m.group(1), m.group(2), m.group(3)
            assertions[name] = {"result": verdict, "message": message}

    failed = [n for n, v in assertions.items() if v["result"] == "fail"]
    primary_failed = [n for n in failed if "_secondary" not in n]

    if not assertions:
        # Functional test crashed before emitting any ASSERT line.
        synthetic_name = "__no_assertions__"
        assertions = {
            synthetic_name: {
                "result": "fail",
                "message": (
                    "functional_test.py produced no ASSERT pass|fail lines "
                    "(likely crashed or mis-configured)."
                ),
            }
        }
        failed = [synthetic_name]
        primary_failed = [synthetic_name]

    return {
        "assertions": assertions,
        "primary_assertions_passed": len(primary_failed) == 0,
        "all_assertions_passed": len(failed) == 0,
        "failed_assertion_names": failed,
    }
