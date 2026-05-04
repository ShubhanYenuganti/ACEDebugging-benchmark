import json
from harness.scoring.agent import call_scoring_agent, SYSTEM_PROMPT


def compute(
    verify_result: dict,
    manifest: dict,
    known_good_yaml: str,
    traffic_flow_md: str,
) -> dict:
    p2 = verify_result["pass2_regression"]
    critical = p2["critical_regression_count"]
    non_critical = p2["non_critical_regression_count"]
    details = p2.get("regressions", [])

    if critical > 1 or (critical >= 1 and non_critical >= 1):
        penalty = 0.28
    elif critical == 1:
        penalty = 0.18
    elif non_critical == 1:
        penalty = 0.08
    else:
        penalty = 0.00

    user_prompt = f"""=== known_good.yaml ===
{known_good_yaml}

=== traffic_flow.md ===
{traffic_flow_md}

=== Fault that was fixed ===
Resource:  {manifest["target_resource"]}
Property:  {manifest["target_property"]}

=== Regression test result ===
critical_regression_count:     {critical}
non_critical_regression_count: {non_critical}
regression_details:            {json.dumps(details)}

=== Deterministic regression penalty ===
{penalty}

=== Task ===
In exactly 1-2 sentences, explain what these regression results mean for
the architecture. If regressions are present, name the affected traffic-flow
hop using the hop labels in traffic_flow.md. If no regressions, state that
the fix did not affect other paths.

Return JSON only, no markdown fences:
{{"rationale": "<exactly 1-2 sentences>"}}"""

    raw = call_scoring_agent(SYSTEM_PROMPT, user_prompt)
    parsed = json.loads(raw)
    return {"penalty": penalty, "rationale": str(parsed["rationale"])}
