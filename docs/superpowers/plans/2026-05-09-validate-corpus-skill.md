# Validate Corpus Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract inline ZipFile Lambda code from `known_good.yaml` files into `deployment/lambda/` subdirectories, update YAMLs to use S3Key references, then create `validate_deploy.py` CLI and `validate-corpus` skill for iterative LocalStack deployment + functional test validation.

**Architecture:** Four architectures (arch_01_serverless, arch_02_fuzzy_movie_search, arch_08_event_driven_sns_fifo, arch_12_sqs_dynamodb) each need Lambda source extracted to `corpus/<arch>/deployment/lambda/<name>/index.py`, their `known_good.yaml` updated to reference `S3Bucket: ace-bench-artifacts` / `S3Key: <name>.zip`. Then a validate_deploy.py CLI zips + uploads those files, deploys the stack, and a SKILL.md guides an agent through iterative fix loops.

**Tech Stack:** Python 3.11, boto3, cfn-lint, LocalStack at `http://localhost:4566` (creds: test/test), CloudFormation stack name `ace-bench-stack`, artifact bucket `ace-bench-artifacts`

---

### Task 1: arch_02 Lambda extraction

**Files:**
- Create: `corpus/arch_02_fuzzy_movie_search/deployment/lambda/ingest/index.py`
- Create: `corpus/arch_02_fuzzy_movie_search/deployment/lambda/search/index.py`
- Modify: `corpus/arch_02_fuzzy_movie_search/known_good.yaml`

- [ ] **Step 1: Create ingest Lambda**

Create `corpus/arch_02_fuzzy_movie_search/deployment/lambda/ingest/index.py`:

```python
import json
import os

import boto3

kinesis = boto3.client("kinesis")

def handler(event, context):
    body = event.get("body", "{}")
    if not isinstance(body, str):
        body = json.dumps(body)
    kinesis.put_record(
        StreamName=os.environ["STREAM_NAME"],
        Data=body.encode("utf-8"),
        PartitionKey="1",
    )
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"status": "accepted"}),
    }
```

- [ ] **Step 2: Create search Lambda**

Create `corpus/arch_02_fuzzy_movie_search/deployment/lambda/search/index.py`:

```python
import json
import os
import urllib.parse
import urllib.request


def _response(status, body):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body),
    }


def handler(event, context):
    params = event.get("queryStringParameters") or {}
    query = params.get("q")
    if not query:
        return _response(400, {"error": "Mandatory query parameter q missing"})
    search_query = {
        "query": {
            "multi_match": {
                "fields": ["title", "directors", "actors"],
                "query": query,
                "fuzziness": "AUTO",
                "type": "best_fields",
            }
        }
    }
    endpoint = os.environ["ELASTICSEARCH_ENDPOINT"]
    index = os.environ["ELASTICSEARCH_INDEX"]
    url = f"http://{endpoint}/{urllib.parse.quote(index)}/_search"
    req = urllib.request.Request(
        url,
        data=json.dumps(search_query).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    movies = []
    for hit in result.get("hits", {}).get("hits", []):
        movie = {
            "_search_id": hit.get("_id"),
            "_search_score": hit.get("_score"),
        }
        movie.update(hit.get("_source", {}))
        movies.append(movie)
    return _response(200, movies)
```

- [ ] **Step 3: Update known_good.yaml — IngestFunction**

In `corpus/arch_02_fuzzy_movie_search/known_good.yaml`, replace the `IngestFunction` Code block:

Old:
```yaml
      Code:
        ZipFile: |
          import json
          import os

          import boto3

          kinesis = boto3.client("kinesis")

          def handler(event, context):
              body = event.get("body", "{}")
              if not isinstance(body, str):
                  body = json.dumps(body)
              kinesis.put_record(
                  StreamName=os.environ["STREAM_NAME"],
                  Data=body.encode("utf-8"),
                  PartitionKey="1",
              )
              return {
                  "statusCode": 200,
                  "headers": {"Content-Type": "application/json"},
                  "body": json.dumps({"status": "accepted"}),
              }
```

New:
```yaml
      Code:
        S3Bucket: ace-bench-artifacts
        S3Key: ingest.zip
```

- [ ] **Step 4: Update known_good.yaml — SearchFunction**

In `corpus/arch_02_fuzzy_movie_search/known_good.yaml`, replace the `SearchFunction` Code block (the entire multi-line ZipFile starting with `import json\nimport os\nimport urllib.parse`):

