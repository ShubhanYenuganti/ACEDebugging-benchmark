"""
deployment/lambda/handler.py — Processor Lambda
scenarios/arch01_fault01_security/

Receives SQS messages containing order payloads, enriches them with
status and processed_at, and writes full records to DynamoDB.

Environment variables:
  TABLE_NAME    — DynamoDB table name (ace-bench-orders)
  OUTPUT_SCHEMA — "full" writes all fields; "minimal" writes only order_id and status
                  (OUTPUT_SCHEMA is intentionally configurable to support
                  the data-correctness fault class in arch01_fault05)

This handler is structurally correct. The fault in this scenario is
in the IAM policy, not in this code. The handler will raise
AccessDeniedException when it attempts to call PutItem, which is
the observable signal the benchmarked model must detect.
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
    """Build a DynamoDB item from an order payload.

    full schema:   order_id, item, quantity, status, processed_at
    minimal schema: order_id, status  (used to inject data correctness fault)
    """
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

    # This call will raise AccessDeniedException when IAM fault is active.
    # That exception propagates up and is logged by Lambda runtime.
    ddb.put_item(TableName=TABLE_NAME, Item=item)

    logger.info(f"Successfully wrote order {order['order_id']}")
    return {"order_id": order["order_id"], "status": "fulfilled"}


def handler(event, context):
    """SQS trigger handler. Processes each record in the batch."""
    results = []
    failures = []

    for record in event.get("Records", []):
        try:
            result = process_record(record)
            results.append(result)
        except Exception as e:
            logger.error(f"Failed to process record: {e}", exc_info=True)
            failures.append({"itemIdentifier": record.get("messageId", "unknown")})

    response = {
        "processed": len(results),
        "failed": len(failures)
    }

    if failures:
        # ReportBatchItemFailures — tell SQS which messages failed
        response["batchItemFailures"] = failures

    return response
