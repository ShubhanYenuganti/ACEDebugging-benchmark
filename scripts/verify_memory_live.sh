#!/usr/bin/env bash
# Live verification of the agent memory layer (and the surrounding pipeline).
#
# Runs ONE real scenario through the inline agent against LocalStack, then audits
# the durable artifacts to confirm: the memory store is torn down after scoring,
# memory ops never leak into the diagnostic tool-call trace, memory_trace.json is
# written, and the run reaches scoring. This is the live counterpart to the
# deterministic unit/loop tests in tests/test_memory.py + tests/test_agent_loop.py.
#
# Usage:
#   scripts/verify_memory_live.sh
#
# Environment overrides:
#   MODEL            LiteLLM model id      (default: openai/gpt-4.1-mini)
#   SCENARIO         scenario dir          (default: scenarios/arch01_fault01_connectivity)
#   RUN_ID           results run id        (default: memverify__<scenario>)
#   REQUIRE_MEMORY   1 = hard-fail if the agent never writes to memory (default: 0 = warn)
#   MAX_TURNS        agent turn budget     (default: harness default)
#
# Cost note: this makes real model API calls and deploys to LocalStack.

set -u
set -o pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

MODEL="${MODEL:-openai/gpt-4.1-mini}"
SCENARIO="${SCENARIO:-scenarios/arch01_fault01_connectivity}"
REQUIRE_MEMORY="${REQUIRE_MEMORY:-0}"

SID="$(basename "$SCENARIO")"
RUN_ID="${RUN_ID:-memverify__${SID}}"

# ── pre-flight ─────────────────────────────────────────────────────────────
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
fi

err=0
if [ ! -d "$SCENARIO" ]; then
    echo "ERROR: scenario dir not found: $SCENARIO" >&2
    err=1
fi
if [ -z "${HARNESS_API_KEY:-}" ]; then
    echo "ERROR: HARNESS_API_KEY not set (required when using --model)." >&2
    err=1
fi
if [ -z "${OPENAI_API_KEY:-}" ]; then
    echo "ERROR: OPENAI_API_KEY not set (required for $MODEL and the scorer)." >&2
    err=1
fi
if ! curl -fsS http://localhost:4566/_localstack/health >/dev/null 2>&1; then
    echo "ERROR: LocalStack not reachable at http://localhost:4566. Start: localstack start -d" >&2
    err=1
fi
[ "$err" -eq 0 ] || exit 1

echo "═══════════════════════════════════════════════════════════"
echo " ACE-Bench memory-layer live verification"
echo "   model     : $MODEL"
echo "   scenario  : $SID"
echo "   run id    : $RUN_ID"
echo "   strict    : REQUIRE_MEMORY=$REQUIRE_MEMORY"
echo "═══════════════════════════════════════════════════════════"

# Fresh run dir so the audit reflects only this invocation.
rm -rf "results/${RUN_ID}"

EXTRA=()
[ -n "${MAX_TURNS:-}" ] && EXTRA+=(--max-turns "$MAX_TURNS")

set +e
python harness/run.py "$SCENARIO" \
    --run-id "$RUN_ID" \
    --model "$MODEL" \
    --verbose \
    ${EXTRA[@]+"${EXTRA[@]}"}
run_rc=$?
set -e 2>/dev/null || true

echo
echo "── agent run exit code: $run_rc ──"
echo

AUDIT_ARGS=("$RUN_ID")
[ "$REQUIRE_MEMORY" = "1" ] && AUDIT_ARGS+=(--require-memory)

python scripts/audit_memory_run.py "${AUDIT_ARGS[@]}"
audit_rc=$?

echo
if [ "$audit_rc" -eq 0 ]; then
    echo "✅ Live memory verification PASSED for $SID"
else
    echo "❌ Live memory verification FAILED for $SID (see audit output above)"
fi
exit "$audit_rc"
