"""
functional_test.py - Serverless Microservices with API Gateway, DynamoDB, SQS, and Lambda
corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/

Verifies that queued friend actions create reciprocal relationship state and that the read API returns the terminal friendship.

Output format: ASSERT pass|fail [name]: [message]
Primary assertions: no suffix - all must pass for a run to score
Secondary assertions: _secondary suffix - tracked for regression check
Exit code: always 0
"""

import json
import sys
import time
import uuid
from urllib import error, request

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
    if not stacks:
        raise RuntimeError(f"Stack not found: {STACK_NAME}")
    for output in stacks[0].get("Outputs", []):
        if output.get("OutputKey") == key:
            return output.get("OutputValue")
    raise KeyError(f"Missing CloudFormation output: {key}")


def send_friend_action(queue_url, player_id, friend_id, action):
    sqs = client("sqs")
    sqs.send_message(
        QueueUrl=queue_url,
        MessageBody=json.dumps(
            {
                "player_id": player_id,
                "friend_id": friend_id,
                "friend_action": action,
            }
        ),
    )


def get_friend_item(table_name, player_id, friend_id):
    ddb = client("dynamodb")
    response = ddb.get_item(
        TableName=table_name,
        Key={"player_id": {"S": player_id}, "friend_id": {"S": friend_id}},
    )
    return response.get("Item")


def wait_for_state(table_name, player_id, friend_id, expected_state):
    deadline = time.time() + WAIT_TIMEOUT_SECONDS
    while time.time() < deadline:
        item = get_friend_item(table_name, player_id, friend_id)
        state = item.get("state", {}).get("S") if item else None
        if state == expected_state:
            return True
        time.sleep(2)
    return False


def http_get_json(url):
    req = request.Request(url, method="GET")
    try:
        with request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else None
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8") if exc.fp else ""
        try:
            payload = json.loads(raw) if raw else {"raw": raw}
        except json.JSONDecodeError:
            payload = {"raw": raw}
        return exc.code, payload


def api_candidates(api_url, api_id, path):
    base = api_url.rstrip("/")
    return [
        f"{base}{path}",
        f"http://{api_id}.execute-api.localhost.localstack.cloud:4566/prod{path}",
        f"http://localhost:4566/restapis/{api_id}/prod/_user_request_{path}",
    ]


def assert_request_record_written(queue_url, table_name, player_id, friend_id):
    send_friend_action(queue_url, player_id, friend_id, "Request")
    if wait_for_state(table_name, player_id, friend_id, "Requested"):
        emit("pass", "request_record_written", "requester-side item reached Requested")
    else:
        emit("fail", "request_record_written", "requester-side item did not reach Requested")


def assert_pending_record_created(table_name, player_id, friend_id):
    if wait_for_state(table_name, friend_id, player_id, "Pending"):
        emit("pass", "pending_record_created", "receiver-side item reached Pending")
    else:
        emit("fail", "pending_record_created", "receiver-side item did not reach Pending")


def assert_accept_terminal_state(queue_url, table_name, player_id, friend_id):
    send_friend_action(queue_url, friend_id, player_id, "Accept")
    receiver_ok = wait_for_state(table_name, friend_id, player_id, "Friends")
    requester_ok = wait_for_state(table_name, player_id, friend_id, "Friends")
    if receiver_ok and requester_ok:
        emit("pass", "accept_terminal_state", "both relationship records reached Friends")
    else:
        emit(
            "fail",
            "accept_terminal_state",
            f"receiver_ok={receiver_ok}, requester_ok={requester_ok}",
        )


def assert_read_api_terminal_state(api_url, api_id, player_id, friend_id):
    path = f"/friends/{player_id}"
    last_status = None
    last_payload = None
    for url in api_candidates(api_url, api_id, path):
        status, payload = http_get_json(url)
        last_status = status
        last_payload = payload
        if status == 200 and isinstance(payload, list):
            for item in payload:
                if item.get("friend_id") == friend_id and item.get("state") == "Friends":
                    emit("pass", "read_api_terminal_state", "read API returned terminal friendship")
                    return
    emit("fail", "read_api_terminal_state", f"status={last_status}, payload={last_payload}")


def assert_table_active_secondary(table_name):
    ddb = client("dynamodb")
    status = ddb.describe_table(TableName=table_name)["Table"]["TableStatus"]
    if status == "ACTIVE":
        emit("pass", "table_active_secondary", f"status={status}")
    else:
        emit("fail", "table_active_secondary", f"status={status}")


def assert_queue_mapping_enabled_secondary(front_handler_name, queue_arn):
    lam = client("lambda")
    mappings = lam.list_event_source_mappings(FunctionName=front_handler_name).get("EventSourceMappings", [])
    enabled = [
        mapping
        for mapping in mappings
        if mapping.get("EventSourceArn") == queue_arn and mapping.get("State") in {"Enabled", "Enabling"}
    ]
    if enabled:
        emit("pass", "queue_mapping_enabled_secondary", "front queue event source mapping is enabled")
    else:
        emit("fail", "queue_mapping_enabled_secondary", "front queue event source mapping is not enabled")


def assert_stream_mappings_enabled_secondary(function_names, table_stream_arn):
    lam = client("lambda")
    missing = []
    for function_name in function_names:
        mappings = lam.list_event_source_mappings(FunctionName=function_name).get("EventSourceMappings", [])
        match = [
            mapping
            for mapping in mappings
            if mapping.get("EventSourceArn") == table_stream_arn and mapping.get("State") in {"Enabled", "Enabling"}
        ]
        if not match:
            missing.append(function_name)
    if not missing:
        emit("pass", "stream_mappings_enabled_secondary", "DynamoDB stream mappings are enabled")
    else:
        emit("fail", "stream_mappings_enabled_secondary", f"missing={missing}")


if __name__ == "__main__":
    try:
        queue_url = get_stack_output("QueueUrl")
        queue_arn = get_stack_output("QueueArn")
        table_name = get_stack_output("TableName")
        table_stream_arn = get_stack_output("TableStreamArn")
        api_id = get_stack_output("ApiId")
        api_url = get_stack_output("ApiUrl")
        front_handler_name = get_stack_output("FrontHandlerFunctionName")
        request_handler_name = get_stack_output("RequestStateHandlerFunctionName")
        accept_handler_name = get_stack_output("AcceptStateHandlerFunctionName")

        suffix = str(uuid.uuid4())[:8]
        player_id = f"player-{suffix}-a"
        friend_id = f"player-{suffix}-b"

        assert_request_record_written(queue_url, table_name, player_id, friend_id)
        assert_pending_record_created(table_name, player_id, friend_id)
        assert_accept_terminal_state(queue_url, table_name, player_id, friend_id)
        assert_read_api_terminal_state(api_url, api_id, player_id, friend_id)

        assert_table_active_secondary(table_name)
        assert_queue_mapping_enabled_secondary(front_handler_name, queue_arn)
        assert_stream_mappings_enabled_secondary([request_handler_name, accept_handler_name], table_stream_arn)
    except Exception as exc:  # pylint: disable=broad-except
        emit("fail", "test_harness_exception", str(exc))

    sys.exit(0)
