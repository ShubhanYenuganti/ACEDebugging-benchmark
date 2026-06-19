import json
import os
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key
from xray_instrument import traced

table = boto3.resource("dynamodb").Table(os.environ["FRIEND_TABLE"])


def _json_default(value):
    if isinstance(value, Decimal):
        return int(value)
    raise TypeError(f"Unsupported type: {type(value)}")


def _response(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=_json_default),
    }


@traced("ReadHandlerFunction")
def handler(event, context):
    params = event.get("pathParameters") or {}
    player_id = params.get("playerId")
    friend_id = params.get("friendId")
    if friend_id:
        result = table.get_item(Key={"player_id": player_id, "friend_id": friend_id})
        item = result.get("Item")
        return _response(200, item.get("state") if item else None)
    result = table.query(
        KeyConditionExpression=Key("player_id").eq(player_id),
        ExpressionAttributeValues={":player_id": player_id},
    )
    return _response(200, result.get("Items", []))
