#!/usr/bin/env bash
# Deploy a known_good architecture and run its functional test.
# Confirms the test passes (and how long it takes) against correct infrastructure.
#
# Usage:
#   scripts/test_known_good.sh [arch_dir_name]
#
# Examples:
#   scripts/test_known_good.sh
#       # interactive: lists available architectures, prompts for selection
#
#   scripts/test_known_good.sh arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda
#       # run directly for the named architecture
#
#   ARCH=arch_12 scripts/test_known_good.sh
#       # partial prefix match — picks the first corpus dir matching "arch_12"
#
# Environment overrides:
#   ARCH          Partial name match for corpus dir (skips interactive prompt)
#   ENDPOINT      LocalStack endpoint (default: http://localhost:4566)
#   STACK_NAME    CloudFormation stack name (default: ace-bench-stack)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

ENDPOINT="${ENDPOINT:-http://localhost:4566}"
STACK_NAME="${STACK_NAME:-ace-bench-stack}"
PYTHON=".venv/bin/python"
DEPLOYER=".venv/bin/localstack-deployer"

# ── helpers ───────────────────────────────────────────────────────────────────

die() { echo "ERROR: $*" >&2; exit 1; }

check_localstack() {
  localstack status services 2>/dev/null | grep -q "running\|available" \
    || die "LocalStack does not appear to be running. Start it with: localstack start -d"
}

delete_stack() {
  "$PYTHON" - <<PYEOF
import boto3, time, sys
cf = boto3.client('cloudformation', endpoint_url='$ENDPOINT', region_name='us-east-1',
    aws_access_key_id='test', aws_secret_access_key='test')
try:
    status = cf.describe_stacks(StackName='$STACK_NAME')['Stacks'][0]['StackStatus']
except Exception as e:
    if 'does not exist' in str(e):
        print('  (no existing stack)')
        sys.exit(0)
    raise
if status == 'DELETE_COMPLETE':
    print('  (stack already deleted)')
    sys.exit(0)
print(f'  Deleting stack (current status: {status})...')
cf.delete_stack(StackName='$STACK_NAME')
for _ in range(60):
    time.sleep(3)
    try:
        s = cf.describe_stacks(StackName='$STACK_NAME')['Stacks'][0]['StackStatus']
        if s == 'DELETE_COMPLETE':
            break
    except Exception as e:
        if 'does not exist' in str(e):
            break
        raise
print('  Stack deleted.')
PYEOF
}

# ── resolve architecture ───────────────────────────────────────────────────────

IFS=$'\n' read -r -d '' -a ARCH_DIRS < <(find corpus -maxdepth 1 -mindepth 1 -type d | sort && printf '\0') || true
[[ ${#ARCH_DIRS[@]} -gt 0 ]] || die "No corpus directories found under corpus/"

# Positional arg takes priority, then ARCH env var, then interactive
ARCH_ARG="${1:-${ARCH:-}}"

if [[ -n "$ARCH_ARG" ]]; then
  SELECTED=""
  for d in "${ARCH_DIRS[@]}"; do
    base="$(basename "$d")"
    if [[ "$base" == "$ARCH_ARG" || "$base" == *"$ARCH_ARG"* ]]; then
      SELECTED="$d"
      break
    fi
  done
  [[ -n "$SELECTED" ]] || die "No corpus dir matching '$ARCH_ARG'. Available: $(printf '\n  %s' "${ARCH_DIRS[@]}")"
else
  echo "Available architectures:"
  for i in "${!ARCH_DIRS[@]}"; do
    printf "  [%d] %s\n" "$((i+1))" "$(basename "${ARCH_DIRS[$i]}")"
  done
  printf "\nSelect [1-%d]: " "${#ARCH_DIRS[@]}"
  read -r CHOICE
  [[ "$CHOICE" =~ ^[0-9]+$ && "$CHOICE" -ge 1 && "$CHOICE" -le "${#ARCH_DIRS[@]}" ]] \
    || die "Invalid selection."
  SELECTED="${ARCH_DIRS[$((CHOICE-1))]}"
fi

ARCH_NAME="$(basename "$SELECTED")"
KNOWN_GOOD="$SELECTED/known_good.yaml"
FUNC_TEST="$SELECTED/functional_test.py"

[[ -f "$KNOWN_GOOD" ]] || die "known_good.yaml not found: $KNOWN_GOOD"
[[ -f "$FUNC_TEST" ]]  || die "functional_test.py not found: $FUNC_TEST"

# ── run ───────────────────────────────────────────────────────────────────────

echo ""
echo "Architecture : $ARCH_NAME"
echo "Template     : $KNOWN_GOOD"
echo "Test         : $FUNC_TEST"
echo ""

check_localstack

echo "[1/3] Deleting any existing stack..."
delete_stack

echo ""
echo "[2/3] Deploying known_good.yaml..."
DEPLOY_START=$SECONDS
"$DEPLOYER" create-stack \
  --stack-name "$STACK_NAME" \
  --template "$KNOWN_GOOD"
DEPLOY_ELAPSED=$((SECONDS - DEPLOY_START))
echo "      Deploy completed in ${DEPLOY_ELAPSED}s."

echo ""
echo "[3/3] Running functional_test.py (timeout: none — runs to completion)..."
TEST_START=$SECONDS
set +e
"$PYTHON" "$FUNC_TEST"
TEST_RC=$?
set -e
TEST_ELAPSED=$((SECONDS - TEST_START))

echo ""
echo "────────────────────────────────────────────────────────────"
echo "Result   : $([ $TEST_RC -eq 0 ] && echo 'PASS' || echo 'FAIL')"
echo "Exit code: $TEST_RC"
echo "Test time: ${TEST_ELAPSED}s"
echo "────────────────────────────────────────────────────────────"
exit $TEST_RC
