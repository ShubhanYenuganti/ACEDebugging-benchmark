#!/usr/bin/env bash
# Benchmark an OpenAI/GPT model across the validated ACE-Bench scenarios
# (arch_01 + arch_12, 20 scenarios). Each scenario is a separate `python
# harness/run.py` invocation with its own --run-id, so the sweep is
# fully resumable: re-running this script skips any scenario whose
# results/<run-id>/score.json already exists.
#
# Usage:
#   scripts/benchmark_gpt.sh
#
# Environment overrides:
#   MODEL       LiteLLM model id   (default: openai/gpt-4.1-mini)
#   RUN_LABEL   Run-id prefix      (default: derived from MODEL)
#   ARCH_RE     Scenario name regex (default: '^(arch01|arch12)_')
#   FRESH       1 = ignore existing score.json and rerun everything
#
# Example: rerun with a different model, keeping prior results
#   MODEL=openai/gpt-4o RUN_LABEL=gpt4o scripts/benchmark_gpt.sh

set -u
set -o pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

MODEL="${MODEL:-openai/gpt-4.1-mini}"
DEFAULT_LABEL="$(echo "$MODEL" | tr '/:.' '___' | tr -cd '[:alnum:]_')"
RUN_LABEL="${RUN_LABEL:-$DEFAULT_LABEL}"
ARCH_RE="${ARCH_RE:-^(arch01|arch12)_}"
FRESH="${FRESH:-0}"

LOG_DIR="results/_benchmark_logs/${RUN_LABEL}"
SUMMARY_FILE="${LOG_DIR}/summary.tsv"
mkdir -p "$LOG_DIR"

# ── pre-flight checks ──────────────────────────────────────────────────────
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
fi

err=0
if [ -z "${HARNESS_API_KEY:-}" ]; then
    echo "ERROR: HARNESS_API_KEY not set. Generate one with:" >&2
    echo "  echo \"HARNESS_API_KEY=\$(openssl rand -hex 32)\" >> .env" >&2
    err=1
fi
if [ -z "${OPENAI_API_KEY:-}" ]; then
    echo "ERROR: OPENAI_API_KEY not set (required for $MODEL and the gpt-4o scorer)." >&2
    err=1
fi
if ! curl -fsS http://localhost:4566/_localstack/health >/dev/null 2>&1; then
    echo "ERROR: LocalStack not reachable at http://localhost:4566. Start it with:" >&2
    echo "  localstack start -d" >&2
    err=1
fi
[ "$err" -eq 0 ] || exit 1

# ── enumerate scenarios ────────────────────────────────────────────────────
SCENARIOS=()
while IFS= read -r line; do SCENARIOS+=("$line"); done < <(
    ls -d scenarios/*/ 2>/dev/null \
      | sed 's:/$::' \
      | awk -F/ -v re="$ARCH_RE" '$NF ~ re' \
      | sort
)

if [ "${#SCENARIOS[@]}" -eq 0 ]; then
    echo "ERROR: no scenarios matched regex '$ARCH_RE'" >&2
    exit 1
fi

echo "═══════════════════════════════════════════════════════════"
echo " ACE-Bench GPT sweep"
echo "   model      : $MODEL"
echo "   run label  : $RUN_LABEL"
echo "   scenarios  : ${#SCENARIOS[@]}"
echo "   logs       : $LOG_DIR"
echo "   resumable  : skip scenarios with existing score.json"
[ "$FRESH" = "1" ] && echo "   FRESH=1    : ignoring prior score.json, rerunning all"
echo "═══════════════════════════════════════════════════════════"
echo

# Reset summary on a fresh sweep, otherwise append.
if [ "$FRESH" = "1" ] || [ ! -f "$SUMMARY_FILE" ]; then
    printf "scenario\trun_id\tstatus\tfinal_score\texit_code\n" > "$SUMMARY_FILE"
fi

passed=0
failed=0
skipped=0

for scen in "${SCENARIOS[@]}"; do
    sid="$(basename "$scen")"
    run_id="${RUN_LABEL}__${sid}"
    score_file="results/${run_id}/score.json"

    if [ "$FRESH" != "1" ] && [ -f "$score_file" ]; then
        score=$(python3 -c "import json,sys; print(json.load(open('$score_file')).get('final_score',''))" 2>/dev/null || echo "")
        echo "[skip] $sid — score.json present (final_score=$score)"
        printf "%s\t%s\t%s\t%s\t%s\n" "$sid" "$run_id" "skip" "$score" "-" >> "$SUMMARY_FILE"
        skipped=$((skipped + 1))
        continue
    fi

    if [ "$FRESH" = "1" ] && [ -d "results/${run_id}" ]; then
        rm -rf "results/${run_id}"
    fi

    log_file="${LOG_DIR}/${sid}.log"
    echo "═════════════════════════════════════════════════════════════"
    echo "[run ] $sid  →  run-id=$run_id"
    echo "       log: $log_file"
    echo "═════════════════════════════════════════════════════════════"

    start_ts=$(date +%s)
    set +e
    python harness/run.py "$scen" \
        --run-id "$run_id" \
        --model "$MODEL" \
        2>&1 | tee "$log_file"
    rc=${PIPESTATUS[0]}
    set -e
    elapsed=$(( $(date +%s) - start_ts ))

    if [ -f "$score_file" ]; then
        score=$(python3 -c "import json; print(json.load(open('$score_file')).get('final_score',''))" 2>/dev/null || echo "")
        status="ok"
        passed=$((passed + 1))
    else
        score=""
        status="fail"
        failed=$((failed + 1))
    fi

    printf "%s\t%s\t%s\t%s\t%s\n" "$sid" "$run_id" "$status" "$score" "$rc" >> "$SUMMARY_FILE"
    echo "[done] $sid  status=$status  exit=$rc  score=$score  (${elapsed}s)"
    echo
done

# ── final summary ──────────────────────────────────────────────────────────
echo "═══════════════════════════════════════════════════════════"
echo " Sweep complete"
echo "   passed : $passed"
echo "   failed : $failed"
echo "   skipped: $skipped"
echo "   summary: $SUMMARY_FILE"
echo "═══════════════════════════════════════════════════════════"

# Tabular printout from the TSV.
column -t -s $'\t' "$SUMMARY_FILE" 2>/dev/null || cat "$SUMMARY_FILE"

# Exit non-zero if any scenario failed in this invocation.
[ "$failed" -eq 0 ]
