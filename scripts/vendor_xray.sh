#!/usr/bin/env bash
# Vendor aws-xray-sdk + the shared module into a handler package dir so the
# Lambda zip is self-contained. Usage: scripts/vendor_xray.sh <handler_dir>
set -euo pipefail
HANDLER_DIR="$1"
SHARED="$(dirname "$0")/../corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/deployment/lambda/_shared/xray_instrument.py"
cp "$SHARED" "$HANDLER_DIR/xray_instrument.py"
pip install aws-xray-sdk --target "$HANDLER_DIR" --quiet \
  --platform manylinux2014_aarch64 --python-version 3.11 \
  --only-binary=:all: --implementation cp 2>/dev/null \
  || pip install aws-xray-sdk --target "$HANDLER_DIR" --quiet
echo "vendored aws-xray-sdk + xray_instrument.py into $HANDLER_DIR"
