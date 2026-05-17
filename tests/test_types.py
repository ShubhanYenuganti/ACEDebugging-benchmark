from harness.shared.types import (
    AssertionResult,
    AssertionRunResult,
    DeploymentResult,
    LambdaUpload,
    PackagingPlan,
)


def test_deployment_result_success_property():
    assert DeploymentResult(outcome="deploy_success").success is True
    assert DeploymentResult(outcome="deploy_fail").success is False
    assert DeploymentResult(outcome="no_changes").success is False


def test_deployment_result_default_lists_are_empty():
    r = DeploymentResult(outcome="lint_fail")
    assert r.skipped_lambda_files == []
    assert r.packaged_files == []
    assert r.lint_errors == []
    assert r.cfn_events == []
    assert r.error == ""


def test_packaging_plan_emptiness():
    empty = PackagingPlan()
    assert empty.has_packaging_work is False
    assert empty.has_orphans is False

    with_uploads = PackagingPlan(uploads=[LambdaUpload(
        rel_path="lambda/h.py", stem="h", s3_key_original="h.zip",
        s3_key_new="lambdas/r/abc/h.zip", sha256="abc", arcname="index.py",
    )])
    assert with_uploads.has_packaging_work is True

    with_template = PackagingPlan(template_changed=True)
    assert with_template.has_packaging_work is True

    with_orphans = PackagingPlan(orphans=["lambda/typo.py"])
    assert with_orphans.has_orphans is True


def test_assertion_result_is_secondary_detection():
    assert AssertionResult(name="foo", verdict="pass").is_secondary is False
    assert AssertionResult(name="foo_secondary", verdict="pass").is_secondary is True
    assert AssertionResult(name="latency_secondary_check", verdict="fail").is_secondary is True


def test_assertion_run_result_passed_failed_partition():
    r = AssertionRunResult(assertions=[
        AssertionResult(name="a", verdict="pass"),
        AssertionResult(name="b", verdict="fail", message="oops"),
        AssertionResult(name="c_secondary", verdict="fail"),
    ])
    assert [a.name for a in r.passed] == ["a"]
    assert [a.name for a in r.failed] == ["b", "c_secondary"]
    assert r.primary_failed_names == ["b"]
    assert r.all_failed_names == ["b", "c_secondary"]
    assert r.primary_assertions_passed is False
    assert r.all_assertions_passed is False


def test_assertion_run_result_all_pass_when_only_secondary_failed():
    r = AssertionRunResult(assertions=[
        AssertionResult(name="primary", verdict="pass"),
        AssertionResult(name="opt_secondary", verdict="fail"),
    ])
    assert r.primary_assertions_passed is True
    assert r.all_assertions_passed is False


def test_assertion_run_result_crash_reason_overrides_passed():
    # Even if every emitted assertion passed, a crash means we don't trust the run.
    r = AssertionRunResult(
        assertions=[AssertionResult(name="a", verdict="pass")],
        returncode=2,
        crash_reason="exit code 2",
    )
    assert r.primary_assertions_passed is False
    assert r.all_assertions_passed is False


def test_assertion_run_result_by_name_lookup():
    r = AssertionRunResult(assertions=[
        AssertionResult(name="a", verdict="pass"),
        AssertionResult(name="b", verdict="fail"),
    ])
    by_name = r.assertions_by_name
    assert by_name["a"].verdict == "pass"
    assert by_name["b"].verdict == "fail"
