import json
import os
import uuid

import boto3

dynamodb = boto3.client("dynamodb")


def lambda_handler(event, context):
    for message in event.get("Records", []):
        body = json.loads(message["body"])
        dynamodb.put_item(
            TableName=os.environ["DYNAMODB_TABLE_NAME"],
            Item={
                "id": {"S": str(uuid.uuid4())},
                "product_id": {"S": body["product_id"]},
                "location": {"S": body["location"]},
                "quantity": {"N": str(body["qty"])},  # FAULT INJECTION
                "update_date": {"S": body["update_date"]},
            },
        )
    return {}
