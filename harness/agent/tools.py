import json
import os
import pathlib
import re

from harness.shared.cfn_lint_runner import run_lint
from harness.shared.template_parser import extract_s3key_stems

SIGNAL_FILE = os.environ.get("ACE_BENCH_SIGNAL_FILE", "/tmp/ace-bench-update.json")

READ_MAX_BYTES = 1_048_576   # 1 MB
WRITE_MAX_BYTES = 524_288    # 512 KB

_BLOCKED_READS = {"fault_manifest.json", "known_good.yaml"}
_SUBMIT_TOOL = "submit_fix"
_FILE_TOOL_NAMES = {"read_file", "write_file", "list_directory", "submit_fix", "validate_fix"}


def mcp_to_openai_tool(mcp_tool) -> dict:
    """Convert an MCP tool object to an OpenAI-format tool dict."""
    return {
        "type": "function",
        "function": {
            "name": mcp_tool.name,
            "description": mcp_tool.description or "",
            "parameters": mcp_tool.inputSchema,
        },
    }


def filter_model_tools(tools: list[dict]) -> list[dict]:
    """Remove score tools the model must not call."""
    blocked = {"ace_verify_fix", "ace_score_run"}
    return [t for t in tools if t["function"]["name"] not in blocked]


FILE_TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read a file within the scenario directory. "
                "Path is relative to the scenario root."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path from scenario root, e.g. deployment/lambda/handler.py",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Write content to a file. "
                "Only files inside deployment/ or faulted.yaml may be written."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path from scenario root",
                    },
                    "content": {
                        "type": "string",
                        "description": "Full file content to write",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List entries in a directory within the scenario.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path from scenario root",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_fix",
            "description": (
                "Submit your fix for redeployment. "
                "Call this once you have edited all necessary files and are ready to deploy. "
                "Later successful retries are allowed but receive a retry penalty."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "validate_fix",
            "description": (
                "Run a non-deploy validation preview: cfn-lint on faulted.yaml "
                "and a Lambda packaging source check. Does not submit or score."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


def _sibling_package_stems(rel_path: str, scenario_root: pathlib.Path, stems: dict) -> list[str]:
    norm = rel_path.replace("\\", "/")
    if not norm.startswith("deployment/") or not norm.endswith(".py"):
        return []
    target_dir = (scenario_root / norm).parent
    matches = []
    for stem in stems:
        if (target_dir / f"{stem}.py").exists() or (target_dir / stem).is_dir():
            matches.append(stem)
    return sorted(matches)


def _check_lambda_orphan(rel_path: str, scenario_root: pathlib.Path) -> str | None:
    """Return an error message if rel_path writes to deployment/ with no matching
    S3Key stem in faulted.yaml; return None if the write is permitted.

    Accepts writes where any path component (directory name or file stem) matches
    a known stem from the YAML-parsed template, enabling both flat-file and
    directory-based Lambda package layouts.
    """
    norm = rel_path.replace("\\", "/")
    if not norm.startswith("deployment/") or not norm.endswith(".py"):
        return None
    template_path = scenario_root / "faulted.yaml"
    if not template_path.exists():
        return None
    stems = extract_s3key_stems(str(template_path))
    if not stems:
        return None

    parts = norm[len("deployment/"):].split("/")
    for part in parts:
        candidate = os.path.splitext(part)[0] if part.endswith(".py") else part
        if candidate in stems:
            return None

    sibling_matches = _sibling_package_stems(rel_path, scenario_root, stems)
    if len(sibling_matches) == 1:
        return None

    return (
        f"Error: no matching S3Key found for write to {rel_path}. "
        f"Available stems from faulted.yaml: {sorted(stems.keys())}. "
        f"Either rename your file/directory to match one of these stems, "
        f"or edit faulted.yaml to add an S3Key for your target stem."
    )


def dispatch_file_tool(name: str, inputs: dict, scenario_dir: str) -> str:
    scenario_root = pathlib.Path(scenario_dir).resolve()

    def _safe_resolve(rel: str) -> pathlib.Path | None:
        target = (scenario_root / rel).resolve()
        try:
            target.relative_to(scenario_root)
        except ValueError:
            return None
        return target

    if name == "read_file":
        rel = inputs.get("path", "")
        if pathlib.Path(rel).name in _BLOCKED_READS:
            return f"Error: access to {pathlib.Path(rel).name} is not allowed."
        target = _safe_resolve(rel)
        if target is None:
            return "Error: path traversal not allowed."
        if not target.exists():
            return f"Error: {rel} does not exist."
        if target.stat().st_size > READ_MAX_BYTES:
            return f"Error: {rel} is too large to read ({target.stat().st_size} bytes; limit {READ_MAX_BYTES})."
        return target.read_text(encoding="utf-8")

    if name == "write_file":
        rel = inputs.get("path", "")
        content = inputs.get("content", "")
        # Fail fast on oversized content before any path resolution.
        content_bytes = len(content.encode("utf-8"))
        if content_bytes > WRITE_MAX_BYTES:
            return f"Error: content for {rel} is too large ({content_bytes} bytes; limit {WRITE_MAX_BYTES})."
        norm = rel.replace("\\", "/")
        if not (norm.startswith("deployment/") or norm == "faulted.yaml"):
            return f"Error: writing to {rel} is not allowed. Only deployment/ files and faulted.yaml may be modified."
        target = _safe_resolve(rel)
        if target is None:
            return "Error: path traversal not allowed."
        orphan_err = _check_lambda_orphan(rel, scenario_root)
        if orphan_err is not None:
            return orphan_err
        if target.exists() and target.read_text(encoding="utf-8") == content:
            return (
                f"Error: {rel} is unchanged — the content you wrote is identical to the "
                "current file. Your fix had no effect. Re-read the file, identify what "
                "specifically needs to change, and write a corrected version."
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"Written {rel} ({len(content)} chars)."

    if name == "list_directory":
        rel = inputs.get("path", "")
        target = _safe_resolve(rel)
        if target is None:
            return "Error: path traversal not allowed."
        if not target.is_dir():
            return f"Error: {rel} is not a directory."
        entries = sorted(
            ("DIR  " if (target / e).is_dir() else "FILE ") + e
            for e in os.listdir(target)
            if e != "fault_manifest.json"
        )
        return "\n".join(entries) if entries else "(empty)"

    if name == "validate_fix":
        template_path = scenario_root / "faulted.yaml"
        deployment_dir = scenario_root / "deployment"
        if not template_path.exists():
            return "Error: faulted.yaml does not exist."
        lint = run_lint(str(template_path))
        stems = extract_s3key_stems(str(template_path))
        found = []
        missing = []
        for stem in sorted(stems):
            matches = []
            for dirpath, dirnames, filenames in os.walk(deployment_dir):
                if stem in dirnames:
                    matches.append(os.path.relpath(os.path.join(dirpath, stem), deployment_dir))
                if f"{stem}.py" in filenames:
                    matches.append(os.path.relpath(os.path.join(dirpath, f"{stem}.py"), deployment_dir))
            if matches:
                found.append(f"{stem}: {matches[0].replace(os.sep, '/')}")
            else:
                missing.append(stem)
        status = "Validation passed" if lint.get("passed") and not missing else "Validation failed"
        parts = [status]
        parts.append("cfn-lint: passed" if lint.get("passed") else "cfn-lint: failed")
        if lint.get("fatal_errors"):
            parts.append("lint_errors: " + "; ".join(str(e) for e in lint["fatal_errors"]))
        parts.append("packages: " + (", ".join(found) if found else "(none)"))
        if missing:
            parts.append("missing package sources: " + ", ".join(missing))
        return "\n".join(parts)

    if name == "submit_fix":
        return ""

    return f"Error: unknown file tool '{name}'."
