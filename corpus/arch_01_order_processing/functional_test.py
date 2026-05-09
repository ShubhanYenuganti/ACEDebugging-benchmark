"""
functional_test.py — Order Processing Pipeline
corpus/arch_01_order_processing/

Structural contract:
  - Every assertion prints exactly one line:
      ASSERT [pass|fail] [assertion_name]: [message]
  - Assertion names without _secondary suffix are PRIMARY assertions.
  - Assertion names with _secondary suffix are SECONDARY (non-blocking
    for primary_assertions_passed but included in regression check).
  - Exit code 0 always — the harness reads output lines, not exit code.
  - All assertions run even if earlier ones fail — never short-circuit.
  - This file exercises the known-good deployment. The same file is run
    against the fixed deployment in Pass 1 of the verify loop.

LocalStack endpoint: http://localhost:4566
Fake credentials: accessKeyId=test, secretAccessKey=test, region=us-east-1
"""

import json
import sys
import time
import uuid

import boto3
import requests

ENDPOINT = "http://localhost:4566"
REGION = "us-east-1"
CREDS = {"aws_access_key_id": "test", "aws_secret_access_key": "test"}

cf = boto3.client("cloudformation", endpoint_url=ENDPOINT, region_name=REGION, **CREDS)
lmb = boto3.client("lambda", endpoint_url=ENDPOINT, region_name=REGION, **CREDS)
ddb = boto3.client("dynamodb", endpoint_url=ENDPOINT, region_name=REGION, **CREDS)
sqs = boto3.client("sqs", endpoint_url=ENDPOINT, region_name=REGION, **CREDS)

STACK_NAME = "ace-bench-stack"
TABLE_NAME = "ace-bench-orders"
QUEUE_NAME = "ace-bench-order-queue"
INGESTION_FN = "ace-bench-ingestion"
PROCESSOR_FN = "ace-bench-processor"


def emit(result: str, name: str, message: str = ""):
    print(f"ASSERT {result} {name}: {message}", flush=True)


def get_stack_output(key: str) -> str | None:
    try:
        stacks = cf.describe_stacks(StackName=STACK_NAME)["Stacks"]
        outputs = stacks[0].get("Outputs", [])
        for o in outputs:
            if o["OutputKey"] == key:
                return o["OutputValue"]
    except Exception:
        pass
    return None


def get_queue_url() -> str | None:
    try:
        return sqs.get_queue_url(QueueName=QUEUE_NAME)["QueueUrl"]
    except Exception:
        return None


# ── Assertion 1: Stack outputs present ────────────────────────────────────────


def assert_stack_outputs():
    name = "stack_outputs_present"
    api = get_stack_output("ApiEndpoint")
    table = get_stack_output("OrdersTableName")
    queue = get_stack_output("OrderQueueUrl")
    if api and table and queue:
        emit("pass", name, f"ApiEndpoint={api}")
    else:
        missing = [
            k
            for k, v in [
                ("ApiEndpoint", api),
                ("OrdersTableName", table),
                ("OrderQueueUrl", queue),
            ]
            if not v
        ]
        emit("fail", name, f"Missing outputs: {missing}")


# ── Assertion 2: Ingestion Lambda accepts a POST and returns 200 ───────────────


def assert_ingestion_accepts_post() -> dict | None:
    name = "ingestion_accepts_post"
    order_id = str(uuid.uuid4())
    payload = {"order_id": order_id, "item": "widget", "quantity": 2}
    try:
        response = lmb.invoke(
            FunctionName=INGESTION_FN,
            InvocationType="RequestResponse",
            Payload=json.dumps(
                {
                    "httpMethod": "POST",
                    "body": json.dumps(payload),
                    "headers": {"Content-Type": "application/json"},
                }
            ).encode(),
        )
        body = json.loads(response["Payload"].read())
        status = body.get("statusCode", 0)
        if status == 200:
            emit("pass", name, f"order_id={order_id} status=200")
            return {"order_id": order_id, "payload": payload}
        else:
            emit("fail", name, f"Expected 200 got {status}: {body}")
            return None
    except Exception as e:
        emit("fail", name, f"Exception: {e}")
        return None


# ── Assertion 3: Order enqueued to SQS after ingestion ────────────────────────


def assert_order_enqueued(order_id: str | None):
    name = "order_enqueued"
    if order_id is None:
        emit("fail", name, "Skipped — ingestion assertion did not return order_id")
        return
    queue_url = get_queue_url()
    if not queue_url:
        emit("fail", name, "Could not resolve queue URL")
        return
    try:
        attrs = sqs.get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=[
                "ApproximateNumberOfMessages",
                "ApproximateNumberOfMessagesNotVisible",
            ],
        )["Attributes"]
        visible = int(attrs.get("ApproximateNumberOfMessages", 0))
        in_flight = int(attrs.get("ApproximateNumberOfMessagesNotVisible", 0))
        total = visible + in_flight
        if total > 0:
            emit(
                "pass",
                name,
                f"queue depth={total} (visible={visible} in_flight={in_flight})",
            )
        else:
            emit("fail", name, "Queue is empty after ingestion — message not enqueued")
    except Exception as e:
        emit("fail", name, f"Exception: {e}")


