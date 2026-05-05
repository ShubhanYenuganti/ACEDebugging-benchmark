def score(verify_result: dict) -> dict:
    p1 = verify_result["pass1_functional"]

    if p1["all_assertions_passed"]:
        s = 1.0
        rationale = "All assertions passed."
    elif p1["primary_assertions_passed"]:
        s = 0.6
        failed = p1["failed_assertion_names"]
        rationale = f"Primary assertions passed; secondary failures: {failed}."
    elif len(p1["failed_assertion_names"]) < len(p1["assertions"]):
        s = 0.3
        failed = p1["failed_assertion_names"]
        rationale = f"Partial pass; failed assertions: {failed}."
    else:
        s = 0.0
        rationale = "All assertions failed."

    return {"score": s, "rationale": rationale}
