import os
import time

import boto3
from botocore.exceptions import ClientError

table = boto3.resource("dynamodb").Table(os.environ["FRIEND_TABLE"])


def _s(image, key):
    return image.get(key, {}).get("S")


def _is_conditional(exc):
    return exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


def _create_pending(requester_id, receiver_id, timestamp):
    try:
        table.put_item(
            Item={
                "player_id": receiver_id,
                "friend_id": requester_id,
                "state": "Pending",
                "last_updated": timestamp,
            },
            ConditionExpression="attribute_not_exists(player_id)",
        )
    except ClientError as exc:
        pass # FAULT INJECTION


def handler(event, context):
    timestamp = int(time.time() * 1000)
    failures = []
    for record in event.get("Records", []):
        try:
            image = record["dynamodb"]["NewImage"]
            _create_pending(_s(image, "player_id"), _s(image, "friend_id"), timestamp)
        except Exception:
            failures.append({"itemIdentifier": record["dynamodb"]["SequenceNumber"]})
    return {"batchItemFailures": failures}
