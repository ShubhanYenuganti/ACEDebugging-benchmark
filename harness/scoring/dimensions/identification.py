import json
from harness.scoring.agent import call_scoring_agent, SYSTEM_PROMPT


def score(
    tool_trace: list,
    manifest: dict,
    verify_result: dict,
    known_good_yaml: str,
    traffic_flow_md: str,
) -> dict:
    tool_sequence = [
        {"turn": c["turn"], "tool": c["tool"], "input_keys": sorted(c["input"].keys())}
        for c in tool_trace
    ]

    user_prompt = f"""=== known_good.yaml ===
{known_good_yaml}

=== traffic_flow.md ===
{traffic_flow_md}

=== Fault injected ===
Resource:  {manifest["target_resource"]}
Property:  {manifest["target_property"]}
Original:  {manifest["original_value"]}
Injected:  {manifest["injected_value"]}

=== Model tool-call sequence (in order) ===
{json.dumps(tool_sequence, indent=2)}

=== Fix classification (deterministic, from verify loop) ===
{verify_result["pass3_classification"]["classification"]}

=== Task ===
Score whether the tool-call sequence demonstrates the model identified the fault
before submitting a fix. Base your score ONLY on the tool-call sequence above.
Do NOT infer identification from the fix result or classification.

A "fix attempt" is any call to ace_update_template, ace_apply_patch, or equivalent write tool.

Rubric (use these exact values only — no interpolation):
  1.0 — Target resource AND target property explicitly surfaced in a read/inspect
        tool call BEFORE the first fix attempt
  0.5 — Target resource surfaced before the first fix attempt, but target property
        was not, OR target property surfaced only after a fix attempt
  0.3 — No explicit identification; model wrote to the correct resource without
        a prior read/inspect call targeting it
  0.1 — Model targeted the wrong resource; the fix worked coincidentally
  0.0 — No tool calls targeted the fault; fix submission was blind

Return JSON only, no markdown fences:
{{"score": <one of: 1.0, 0.5, 0.3, 0.1, 0.0>, "rationale": "<exactly 1-2 sentences>"}}"""

    raw = call_scoring_agent(SYSTEM_PROMPT, user_prompt)
    parsed = json.loads(raw)
    return {"score": float(parsed["score"]), "rationale": str(parsed["rationale"])}
