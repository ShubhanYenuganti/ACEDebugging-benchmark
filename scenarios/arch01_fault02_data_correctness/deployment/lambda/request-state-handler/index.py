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
        # Create the 'Requested' item for the requester
        table.put_item(
            Item={
                "player_id": requester_id,  # requester should be player_id here
                "friend_id": receiver_id,  # receiver should be friend_id
                "state": "Requested",
                "last_updated": timestamp,
            },
            ConditionExpression="attribute_not_exists(player_id) AND attribute_not_exists(friend_id)",
        )
        # Create the 'Pending' item for the receiver with correct friend_id
        table.put_item(
            Item={
                "player_id": receiver_id,  # receiver should be player_id
                "friend_id": requester_id,  # requester should be friend_id
                "state": "Pending",
                "last_updated": timestamp,
            },
            ConditionExpression="attribute_not_exists(player_id) AND attribute_not_exists(friend_id)",
        )
    except ClientError as exc:
        if not _is_conditional(exc):
            raise


@traced("RequestStateHandlerFunction")
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
