import json
import os

import boto3


dynamodb = boto3.client("dynamodb")


def lambda_handler(event, context):
    for message in event.get("Records", []):
        body = json.loads(message["body"])
        # The 'id' field from the CSV rows is missing, so use 'product_id' plus 'location' as a composite key instead
        item_id = f"{body['product_id']}_{body['location']}"
        try:
            dynamodb.put_item(
                TableName=os.environ["DYNAMODB_TABLE_NAME"],
                Item={
                    "id": {"S": item_id},
                    "product_id": {"S": body["product_id"]},
                    "location": {"S": body["location"]},
                    "quantity": {"N": str(body["quantity"])},
                    "update_date": {"S": body["update_date"]},
                },
                # Add condition to avoid overwriting and handle duplicate processing
                ConditionExpression="attribute_not_exists(id)",
            )
        except dynamodb.exceptions.ConditionalCheckFailedException:
            # Duplicate item, ignore error to prevent retries
            pass
        except Exception as e:
            print(f"Failed to put item: {e}")
    return {}
