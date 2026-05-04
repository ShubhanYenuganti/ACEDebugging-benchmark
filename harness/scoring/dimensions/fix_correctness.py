import json
from harness.scoring.agent import call_scoring_agent, SYSTEM_PROMPT


def score(
    verify_result: dict,
    manifest: dict,
    known_good_yaml: str,
    traffic_flow_md: str,
) -> dict:
    p1 = verify_result["pass1_functional"]

    if p1["all_assertions_passed"]:
        s = 1.0
    elif p1["primary_assertions_passed"]:
        s = 0.6
    elif len(p1["failed_assertion_names"]) < len(p1["assertions"]):
        s = 0.3
    else:
        s = 0.0

    user_prompt = f"""=== known_good.yaml ===
{known_good_yaml}

=== traffic_flow.md ===
{traffic_flow_md}

=== Fault injected ===
Resource:  {manifest["target_resource"]}
Property:  {manifest["target_property"]}
Original:  {manifest["original_value"]}
Injected:  {manifest["injected_value"]}

=== Functional test result ===
all_assertions_passed:    {p1["all_assertions_passed"]}
primary_assertions_passed: {p1["primary_assertions_passed"]}
failed_assertion_names:   {json.dumps(p1["failed_assertion_names"])}
total_assertions:         {len(p1["assertions"])}

=== Deterministic correctness score ===
{s}

=== Task ===
In exactly 1-2 sentences, explain what this score reveals about the fix.
State which assertions passed or failed and what that indicates about whether
the fault was resolved. Reference the traffic flow only if a specific hop is
implicated by the failed assertions.

Return JSON only, no markdown fences:
{{"rationale": "<exactly 1-2 sentences>"}}"""

    raw = call_scoring_agent(SYSTEM_PROMPT, user_prompt)
    parsed = json.loads(raw)
    return {"score": s, "rationale": str(parsed["rationale"])}
