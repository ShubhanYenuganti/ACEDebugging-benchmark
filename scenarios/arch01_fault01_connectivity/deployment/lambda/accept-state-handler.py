import os
import time

import boto3
from botocore.exceptions import ClientError

table = boto3.resource("dynamodb").Table(os.environ["FRIEND_TABLE"])


def _s(image, key):
    return image.get(key, {}).get("S")


def _is_conditional(exc):
    return exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


def _accept_reverse(player_id, friend_id, timestamp):
    try:
        table.update_item(
            Key={"player_id": friend_id, "friend_id": player_id},
            ConditionExpression="#state = :pending",
            UpdateExpression="SET #state = :friends, #last_updated = :timestamp",
            ExpressionAttributeNames={
                "#state": "state",
                "#last_updated": "last_updated",
            },
            ExpressionAttributeValues={
                ":pending": "Pending",
                ":friends": "Friends",
                ":timestamp": timestamp,
            },
        )
    except ClientError as exc:
        if not _is_conditional(exc):
            raise


def handler(event, context):
    timestamp = int(time.time() * 1000)
    failures = []
    for record in event.get("Records", []):
        try:
            image = record["dynamodb"]["NewImage"]
            _accept_reverse(_s(image, "player_id"), _s(image, "friend_id"), timestamp)
        except Exception:
            failures.append({"itemIdentifier": record["dynamodb"]["SequenceNumber"]})
    return {"batchItemFailures": failures}
