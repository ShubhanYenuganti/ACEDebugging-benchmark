"""
functional_test.py - Serverless API on RDS PostgreSQL
corpus/arch_02_serverless_api_with_rds_postgres/

Verifies that the orders API can create and retrieve orders against RDS
PostgreSQL, that the RDS instance is available, and that the secret is
accessible.

Output format: ASSERT pass|fail [name]: [message]
Primary assertions: no suffix — all must pass for a run to score
Secondary assertions: _secondary suffix — tracked for regression check
Exit code: always 0
"""

import json
import sys
import uuid
from urllib import error, request

import boto3

from harness.shared.functional_test_helpers import emit_fail, emit_pass, finalize

ENDPOINT = "http://localhost:4566"
REGION = "us-east-1"
CREDS = {"aws_access_key_id": "test", "aws_secret_access_key": "test"}
STACK_NAME = "ace-bench-stack"


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


def api_candidates(api_base_url, api_id, path):
    """Return URL candidates to try for API Gateway on LocalStack."""
    base = api_base_url.rstrip("/")
    return [
        f"{base}{path}",
        f"http://{api_id}.execute-api.localhost.localstack.cloud:4566/prod{path}",
        f"http://localhost:4566/restapis/{api_id}/prod/_user_request_{path}",
    ]


def http_post_json(url, body_dict, timeout=30):
    body = json.dumps(body_dict).encode()
    req = request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8")
            return r.status, json.loads(raw) if raw else {}
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8") if exc.fp else ""
        try:
            payload = json.loads(raw) if raw else {"raw": raw}
        except json.JSONDecodeError:
            payload = {"raw": raw}
        return exc.code, payload


def http_get_json(url, timeout=30):
    try:
        with request.urlopen(url, timeout=timeout) as r:
            raw = r.read().decode("utf-8")
            return r.status, json.loads(raw) if raw else {}
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8") if exc.fp else ""
        try:
            payload = json.loads(raw) if raw else {"raw": raw}
        except json.JSONDecodeError:
            payload = {"raw": raw}
        return exc.code, payload


def assert_order_created(api_base_url, api_id):
    """POST /orders returns 201 with an order_id."""
    path = "/orders"
    payload = {"customer": f"test-{uuid.uuid4().hex[:8]}", "amount": 42}
    last_status, last_payload = None, None
    for url in api_candidates(api_base_url, api_id, path):
        status, payload_resp = http_post_json(url, payload)
        last_status, last_payload = status, payload_resp
        if status == 201 and "order_id" in payload_resp:
            emit_pass("order_created", f"POST /orders returned 201 with order_id={payload_resp['order_id']!r}")
            return payload_resp["order_id"], payload["customer"], payload["amount"]
    emit_fail("order_created", f"POST /orders failed: status={last_status}, body={last_payload}")
    return None, None, None


def assert_order_readback(api_base_url, api_id, order_id, expected_customer, expected_amount):
    """GET /orders/{id} returns the persisted order with correct fields."""
    if order_id is None:
        emit_fail("order_readback", "skipped — order_created failed")
        return
    path = f"/orders/{order_id}"
    last_status, last_payload = None, None
    for url in api_candidates(api_base_url, api_id, path):
        status, got = http_get_json(url)
        last_status, last_payload = status, got
        if (
            status == 200
            and got.get("order_id") == order_id
            and got.get("customer") == expected_customer
            and got.get("amount") == expected_amount
        ):
            emit_pass("order_readback", f"GET /orders/{order_id} returned correct customer and amount")
            return
    emit_fail("order_readback", f"unexpected: status={last_status}, body={last_payload}")


def assert_db_available_secondary(db_instance_id):
    """RDS instance is in available state (secondary)."""
    try:
        rds = client("rds")
        instances = rds.describe_db_instances(DBInstanceIdentifier=db_instance_id)["DBInstances"]
        db_status = instances[0]["DBInstanceStatus"]
        if db_status == "available":
            emit_pass("db_available_secondary", f"RDS instance status={db_status!r}")
        else:
            emit_fail("db_available_secondary", f"RDS instance status={db_status!r}")
    except Exception as exc:
        emit_fail("db_available_secondary", str(exc))


def assert_secret_accessible_secondary(db_secret_arn):
    """Secrets Manager secret is readable (secondary)."""
    try:
        sm = client("secretsmanager")
        raw = sm.get_secret_value(SecretId=db_secret_arn)["SecretString"]
        data = json.loads(raw)
        if "username" in data and "password" in data:
            emit_pass("secret_accessible_secondary", "secret contains username and password")
        else:
            emit_fail("secret_accessible_secondary", f"secret missing expected keys: {list(data.keys())}")
    except Exception as exc:
        emit_fail("secret_accessible_secondary", str(exc))


if __name__ == "__main__":
    try:
        api_base_url = get_stack_output("ApiBaseUrl")
        db_instance_id = get_stack_output("DbInstanceId")
        db_secret_arn = get_stack_output("DbSecretArn")

        # Derive API ID from the base URL (format: http://<api_id>.execute-api.localhost...)
        api_id = api_base_url.split(".")[0].split("//")[-1]

        # Primary assertions
        order_id, customer, amount = assert_order_created(api_base_url, api_id)
        assert_order_readback(api_base_url, api_id, order_id, customer, amount)

        # Secondary assertions
        assert_db_available_secondary(db_instance_id)
        assert_secret_accessible_secondary(db_secret_arn)

    except Exception as exc:  # pylint: disable=broad-except
        emit_fail("test_harness_exception", str(exc))

    finalize()
    sys.exit(0)
