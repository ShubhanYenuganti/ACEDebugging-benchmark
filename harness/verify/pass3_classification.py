import json
import os
import re

import yaml

RESULTS_DIR = "results"

# CloudFormation YAML uses !Sub, !Ref, !GetAtt etc. Register no-op constructors
# so safe_load can parse CF templates without evaluating intrinsic functions.
def _cf_constructor(loader, node):
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_mapping(node)

for _cf_tag in ["!Sub", "!Ref", "!GetAtt", "!Select", "!Split", "!Join", "!If",
                "!Equals", "!Not", "!And", "!Or", "!FindInMap", "!Base64",
                "!Condition", "!ImportValue", "!Transform", "!Cidr"]:
    yaml.SafeLoader.add_constructor(_cf_tag, _cf_constructor)


# Splits a dotted segment like "Statement[0]" into ("Statement", [0]).
# Supports any number of trailing indices, e.g. "Foo[0][1]".
_SEGMENT_RE = re.compile(r"^([^\[]+)((?:\[\d+\])*)$")
_INDEX_RE = re.compile(r"\[(\d+)\]")


def _navigate(data, dot_path: str):
    """Navigate a nested dict/list structure using a dotted path with optional
    bracket indices, e.g. "Properties.Policies[0].Statement[0].Action".
    Returns None if any segment cannot be resolved.
    """
    node = data
    for part in dot_path.split("."):
        m = _SEGMENT_RE.match(part)
        if not m:
            return None
        key, indices = m.group(1), m.group(2)
        if not isinstance(node, dict):
            return None
        node = node.get(key)
        for idx_str in _INDEX_RE.findall(indices):
            idx = int(idx_str)
            if not isinstance(node, list) or idx >= len(node) or idx < -len(node):
                return None
            node = node[idx]
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
