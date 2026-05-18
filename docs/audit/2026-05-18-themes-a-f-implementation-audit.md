# Themes A–F Implementation Audit (2026-05-18)

Cross-checks each completed theme commit (`909014a..HEAD`) against the corresponding
`docs/superpowers/plans/2026-05-1{7,8}-theme-{a,b,c,d,e,f}-*.md` plan. Every
deviation is recorded with the plan's exact requirement, the as-shipped behavior,
and the corrective patch applied in this audit pass.

Audit status legend:
- ✅ Aligned with plan
- 🔧 Deviation — fixed in this audit
- ⚠️ Deliberate deviation — kept (with rationale)

Final state: **all listed deviations fixed (verified — see "Verification" section)**; 170 tests pass, 1 skipped.

---

## Theme A — YAML-driven Lambda packaging

Plan: `2026-05-17-theme-a-template-packaging.md`

| Plan requirement | As-shipped | Status |
|---|---|---|
| `template_parser.extract_s3key_stems()` with CFN-intrinsic-safe loader | implemented | ✅ |
| `LambdaUpload.source_path` + `is_dir` | added with defaults | ✅ |
| `_check_lambda_orphan` walks all of `deployment/` and accepts dir or file stems | implemented | ✅ |
| `find_source_for_stem` prefers directory then flat file, walks all of `deployment_dir` | implemented | ✅ |
| `_zip_dir` preserves relative paths | implemented | ✅ |
| `_build_packaging_plan` uses YAML-parsed stems and raises `ValueError` on stem collision | implemented | ✅ |
| `_upload_initial_lambda_zips` uses same helpers | implemented | ✅ |
| `plan.orphans` semantics: plan said append `stem`; production also appends full path for unmatched `.py` files | ⚠️ Deliberate — preserves pre-existing `test_handle_submission_reports_skipped_lambda_files` contract that checks `"lambda/typo_handler.py" in skipped_lambda_files` |

No functional misalignment.

---

## Theme B — Text-mode tool-call extraction

Plan: `2026-05-17-theme-b-text-mode-tool-calls-design.md`

This theme had the largest divergence. The plan asks for a substantively
different control flow than what landed.

| Plan requirement | As-shipped (pre-audit) | Status |
|---|---|---|
| `_extract_text_tool_calls` is fence-only and returns parse failures | implemented as `tuple[list \| None, list[dict]]` | ✅ |
| Replace `retried_no_tool: bool` with `consecutive_no_tool_failures: int` | still `retried_no_tool: bool` | 🔧 |
| `max_no_tool_failures` parameter (default 3) | absent | 🔧 |
| Never `break` on parse failure — let `max_turns` exhaust | `break` after second no-tool turn | 🔧 |
| Include first-300-char content preview in retry messages | retry message is fixed text, no preview | 🔧 |
| Reset counter to 0 on successful tool call | not implemented (boolean was reset but no counter existed) | 🔧 |
| Escalated message after ≥ `max_no_tool_failures` consecutive failures | not implemented | 🔧 |
| `result_logger.log_text_mode_failure(run_id, turn, raw, error)` | shipped as `_append_text_mode_failures` private helper inside `loop.py` | 🔧 |

### Fixes applied ✅
- `log_text_mode_failure(run_id, turn, raw, error)` now lives in
  `harness/shared/result_logger.py`. The loop-side `_append_text_mode_failures`
  is a thin forwarder.
- `retried_no_tool: bool` replaced by `consecutive_no_tool_failures: int = 0`.
- `max_no_tool_failures: int = 3` added as a `run_agent_loop` parameter.
- Removed the silent `break` on the no-tool path; the loop now appends a
  retry message and `continue`s, relying on `max_turns` to exhaust naturally.
- Both retry messages (standard and escalated) now include `msg.content[:300]`
  as a preview block so the model can self-correct.
- Escalated message fires once `consecutive_no_tool_failures >= max_no_tool_failures`.
- The counter resets to 0 after any successful tool dispatch
  (`consecutive_no_tool_failures = 0` line right before the dispatch loop).

---

## Theme C — State-machine safety

Plan: `2026-05-18-theme-c-state-machine-safety.md`

| Plan requirement | As-shipped (pre-audit) | Status |
|---|---|---|
| `_recover_hidden_manifest()` is idempotent and "does nothing if the manifest is present" | also deletes the `.hidden` file when both exist | 🔧 Tightened to spec |
| `RESULTS_DIR = "results"` module constant in `scenario_runner.py` so tests can patch | absent — hardcoded literal `"results"` | 🔧 |
| `submitted.yaml` snapshot via `shutil.copy2` | manual read/write open/open pair | 🔧 |
| `initial_deployment_outcome` field on `SubmissionState` | present | ✅ |
| Field set **inside** the same `with self._lock` block as `submitted=True` | set outside the lock | 🔧 |
| `_initial_deployment_outcome` property (underscore-prefixed, internal) | shipped as `initial_deployment_outcome` (public) | 🔧 Added underscore variant as the canonical accessor |
| `_resolve_deployment_outcome(runner)` helper in `run.py` | inline logic in Step 8 | 🔧 |

