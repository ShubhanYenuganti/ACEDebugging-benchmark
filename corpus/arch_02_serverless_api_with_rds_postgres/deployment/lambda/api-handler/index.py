"""
index.py - API handler for arch02 serverless API on RDS PostgreSQL.

Handles POST /orders (create) and GET /orders/{id} (read).
Opens a fresh psycopg2 connection per invocation (no module-level caching),
as required so fault04 (Timeout=1s) reliably reproduces latency.
"""
import json
import os
import uuid

import boto3
import psycopg2
from aws_xray_sdk.ext.dbapi2 import XRayTracedConn

# Importing xray_instrument configures the X-Ray recorder (PutSegmentsEmitter,
# high streaming_threshold, plain Context) and calls patch_all() so the boto3
# SecretsManager call becomes a subsegment. `traced` wraps the handler in an
# explicit segment. Must be imported at module load.
from xray_instrument import traced


def _resp(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _get_secret():
    arn = os.environ["DB_SECRET_ARN"]
    sm = boto3.client(
        "secretsmanager",
        endpoint_url=os.environ.get("AWS_ENDPOINT_URL"),
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
    )
    raw = sm.get_secret_value(SecretId=arn)["SecretString"]
    return json.loads(raw)


def _open_conn():
    """Open a fresh psycopg2 connection. Called on every invocation.

    The raw connection is wrapped in XRayTracedConn so that cursors created
    from it emit each SQL statement as an X-Ray subsegment.
    """
    secret = _get_secret()
    raw_conn = psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=int(os.environ["DB_PORT"]),
        dbname=os.environ.get("DB_NAME", "postgres"),
        user=secret["username"],
        password=secret["password"],
        connect_timeout=10,
    )
    raw_conn.autocommit = True
    return XRayTracedConn(raw_conn)


def _ensure_schema(conn):
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS orders ("
            "  order_id   TEXT PRIMARY KEY,"
            "  customer   TEXT NOT NULL,"
            "  amount     INTEGER NOT NULL,"
            "  status     TEXT NOT NULL DEFAULT 'NEW',"
            "  created_at TIMESTAMPTZ NOT NULL DEFAULT now()"
            ")"
        )


@traced("ApiHandlerFunction")
def handler(event, context):
    conn = _open_conn()
    try:
        _ensure_schema(conn)
        method = event.get("httpMethod")
        path_params = event.get("pathParameters") or {}

        if method == "POST":
            payload = json.loads(event.get("body") or "{}")
            order_id = str(uuid.uuid4())
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO orders (order_id, customer, amount) VALUES (%s, %s, %s)",
                    (order_id, payload["customer"], int(payload["amount"])),
                )
            return _resp(201, {"order_id": order_id, "status": "NEW"})

        if method == "GET":
            order_id = path_params.get("id")
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT order_id, customer, amount, status FROM orders WHERE order_id = %s",
                    (order_id,),
                )
                row = cur.fetchone()
            if not row:
                return _resp(404, {"error": "not found"})
            return _resp(200, {
                "order_id": row[0],
                "customer": row[1],
                "amount": row[2],
                "status": row[3],
            })

        return _resp(405, {"error": "method not allowed"})
    finally:
        conn.close()
