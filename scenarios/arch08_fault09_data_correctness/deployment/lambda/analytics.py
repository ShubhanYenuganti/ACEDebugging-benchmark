import json
import os
import uuid
from datetime import date

import boto3

s3 = boto3.client("s3")


def lambda_handler(event, context):
    today = date.today()
    body = {"Records": []}
    for record in event.get("Records", []):
        body["Records"].append(json.loads(record["body"]))
    key = f"{today.year}/{today.month}/{today.day}/{uuid.uuid4()}"
    s3.put_object(Bucket=os.environ["BUCKET_NAME"], Key=key, Body=json.dumps(body))
    return {"key": key}
