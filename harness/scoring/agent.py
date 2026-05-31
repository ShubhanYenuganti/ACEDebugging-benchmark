import os

import litellm

SCORING_MODEL = os.environ.get("ACE_SCORING_MODEL", "gpt-4o-2024-08-06")

SYSTEM_PROMPT = """You are an infrastructure debugging benchmark scorer.
You evaluate AI model runs against a known-good AWS architecture.

Each prompt provides exactly:
- known_good.yaml: the correct architecture template
- traffic_flow.md: how requests flow through the architecture
- fault_manifest fields: what was injected and what the correct value is
- tool_call_trace and/or verify_result: what the model did

Return ONLY valid JSON matching the schema in each prompt.
No markdown fences. No explanation outside the JSON.
Scores are floats. Use only the values listed in each rubric — no interpolation.
Rationale is exactly 1-2 sentences. Do not reference information not given in the prompt."""


def call_scoring_agent(system_prompt: str, user_prompt: str) -> str:
    response = litellm.completion(
        model=SCORING_MODEL,
        temperature=0,
        max_tokens=1024,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    return response.choices[0].message.content.strip()
