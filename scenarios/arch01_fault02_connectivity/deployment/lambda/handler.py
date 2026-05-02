"""
deployment/lambda/handler.py — Processor Lambda
scenarios/arch01_fault02_connectivity/

This handler is structurally identical to the known-good handler.
The fault in this scenario is entirely in the CloudFormation template
(EventSourceMapping Enabled: false), not in this code. The function
never runs because SQS never invokes it.

The model may discover this by noticing the handler looks correct
after inspecting it, and then turning attention to the infrastructure
configuration — specifically the event source mapping state.
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