New:
```yaml
      Code:
        S3Bucket: ace-bench-artifacts
        S3Key: search.zip
```

- [ ] **Step 5: Verify no ZipFile remains**

Run: `grep -n "ZipFile" corpus/arch_02_fuzzy_movie_search/known_good.yaml`
Expected: no output

- [ ] **Step 6: Commit**

```bash
git add corpus/arch_02_fuzzy_movie_search/deployment/ corpus/arch_02_fuzzy_movie_search/known_good.yaml
git commit -m "feat(corpus): extract arch_02 Lambda code to deployment/lambda, use S3Key refs"
```

---

### Task 2: arch_08 Lambda extraction

**Files:**
- Create: `corpus/arch_08_event_driven_architecture_with_sns_fifo_dynamodb_lambda_and_s3/deployment/lambda/anti-corruption/index.py`
- Create: `corpus/arch_08_event_driven_architecture_with_sns_fifo_dynamodb_lambda_and_s3/deployment/lambda/analytics/index.py`
- Create: `corpus/arch_08_event_driven_architecture_with_sns_fifo_dynamodb_lambda_and_s3/deployment/lambda/inventory/index.py`
- Modify: `corpus/arch_08_event_driven_architecture_with_sns_fifo_dynamodb_lambda_and_s3/known_good.yaml`

- [ ] **Step 1: Create anti-corruption Lambda**

Create `corpus/arch_08_event_driven_architecture_with_sns_fifo_dynamodb_lambda_and_s3/deployment/lambda/anti-corruption/index.py`:

```python
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
        Message=json.dumps({
            "id": message_id,
            "jobId": job_id,
            "eventCreated": str(datetime.utcnow()),
            "eventType": event_type,
            "eventSource": "anti-corruption-service",
            "eventDetails": details,
        }),
        MessageAttributes={
            "eventType": {
                "DataType": "String",
                "StringValue": event_type,
            }
        },
    )


def lambda_handler(event, context):
    job_id = str((event or {}).get("jobId") or uuid.uuid4())[:8]
    _publish(job_id, "JobCreated", {"jobCategory": "Architecture and Engineering", "employer": "a2z.com"})
    _publish(job_id, "JobSalaryUpdated", {"annualSalary": "$57,000"})
    _publish(job_id, "JobDeleted", {"reason": "filled"})
    return {"jobId": job_id}
```

- [ ] **Step 2: Create analytics Lambda**

Create `corpus/arch_08_event_driven_architecture_with_sns_fifo_dynamodb_lambda_and_s3/deployment/lambda/analytics/index.py`:

```python
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
```

- [ ] **Step 3: Create inventory Lambda**

Create `corpus/arch_08_event_driven_architecture_with_sns_fifo_dynamodb_lambda_and_s3/deployment/lambda/inventory/index.py`:

```python
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
                    "id": {"S": payload["jobId"]},
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
```

- [ ] **Step 4: Update known_good.yaml — all three functions**

In `corpus/arch_08_event_driven_architecture_with_sns_fifo_dynamodb_lambda_and_s3/known_good.yaml`, replace `AntiCorruptionFunction` Code block:

Old (starts at `Code:\n        ZipFile: |` under `AntiCorruptionFunction`):
```yaml
      Code:
        ZipFile: |
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
                  Message=json.dumps({
                      "id": message_id,
                      "jobId": job_id,
                      "eventCreated": str(datetime.utcnow()),
                      "eventType": event_type,
                      "eventSource": "anti-corruption-service",
                      "eventDetails": details,
                  }),
                  MessageAttributes={
                      "eventType": {
                          "DataType": "String",
                          "StringValue": event_type,
                      }
                  },
              )

          def lambda_handler(event, context):
              job_id = str((event or {}).get("jobId") or uuid.uuid4())[:8]
              _publish(job_id, "JobCreated", {"jobCategory": "Architecture and Engineering", "employer": "a2z.com"})
              _publish(job_id, "JobSalaryUpdated", {"annualSalary": "$57,000"})
              _publish(job_id, "JobDeleted", {"reason": "filled"})
              return {"jobId": job_id}
```

New:
```yaml
      Code:
        S3Bucket: ace-bench-artifacts
        S3Key: anti-corruption.zip
```

Replace `AnalyticsFunction` Code block (the one with `s3 = boto3.client("s3")`):

New:
```yaml
      Code:
        S3Bucket: ace-bench-artifacts
        S3Key: analytics.zip
```

