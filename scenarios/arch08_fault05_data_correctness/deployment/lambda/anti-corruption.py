import json
import os
import uuid
from datetime import datetime

import boto3

sns = boto3.client("sns")


def _publish(job_id, event_type, details):
    message_id = str(uuid.uuid4())
    sns.publish(
        TopicArn=os.environ["TOPIC_ARN"],
        Subject=f"Job {job_id} {event_type}",
        MessageDeduplicationId=message_id,
        MessageGroupId=f"JOB-{job_id}",
        Message=json.dumps(json.dumps({
            "id": message_id,
            "jobId": job_id,
            "eventCreated": str(datetime.utcnow()),
            "eventType": event_type,
            "eventSource": "anti-corruption-service",
            "eventDetails": details,
        })),
        MessageAttributes={
            "eventType": {
                "DataType": "String",
                "StringValue": event_type,
            }
        },
    )


def lambda_handler(event, context):
    job_id = str((event or {}).get("jobId") or uuid.uuid4())
    _publish(job_id, "JobCreated", {"jobCategory": "Architecture and Engineering", "employer": "a2z.com"})
    _publish(job_id, "JobSalaryUpdated", {"annualSalary": "$57,000"})
    _publish(job_id, "JobDeleted", {"reason": "filled"})
    return {"jobId": job_id}
