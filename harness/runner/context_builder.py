import os

from botocore.exceptions import ClientError

from harness.runner.deployment_handler import _STACK_NAME
from harness.shared.localstack_client import cf_client

_FIXED_INSTRUCTION = (
    "A deployed instance of this system is running in your local environment. "
    "Inspect the deployment directory and CloudFormation template with read_file "
    "and list_directory. Diagnostic tools are available via MCP to probe the "
    "running system. Diagnose the reported symptom, edit any broken files with "
    "write_file (deployment/ files or faulted.yaml), then call submit_fix to "
    "redeploy. submit_fix will refuse if no write_file has occurred — you must "
    "edit at least one file before submitting. Your first submit_fix is your "
    "scored submission."
)


def _get_stack_outputs() -> dict:
    try:
        res = cf_client.describe_stacks(StackName=_STACK_NAME)
        outputs = {}
        for o in res["Stacks"][0].get("Outputs", []):
            outputs[o["OutputKey"]] = o["OutputValue"]
        return outputs
    except ClientError:
        return {}


def build_context(scenario_dir: str) -> dict:
    scenario_dir = os.path.abspath(scenario_dir)
    manifest_path = os.path.join(scenario_dir, "fault_manifest.json")
    if os.path.isfile(manifest_path):
        raise ValueError(
            f"fault_manifest.json is readable from model-accessible path: {manifest_path}. "
            "Move it out of the scenario directory before running the harness."
        )

    with open(os.path.join(scenario_dir, "scenario.md"), "r", encoding="utf-8") as f:
        scenario_brief = f.read()

    return {
        "scenario_brief": scenario_brief,
        "template_path": os.path.join(scenario_dir, "faulted.yaml"),
        "deployment_dir": os.path.join(scenario_dir, "deployment"),
        "stack_outputs": _get_stack_outputs(),
        "instruction": _FIXED_INSTRUCTION,
    }


def corpus_dir_for_scenario(
    scenario_dir: "Path | str",
    corpus_root: "Path | str | None" = None,
) -> "Path":
    import re
    from pathlib import Path as _Path
    scenario_dir = _Path(scenario_dir).resolve()
    if corpus_root is None:
        corpus_root = scenario_dir.parent.parent / "corpus"
    corpus_root = _Path(corpus_root)
    m = re.match(r"arch(\d+)_", scenario_dir.name)
    if not m:
        raise ValueError(f"Cannot parse arch number from: {scenario_dir.name}")
    arch_num = m.group(1).zfill(2)
    candidates = [
        d for d in corpus_root.iterdir()
        if d.is_dir() and re.match(rf"arch_{arch_num}_", d.name)
    ]
    if not candidates:
        raise FileNotFoundError(
            f"No corpus directory for arch {arch_num} under {corpus_root}"
        )
    return candidates[0]
