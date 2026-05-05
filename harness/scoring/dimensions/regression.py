def compute(verify_result: dict) -> dict:
    p2 = verify_result["pass2_regression"]
    critical = p2["critical_regression_count"]
    non_critical = p2["non_critical_regression_count"]

    if critical > 1 or (critical >= 1 and non_critical >= 1):
        penalty = 0.28
        rationale = f"{critical} critical and {non_critical} non-critical regressions introduced."
    elif critical == 1:
        penalty = 0.18
        rationale = "1 critical regression introduced."
    elif non_critical == 1:
        penalty = 0.08
        rationale = "1 non-critical regression introduced."
    else:
        penalty = 0.00
        rationale = "No regressions."

    return {"penalty": penalty, "rationale": rationale}
