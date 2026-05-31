"""Retry penalty — reward getting the fix right sooner.

An agent that resolves the fault on its first submission scores higher than one
that only converges after several submit_fix → deploy cycles. The penalty is a
deterministic, graduated subtraction from the composite (mirrors regression.py),
keyed on the number of submission attempts recorded by the runner.

  attempts <= 1 → 0.00   (first-try success — no penalty)
  attempts == 2 → 0.05
  attempts == 3 → 0.10
  attempts == 4 → 0.15
  attempts >= 5 → 0.20   (capped)
"""

_STEP = 0.05
_CAP = 0.20


def compute(attempts: int) -> dict:
    extra = max(0, (attempts or 0) - 1)
    penalty = min(_CAP, round(_STEP * extra, 4))
    if extra == 0:
        rationale = "Fixed on the first submission; no retry penalty."
    else:
        plural = "attempt" if extra == 1 else "attempts"
        rationale = (
            f"Converged on submission attempt {attempts}; "
            f"{extra} extra {plural} → -{penalty:.2f}."
        )
    return {"penalty": penalty, "attempts": attempts or 0, "rationale": rationale}