Replace `InventoryFunction` Code block (the one with `dynamodb = boto3.client("dynamodb")`):

New:
```yaml
      Code:
        S3Bucket: ace-bench-artifacts
        S3Key: inventory.zip
```

- [ ] **Step 5: Verify no ZipFile remains**

Run: `grep -n "ZipFile" corpus/arch_08_event_driven_architecture_with_sns_fifo_dynamodb_lambda_and_s3/known_good.yaml`
Expected: no output

- [ ] **Step 6: Commit**

```bash
git add corpus/arch_08_event_driven_architecture_with_sns_fifo_dynamodb_lambda_and_s3/
git commit -m "feat(corpus): extract arch_08 Lambda code to deployment/lambda, use S3Key refs"
```

---

### Task 3: arch_12 Lambda extraction

**Files:**
- Create: `corpus/arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3/deployment/lambda/csv-to-sqs/index.py`
- Create: `corpus/arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3/deployment/lambda/sqs-to-dynamodb/index.py`
- Modify: `corpus/arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3/known_good.yaml`

- [ ] **Step 1: Create csv-to-sqs Lambda**

Create `corpus/arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3/deployment/lambda/csv-to-sqs/index.py`:

```python
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
```

- [ ] **Step 2: Create sqs-to-dynamodb Lambda**

Create `corpus/arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3/deployment/lambda/sqs-to-dynamodb/index.py`:

```python
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
                "quantity": {"N": str(body["quantity"])},
                "update_date": {"S": body["update_date"]},
            },
        )
    return {}
```

- [ ] **Step 3: Update known_good.yaml**

In `corpus/arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3/known_good.yaml`, replace `CSVProcessingToSQSFunction` Code block:

Old:
```yaml
      Code:
        ZipFile: |
          import csv
          import json
          import os

          import boto3

          s3 = boto3.client("s3")
          sqs = boto3.client("sqs")

          def lambda_handler(event, context):
              for record in event.get("Records", []):
                  bucket = record["s3"]["bucket"]["name"]
                  key = record["s3"]["object"]["key"]
                  response = s3.get_object(Bucket=bucket, Key=key)
                  csv_content = response["Body"].read().decode("utf-8-sig")
                  batch = []
                  sent = 0
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
```

New:
```yaml
      Code:
        S3Bucket: ace-bench-artifacts
        S3Key: csv-to-sqs.zip
```

Replace `SQSToDynamoDBFunction` Code block:

Old:
```yaml
      Code:
        ZipFile: |
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
                          "quantity": {"N": str(body["quantity"])},
                          "update_date": {"S": body["update_date"]},
                      },
                  )
              return {}
```

New:
```yaml
      Code:
        S3Bucket: ace-bench-artifacts
        S3Key: sqs-to-dynamodb.zip
```

- [ ] **Step 4: Verify no ZipFile remains**

Run: `grep -n "ZipFile" corpus/arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3/known_good.yaml`
Expected: no output

- [ ] **Step 5: Commit**

```bash
git add corpus/arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3/
git commit -m "feat(corpus): extract arch_12 Lambda code to deployment/lambda, use S3Key refs"
```

---

### Task 4: arch_01_serverless Lambda extraction

**Files:**
- Create: `corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/deployment/lambda/front-handler/index.py`
- Create: `corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/deployment/lambda/request-state-handler/index.py`
- Create: `corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/deployment/lambda/accept-state-handler/index.py`
- Create: `corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/deployment/lambda/reject-state-handler/index.py`
- Create: `corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/deployment/lambda/unfriend-state-handler/index.py`
- Create: `corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/deployment/lambda/read-handler/index.py`
- Modify: `corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/known_good.yaml`

Note: The `implementation/` folder is TypeScript CDK; the `known_good.yaml` already provides Python 3.11 implementations. Extract those.

- [ ] **Step 1: Create front-handler Lambda**

Create `corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/deployment/lambda/front-handler/index.py`:

