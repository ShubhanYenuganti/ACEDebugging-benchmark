import difflib
import os
from typing import Dict


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
        n = len(after[path].splitlines())
        per_file_line_changes[path] = {
            "lines_added": n,
            "lines_modified": 0,
            "lines_removed": 0,
            "total_lines_changed": n,
        }

    for path in files_removed:
        n = len(before[path].splitlines())
        per_file_line_changes[path] = {
            "lines_added": 0,
            "lines_modified": 0,
            "lines_removed": n,
            "total_lines_changed": n,
        }

    for path in files_modified:
        added = removed = 0
        for line in difflib.unified_diff(
            before[path].splitlines(), after[path].splitlines()
        ):
            if line.startswith("+") and not line.startswith("+++"):
                added += 1
            elif line.startswith("-") and not line.startswith("---"):
                removed += 1
        per_file_line_changes[path] = {
            "lines_added": added,
            "lines_modified": 0,
            "lines_removed": removed,
            "total_lines_changed": added + removed,
        }

    total = sum(v["total_lines_changed"] for v in per_file_line_changes.values())

    return {
        "files_added": files_added,
        "files_modified": files_modified,
        "files_removed": files_removed,
        "total_files_changed": len(files_added) + len(files_modified),
        "per_file_line_changes": per_file_line_changes,
        "total_lines_changed": total,
    }
