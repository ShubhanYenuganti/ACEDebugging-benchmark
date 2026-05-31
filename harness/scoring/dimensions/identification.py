import json
from harness.scoring.agent import call_scoring_agent, SYSTEM_PROMPT


def score(
    tool_trace: list,
    manifest: dict,
    verify_result: dict,
    known_good_yaml: str,
    traffic_flow_md: str,
    edit_trace: list | None = None,
) -> dict:
    """Score whether the model identified the fault before its first fix attempt.

    The fix-attempt boundary comes from `edit_trace` (the write_file / submit_fix
    record), NOT from the diagnostic tool_call_trace — file edits are never logged
    as MCP tool calls. The first `write_file` event is the fix attempt.
    """
    edit_trace = edit_trace or []
    write_turns = [e["turn"] for e in edit_trace if e.get("action") == "write_file"]
    first_fix_turn = min(write_turns) if write_turns else None

    tool_sequence = [
        {"turn": c["turn"], "tool": c["tool"], "input_keys": sorted(c["input"].keys())}
        for c in tool_trace
    ]
    edit_sequence = [
        {"turn": e["turn"], "action": e.get("action"), "path": e.get("path", "")}
        for e in edit_trace
    ]

    if first_fix_turn is not None:
        boundary_line = (
            f"First fix attempt (first write_file) at turn {first_fix_turn}. "
            "Only diagnostic calls with turn < this value count as 'before the fix'."
        )
    else:
        boundary_line = (
            "No write_file was recorded — no fix attempt is present. Treat every "
            "diagnostic call as occurring before the (absent) fix."
        )

    user_prompt = f"""=== known_good.yaml ===
{known_good_yaml}

=== traffic_flow.md ===
{traffic_flow_md}

=== Fault injected ===
Resource:  {manifest["target_resource"]}
Property:  {manifest["target_property"]}
Original:  {manifest["original_value"]}
Injected:  {manifest["injected_value"]}

=== Diagnostic tool-call sequence (MCP calls only, in order) ===
{json.dumps(tool_sequence, indent=2)}

=== File edits (write_file / submit_fix, in order) ===
{json.dumps(edit_sequence, indent=2)}

=== Fix-attempt boundary ===
{boundary_line}

=== Fix classification (deterministic, from verify loop) ===
{verify_result["pass3_classification"]["classification"]}

=== Task ===
Score whether the DIAGNOSTIC tool-call sequence demonstrates the model identified
the fault BEFORE its first fix attempt (the boundary turn above). Base your score
ONLY on diagnostic calls that occur before the boundary turn. Do NOT infer
identification from the fix result or classification.

Rubric (use these exact values only — no interpolation):
  1.0 — Target resource AND target property explicitly surfaced in a diagnostic
        tool call BEFORE the first fix attempt
  0.5 — Target resource surfaced before the first fix attempt, but target property
        was not, OR target property surfaced only at/after the first fix attempt
  0.3 — No explicit identification; model wrote to the correct resource without
        a prior diagnostic call targeting it
  0.1 — Model targeted the wrong resource; the fix worked coincidentally
  0.0 — No diagnostic call targeted the fault; the fix submission was blind

Return JSON only, no markdown fences:
{{"score": <one of: 1.0, 0.5, 0.3, 0.1, 0.0>, "rationale": "<exactly 1-2 sentences>"}}"""

    raw = call_scoring_agent(SYSTEM_PROMPT, user_prompt)
    parsed = json.loads(raw)
    return {"score": float(parsed["score"]), "rationale": str(parsed["rationale"])}