```python
import json
import os
import time

import boto3
from botocore.exceptions import ClientError

table = boto3.resource("dynamodb").Table(os.environ["FRIEND_TABLE"])


def _conditional_name(exc):
    return exc.response.get("Error", {}).get("Code", "")


def _request(player_id, friend_id, timestamp):
    if player_id == friend_id:
        return
    try:
        table.put_item(
            Item={
                "player_id": player_id,
                "friend_id": friend_id,
                "state": "Requested",
                "last_updated": timestamp,
            },
            ConditionExpression="attribute_not_exists(player_id)",
        )
    except ClientError as exc:
        if _conditional_name(exc) != "ConditionalCheckFailedException":
            raise


def _accept(player_id, friend_id, timestamp):
    try:
        table.update_item(
            Key={"player_id": player_id, "friend_id": friend_id},
            ConditionExpression="#state = :pending",
            UpdateExpression="SET #state = :friends, #last_updated = :timestamp",
            ExpressionAttributeNames={
                "#state": "state",
                "#last_updated": "last_updated",
            },
            ExpressionAttributeValues={
                ":pending": "Pending",
                ":friends": "Friends",
                ":timestamp": timestamp,
            },
        )
    except ClientError as exc:
        if _conditional_name(exc) != "ConditionalCheckFailedException":
            raise


def _delete_if_state(player_id, friend_id, expected_state):
    try:
        table.delete_item(
            Key={"player_id": player_id, "friend_id": friend_id},
            ConditionExpression="#state = :expected",
            ExpressionAttributeNames={"#state": "state"},
            ExpressionAttributeValues={":expected": expected_state},
        )
    except ClientError as exc:
        if _conditional_name(exc) != "ConditionalCheckFailedException":
            raise


def _process(message, timestamp):
    player_id = message["player_id"]
    friend_id = message["friend_id"]
    action = message["friend_action"]
    if action == "Request":
        _request(player_id, friend_id, timestamp)
    elif action == "Accept":
        _accept(player_id, friend_id, timestamp)
    elif action == "Reject":
        _delete_if_state(player_id, friend_id, "Pending")
    elif action == "Unfriend":
        _delete_if_state(player_id, friend_id, "Friends")


def handler(event, context):
    timestamp = int(time.time() * 1000)
    failures = []
    for record in event.get("Records", []):
        try:
            _process(json.loads(record["body"]), timestamp)
        except Exception:
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}
```

- [ ] **Step 2: Create request-state-handler Lambda**

Create `corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/deployment/lambda/request-state-handler/index.py`:

```python
import os
import time

import boto3
from botocore.exceptions import ClientError

table = boto3.resource("dynamodb").Table(os.environ["FRIEND_TABLE"])


def _s(image, key):
    return image.get(key, {}).get("S")


def _is_conditional(exc):
    return exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


def _create_pending(requester_id, receiver_id, timestamp):
    try:
        table.put_item(
            Item={
                "player_id": receiver_id,
                "friend_id": requester_id,
                "state": "Pending",
                "last_updated": timestamp,
            },
            ConditionExpression="attribute_not_exists(player_id)",
        )
    except ClientError as exc:
        if not _is_conditional(exc):
            raise


def handler(event, context):
    timestamp = int(time.time() * 1000)
    failures = []
    for record in event.get("Records", []):
        try:
            image = record["dynamodb"]["NewImage"]
            _create_pending(_s(image, "player_id"), _s(image, "friend_id"), timestamp)
        except Exception:
            failures.append({"itemIdentifier": record["dynamodb"]["SequenceNumber"]})
    return {"batchItemFailures": failures}
```

- [ ] **Step 3: Create accept-state-handler Lambda**

Create `corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/deployment/lambda/accept-state-handler/index.py`:

```python
import os
import time

import boto3
from botocore.exceptions import ClientError

table = boto3.resource("dynamodb").Table(os.environ["FRIEND_TABLE"])


def _s(image, key):
    return image.get(key, {}).get("S")


def _is_conditional(exc):
    return exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


def _accept_reverse(player_id, friend_id, timestamp):
    try:
        table.update_item(
            Key={"player_id": friend_id, "friend_id": player_id},
            ConditionExpression="#state = :requested",
            UpdateExpression="SET #state = :friends, #last_updated = :timestamp",
            ExpressionAttributeNames={
                "#state": "state",
                "#last_updated": "last_updated",
            },
            ExpressionAttributeValues={
                ":requested": "Requested",
                ":friends": "Friends",
                ":timestamp": timestamp,
            },
        )
    except ClientError as exc:
        if not _is_conditional(exc):
            raise


def handler(event, context):
    timestamp = int(time.time() * 1000)
    failures = []
    for record in event.get("Records", []):
        try:
            image = record["dynamodb"]["NewImage"]
            _accept_reverse(_s(image, "player_id"), _s(image, "friend_id"), timestamp)
        except Exception:
            failures.append({"itemIdentifier": record["dynamodb"]["SequenceNumber"]})
    return {"batchItemFailures": failures}
```

