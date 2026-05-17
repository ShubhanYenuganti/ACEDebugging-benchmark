from harness.shared.assertion_parser import parse, parse_from_json_file, parse_with_fallback


def test_parse_pass_and_fail():
    out = (
        "ASSERT pass check_a: ok\n"
        "ASSERT fail check_b: bad value 42\n"
    )
    r = parse(out)
    assert len(r.assertions) == 2
    assert r.assertions[0].name == "check_a"
    assert r.assertions[0].verdict == "pass"
    assert r.assertions[1].verdict == "fail"
    assert r.assertions[1].message == "bad value 42"
    assert r.primary_assertions_passed is False
    assert r.all_assertions_passed is False
    assert r.crash_reason == ""


def test_secondary_failure_does_not_fail_primary():
    r = parse("ASSERT pass primary: ok\nASSERT fail optional_secondary: minor\n")
    assert r.primary_assertions_passed is True
    assert r.all_assertions_passed is False


def test_zero_assertions_synthesizes_failure():
    r = parse("Traceback (most recent call last):\nImportError: foo\n")
    assert r.crash_reason == "no_assertions_emitted"
    assert r.primary_assertions_passed is False
    assert any(a.name == "__no_assertions__" for a in r.assertions)


def test_empty_output_synthesizes_failure():
    r = parse("")
    assert r.crash_reason == "no_assertions_emitted"
    assert r.primary_assertions_passed is False


def test_nonzero_returncode_synthesizes_crash():
    r = parse("ASSERT pass a: ok\n", returncode=2)
    assert r.crash_reason == "exit_code_2"
    assert r.primary_assertions_passed is False
    assert any(a.name == "__test_crashed__" for a in r.assertions)


def test_zero_assertions_plus_crash_returns_both():
    r = parse("traceback...", returncode=137)
    names = [a.name for a in r.assertions]
    assert "__no_assertions__" in names
    assert "__test_crashed__" in names
    assert r.crash_reason == "no_assertions_emitted"  # first one wins


def test_all_pass_run_is_happy():
    r = parse("ASSERT pass a: ok\nASSERT pass b: ok\n", returncode=0)
    assert r.primary_assertions_passed is True
    assert r.all_assertions_passed is True
    assert r.crash_reason == ""


def test_parse_with_fallback_prefers_json(tmp_path):
    json_path = tmp_path / "r.json"
    json_path.write_text('{"assertions": [{"name": "a", "verdict": "pass", "message": "ok"}]}')
    r = parse_with_fallback("ASSERT fail b: bad\n", returncode=0, json_path=str(json_path))
    names = [a.name for a in r.assertions]
    assert names == ["a"]
    assert r.primary_assertions_passed is True


def test_parse_with_fallback_uses_stdout_when_json_absent(tmp_path):
    r = parse_with_fallback("ASSERT pass a: ok\n", returncode=0, json_path=str(tmp_path / "missing.json"))
    assert r.assertions[0].name == "a"


def test_parse_with_fallback_layers_crash_on_json(tmp_path):
    json_path = tmp_path / "r.json"
    json_path.write_text('{"assertions": [{"name": "a", "verdict": "pass", "message": ""}]}')
    r = parse_with_fallback("", returncode=137, json_path=str(json_path))
    names = [a.name for a in r.assertions]
    assert "__test_crashed__" in names
    assert r.crash_reason == "exit_code_137"
