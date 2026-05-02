import json
import os

import yaml

RESULTS_DIR = "results"


def _navigate(data: dict, dot_path: str):
    node = data
    for part in dot_path.split("."):
        if isinstance(node, dict):
            node = node.get(part)
        else:
            return None
    return node


def run_pass3(
    scenario_dir: str, run_id: str, pass1_result: dict, manifest_path: str
) -> dict:
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    with open(os.path.join(scenario_dir, "faulted.yaml"), "r", encoding="utf-8") as f:
        faulted_doc = yaml.safe_load(f)

    submitted_path = os.path.join(RESULTS_DIR, run_id, "submitted.yaml")
    if os.path.isfile(submitted_path):
        with open(submitted_path, "r", encoding="utf-8") as f:
            submitted_doc = yaml.safe_load(f)
    else:
        submitted_doc = faulted_doc

    target_resource = manifest.get("target_resource", "")
    target_property = manifest.get("target_property", "")
    original_value = manifest.get("original_value")
    invalid_patches = manifest.get("invalid_patches", [])

    # Signal 1 — structural diff: did submitted template restore the original value?
    submitted_resources = (submitted_doc or {}).get("Resources", {})
    resource_node = submitted_resources.get(target_resource, {})
    submitted_value = _navigate(resource_node, target_property)
    structural_match = submitted_value == original_value

    # Signal 2 — invalid patch substring in diff text
    change_log_path = os.path.join(RESULTS_DIR, run_id, "file_change_log.json")
    diff_text = ""
    if os.path.isfile(change_log_path):
        with open(change_log_path, "r", encoding="utf-8") as f:
            change_log = json.load(f)
        diff_text = change_log.get("diff_text", "")

    invalid_patch_detected = any(p in diff_text for p in invalid_patches)

    primary_passed = pass1_result.get("primary_assertions_passed", False)
    assertions = pass1_result.get("assertions", {})

    if structural_match and not invalid_patch_detected:
        classification = "root_cause"
    elif primary_passed and not structural_match:
        classification = "workaround"
    elif not primary_passed:
        any_improvement = any(v["result"] == "pass" for v in assertions.values())
        classification = "partial" if any_improvement else "none"
    else:
        classification = "none"

    return {
        "structural_match": structural_match,
        "invalid_patch_detected": invalid_patch_detected,
        "classification": classification,
        "root_cause_addressed": classification == "root_cause",
    }