- [ ] **Step 4: Create reject-state-handler Lambda**

Create `corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/deployment/lambda/reject-state-handler/index.py`:

```python
import os

import boto3
from botocore.exceptions import ClientError

table = boto3.resource("dynamodb").Table(os.environ["FRIEND_TABLE"])


def _s(image, key):
    return image.get(key, {}).get("S")


def _delete_reverse(player_id, friend_id):
    try:
        table.delete_item(
            Key={"player_id": friend_id, "friend_id": player_id},
            ConditionExpression="#state = :requested",
            ExpressionAttributeNames={"#state": "state"},
            ExpressionAttributeValues={":requested": "Requested"},
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise


def handler(event, context):
    failures = []
    for record in event.get("Records", []):
        try:
            image = record["dynamodb"]["OldImage"]
            _delete_reverse(_s(image, "player_id"), _s(image, "friend_id"))
        except Exception:
            failures.append({"itemIdentifier": record["dynamodb"]["SequenceNumber"]})
    return {"batchItemFailures": failures}
```

- [ ] **Step 5: Create unfriend-state-handler Lambda**

Create `corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/deployment/lambda/unfriend-state-handler/index.py`:

```python
import os

import boto3
from botocore.exceptions import ClientError

table = boto3.resource("dynamodb").Table(os.environ["FRIEND_TABLE"])


def _s(image, key):
    return image.get(key, {}).get("S")


def _delete_reverse(player_id, friend_id):
    try:
        table.delete_item(
            Key={"player_id": friend_id, "friend_id": player_id},
            ConditionExpression="#state = :friends",
            ExpressionAttributeNames={"#state": "state"},
            ExpressionAttributeValues={":friends": "Friends"},
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise


def handler(event, context):
    failures = []
    for record in event.get("Records", []):
        try:
            image = record["dynamodb"]["OldImage"]
            _delete_reverse(_s(image, "player_id"), _s(image, "friend_id"))
        except Exception:
            failures.append({"itemIdentifier": record["dynamodb"]["SequenceNumber"]})
    return {"batchItemFailures": failures}
```

- [ ] **Step 6: Create read-handler Lambda**

Create `corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/deployment/lambda/read-handler/index.py`:

```python
import json
import os
from decimal import Decimal

import boto3

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


def handler(event, context):
    params = event.get("pathParameters") or {}
    player_id = params.get("playerId")
    friend_id = params.get("friendId")
    if friend_id:
        result = table.get_item(Key={"player_id": player_id, "friend_id": friend_id})
        item = result.get("Item")
        return _response(200, item.get("state") if item else None)
    result = table.query(
        KeyConditionExpression="player_id = :player_id",
        ExpressionAttributeValues={":player_id": player_id},
    )
    return _response(200, result.get("Items", []))
```

- [ ] **Step 7: Update known_good.yaml — all six functions**

In `corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/known_good.yaml`, replace each `Code: ZipFile:` block with the corresponding S3Key reference.

For `FrontHandlerFunction` (handler `index.handler`):
```yaml
      Code:
        S3Bucket: ace-bench-artifacts
        S3Key: front-handler.zip
```

For `RequestStateHandlerFunction` (handler `index.handler`):
```yaml
      Code:
        S3Bucket: ace-bench-artifacts
        S3Key: request-state-handler.zip
```

For `AcceptStateHandlerFunction` (handler `index.handler`):
```yaml
      Code:
        S3Bucket: ace-bench-artifacts
        S3Key: accept-state-handler.zip
```

For `RejectStateHandlerFunction` (handler `index.handler`):
```yaml
      Code:
        S3Bucket: ace-bench-artifacts
        S3Key: reject-state-handler.zip
```

For `UnfriendStateHandlerFunction` (handler `index.handler`):
```yaml
      Code:
        S3Bucket: ace-bench-artifacts
        S3Key: unfriend-state-handler.zip
```

For `ReadHandlerFunction` (handler `index.handler`):
```yaml
      Code:
        S3Bucket: ace-bench-artifacts
        S3Key: read-handler.zip
```

- [ ] **Step 8: Verify no ZipFile remains**

Run: `grep -n "ZipFile" corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/known_good.yaml`
Expected: no output

- [ ] **Step 9: Commit**

```bash
git add corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/
git commit -m "feat(corpus): extract arch_01_serverless Lambda code to deployment/lambda, use S3Key refs"
```

