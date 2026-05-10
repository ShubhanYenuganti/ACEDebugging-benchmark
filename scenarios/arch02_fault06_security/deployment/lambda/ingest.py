import json
import os

import boto3

kinesis = boto3.client("kinesis")

def handler(event, context):
    body = event.get("body", "{}")
    if not isinstance(body, str):
        body = json.dumps(body)
    kinesis.put_record(
        StreamName=os.environ["STREAM_NAME"],
        Data=body.encode("utf-8"),
        PartitionKey="1",
    )
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"status": "accepted"}),
    }
