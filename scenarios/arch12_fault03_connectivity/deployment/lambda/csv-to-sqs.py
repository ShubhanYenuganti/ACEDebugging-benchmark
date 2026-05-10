import csv
import json
import os

import boto3

s3 = boto3.client("s3")
sqs = boto3.client("sqs")


def lambda_handler(event, context):
    sent = 0
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = record["s3"]["object"]["key"]
        response = s3.get_object(Bucket=bucket, Key=key)
        csv_content = response["Body"].read().decode("utf-8-sig")
        batch = []
        for row in csv.DictReader(csv_content.splitlines()):
            batch.append({"Id": str(len(batch) + 1), "MessageBody": json.dumps(row)})
            if len(batch) == 10:
                sqs.send_message_batch(QueueUrl=os.environ["SQS_QUEUE_URL"], Entries=batch)
                sent += len(batch)
                batch = []
        if batch:
            sqs.send_message_batch(QueueUrl=os.environ["SQS_QUEUE_URL"], Entries=batch)
            sent += len(batch)
    return {"sent": sent}
