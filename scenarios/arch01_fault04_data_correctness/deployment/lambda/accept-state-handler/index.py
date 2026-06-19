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


def _accept_reverse(player_id, friend_id, timestamp):
    try:
        table.update_item(
            Key={"player_id": player_id, "friend_id": friend_id},  # FIX: swap Key for proper update
            ConditionExpression="#state = :requested",
            UpdateExpression="SET #state = :friends, #last_updated = :timestamp",
            ExpressionAttributeNames={
                "#state": "state",
                "#last_updated": "last_updated",
            },
            ExpressionAttributeValues={
                ":requested": "Requested",
                ":friends": "Friends",
                ":timestamp": timestamp,
            },
        )
    except ClientError as exc:
        if not _is_conditional(exc):
            raise


@traced("AcceptStateHandlerFunction")
def handler(event, context):
    timestamp = int(time.time() * 1000)
    failures = []
    for record in event.get("Records", []):
        try:
            image = record["dynamodb"]["NewImage"]
            player_id = _s(image, "player_id")
            friend_id = _s(image, "friend_id")
            # Only update the reverse record (requester's side) if different
            if player_id != friend_id:
                _accept_reverse(friend_id, player_id, timestamp)  # maintain caller order for args
        except Exception:
            failures.append({"itemIdentifier": record["dynamodb"]["SequenceNumber"]})
    return {"batchItemFailures": failures}
