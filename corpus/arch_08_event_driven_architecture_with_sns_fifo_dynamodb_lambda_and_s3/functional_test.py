"""
functional_test.py - Event-driven architecture with SNS FIFO, DynamoDB, Lambda, and S3
corpus/arch_08_event_driven_architecture_with_sns_fifo_dynamodb_lambda_and_s3/

Verifies that one producer invocation fans out ordered job events to analytics S3 storage and inventory DynamoDB state.

Output format: ASSERT pass|fail [name]: [message]
Primary assertions: no suffix - all must pass for a run to score
Secondary assertions: _secondary suffix - tracked for regression check
Exit code: always 0
"""

import json
import sys
import time
import uuid

import boto3

ENDPOINT = "http://localhost:4566"
REGION = "us-east-1"
CREDS = {"aws_access_key_id": "test", "aws_secret_access_key": "test"}
STACK_NAME = "ace-bench-stack"
WAIT_TIMEOUT_SECONDS = 120


def emit(result, name, message=""):
    print(f"ASSERT {result} {name}: {message}", flush=True)


def client(service):
    return boto3.client(service, endpoint_url=ENDPOINT, region_name=REGION, **CREDS)


def get_stack_output(key):
    cfn = client("cloudformation")
    stacks = cfn.describe_stacks(StackName=STACK_NAME).get("Stacks", [])
    for output in stacks[0].get("Outputs", []):
        if output.get("OutputKey") == key:
            return output.get("OutputValue")
    raise KeyError(f"Missing CloudFormation output: {key}")


def invoke_producer(function_name, job_id):
    lam = client("lambda")
    response = lam.invoke(
        FunctionName=function_name,
        Payload=json.dumps({"jobId": job_id}).encode("utf-8"),
    )
    payload = json.loads(response["Payload"].read().decode("utf-8") or "{}")
    return response.get("StatusCode"), payload


def list_bucket_objects(bucket_name):
    s3 = client("s3")
    return s3.list_objects_v2(Bucket=bucket_name).get("Contents", [])


def get_inventory_item(table_name, job_id):
    ddb = client("dynamodb")
    return ddb.get_item(TableName=table_name, Key={"id": {"S": job_id}}).get("Item")


def wait_until(predicate):
    deadline = time.time() + WAIT_TIMEOUT_SECONDS
    last = None
    while time.time() < deadline:
        ok, last = predicate()
        if ok:
            return True, last
        time.sleep(2)
    return False, last


def assert_events_published(function_name, job_id):
    status, payload = invoke_producer(function_name, job_id)
    if status == 200 and payload.get("jobId") == job_id:
        emit("pass", "events_published", "producer accepted test job and published events")
    else:
        emit("fail", "events_published", f"status={status}, payload={payload}")


def assert_analytics_object_created(bucket_name):
    def _check():
        objects = list_bucket_objects(bucket_name)
        return bool(objects), objects

    ok, objects = wait_until(_check)
    if ok:
        emit("pass", "analytics_object_created", f"objects={len(objects)}")
    else:
        emit("fail", "analytics_object_created", f"objects={objects}")


def assert_inventory_terminal_state(table_name, job_id):
    def _check():
        item = get_inventory_item(table_name, job_id)
        deleted = item.get("markAsDeleted", {}).get("BOOL") if item else False
        return bool(item) and deleted is True, item

    ok, item = wait_until(_check)
    if ok:
        emit("pass", "inventory_terminal_state", "job item exists and is marked deleted")
    else:
        emit("fail", "inventory_terminal_state", f"item={item}")


def assert_table_active_secondary(table_name):
    status = client("dynamodb").describe_table(TableName=table_name)["Table"]["TableStatus"]
    emit("pass" if status == "ACTIVE" else "fail", "table_active_secondary", f"status={status}")


def assert_bucket_exists_secondary(bucket_name):
    try:
        client("s3").head_bucket(Bucket=bucket_name)
        emit("pass", "analytics_bucket_exists_secondary", f"bucket={bucket_name}")
    except Exception as exc:  # pylint: disable=broad-except
        emit("fail", "analytics_bucket_exists_secondary", str(exc))


def assert_event_sources_enabled_secondary(function_names):
    lam = client("lambda")
    missing = []
    for name in function_names:
        mappings = lam.list_event_source_mappings(FunctionName=name).get("EventSourceMappings", [])
        if not any(mapping.get("State") in {"Enabled", "Enabling"} for mapping in mappings):
            missing.append(name)
    if missing:
        emit("fail", "event_sources_enabled_secondary", f"missing={missing}")
    else:
        emit("pass", "event_sources_enabled_secondary", "SQS event source mappings are enabled")


if __name__ == "__main__":
    try:
        producer = get_stack_output("AntiCorruptionFunctionName")
        bucket = get_stack_output("AnalyticsBucketName")
        table = get_stack_output("InventoryTableName")
        inventory_function = get_stack_output("InventoryFunctionName")
        job_id = f"job-{str(uuid.uuid4())[:8]}"

        assert_events_published(producer, job_id)
        assert_analytics_object_created(bucket)
        assert_inventory_terminal_state(table, job_id)
        assert_table_active_secondary(table)
        assert_bucket_exists_secondary(bucket)
        assert_event_sources_enabled_secondary([inventory_function])
    except Exception as exc:  # pylint: disable=broad-except
        emit("fail", "test_harness_exception", str(exc))

    sys.exit(0)
