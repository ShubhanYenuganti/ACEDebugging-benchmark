import os
import time

import boto3
from botocore.exceptions import ClientError
from xray_instrument import traced

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
            ConditionExpression="attribute_not_exists(player_id) AND attribute_not_exists(friend_id)",
        )
    except ClientError as exc:
        # Only silently ignore ConditionCheckFailed, propagate others
        if not _is_conditional(exc):
            raise


@traced("RequestStateHandlerFunction")
def handler(event, context):
    timestamp = int(time.time() * 1000)
    failures = []
    for record in event.get("Records", []):
        try:
            image = record["dynamodb"]["NewImage"]
            requester = _s(image, "player_id")
            receiver = _s(image, "friend_id")
            if not requester or not receiver:
                raise ValueError(f"Missing player_id or friend_id in record: {record}")
            _create_pending(requester, receiver, timestamp)
        except Exception:
            failures.append({"itemIdentifier": record["dynamodb"]["SequenceNumber"]})
            continue
    return {"batchItemFailures": failures}