---

### Task 5: Create validate_deploy.py

**Files:**
- Create: `corpus/validate_deploy.py`

- [ ] **Step 1: Write validate_deploy.py**

Create `corpus/validate_deploy.py`:

```python
#!/usr/bin/env python3
"""
Deploy a corpus architecture to LocalStack using known_good.yaml + deployment/lambda/.
Usage: python corpus/validate_deploy.py <arch_dir>
Output: JSON to stdout with "outcome" key:
  success        — stack reached CREATE_COMPLETE
  lint_fail      — cfn-lint found errors
  deploy_fail    — CloudFormation deployment failed
  localstack_unreachable — LocalStack not responding
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import boto3
import requests
from botocore.exceptions import ClientError

ENDPOINT = "http://localhost:4566"
REGION = "us-east-1"
CREDS = {"aws_access_key_id": "test", "aws_secret_access_key": "test"}
STACK_NAME = "ace-bench-stack"
ARTIFACT_BUCKET = "ace-bench-artifacts"


def _result(outcome, **kwargs):
    print(json.dumps({"outcome": outcome, **kwargs}))
    sys.exit(0)


def _check_localstack():
    try:
        resp = requests.get(f"{ENDPOINT}/_localstack/health", timeout=5)
        return resp.status_code == 200
    except Exception:
        return False


def _cfn_lint(yaml_path):
    proc = subprocess.run(
        ["cfn-lint", str(yaml_path), "--format", "json"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        try:
            return json.loads(proc.stdout)
        except Exception:
            return [{"message": proc.stdout or proc.stderr}]
    return None


def _ensure_bucket(s3):
    try:
        s3.head_bucket(Bucket=ARTIFACT_BUCKET)
    except ClientError:
        s3.create_bucket(Bucket=ARTIFACT_BUCKET)


def _zip_and_upload(arch_dir, s3):
    lambda_dir = Path(arch_dir) / "deployment" / "lambda"
    if not lambda_dir.exists():
        return []
    uploaded = []
    for fn_dir in sorted(lambda_dir.iterdir()):
        if not fn_dir.is_dir():
            continue
        index_file = fn_dir / "index.py"
        if not index_file.exists():
            continue
        zip_key = f"{fn_dir.name}.zip"
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.write(index_file, "index.py")
            with open(tmp_path, "rb") as f:
                s3.put_object(Bucket=ARTIFACT_BUCKET, Key=zip_key, Body=f.read())
            uploaded.append(zip_key)
        finally:
            os.unlink(tmp_path)
    return uploaded


def _delete_stack_if_exists(cfn):
    try:
        stacks = cfn.describe_stacks(StackName=STACK_NAME)["Stacks"]
        if stacks and stacks[0]["StackStatus"] != "DELETE_COMPLETE":
            cfn.delete_stack(StackName=STACK_NAME)
            _wait_stack(cfn, "DELETE_COMPLETE", timeout=180)
    except ClientError as exc:
        if "does not exist" not in str(exc):
            raise


def _wait_stack(cfn, target, timeout=300):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            status = cfn.describe_stacks(StackName=STACK_NAME)["Stacks"][0]["StackStatus"]
        except ClientError as exc:
            if "does not exist" in str(exc) and target == "DELETE_COMPLETE":
                return "DELETE_COMPLETE"
            raise
        if status == target:
            return status
        if "FAILED" in status or ("ROLLBACK" in status and target == "CREATE_COMPLETE"):
            return status
        time.sleep(5)
    raise TimeoutError(f"Stack did not reach {target} in {timeout}s")


def _get_failure_events(cfn):
    try:
        events = cfn.describe_stack_events(StackName=STACK_NAME)["StackEvents"]
        return [
            {
                "resource": e.get("LogicalResourceId"),
                "status": e.get("ResourceStatus"),
                "reason": e.get("ResourceStatusReason"),
            }
            for e in events
            if "FAILED" in e.get("ResourceStatus", "")
        ]
    except Exception:
        return []


def main():
    if len(sys.argv) < 2:
        _result("error", message="Usage: validate_deploy.py <arch_dir>")

    arch_dir = Path(sys.argv[1]).resolve()
    yaml_path = arch_dir / "known_good.yaml"

    if not yaml_path.exists():
        _result("error", message=f"No known_good.yaml at {yaml_path}")

    if not _check_localstack():
        _result("localstack_unreachable", message="LocalStack not responding at http://localhost:4566")

    lint_findings = _cfn_lint(yaml_path)
    if lint_findings:
        _result("lint_fail", findings=lint_findings)

    s3 = boto3.client("s3", endpoint_url=ENDPOINT, region_name=REGION, **CREDS)
    cfn = boto3.client("cloudformation", endpoint_url=ENDPOINT, region_name=REGION, **CREDS)

    _ensure_bucket(s3)
    uploaded = _zip_and_upload(arch_dir, s3)

    _delete_stack_if_exists(cfn)

    template_body = yaml_path.read_text()
    cfn.create_stack(
        StackName=STACK_NAME,
        TemplateBody=template_body,
        Capabilities=["CAPABILITY_IAM", "CAPABILITY_NAMED_IAM"],
        OnFailure="DO_NOTHING",
    )

    final_status = _wait_stack(cfn, "CREATE_COMPLETE", timeout=300)
    if final_status == "CREATE_COMPLETE":
        _result("success", stack=STACK_NAME, uploaded_zips=uploaded)
    else:
        _result(
            "deploy_fail",
            stack_status=final_status,
            failures=_get_failure_events(cfn),
        )


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify script is importable**

Run: `python -c "import ast; ast.parse(open('corpus/validate_deploy.py').read()); print('syntax ok')" `
Expected: `syntax ok`

- [ ] **Step 3: Commit**

```bash
git add corpus/validate_deploy.py
git commit -m "feat(corpus): add validate_deploy.py CLI for LocalStack deployment pipeline"
```

---

### Task 6: Create validate-corpus skill

**Files:**
- Create: `~/.claude/skills/validate-corpus/SKILL.md`

- [ ] **Step 1: Create skill directory and SKILL.md**

Create `~/.claude/skills/validate-corpus/SKILL.md`:

```markdown
---
name: validate-corpus
description: Deploy a corpus architecture's known_good.yaml to LocalStack and validate it with functional tests. Iteratively fixes deployment or test failures.
---

