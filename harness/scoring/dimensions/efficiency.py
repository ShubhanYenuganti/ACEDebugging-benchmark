import json
from harness.scoring.agent import call_scoring_agent, SYSTEM_PROMPT


def threshold_score(actual: int, optimal: int) -> float:
    """
    Score based on ratio of actual to optimal.
    - ratio <= 1.5: 1.0
    - 1.5 < ratio <= 2.5: linear interpolation from 1.0 to 0.6
    - 2.5 < ratio <= 4.0: linear interpolation from 0.6 to 0.0
    - ratio > 4.0: 0.0
    """
    if optimal == 0:
        return 1.0 if actual == 0 else 0.0

    ratio = actual / optimal

    if ratio <= 1.5:
        return 1.0
    elif ratio <= 2.5:
        # Linear interpolation from 1.0 to 0.6 over [1.5, 2.5]
        return 1.0 - 0.4 * (ratio - 1.5)
    elif ratio <= 4.0:
        # Linear interpolation from 0.6 to 0.0 over [2.5, 4.0]
        return 0.6 - 0.4 * (ratio - 2.5)
    else:
        return 0.0


def score(
    tool_trace: list,
    file_log: dict,
    manifest: dict,
    known_good_yaml: str,
    traffic_flow_md: str,
) -> dict:
    """
    Score efficiency of the model's diagnostic and fix process.

    Returns a dict with:
    - score: combined efficiency score (0.0-1.0)
    - rationale: 1-2 sentence explanation from the agent
    - tool_calls: {actual, optimal, ratio, score}
    - files_changed: {actual, optimal, ratio, score}
    - lines_changed: {actual, optimal, ratio, score}
    """
    actual_tc = len(tool_trace)
    actual_fc = file_log["total_files_changed"]
    actual_lc = file_log["total_lines_changed"]

    opt_tc = manifest["optimal_tool_calls"]
    opt_fc = manifest["optimal_files_changed"]
    opt_lc = manifest["optimal_lines_changed"]

    tc_score = threshold_score(actual_tc, opt_tc)
    fc_score = threshold_score(actual_fc, opt_fc)
    lc_score = threshold_score(actual_lc, opt_lc)

    combined = round((tc_score * 0.50) + (fc_score * 0.25) + (lc_score * 0.25), 4)

    tc_ratio = round(actual_tc / opt_tc, 2) if opt_tc else None
    fc_ratio = round(actual_fc / opt_fc, 2) if opt_fc else None
    lc_ratio = round(actual_lc / opt_lc, 2) if opt_lc else None

    tool_sequence = [c["tool"] for c in tool_trace]

    user_prompt = f"""=== known_good.yaml ===
{known_good_yaml}

=== traffic_flow.md ===
{traffic_flow_md}

=== Efficiency sub-scores (all computed deterministically — do not re-derive) ===
Tool calls:    actual={actual_tc}, optimal={opt_tc}, ratio={tc_ratio}, score={round(tc_score, 2)}
Files changed: actual={actual_fc}, optimal={opt_fc}, ratio={fc_ratio}, score={round(fc_score, 2)}
Lines changed: actual={actual_lc}, optimal={opt_lc}, ratio={lc_ratio}, score={round(lc_score, 2)}
Combined:      {combined}

=== Tool call sequence (tool names only, in order) ===
{json.dumps(tool_sequence)}

=== Task ===
In exactly 1-2 sentences, explain what the efficiency sub-scores reveal about
how the model approached diagnosis and fix. Note redundant calls or unnecessary
edits if present. Reference the traffic flow only if a specific hop explains
why calls were redundant or necessary.

Return JSON only, no markdown fences:
{{"rationale": "<exactly 1-2 sentences>"}}"""

    raw = call_scoring_agent(SYSTEM_PROMPT, user_prompt)
    parsed = json.loads(raw)

    return {
        "score": combined,
        "rationale": str(parsed["rationale"]),
        "tool_calls": {"actual": actual_tc, "optimal": opt_tc, "ratio": tc_ratio, "score": round(tc_score, 4)},
        "files_changed": {"actual": actual_fc, "optimal": opt_fc, "ratio": fc_ratio, "score": round(fc_score, 4)},
        "lines_changed": {"actual": actual_lc, "optimal": opt_lc, "ratio": lc_ratio, "score": round(lc_score, 4)},
    }
