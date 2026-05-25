import json
import os
import uuid

import boto3

dynamodb = boto3.resource("dynamodb")

def lambda_handler(event, context):
    table_name = os.environ["DYNAMODB_TABLE_NAME"]
    table = dynamodb.Table(table_name)
    for message in event.get("Records", []):
        body = json.loads(message["body"])
        try:
            # Use 'id' as the partition key and ensure all keys exist in the item
            item = {
                "id": str(uuid.uuid4()),
                "product_id": body.get("product_id", ""),
                "location": body.get("location", ""),
                "quantity": int(body.get("quantity", 0)),
                "update_date": body.get("update_date", ""),
            }
            table.put_item(Item=item)
        except Exception as e:
            print(f"Error writing to DynamoDB: {e}")
            raise
    return {}