# validate-corpus

Validate that `corpus/<arch_dir>/known_good.yaml` + `deployment/lambda/` deploy and pass functional tests on LocalStack.

**Invocation:** `/validate-corpus <arch_dir>`
Example: `/validate-corpus arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3`

## Pre-flight: Start LocalStack

1. Check if LocalStack is running:
   ```bash
   localstack status services 2>/dev/null | grep -q "running" && echo "running" || echo "not_running"
   ```

2. If not running, start it:
   ```bash
   localstack start -d
   ```

3. Poll until ready (up to 60s):
   ```bash
   for i in $(seq 1 30); do
     localstack status services 2>/dev/null | grep -q "running" && echo "LocalStack ready" && break
     echo "Waiting for LocalStack... ($i/30)"
     sleep 2
   done
   ```

4. If still not ready after 60s, stop and inform the user: "LocalStack failed to start. Check Docker is running."

## Phase 1: Deploy Loop (max 5 attempts)

Run the deploy script and interpret the JSON outcome. Fix errors and retry.

**Run:**
```bash
python corpus/validate_deploy.py corpus/<arch_dir> 2>&1
```

Parse the JSON `outcome` field from stdout:

### outcome: `localstack_unreachable`
LocalStack became unavailable. Re-run pre-flight to restart it, then retry.

### outcome: `lint_fail`
The `findings` array contains cfn-lint errors. Each finding has:
- `Rule.Id` — the cfn-lint rule that fired
- `Location` — file path and line numbers
- `Message` — what's wrong

Fix the errors in `corpus/<arch_dir>/known_good.yaml`. Common fixes:
- Invalid resource type → check CloudFormation resource type spelling
- Missing required property → add the required field
- Type mismatch → fix the value type

After fixing, run cfn-lint directly to verify before retrying deploy:
```bash
cfn-lint corpus/<arch_dir>/known_good.yaml
```

### outcome: `deploy_fail`
The `failures` array contains CloudFormation resource failures with `resource`, `status`, and `reason` fields.

Diagnose by resource type:
- **Lambda::Function** failures → check `S3Key` exists in `ace-bench-artifacts`, verify handler name matches function in `deployment/lambda/<name>/index.py`
- **IAM::Role** failures → LocalStack IAM is permissive; likely a YAML syntax issue
- **DynamoDB::Table / SQS::Queue** failures → check attribute types and key schema
- **EventSourceMapping** failures → the source (SQS queue / DynamoDB stream) must exist first; check DependsOn or resource ordering

