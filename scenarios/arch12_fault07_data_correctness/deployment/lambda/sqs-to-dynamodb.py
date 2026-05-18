import json
import os
import uuid

import boto3

# Update endpoint URL to use AWS-SDK style environment variables
dynamodb = boto3.client('dynamodb', endpoint_url="http://dynamodb.us-east-1.localhost.localstack.cloud:4566")

def lambda_handler(event, context):
    for message in event.get('Records', []):
        body = json.loads(message['body'])
        if 'qty' in body:
            body['quantity'] = body.pop('qty')

        if not all(key in body for key in ('product_id', 'location', 'quantity', 'update_date')):
            print(f"Skipping record due to missing keys: {body}")
            continue

        quantity = body.get('quantity', 0)

        try:
            quantity_value = int(quantity)
        except ValueError:
            print(f"Invalid quantity value: {quantity}")
            quantity_value = 0

        try:
            dynamodb.put_item(
                TableName=os.environ['DYNAMODB_TABLE_NAME'],
                Item={
                    'id': {'S': str(uuid.uuid4())},
                    'product_id': {'S': body['product_id']},
                    'location': {'S': body['location']},
                    'quantity': {'N': str(quantity_value)},
                    'update_date': {'S': body['update_date']},
                },
            )
        except Exception as e:
            print(f"Error inserting record into DynamoDB: {e}")
    return {}