#!/usr/bin/env python3
"""
Stub model for E2E testing. Consumes harness context from stdin,
applies the known-correct fix from fault_manifest.json, and triggers
redeployment via localstack-deployer.

Usage:
    python harness/run.py scenarios/arch01_fault01_security/ --run-id e2e-test | \
        python tests/stubs/stub_model.py scenarios/arch01_fault01_security/ \
                                         scenarios/arch01_fault01_security/fault_manifest.json
"""

import json
import os
import re
import subprocess
import sys


def _apply_sequence_fix(
    template_text: str,
    injected: list,
    original: list,
) -> tuple:
    """
    Replace the YAML block-sequence representation of `injected` with
    `original`, also stripping any preceding # FAULT INJECTED comment
    lines at the same indentation level.

    Returns (changed: bool, new_text: str).
    """
    first = re.escape(injected[0])
    m = re.search(r"^(\s*)- " + first + r"\s*$", template_text, re.MULTILINE)
    if not m:
        return False, template_text

    pad = m.group(1)
    comment_block = r"(?:" + re.escape(pad) + r"#[^\n]*\n)*"
    injected_block = re.escape("\n".join(f"{pad}- {v}" for v in injected))
    original_block = "\n".join(f"{pad}- {v}" for v in original)

    new_text = re.sub(comment_block + injected_block, original_block, template_text, count=1)
    return new_text != template_text, new_text


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: stub_model.py <scenario_dir> <manifest_path>", file=sys.stderr)
        sys.exit(1)

    scenario_dir = os.path.abspath(sys.argv[1])
    manifest_path = os.path.abspath(sys.argv[2])

    # Consume all stdin (unblocks harness stdout pipe)
    _ = sys.stdin.read()

    with open(manifest_path) as f:
        manifest = json.load(f)

    template_path = os.path.join(scenario_dir, "faulted.yaml")
    with open(template_path) as f:
        template = f.read()

    injected_value = manifest.get("injected_value")
    original_value = manifest.get("original_value")

    if not injected_value or not original_value:
        print("stub_model: manifest missing injected_value or original_value", file=sys.stderr)
        sys.exit(1)

    if isinstance(injected_value, list):
        changed, fixed = _apply_sequence_fix(template, injected_value, original_value)
    else:
        changed = str(injected_value) in template
        fixed = template.replace(str(injected_value), str(original_value), 1)

    if not changed:
        print("stub_model: injected_value not found in template — no patch applied", file=sys.stderr)
        sys.exit(1)

    with open(template_path, "w") as f:
        f.write(fixed)
    print(f"stub_model: applied fix — injected={injected_value!r} -> original={original_value!r}")

    result = subprocess.run(
        ["localstack-deployer", "update-stack", "--stack-name", "ace-bench-stack"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"stub_model: update-stack failed:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)

    print("stub_model: redeployment triggered successfully")


if __name__ == "__main__":
    main()
