import json
import os
import shutil
import subprocess
import sys


def run_lint(template_path: str) -> dict:
    if shutil.which("cfn-lint") is None:
        raise EnvironmentError(
            "cfn-lint is not installed. Install it with: pip install cfn-lint"
        )

    # Prefer the binary co-located with the current interpreter (venv) so we
    # don't accidentally call a system cfn-lint built against a different Python.
    _bin_dir = os.path.dirname(sys.executable)
    _venv_bin = os.path.join(_bin_dir, "cfn-lint")
    cfn_lint_cmd = _venv_bin if os.path.isfile(_venv_bin) else "cfn-lint"

    result = subprocess.run(
        [cfn_lint_cmd, "--format", "json", template_path],
        capture_output=True,
        text=True,
    )

    fatal_errors = []
    warnings = []

    output = result.stdout.strip()
    if output:
        try:
            matches = json.loads(output)
            for match in matches:
                rule_id = match.get("Rule", {}).get("Id", "")
                message = match.get("Message", "")
                start = match.get("Location", {}).get("Start", {})
                location = f"line {start.get('LineNumber', '?')}"
                entry = {"rule": rule_id, "message": message, "location": location}
                if rule_id.startswith("E"):
                    fatal_errors.append(entry)
                elif rule_id.startswith("W"):
                    warnings.append(entry)
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass

    return {
        "passed": len(fatal_errors) == 0,
        "fatal_errors": fatal_errors,
        "warnings": warnings,
    }
