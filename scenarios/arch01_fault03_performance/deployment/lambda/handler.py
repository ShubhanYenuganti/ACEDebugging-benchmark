"""
deployment/lambda/handler.py — Processor Lambda
scenarios/arch01_fault03_performance/

This handler is structurally correct. The fault in this scenario is
the Lambda Timeout property in the CloudFormation template (3s instead
of 30s). Under light load the handler completes within the budget.
Under any realistic batch or concurrent load it will be terminated
mid-execution with "Task timed out after 3.00 seconds".

The model should identify the Timeout property as the root cause
rather than attempting to optimise this code.
"""

import boto3
import json
import os
import logging
from datetime import datetime, timezone

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ENDPOINT = "http://localhost:4566"
REGION = "us-east-1"
CREDS = {
    "aws_access_key_id": "test",
    "aws_secret_access_key": "test"
}

TABLE_NAME = os.environ.get("TABLE_NAME", "ace-bench-orders")
OUTPUT_SCHEMA = os.environ.get("OUTPUT_SCHEMA", "full")

ddb = boto3.client(
    "dynamodb",
    endpoint_url=ENDPOINT,
    region_name=REGION,
    **CREDS
)


def build_item(order: dict) -> dict:
    base = {
        "order_id": {"S": order["order_id"]},
        "status": {"S": "fulfilled"},
        "processed_at": {"S": datetime.now(timezone.utc).isoformat()}
    }
    if OUTPUT_SCHEMA == "full":
        base["item"] = {"S": str(order.get("item", ""))}
        base["quantity"] = {"N": str(order.get("quantity", 0))}
    return base


def process_record(record: dict) -> dict:
    body = record.get("body", "{}")
    if isinstance(body, str):
        order = json.loads(body)
    else:
        order = body
    item = build_item(order)
    logger.info(f"Writing order {order['order_id']} to {TABLE_NAME}")
    ddb.put_item(TableName=TABLE_NAME, Item=item)
    logger.info(f"Successfully wrote order {order['order_id']}")
    return {"order_id": order["order_id"], "status": "fulfilled"}


def handler(event, context):
    results = []
    failures = []
    for record in event.get("Records", []):
        try:
            result = process_record(record)
            results.append(result)
        except Exception as e:
            logger.error(f"Failed to process record: {e}", exc_info=True)
            failures.append({"itemIdentifier": record.get("messageId", "unknown")})
    response = {"processed": len(results), "failed": len(failures)}
    if failures:
        response["batchItemFailures"] = failures
    return response
