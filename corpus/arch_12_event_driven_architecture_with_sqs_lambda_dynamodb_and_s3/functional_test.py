"""
functional_test.py - Event-driven architecture with SQS, Lambda, DynamoDB, and S3
corpus/arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3/

Verifies that a CSV upload is parsed into queue messages and persisted as DynamoDB inventory update records.

Output format: ASSERT pass|fail [name]: [message]
Primary assertions: no suffix - all must pass for a run to score
Secondary assertions: _secondary suffix - tracked for regression check
Exit code: always 0
"""

import sys
import time
import uuid

import boto3

from harness.shared.functional_test_helpers import emit_fail, emit_pass, finalize

ENDPOINT = "http://localhost:4566"
REGION = "us-east-1"
CREDS = {"aws_access_key_id": "test", "aws_secret_access_key": "test"}
STACK_NAME = "ace-bench-stack"
WAIT_TIMEOUT_SECONDS = 120


def client(service):
    return boto3.client(service, endpoint_url=ENDPOINT, region_name=REGION, **CREDS)


def get_stack_output(key):
    stacks = client("cloudformation").describe_stacks(StackName=STACK_NAME).get("Stacks", [])
    for output in stacks[0].get("Outputs", []):
        if output.get("OutputKey") == key:
            return output.get("OutputValue")
    raise KeyError(f"Missing CloudFormation output: {key}")


def wait_for_table_records(table_name, product_id, expected_count):
    ddb = client("dynamodb")
    deadline = time.time() + WAIT_TIMEOUT_SECONDS
    last = []
    while time.time() < deadline:
        response = ddb.scan(
            TableName=table_name,
            FilterExpression="product_id = :p",
            ExpressionAttributeValues={":p": {"S": product_id}},
        )
        last = response.get("Items", [])
        if len(last) >= expected_count:
            return True, last
        time.sleep(2)
    return False, last


def assert_csv_uploaded(bucket_name, key, body):
    client("s3").put_object(Bucket=bucket_name, Key=key, Body=body.encode("utf-8"))
    head = client("s3").head_object(Bucket=bucket_name, Key=key)
    if head.get("ContentLength", 0) > 0:
        emit_pass("csv_uploaded", f"key={key}")
    else:
        emit_fail("csv_uploaded", "uploaded object has no content")


def assert_inventory_records_written(table_name, product_id, expected_count):
    ok, items = wait_for_table_records(table_name, product_id, expected_count)
    if ok:
        emit_pass("inventory_records_written", f"records={len(items)}")
    else:
        emit_fail("inventory_records_written", f"records={len(items)}, items={items}")


def assert_queue_drained_terminal_state(queue_url):
    attrs = client("sqs").get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
    )["Attributes"]
    visible = int(attrs.get("ApproximateNumberOfMessages", "0"))
    hidden = int(attrs.get("ApproximateNumberOfMessagesNotVisible", "0"))
    if visible == 0 and hidden == 0:
        emit_pass("queue_drained_terminal_state", "queue has no visible or in-flight messages")
    else:
        emit_fail("queue_drained_terminal_state", f"visible={visible}, in_flight={hidden}")


def assert_table_active_secondary(table_name):
    status = client("dynamodb").describe_table(TableName=table_name)["Table"]["TableStatus"]
    if status == "ACTIVE":
        emit_pass("table_active_secondary", f"status={status}")
    else:
        emit_fail("table_active_secondary", f"status={status}")


def assert_bucket_notification_secondary(bucket_name):
    config = client("s3").get_bucket_notification_configuration(Bucket=bucket_name)
    lambdas = config.get("LambdaFunctionConfigurations", [])
    if lambdas:
        emit_pass("bucket_notification_secondary", f"lambda_notifications={len(lambdas)}")
    else:
        emit_fail("bucket_notification_secondary", "no Lambda notification configuration found")


def assert_queue_mapping_enabled_secondary(function_name, queue_arn):
    mappings = client("lambda").list_event_source_mappings(FunctionName=function_name).get("EventSourceMappings", [])
    enabled = [m for m in mappings if m.get("EventSourceArn") == queue_arn and m.get("State") in {"Enabled", "Enabling"}]
    if enabled:
        emit_pass("queue_mapping_enabled_secondary", "SQS event source mapping is enabled")
    else:
        emit_fail("queue_mapping_enabled_secondary", "SQS event source mapping is not enabled")


if __name__ == "__main__":
    try:
        bucket = get_stack_output("InventoryUpdatesBucketName")
        queue_url = get_stack_output("InventoryUpdatesQueueUrl")
        queue_arn = get_stack_output("InventoryUpdatesQueueArn")
        table = get_stack_output("InventoryUpdatesTableName")
        consumer = get_stack_output("SQSToDynamoDBFunctionName")

        suffix = str(uuid.uuid4())[:8]
        product_id = f"P-{suffix}"
        key = f"inventory-{suffix}.csv"
        csv_body = (
            "product_id,location,quantity,update_date\n"
            f"{product_id},Warehouse A,100,2026-05-06\n"
            f"{product_id},Warehouse B,25,2026-05-06\n"
        )

        assert_csv_uploaded(bucket, key, csv_body)
        assert_inventory_records_written(table, product_id, 2)
        assert_queue_drained_terminal_state(queue_url)
        assert_table_active_secondary(table)
        assert_bucket_notification_secondary(bucket)
        assert_queue_mapping_enabled_secondary(consumer, queue_arn)
    except Exception as exc:  # pylint: disable=broad-except
        emit_fail("test_harness_exception", str(exc))

    finalize()
    sys.exit(0)
