import os

import boto3
from botocore.exceptions import ClientError
from xray_instrument import traced

table = boto3.resource("dynamodb").Table(os.environ["FRIEND_TABLE"])


def _s(image, key):
    return image.get(key, {}).get("S")


def _delete_reverse(player_id, friend_id):
    try:
        table.delete_item(
            Key={"player_id": friend_id, "friend_id": player_id},
            ConditionExpression="#state = :friends",
            ExpressionAttributeNames={"#state": "state"},
            ExpressionAttributeValues={":friends": "Friends"},
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise


@traced("UnfriendStateHandlerFunction")
def handler(event, context):
    failures = []
    for record in event.get("Records", []):
        try:
            image = record["dynamodb"]["OldImage"]
            _delete_reverse(_s(image, "player_id"), _s(image, "friend_id"))
        except Exception:
            failures.append({"itemIdentifier": record["dynamodb"]["SequenceNumber"]})
    return {"batchItemFailures": failures}
