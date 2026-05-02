import os

from botocore.exceptions import ClientError

from harness.runner.deployment_handler import _STACK_NAME
from harness.shared.localstack_client import cf_client

_FIXED_INSTRUCTION = (
    "A deployed instance of this system is running in your local environment. "
    "The deployment directory and CloudFormation template are available to you directly. "
    "Diagnostic tools are available via MCP. Diagnose the reported symptom, edit whatever "
    "files need changing, and redeploy using localstack-deployer when ready. "
    "Your first successful redeployment is your scored submission."
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
