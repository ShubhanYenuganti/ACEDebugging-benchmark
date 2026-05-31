import json
from harness.scoring.agent import call_scoring_agent, SYSTEM_PROMPT


def check_gate(verify_result: dict) -> bool:
    """Quality gate: did the run actually address the fault?

    The gate guards two things only — a valid fix classification and passing
    primary assertions. Regressions are NOT a gate condition: they are penalised
    gradually in the composite via the regression dimension (see scorer.py), so a
    fix that works but introduces a regression earns a reduced — not zero — score.
    """
    p1 = verify_result["pass1_functional"]
    p3 = verify_result["pass3_classification"]
    classification_ok = p3["classification"] in ("root_cause", "workaround")
    assertions_ok = p1["primary_assertions_passed"]
    return classification_ok and assertions_ok


def score(
    verify_result: dict,
    manifest: dict,
    file_log: dict,
    known_good_yaml: str,
    traffic_flow_md: str,
) -> dict:
    p1 = verify_result["pass1_functional"]
    p3 = verify_result["pass3_classification"]

    user_prompt = f"""=== known_good.yaml ===
{known_good_yaml}

=== traffic_flow.md ===
{traffic_flow_md}

=== Fault injected ===
Resource:  {manifest["target_resource"]}
Property:  {manifest["target_property"]}
Original:  {manifest["original_value"]}
Injected:  {manifest["injected_value"]}

=== Valid fixes (any of these constitutes a correct root-cause fix) ===
{json.dumps(manifest["valid_fixes"], indent=2)}

=== Invalid patches (resolve the symptom but not the root cause) ===
{json.dumps(manifest["invalid_patches"], indent=2)}

=== Files the model changed ===
{json.dumps(file_log["files_modified"] + file_log["files_added"], indent=2)}

=== Line-level changes per file ===
{json.dumps(file_log["per_file_line_changes"], indent=2)}

=== Verify loop results (deterministic) ===
fix_classification:           {p3["classification"]}
structural_match:             {p3["structural_match"]}
invalid_patch_detected:       {p3["invalid_patch_detected"]}
primary_assertions_passed:    {p1["primary_assertions_passed"]}
all_assertions_passed:        {p1["all_assertions_passed"]}

=== Task ===
Score the quality of the fix using known_good.yaml as the reference for what
the correct state looks like. Determine whether the model's changes match a
valid fix, an invalid patch, or neither.

Rubric (use these exact values only — no interpolation):
  1.00 — Root cause addressed: change matches a valid fix, no regressions,
          implementation is minimal (does not change more than necessary)
  0.85 — Root cause addressed: change matches a valid fix but touches
          additional properties or resources beyond what is needed
  0.60 — Root cause addressed via an over-permissive fix (e.g. wildcard IAM
          Resource, overly broad managed policy) — not in valid_fixes list
  0.35 — Workaround: symptom resolved but change matches an invalid_patch
          or does not address the root-cause property
  0.15 — Partial: primary assertion still fails; some improvement visible
  0.00 — No meaningful change from the faulted state

Return JSON only, no markdown fences:
{{
  "score": <one of: 1.00, 0.85, 0.60, 0.35, 0.15, 0.00>,
  "classification": "<one of: root_cause, workaround, partial, none>",
  "rationale": "<exactly 1-2 sentences>"
}}"""

    raw = call_scoring_agent(SYSTEM_PROMPT, user_prompt)
    parsed = json.loads(raw)
    return {
        "score": float(parsed["score"]),
        "classification": str(parsed["classification"]),
        "rationale": str(parsed["rationale"]),
    }
