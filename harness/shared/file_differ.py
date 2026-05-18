import difflib
import os
import re
from typing import Dict


def extract_line_changes(before_lines: list, after_lines: list) -> list:
    """Return per-line change records with line number and content."""
    changes = []
    cur_old = cur_new = 0
    for line in difflib.unified_diff(before_lines, after_lines, lineterm=""):
        if line.startswith("---") or line.startswith("+++"):
            continue
        if line.startswith("@@"):
            m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            if m:
                cur_old = int(m.group(1))
                cur_new = int(m.group(2))
            continue
        if line.startswith("-"):
            changes.append({"line": cur_old, "type": "removed", "content": line[1:]})
            cur_old += 1
        elif line.startswith("+"):
            changes.append({"line": cur_new, "type": "added", "content": line[1:]})
            cur_new += 1
        else:
            cur_old += 1
            cur_new += 1
    return changes


def snapshot(directory: str) -> Dict[str, str]:
    result = {}
    for root, _, files in os.walk(directory):
        for fname in sorted(files):
            full_path = os.path.join(root, fname)
            rel_path = os.path.relpath(full_path, directory)
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                result[rel_path] = f.read()
    return result


def diff_snapshots(
    before: Dict[str, str], after: Dict[str, str], directory: str
) -> dict:
    before_keys = set(before)
    after_keys = set(after)

    files_added = sorted(after_keys - before_keys)
    files_removed = sorted(before_keys - after_keys)
    files_modified = sorted(
        p for p in before_keys & after_keys if before[p] != after[p]
    )

    per_file_line_changes: dict = {}

    for path in files_added:
        after_lines = after[path].splitlines()
        n = len(after_lines)
        per_file_line_changes[path] = {
            "lines_added": n,
            "lines_modified": 0,
            "lines_removed": 0,
            "total_lines_changed": n,
            "changes": [{"line": i + 1, "type": "added", "content": l} for i, l in enumerate(after_lines)],
        }

    for path in files_removed:
        before_lines = before[path].splitlines()
        n = len(before_lines)
        per_file_line_changes[path] = {
            "lines_added": 0,
            "lines_modified": 0,
            "lines_removed": n,
            "total_lines_changed": n,
            "changes": [{"line": i + 1, "type": "removed", "content": l} for i, l in enumerate(before_lines)],
        }

    for path in files_modified:
        before_lines = before[path].splitlines()
        after_lines = after[path].splitlines()
        changes = extract_line_changes(before_lines, after_lines)
        added = sum(1 for c in changes if c["type"] == "added")
        removed = sum(1 for c in changes if c["type"] == "removed")
        per_file_line_changes[path] = {
            "lines_added": added,
            "lines_modified": 0,
            "lines_removed": removed,
            "total_lines_changed": added + removed,
            "changes": changes,
        }

    total = sum(v["total_lines_changed"] for v in per_file_line_changes.values())

    diff_parts: list[str] = []
    for path in files_modified:
        diff_parts.extend(difflib.unified_diff(
            before[path].splitlines(keepends=True),
            after[path].splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        ))
    diff_text = "".join(diff_parts)

    return {
        "files_added": files_added,
        "files_modified": files_modified,
        "files_removed": files_removed,
        "total_files_changed": len(files_added) + len(files_modified),
        "per_file_line_changes": per_file_line_changes,
        "total_lines_changed": total,
        "diff_text": diff_text,
    }