### Fixes applied ✅
- `_recover_hidden_manifest` now only acts when `.hidden` is present without a
  real manifest. The "both exist" branch was removed.
- `RESULTS_DIR = "results"` module constant added to `scenario_runner.py`.
- `_write_submitted_yaml` rewritten to use `shutil.copy2`.
- `initial_deployment_outcome` assignment moved inside the same
  `with self._lock` block as `submitted = True`.
- Property renamed `initial_deployment_outcome → _initial_deployment_outcome`
  (underscore-prefixed internal accessor per spec).
- `_resolve_deployment_outcome(runner)` added to `run.py`; Step 8 now calls it
  and the verify loop receives the resolved outcome.

---

## Theme D — Score signal accuracy

Plan: `2026-05-18-theme-d-score-signal-accuracy.md`

| Plan requirement | As-shipped (pre-audit) | Status |
|---|---|---|
| `_cfn_normalize()` with `_CFN_INTRINSIC_TAGS` set | implemented | ✅ |
| Applied to both sides of `structural_match` equality | implemented | ✅ |
| Pass 4: `should_run` gates on `fault_class` only; endpoint guard inside `run_pass4` | `Pass4Step.run` does both checks; `run_pass4` has no endpoint guard | 🔧 |
| Pass 4 skip dict reason `"no_api_endpoint"` | shipped as `"not_applicable"` | 🔧 |
| `diff_text` in `diff_snapshots` return value | implemented | ✅ |

### Fixes applied ✅
- `Pass4Step.should_run` now gates on `fault_class` only.
- `Pass4Step.run` simply calls `run_pass4`; the `api_endpoint` guard moved
  into `run_pass4` itself.
- `PASS4_SKIP_RESULT` reason changed from `"not_applicable"` to
  `"no_api_endpoint"` to match the spec's distinguishing intent.
- `test_pass4_skipped_for_non_performance_fault_class` updated to assert
  `None` (pipeline's `should_run=False` path) — this matches the plan's
  scope, which fixes only the missing-endpoint case, not the wrong-class
  case (still handled by pipeline storing `None`).

---

## Theme E — I/O safety

Plan: `2026-05-18-theme-e-io-safety.md`

| Plan requirement | As-shipped (pre-audit) | Status |
|---|---|---|
| `cfn-lint` absence returns warning dict with `rule="HARNESS_WARN_001"` | implemented (with extra `location` key, harmless) | ✅ |
| `READ_MAX_BYTES = 1_048_576`, guarded by `target.stat().st_size` | implemented | ✅ |
| `WRITE_MAX_BYTES = 524_288` | implemented | ✅ |
| Write content size check is **at the very start of the `write_file` branch (before path resolution)** | implemented **after** path-permission and `_safe_resolve` checks | 🔧 |

### Fixes applied ✅
- The `len(content.encode("utf-8")) > WRITE_MAX_BYTES` check is now the first
  statement in the `write_file` branch (line 173 of `tools.py`), before path
  permission, traversal, orphan, and unchanged-content checks. Fails fast.

---

## Theme F — Naming, dead code, baseline idempotency

Plan: `2026-05-18-theme-f-naming-dead-code.md`

| Plan requirement | As-shipped (pre-audit) | Status |
|---|---|---|
| `corpus_dir_for_scenario` regex `r"arch_?(\d+)_"` | implemented | ✅ |
| Delete `intercept_tool_call`, `tool_call_count`, and their test | implemented | ✅ |
| `run_pass1` reads `baseline_idempotent` from manifest and skips when `false` | implemented | ✅ |
| Skip return dict shape: `{"baseline": "skipped_non_idempotent", "passed": None, "baseline_passed": None}` | shipped as `{"skipped": True, "reason": "baseline_not_idempotent"}` | 🔧 |

### Fixes applied ✅
- Pass 1 skip return dict shape changed from `{"skipped": ..., "reason": ...}`
  to `{"baseline": "skipped_non_idempotent", "passed": None, "baseline_passed": None}`
  as the plan specifies. Downstream Pass 2/3 logic now reads
  `baseline_passed` and the human-readable `baseline` field.
- `ctx.pass1_result` is still populated with an `AssertionRunResult` carrying
  `crash_reason="skipped_non_idempotent"` so downstream Python-side callers
  still see a typed object.

---

## Verification

After applying all fixes above:

```
pytest tests/ -q
# 170 passed, 1 skipped
```

All deviations are closed; no functional regressions.
