import anthropic

client = anthropic.Anthropic()
SCORING_MODEL = "claude-sonnet-4-6"

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
    message = client.messages.create(
        model=SCORING_MODEL,
        max_tokens=1024,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return message.content[0].text.strip()