To check if a zip was uploaded:
```bash
aws --endpoint-url=http://localhost:4566 s3 ls s3://ace-bench-artifacts/
```

Fix the issue in `known_good.yaml` or the Lambda source file, then retry from step 1 of this phase.

### outcome: `success`
Stack deployed. Proceed to Phase 2.

**After 5 failed attempts:** Report the last `deploy_fail` output with a summary of what was tried and ask the user how to proceed.

## Phase 2: Functional Test Loop (max 5 attempts)

**Run:**
```bash
python corpus/<arch_dir>/functional_test.py 2>&1
```

Parse lines matching `ASSERT (pass|fail) ([^:]+): (.*)`:
- Lines without `_secondary` suffix are **primary assertions** — all must pass
- Lines with `_secondary` suffix are **secondary** — failures here indicate regression, not broken core behavior

### All primary assertions pass
Report: "✓ All primary assertions pass" and list any secondary failures as informational.

### Primary assertion failures
For each failed primary assertion:

1. **Read `corpus/<arch_dir>/traffic_flow.md`** for architecture context.
2. **Determine fault type** based on the assertion name and message:
   - **Data not found / timeout waiting for records** → likely a Lambda execution issue, event source mapping misconfiguration, or missing environment variable
   - **HTTP 4xx/5xx from API endpoint** → API Gateway integration or Lambda permission issue
   - **Missing CloudFormation output** → add the output to `known_good.yaml`
   - **Boto3 resource not found** → the resource didn't deploy; check stack outputs match what the test expects

3. **Check Lambda logs** for the relevant function:
   ```bash
   aws --endpoint-url=http://localhost:4566 logs describe-log-groups --query 'logGroups[*].logGroupName'
   aws --endpoint-url=http://localhost:4566 logs get-log-events \
     --log-group-name /aws/lambda/ace-bench-stack-<function-name> \
     --log-stream-name $(aws --endpoint-url=http://localhost:4566 logs describe-log-streams \
       --log-group-name /aws/lambda/ace-bench-stack-<function-name> \
       --query 'logStreams[-1].logStreamName' --output text)
   ```

4. Fix the appropriate file:
   - Architecture fault (wrong Lambda logic, bad env var, missing permission) → fix `known_good.yaml` or `deployment/lambda/<name>/index.py`, redeploy with Phase 1
   - Test fault (test expects wrong output format, wrong stack output key) → fix `corpus/<arch_dir>/functional_test.py`

5. Re-run the functional test.

**After 5 failed attempts:** Report the assertion failures and Lambda log excerpts, and ask the user for guidance.
```

- [ ] **Step 2: Verify skill file exists**

Run: `cat ~/.claude/skills/validate-corpus/SKILL.md | head -5`
Expected: shows the frontmatter `---` lines

- [ ] **Step 3: Commit**

```bash
git add corpus/validate_deploy.py 2>/dev/null; true
git commit --allow-empty -m "docs(skills): this skill lives outside repo at ~/.claude/skills/validate-corpus/"
```

Note: The skill file at `~/.claude/skills/validate-corpus/SKILL.md` is outside the git repo. Only commit a note if there's nothing else to stage; otherwise skip.

---

### Task 7: End-to-end validation on arch_12

**Files:** No new files — validate the pipeline works end-to-end.

- [ ] **Step 1: Verify deployment files exist**

Run:
```bash
ls corpus/arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3/deployment/lambda/
```
Expected: `csv-to-sqs/` and `sqs-to-dynamodb/` directories

- [ ] **Step 2: Verify no ZipFile in YAML**

Run:
```bash
grep "ZipFile" corpus/arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3/known_good.yaml
```
Expected: no output

- [ ] **Step 3: Run validate_deploy.py**

Run:
```bash
python corpus/validate_deploy.py corpus/arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3 2>&1
```
Expected: `{"outcome": "success", ...}`

If it fails, diagnose using the Phase 1 loop from Task 6 (fix YAML or Lambda source, retry).

- [ ] **Step 4: Run functional test**

Run:
```bash
python corpus/arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3/functional_test.py 2>&1
```
Expected: All primary `ASSERT pass` lines, no primary `ASSERT fail` lines.

- [ ] **Step 5: Commit results**

If any files were fixed during validation, commit them:
```bash
git add corpus/arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3/
git commit -m "fix(corpus): resolve arch_12 deployment/functional test issues found during validation"
```
