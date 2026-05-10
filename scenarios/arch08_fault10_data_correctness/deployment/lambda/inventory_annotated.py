import json
import os

import boto3

dynamodb = boto3.client("dynamodb")


def _event_type(record, payload):
    attrs = record.get("messageAttributes") or record.get("message_attributes") or {}
    event_attr = attrs.get("eventType") or attrs.get("event_type") or {}
    return event_attr.get("stringValue") or event_attr.get("StringValue") or payload.get("eventType")


def lambda_handler(event, context):
    for record in event.get("Records", []):
        payload = json.loads(record["body"])
        event_type = _event_type(record, payload)
        if event_type == "JobCreated":
            dynamodb.put_item(
                TableName=os.environ["TABLE_NAME"],
                Item={
                    "job_id": {"S": payload["jobId"]}, # FAULT INJECTION
                    "eventCreated": {"S": payload["eventCreated"]},
                    "eventSource": {"S": payload["eventSource"]},
                    "eventDetails": {"S": json.dumps(payload["eventDetails"])},
                },
            )
        elif event_type == "JobDeleted":
            dynamodb.update_item(
                TableName=os.environ["TABLE_NAME"],
                Key={"id": {"S": payload["jobId"]}},
                UpdateExpression="SET markAsDeleted = :m",
                ExpressionAttributeValues={":m": {"BOOL": True}},
            )
    return {}
