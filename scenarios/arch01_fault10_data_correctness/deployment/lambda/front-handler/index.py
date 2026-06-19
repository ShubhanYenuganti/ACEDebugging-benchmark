import json
import os
import time

import boto3
from botocore.exceptions import ClientError

table = boto3.resource("dynamodb").Table(os.environ["FRIEND_TABLE"])


def _conditional_name(exc):
    return exc.response.get("Error", {}).get("Code", "")


def _request(player_id, friend_id, timestamp):
    if player_id == friend_id:
        return
    try:
        # Fix: correct guard condition to use both player_id and friend_id for composite key
        table.put_item(
            Item={
                "player_id": player_id,
                "friend_id": friend_id,
                "state": "Requested",
                "last_updated": timestamp,
            },
            ConditionExpression="attribute_not_exists(player_id) AND attribute_not_exists(friend_id)",
        )
    except ClientError as exc:
        if _conditional_name(exc) != "ConditionalCheckFailedException":
            raise


def _accept(player_id, friend_id, timestamp):
    try:
        table.update_item(
            Key={"player_id": player_id, "friend_id": friend_id},
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
        if _conditional_name(exc) != "ConditionalCheckFailedException":
            raise


def _delete_if_state(player_id, friend_id, expected_state):
    try:
        table.delete_item(
            Key={"player_id": player_id, "friend_id": friend_id},
            ConditionExpression="#state = :expected",
            ExpressionAttributeNames={"#state": "state"},
            ExpressionAttributeValues={":expected": expected_state},
        )
    except ClientError as exc:
        if _conditional_name(exc) != "ConditionalCheckFailedException":
            raise


def _process(message, timestamp):
    player_id = message["player_id"]
    friend_id = message["friend_id"]
    action = message["friend_action"]
    if action == "Request":
        _request(player_id, friend_id, timestamp)
    elif action == "Accept":
        _accept(player_id, friend_id, timestamp)
    elif action == "Reject":
        _delete_if_state(player_id, friend_id, "Pending")
    elif action == "Unfriend":
        _delete_if_state(player_id, friend_id, "Friends")


def handler(event, context):
    timestamp = int(time.time() * 1000)
    failures = []
    for record in event.get("Records", []):
        try:
            _process(json.loads(record["body"]), timestamp)
        except Exception:
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}
