"""Pass 1 — functional verification.

Runs the corpus functional_test.py and returns an AssertionRunResult.
The shared parser handles the ASSERT regex and crash detection.
"""
import os
import subprocess
import sys
import tempfile

from harness.shared import assertion_parser
from harness.shared.types import AssertionRunResult


class Pass1Step:
    name = "pass1_functional"

    def should_run(self, ctx) -> bool:
        return True

    def run(self, ctx):
        result = run_pass1(ctx.corpus_dir)
        ctx.pass1_result = result
        return result.to_baseline_dict()


def run_pass1(corpus_dir: str) -> AssertionRunResult:
    functional_test = os.path.join(corpus_dir, "functional_test.py")
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        results_path = tf.name
    try:
        env = {**os.environ, "ACE_BENCH_RESULTS_PATH": results_path}
        result = subprocess.run(
            [sys.executable, functional_test],
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
        return assertion_parser.parse_with_fallback(
            result.stdout + result.stderr,
            returncode=result.returncode,
            json_path=results_path,
        )
    finally:
        if os.path.exists(results_path):
            os.unlink(results_path)