# ── Assertion 4: Record written to DynamoDB by processor ──────────────────────


def assert_record_written(order_id: str | None):
    name = "record_written"
    if order_id is None:
        emit("fail", name, "Skipped — no order_id from upstream assertions")
        return
    # Allow up to 10s for processor to consume from SQS and write to DDB
    deadline = time.time() + 10
    found = False
    item = None
    while time.time() < deadline:
        try:
            response = ddb.get_item(
                TableName=TABLE_NAME, Key={"order_id": {"S": order_id}}
            )
            if "Item" in response:
                found = True
                item = response["Item"]
                break
        except Exception:
            pass
        time.sleep(0.5)
    if found:
        emit("pass", name, f"order_id={order_id} item_keys={list(item.keys())}")
    else:
        emit("fail", name, f"order_id={order_id} not found in table after 10s")


# ── Assertion 5: Written record contains full schema fields ───────────────────


def assert_record_schema(order_id: str | None):
    name = "record_schema_complete"
    if order_id is None:
        emit("fail", name, "Skipped — no order_id from upstream assertions")
        return
    try:
        response = ddb.get_item(TableName=TABLE_NAME, Key={"order_id": {"S": order_id}})
        item = response.get("Item", {})
        required_fields = {"order_id", "item", "quantity", "status", "processed_at"}
        present = set(item.keys())
        missing = required_fields - present
        if not missing:
            emit("pass", name, f"all required fields present: {sorted(present)}")
        else:
            emit("fail", name, f"missing fields: {sorted(missing)}")
    except Exception as e:
        emit("fail", name, f"Exception: {e}")


# ── Assertion 6 (secondary): Processor function has event source mapping ──────


def assert_event_source_mapping_secondary():
    name = "event_source_mapping_enabled_secondary"
    try:
        mappings = lmb.list_event_source_mappings(FunctionName=PROCESSOR_FN)[
            "EventSourceMappings"
        ]
        enabled = [m for m in mappings if m.get("State") == "Enabled"]
        if enabled:
            emit("pass", name, f"active mappings: {len(enabled)}")
        else:
            emit(
                "fail",
                name,
                f"no enabled event source mappings (total={len(mappings)})",
            )
    except Exception as e:
        emit("fail", name, f"Exception: {e}")


# ── Assertion 7 (secondary): DynamoDB table exists and is ACTIVE ──────────────


def assert_table_active_secondary():
    name = "table_active_secondary"
    try:
        response = ddb.describe_table(TableName=TABLE_NAME)
        status = response["Table"]["TableStatus"]
        if status == "ACTIVE":
            emit("pass", name, f"table={TABLE_NAME} status=ACTIVE")
        else:
            emit("fail", name, f"table status={status}")
    except Exception as e:
        emit("fail", name, f"Exception: {e}")


# ── Assertion 8 (secondary): Ingestion role has sqs:SendMessage ───────────────


def assert_ingestion_iam_secondary():
    name = "ingestion_iam_send_message_secondary"
    iam = boto3.client("iam", endpoint_url=ENDPOINT, region_name=REGION, **CREDS)
    try:
        role = iam.get_role(RoleName="ace-bench-ingestion-role")["Role"]
        policies = iam.list_role_policies(RoleName="ace-bench-ingestion-role")[
            "PolicyNames"
        ]
        found_send = False
        for policy_name in policies:
            doc = iam.get_role_policy(
                RoleName="ace-bench-ingestion-role", PolicyName=policy_name
            )["PolicyDocument"]
            for stmt in doc.get("Statement", []):
                actions = stmt.get("Action", [])
                if isinstance(actions, str):
                    actions = [actions]
                if "sqs:SendMessage" in actions:
                    found_send = True
        if found_send:
            emit("pass", name, "sqs:SendMessage found in ingestion role policy")
        else:
            emit("fail", name, "sqs:SendMessage not found in ingestion role policy")
    except Exception as e:
        emit("fail", name, f"Exception: {e}")


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Run all assertions. Collect order_id from ingestion to thread through.
    assert_stack_outputs()
    ingestion_result = assert_ingestion_accepts_post()
    order_id = ingestion_result["order_id"] if ingestion_result else None
    assert_order_enqueued(order_id)
    assert_record_written(order_id)
    assert_record_schema(order_id)
    # Secondary assertions
    assert_event_source_mapping_secondary()
    assert_table_active_secondary()
    assert_ingestion_iam_secondary()
