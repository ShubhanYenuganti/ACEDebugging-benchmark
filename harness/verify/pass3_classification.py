import json
import os
import re

import yaml

from harness.shared.types import AssertionRunResult

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


_CFN_INTRINSIC_TAGS = {
    "Fn::Sub", "Fn::If", "Fn::Select", "Fn::Split", "Fn::Join",
    "Fn::FindInMap", "Fn::Base64", "Fn::ImportValue", "Fn::Transform",
    "Fn::Cidr", "Condition",
}


def _cfn_normalize(value):
    """Normalise a YAML-parsed CFN intrinsic dict to its string equivalent.

    yaml.safe_load converts '!Ref Foo' to the string 'Foo', while fault
    manifests (JSON) store the same value as {"Ref": "Foo"}.  Without this
    normalisation structural_match is always False for templates that use
    intrinsic functions.
    """
    if not isinstance(value, dict) or len(value) != 1:
        return value
    (key, val) = next(iter(value.items()))
    if key == "Ref":
        return val
    if key == "Fn::GetAtt":
        if isinstance(val, list) and len(val) == 2:
            return f"{val[0]}.{val[1]}"
        return val
    if key in _CFN_INTRINSIC_TAGS:
        if isinstance(val, str):
            return val
    return value


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


class Pass3Step:
    name = "pass3_classification"

    def should_run(self, ctx) -> bool:
        return ctx.pass1_result is not None and bool(ctx.manifest_path)

    def run(self, ctx):
        return run_pass3(ctx.scenario_dir, ctx.run_id, ctx.pass1_result, ctx.manifest_path)


def run_pass3(
    scenario_dir: str, run_id: str, pass1_result: AssertionRunResult, manifest_path: str
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
    structural_match = _cfn_normalize(submitted_value) == _cfn_normalize(original_value)

    # Signal 2 — invalid patch substring in diff text
    change_log_path = os.path.join(RESULTS_DIR, run_id, "file_change_log.json")
    diff_text = ""
    if os.path.isfile(change_log_path):
        with open(change_log_path, "r", encoding="utf-8") as f:
            change_log = json.load(f)
        diff_text = change_log.get("diff_text", "")

    invalid_patch_detected = any(p in diff_text for p in invalid_patches)

    primary_passed = pass1_result.primary_assertions_passed
    assertions = pass1_result.assertions_by_name

    if structural_match and not invalid_patch_detected:
        classification = "root_cause"
    elif primary_passed and not structural_match:
        classification = "workaround"
    elif not primary_passed:
        any_improvement = any(a.verdict == "pass" for a in assertions.values())
        classification = "partial" if any_improvement else "none"
    else:
        classification = "none"

    return {
        "structural_match": structural_match,
        "invalid_patch_detected": invalid_patch_detected,
        "classification": classification,
        "root_cause_addressed": classification == "root_cause",
    }
