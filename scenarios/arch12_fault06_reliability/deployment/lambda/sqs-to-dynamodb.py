import json
import os
import uuid

import boto3

dynamodb = boto3.resource("dynamodb")

def lambda_handler(event, context):
    table = dynamodb.Table(os.environ["DYNAMODB_TABLE_NAME"])
    for message in event.get("Records", []):
        body = json.loads(message["body"])
        table.put_item(
            Item={
                # Generate a new unique id for the DB item, do not look for 'id' in the message body
                "id": str(uuid.uuid4()),
                "product_id": body["product_id"],
                "location": body["location"],
                "quantity": int(body["quantity"]),
                "update_date": body["update_date"],
            },
        )
    return {}
