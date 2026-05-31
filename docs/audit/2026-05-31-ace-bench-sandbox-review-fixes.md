# ACE-Bench Sandbox Review Fix Audit

Date: 2026-05-31

## Scope

This audit records the fixes made after the sandbox review of the ACE-Bench agent
debugging harness. The review treated LocalStack free-tier service coverage as
fixed and focused on harness logic, scoring, verification, packaging, and safety
affordances.

## Critical Fixes

### Retry Loop Scoring Mismatch

Original breakage: the harness allowed post-deploy edit/redeploy retries, but
`submitted.yaml` was only written on the first successful deploy. Live
infrastructure reflected the final retry, while Pass 3 structural matching
scored the stale first deploy.

Decision: keep the retry loop even though it diverges from the old
"first deploy is final" spec language. Re-snapshot `submitted.yaml` after every
successful deploy so scoring and live infrastructure refer to the same artifact.

Fix: `ScenarioRunner.deploy()` now writes `submitted.yaml` on every successful
deploy. The agent prompt/tool description now says later successful retries are
allowed but penalized.

Retry penalty decision: a later correct fix receives a graduated penalty:

- attempt 1: `0.00`
- attempt 2: `0.05`
- attempt 3: `0.10`
- attempt 4: `0.15`
- attempt 5 or later: `0.20` cap

Deploy attempts decision: `submission_attempts.json` records
`runner.submission_state.deploy_attempts`, which counts every `submit_fix` that
reaches the deployment path, including lint/deploy failures. That makes failed
submitted attempts part of the retry cost.

### Identification Scoring Had No Fix Boundary

Original breakage: the identification rubric referenced nonexistent mutation
tools (`ace_update_template`, `ace_apply_patch`), while actual edits happened
through local `write_file` calls that were intentionally excluded from
`tool_call_trace.json`.

Decision: preserve the diagnostic-only trace invariant and add a separate edit
trace.

Fix: `edit_trace.json` now records `write_file` and `submit_fix` events.
Identification scoring consumes both diagnostic trace and edit trace, using the
first `write_file` as the fix-attempt boundary.

### Regression Penalty Was Dead Code

Original breakage: the quality gate zeroed any run with regressions, so the
graduated regression penalty code could never affect a nonzero score.

Decision: regressions should apply graduated penalties rather than hard-zeroing
the run unless primary assertions/classification fail.

Fix: the quality gate now checks deployment quality and primary functional
correctness, while `regression.py` applies the existing `0.08 / 0.18 / 0.28`
penalty schedule.

## Remaining Review Fixes Executed

### Lambda Packaging Ceiling

Original breakage: helper modules and multi-file flat Lambda fixes were rejected
or skipped unless their own filename or parent directory matched an `S3Key`
stem.

Fix: deployment writes now allow `.py` helper files that sit beside exactly one
known flat Lambda source. Packaging then uploads the whole containing directory
for that Lambda, so helper modules are included in the zip.

Safety rule: if a helper file sits beside multiple known Lambda stems, it is not
auto-associated because ownership is ambiguous.

### Non-Scored Self-Test Affordance

Original breakage: agents had no way to validate a fix before spending a deploy.

Fix: a new `validate_fix` local file tool runs a non-deploy preview:

- `cfn-lint` on `faulted.yaml`
- package-source discovery for known `S3Key` stems
- missing package-source reporting

It does not submit, deploy, or affect scoring.

### Event-Driven Pass 4

Original breakage: Pass 4 only POSTed to an HTTP API endpoint. Event-driven
architectures with SNS/SQS front doors had no API endpoint, so concurrency was
silently skipped.

Fix: Pass 4 now supports manifest-driven event probes when no HTTP endpoint is
present:

- `concurrency_probe.type = "sqs"` sends `N` SQS messages and optionally checks
  backlog.
- `concurrency_probe.type = "sns"` publishes `N` SNS messages.

If neither HTTP endpoint nor event probe exists, Pass 4 returns an explicit
`{"skipped": true, "reason": "no_concurrency_probe"}` result.

### Scorer Determinism

Original breakage: the LLM grader used a mutable default model and no
temperature pin.

Fix: scoring now defaults to `gpt-4o-2024-08-06` and calls LiteLLM with
`temperature=0`. `ACE_SCORING_MODEL` can still override the model.

### Manifest Visibility During Agent Run

Original breakage: `fault_manifest.json` was restored after context building,
then protected only by `read_file` blocklists. `list_directory` could still
reveal its existence.

Fix: inline agent runs re-hide `fault_manifest.json` for the duration of the
agent loop and restore it in `finally`. `list_directory` also filters
`fault_manifest.json` if present.

### Tool Choice Rigidity

Original breakage: `tool_choice="required"` forced every model turn to be a tool
turn, preventing pure reasoning or final no-tool turns.

Fix: the agent loop now uses `tool_choice="auto"`.

### Max Turns Configuration

Original breakage: `max_turns=50` existed in `run_agent_loop()` but was not
exposed by `harness/run.py`.

Fix: `harness/run.py` now accepts `--max-turns`, defaulting to `50`, and passes
it to the inline agent loop.

### Scorer Banner Accuracy

Original breakage: `harness/run.py` printed a hard-coded "Claude Sonnet" scorer
label even when `SCORING_MODEL` differed.

Fix: the scorer startup line now prints the actual configured scoring model.

## Verification

Focused harness verification after the fixes:

```bash
.venv/bin/python -m pytest tests/test_agent_loop.py tests/test_runner.py tests/test_verify.py tests/test_scoring.py -q
```

Expected result at time of audit: `134 passed`.

Full repository `pytest` still collects multiple `corpus/*/functional_test.py`
files under the same module name and fails during collection before harness
tests run. That is separate from the fixes audited here.
