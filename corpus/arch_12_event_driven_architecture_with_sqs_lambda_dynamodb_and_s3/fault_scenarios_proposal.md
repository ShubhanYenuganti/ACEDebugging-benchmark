# Fault Scenario Proposal — arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3

## Architecture Fault Class Analysis

This architecture's resource graph has five integration edges: S3 bucket object-created notification → CSVProcessingToSQSFunction, CSVProcessingToSQSFunction → InventoryUpdatesQueue via `send_message_batch`, InventoryUpdatesQueue → SQSToDynamoDBFunction via event source mapping, SQSToDynamoDBFunction → InventoryUpdatesTable via `put_item`, and the single shared `InventoryFunctionRole` that gates all SDK calls both Lambda functions make. The combination of a shared role with heterogeneous permissions (s3:GetObject, sqs:SendMessage, sqs:ReceiveMessage/DeleteMessage, dynamodb:PutItem), two environment variables that carry resource names across deployment boundaries, and a DLQ with an SQS queue whose visibility timeout must be coordinated with the consumer Lambda's execution timeout creates a particularly rich surface for **connectivity**, **reliability**, and **data correctness** faults.

**Connectivity** is the richest class: the S3 notification configuration targets the Lambda ARN directly and must match the function name exactly; the event source mapping ARN must reference the correct queue; the `SQS_QUEUE_URL` environment variable is the sole mechanism by which the CSV parser finds the queue; and the `CSVInvokePermission` must scope its SourceArn to the bucket's ARN pattern with no trailing slash. Any mismatch at these seams stops the pipeline silently at a different layer than where messages actually accumulate.

**Reliability** is the second richest: the queue's `VisibilityTimeout` (300 s) and the consumer Lambda's `Timeout` (60 s) interact — shrinking the visibility timeout below the Lambda execution time causes in-flight messages to re-appear before the Lambda finishes, leading to duplicate writes and queue never draining. The DLQ is configured with `maxReceiveCount: 5`, but the role has no `sqs:SendMessage` permission scoped to the DLQ ARN specifically, creating a silent-drop condition on repeated failure.

**Data correctness** is naturally present through the two environment variable bindings and through the handler's field extraction. The `sqs-to-dynamodb` handler writes `quantity` as `{"N": str(body["quantity"])}`, which requires the CSV field to arrive as a string parseable by DynamoDB's number type; the `csv-to-sqs` handler uses `csv.DictReader` whose column names are derived verbatim from the CSV header line — a header field name mismatch between what the handler extracts and what DynamoDB expects produces records with missing attributes but no error.

**Security** has moderate richness given the single shared role: misconfiguring the `Resource` ARN in the `dynamodb:PutItem` statement (e.g., to a wrong stack name prefix) combined with an env var pointing to a correctly named table creates a situation where the function attempts writes that are silently denied in real AWS but the role check is worth exploring.

**Performance** is narrower: there are no Kinesis shards, no Step Functions, and no provisioned DynamoDB throughput to misconfigure, but the visibility-timeout-vs-Lambda-timeout interaction is the one meaningful performance fault surface and is covered under reliability.

Fault classes prioritized: **connectivity** (3 scenarios), **reliability** (3 scenarios), **data correctness** (3 scenarios), **security** (1 scenario). Performance is not treated as a separate class but the visibility-timeout interaction is embedded in the reliability scenarios.

---

## Scenarios

### FAULT-01 — S3 Notification Source ARN Trailing-Slash Mismatch Blocks CSV Invocation

**Class:** connectivity
**Type:** coupled
**Chains with:** N/A
**Coupled properties:** `CSVInvokePermission.SourceArn` (wrong — trailing slash appended to bucket ARN), `InventoryUpdatesBucket.NotificationConfiguration.LambdaConfigurations[0].Function` (correct ARN but permission check fails because SourceArn does not match)
**Fails assertions:** `inventory_records_written`, `queue_drained_terminal_state`

#### Misconfiguration

`CSVInvokePermission` is the `AWS::Lambda::Permission` that grants `s3.amazonaws.com` the right to invoke `CSVProcessingToSQSFunction`. Its `SourceArn` must be `arn:aws:s3:::${AWS::StackName}-inventory-updates` (no trailing slash, no wildcard). The fault sets `SourceArn` to `arn:aws:s3:::${AWS::StackName}-inventory-updates/` (trailing slash added).

Simultaneously, the `Event` field on the `LambdaConfigurations` entry in `InventoryUpdatesBucket` is changed from `s3:ObjectCreated:*` to `s3:ObjectCreated:Put` — this means only PutObject events trigger the notification, not CopyObject or CompleteMultipartUpload. Because the functional test uses `put_object`, the notification fires. However, S3 evaluates the `lambda:InvokeFunction` permission check against the bucket ARN without a trailing slash, so the permission check fails and the invocation is denied. The two properties together create the symptom: the notification is sent but the permission check rejects it, silently. Neither change alone produces the same symptom — a permission-only fix with `s3:ObjectCreated:Put` still works for PutObject invocations; a notification-event-only fix with `s3:ObjectCreated:*` and the correct SourceArn would work normally.

- Wrong: `SourceArn: !Sub 'arn:aws:s3:::${AWS::StackName}-inventory-updates/'`
- Correct: `SourceArn: !Sub 'arn:aws:s3:::${AWS::StackName}-inventory-updates'`
- Wrong: `Event: s3:ObjectCreated:Put`
- Correct: `Event: s3:ObjectCreated:*`

#### Observable Symptom

`csv_uploaded` passes — the object is present in the bucket. `inventory_records_written` fails after the full 120-second wait with `records=0`. `queue_drained_terminal_state` fails because the queue remains empty and the consumer was never triggered (visible=0, in_flight=0, but the records were never written). Lambda invocation metrics for CSVProcessingToSQSFunction remain flat. There are no CloudWatch Logs entries for the CSV parser after the upload.

#### Diagnostic Reasoning Path

Step 1 — Check the S3 object exists and the queue depth immediately after upload. The object exists (`csv_uploaded` passes). Queue depth is 0 with no in-flight messages. This is ambiguous: either the parser never ran, or it ran and failed to send messages. The queue depth alone does not distinguish these two scenarios.

Step 2 — Tail CloudWatch Logs for CSVProcessingToSQSFunction. There are no log entries at all — not even a START record. This eliminates handler-level errors and points to the parser never being invoked. The model now knows the failure is at the S3-to-Lambda notification or permission edge, not inside the function.

Step 3 — Check the S3 bucket notification configuration (via `ace_describe_resource` on `InventoryUpdatesBucket` or `ace_list_resources` filtered by S3). The notification shows `Event: s3:ObjectCreated:Put` and the target Lambda ARN. The ARN looks correct. A model may initially conclude the notification is fine because the test uses PutObject.

Step 4 — Inspect the `CSVInvokePermission` resource. `ace_describe_resource` on `CSVInvokePermission` returns the permission's SourceArn. The SourceArn ends with a trailing slash. A model must know that S3's principal check compares the caller's bucket ARN (no slash) against the permission's SourceArn exactly, so the trailing slash causes the check to fail silently. Only by cross-referencing the notification's event type (Put only vs Put + Copy + Multipart) with the permission's SourceArn format does the dual misconfiguration become clear.

#### Resolution

- `corpus/arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3/known_good.yaml` — Change `CSVInvokePermission.Properties.SourceArn` from `!Sub 'arn:aws:s3:::${AWS::StackName}-inventory-updates/'` to `!Sub 'arn:aws:s3:::${AWS::StackName}-inventory-updates'`. Change `InventoryUpdatesBucket.Properties.NotificationConfiguration.LambdaConfigurations[0].Event` from `s3:ObjectCreated:Put` to `s3:ObjectCreated:*`. Both changes are required: fixing only the SourceArn trailing slash while leaving `s3:ObjectCreated:Put` still works for this test but would break CopyObject-based uploads in production (and leaves the template non-idiomatic), while fixing only the event filter leaves the permission broken for all object types.

No handler changes are needed. A template-only fix resolving both properties fully resolves the fault.

#### Difficulty

**Rating:** hard

The trailing slash in the SourceArn is invisible during a visual scan of the template, and the S3 notification event type (`Put` vs `*`) appears plausible for a PutObject-based test — a model must examine both the permission resource and the notification configuration and understand that S3's ARN comparison is exact-string, not prefix-based.

---

### FAULT-02 — SQS Queue URL Environment Variable Points to Wrong Queue, Sending to Orphan Queue

**Class:** connectivity
**Type:** coupled
**Chains with:** N/A
**Coupled properties:** `CSVProcessingToSQSFunction.Environment.Variables.SQS_QUEUE_URL` (hardcoded wrong queue URL suffix), `InventoryUpdatesQueue.Properties.QueueName` (correct, unchanged)
**Fails assertions:** `inventory_records_written`, `queue_drained_terminal_state`

#### Misconfiguration

`CSVProcessingToSQSFunction` receives `SQS_QUEUE_URL` via `!Ref InventoryUpdatesQueue`, which resolves to the queue URL at deploy time. The fault replaces this with a hardcoded URL that substitutes the stack name with a slightly different value — e.g., uses `ace-bench-stack-inventory-queue-v2` instead of `ace-bench-stack-inventory-queue`. In LocalStack this queue does not exist by default, so `send_message_batch` raises a `QueueDoesNotExist` error inside the Lambda.

Simultaneously, the `QueueEventSourceMapping` retains its `EventSourceArn: !GetAtt InventoryUpdatesQueue.Arn` — pointing to the correct queue. The event source mapping is healthy and enabled, pointing at the real queue, but the real queue never receives messages because the CSV parser is sending to a non-existent queue URL and the Lambda is catching or swallowing the SQS error.

The two properties create the symptom together: if only the env var is wrong but the queue URL happens to resolve to any valid queue that the event source mapping is not watching, messages accumulate in the wrong queue and the consumer never fires. If only the event source mapping were wrong (pointing at a different queue), messages would accumulate in the correct queue but no consumer would drain them — a different, more visible symptom. The combination makes the correct queue appear healthy while the real failure is upstream.

- Wrong: `SQS_QUEUE_URL: !Sub 'https://sqs.us-east-1.localhost.localstack.cloud:4566/000000000000/${AWS::StackName}-inventory-queue-v2'`
- Correct: `SQS_QUEUE_URL: !Ref InventoryUpdatesQueue`

#### Observable Symptom

`csv_uploaded` passes. `inventory_records_written` fails with `records=0` after the wait. `queue_drained_terminal_state` fails — the correct queue has `visible=0, in_flight=0` (never received any messages). The CSV parser Lambda has log entries showing it was invoked (START records present), but the logs contain an SQS error (`QueueDoesNotExist` or similar). The correct queue depth is 0, making it appear the consumer successfully drained it — until log inspection reveals the parser never successfully sent messages.

#### Diagnostic Reasoning Path

Step 1 — Check queue depth for `InventoryUpdatesQueue` immediately after upload. Depth is 0 across both visible and in-flight. The consumer Lambda also shows 0 invocations. A model may initially suspect the event source mapping is broken or the consumer is the problem.

Step 2 — Tail logs for SQSToDynamoDBFunction. There are no invocation records at all — not even a START record. This eliminates a consumer-side failure and shifts focus upstream to whether messages are reaching the queue.

Step 3 — Tail logs for CSVProcessingToSQSFunction. Logs show the Lambda was invoked (S3 notification fired correctly), but there is an error in the SQS send: `QueueDoesNotExist` or a non-2xx response from the SQS endpoint. This confirms messages are not reaching the queue.

Step 4 — Inspect the environment variables of CSVProcessingToSQSFunction via `ace_get_environment_variables`. The `SQS_QUEUE_URL` value contains `inventory-queue-v2` rather than `inventory-queue`. Cross-referencing against the actual queue name obtained from `ace_check_queue_depth` (which requires knowing the correct queue name, obtained from `ace_get_stack_outputs`) reveals the mismatch. The model must compare the env var value against the deployed queue's actual URL to confirm the discrepancy.

#### Resolution

- `corpus/arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3/known_good.yaml` — Restore `CSVProcessingToSQSFunction.Properties.Environment.Variables.SQS_QUEUE_URL` to `!Ref InventoryUpdatesQueue`. The hardcoded URL must be replaced with the CloudFormation intrinsic so it tracks the deployed queue URL correctly.

No handler changes are required. A template-only fix is sufficient.

#### Difficulty

**Rating:** medium

The queue depth showing 0 is ambiguous — it looks like the queue was drained successfully — and a model must check both function logs (to see the parser error) and the environment variable value (to find the wrong URL) before the cause becomes clear. The misdirection is the healthy-looking queue metric.

---

### FAULT-03 — Event Source Mapping Batch Window Silently Skips Two-Row Batches Under Filter

**Class:** connectivity
**Type:** coupled
**Chains with:** N/A
**Coupled properties:** `QueueEventSourceMapping.Properties.FilterCriteria` (message filter that requires a `warehouse` key, which the CSV handler never emits), `QueueEventSourceMapping.Properties.BatchSize` (reduced to 1, creating single-message batches that also fail the filter)
**Fails assertions:** `inventory_records_written`, `queue_drained_terminal_state`

#### Misconfiguration

A `FilterCriteria` is added to `QueueEventSourceMapping` that requires the message body to contain a top-level key `warehouse` with value `"A"`. The CSV parser produces messages with keys `product_id`, `location`, `quantity`, `update_date` — the `location` field carries `"Warehouse A"` but the key name is `location`, not `warehouse`. So every message fails the filter and is silently dropped by Lambda (not sent to the DLQ, not retried beyond `maxReceiveCount`).

Simultaneously, `BatchSize` is changed from 10 to 1. This ensures each filter evaluation happens on exactly one message, ruling out any partial-batch processing that might occasionally let messages through due to filter evaluation semantics. With `BatchSize: 10` and no matching messages, the batch might time out and retry — with `BatchSize: 1`, each message is evaluated individually, fails the filter, and the message's receive count increments toward `maxReceiveCount: 5`, after which it moves to the DLQ. The queue eventually drains to the DLQ rather than being processed.

Neither change alone produces the same compound symptom: a filter alone with `BatchSize: 10` might produce batch-level noise; `BatchSize: 1` alone would work fine since there is no filter. The combination produces a silent filter-drop that appears as the queue draining (messages move to DLQ after 5 receive-count increments) without any records written.

- Wrong: `FilterCriteria: {Filters: [{Pattern: '{"body":{"warehouse":["A"]}}'}]}`
- Correct: no `FilterCriteria` property
- Wrong: `BatchSize: 1`
- Correct: `BatchSize: 10`

#### Observable Symptom

`csv_uploaded` passes. `inventory_records_written` fails with `records=0`. `queue_drained_terminal_state` fails initially (messages accumulate and are retried up to 5 times), then eventually passes once messages exhaust their receive count and move to the DLQ — but by that time the test has already timed out on `inventory_records_written`. SQSToDynamoDBFunction CloudWatch Logs show no invocations. The DLQ (`ace-bench-stack-inventory-dlq`) grows to 2 messages.

#### Diagnostic Reasoning Path

Step 1 — Check queue depth for the main queue. Initially shows `visible=2, in_flight=0` — messages are present but not being processed. This looks like a consumer connectivity issue. A model would first check the event source mapping.

Step 2 — Check the event source mapping via `ace_check_event_source` on SQSToDynamoDBFunction. The mapping shows `enabled: true, state: Enabled, batch_size: 1`. The mapping is enabled. The batch size of 1 is unusual (the known-good uses 10) but appears functional. A model may not immediately flag this as suspicious.

Step 3 — Check SQSToDynamoDBFunction logs. There are zero invocations — the function is never called despite the mapping being enabled and messages being present. This is the key finding: an enabled mapping with messages in the queue but zero consumer invocations can only be explained by a filter rejecting all messages.

Step 4 — Inspect the event source mapping in detail via `ace_describe_resource` on `QueueEventSourceMapping` (which returns the raw CloudFormation resource detail) or by examining the Lambda function configuration for filter criteria. The `FilterCriteria` block requires `warehouse: "A"` in the message body. Comparing this against the message format produced by the CSV parser (which uses `location` not `warehouse`) reveals why all messages are filtered out.

Step 5 — Confirm by checking the DLQ depth via `ace_check_queue_depth` on the DLQ queue name. If the DLQ has messages, that confirms messages were received, evaluated, and repeatedly rejected (hitting maxReceiveCount) rather than being processed.

#### Resolution

- `corpus/arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3/known_good.yaml` — Remove the `FilterCriteria` property from `QueueEventSourceMapping` entirely. Restore `BatchSize` to `10`. Both changes are required: removing only the filter while keeping `BatchSize: 1` would allow processing but produce single-message batches that work for the test; however, the intent is to restore the correct configuration. A template-only fix resolves this fault completely.

No handler changes are needed.

#### Difficulty

**Rating:** hard

The misdirection is that the event source mapping appears healthy (enabled, correct ARN, non-zero batch size) and the queue shows messages present but no consumer invocations. A model must deduce that an enabled mapping with messages and zero invocations implies a filter — which requires either deep knowledge of Lambda ESM filter behavior or inspection of the mapping's FilterCriteria configuration, which is not surfaced by `ace_check_event_source` alone and requires `ace_describe_resource` on the mapping resource.

---

### FAULT-04 — DLQ Misconfiguration Silently Drops Consumer Failures Due to Missing Send Permission

**Class:** reliability
**Type:** coupled
**Chains with:** N/A
**Coupled properties:** `InventoryFunctionRole` inline policy (missing `sqs:SendMessage` for the DLQ ARN), `SQSToDynamoDBFunction.Environment.Variables.DYNAMODB_TABLE_NAME` (points to a table that exists but has a different key schema — `pk` instead of `id`)
**Fails assertions:** `inventory_records_written`, `queue_drained_terminal_state`

#### Misconfiguration

`DYNAMODB_TABLE_NAME` environment variable is set to a hardcoded table name that does not match the deployed table's name (e.g., `ace-bench-stack-inventory-archive` instead of `ace-bench-stack-inventory-updates`). This causes `put_item` to fail with `ResourceNotFoundException` every time the consumer Lambda processes a message.

On repeated failure, SQS should route the message to the DLQ after 5 receive attempts. However, the IAM policy's SQS statement grants `sqs:SendMessage` only to `!GetAtt InventoryUpdatesQueue.Arn` — not to `!GetAtt InventoryUpdatesDlq.Arn`. Lambda's internal mechanism for moving messages to the DLQ requires the execution role to have `sqs:SendMessage` on the DLQ. Without this permission, the DLQ delivery silently fails and messages are dropped after exhausting `maxReceiveCount`, disappearing without a trace in the DLQ.

Neither fault alone produces the compound symptom: the wrong table name alone would cause failures but messages would eventually reach the DLQ (if the role had DLQ send permission); the missing DLQ send permission alone (with the correct table name) would not be observed because no failures occur. Only together do they cause the consumer to fail, attempt DLQ delivery, fail that silently, and lose all messages.

- Wrong: `DYNAMODB_TABLE_NAME: !Sub '${AWS::StackName}-inventory-archive'`
- Correct: `DYNAMODB_TABLE_NAME: !Ref InventoryUpdatesTable`
- Wrong: SQS policy statement `Resource: !GetAtt InventoryUpdatesQueue.Arn` only (no DLQ)
- Correct: SQS policy statement `Resource: [!GetAtt InventoryUpdatesQueue.Arn, !GetAtt InventoryUpdatesDlq.Arn]`

#### Observable Symptom

`csv_uploaded` passes. `inventory_records_written` fails with `records=0` after 120 seconds. `queue_drained_terminal_state` eventually shows `visible=0, in_flight=0` — the queue appears drained — but no records exist in the table and the DLQ also shows depth 0. The consumer Lambda logs show repeated `ResourceNotFoundException` errors for the wrong table name. The queue appears to have drained cleanly, but no records were written and no messages reached the DLQ.

#### Diagnostic Reasoning Path

Step 1 — Check `inventory_records_written` fails and queue appears drained. The queue showing as empty while no records exist is paradoxical — it suggests either messages were processed and discarded, or messages silently disappeared. A model would first look at consumer logs.

Step 2 — Tail logs for SQSToDynamoDBFunction. Logs show repeated invocations with `ResourceNotFoundException: Requested resource not found` for a table named `ace-bench-stack-inventory-archive`. This reveals the wrong table name immediately. However, the model must also explain why the queue is empty and the DLQ is also empty.

Step 3 — Check the DLQ depth via `ace_check_queue_depth` on the DLQ. Depth is 0. Given the consumer failed 5+ times per message, messages should have moved to the DLQ. Their absence means DLQ delivery also failed.

Step 4 — Inspect the IAM role via `ace_get_iam_role` on `InventoryFunctionRole`. The SQS statement's `Resource` field only includes the main queue ARN. The DLQ ARN is absent. This explains why messages silently disappeared: after exhausting receive count, Lambda attempted DLQ delivery, was denied (no `sqs:SendMessage` on DLQ), and dropped the messages.

Step 5 — Confirm the correct table name by calling `ace_get_stack_outputs` and comparing the `InventoryUpdatesTableName` output against the env var value from `ace_get_environment_variables` on the consumer function. This makes the env var mismatch explicit.

#### Resolution

- `corpus/arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3/known_good.yaml` — Restore `SQSToDynamoDBFunction.Properties.Environment.Variables.DYNAMODB_TABLE_NAME` to `!Ref InventoryUpdatesTable`. Add `!GetAtt InventoryUpdatesDlq.Arn` to the `Resource` list of the SQS policy statement in `InventoryFunctionRole` (or use a wildcard scoped to the stack's queue names). Both changes are required: fixing only the table name leaves the role unable to send to the DLQ on future failures; fixing only the DLQ permission leaves the consumer writing to the wrong table. A template-only fix resolves both.

No handler changes are required.

#### Difficulty

**Rating:** hard

The empty DLQ combined with zero records creates a diagnostic paradox — messages appear to have vanished. A model must chain three separate findings (wrong table in logs, empty DLQ, missing DLQ permission in role) to understand the complete failure mode. The fact that the queue shows as drained is a strong misdirection toward concluding the consumer succeeded.

---

### FAULT-05 — Visibility Timeout Shorter Than Lambda Timeout Causes Duplicate Processing and Queue Never Drains

**Class:** reliability
**Type:** coupled
**Chains with:** N/A
**Coupled properties:** `InventoryUpdatesQueue.Properties.VisibilityTimeout` (set to 10 seconds, shorter than Lambda timeout), `SQSToDynamoDBFunction.Properties.Timeout` (set to 60 seconds, unchanged)
**Fails assertions:** `queue_drained_terminal_state`

#### Misconfiguration

`InventoryUpdatesQueue.Properties.VisibilityTimeout` is changed from `300` to `10`. `SQSToDynamoDBFunction.Properties.Timeout` remains at `60`. When the consumer Lambda picks up messages, SQS hides them for only 10 seconds. If the Lambda invocation takes longer than 10 seconds — which it routinely does under LocalStack's simulated latency for DynamoDB writes — SQS makes the messages visible again before the Lambda finishes and deletes them. A second Lambda invocation picks them up and processes them again, writing duplicate records to DynamoDB.

The `queue_drained_terminal_state` assertion checks `visible=0 AND in_flight=0`. With the visibility timeout shorter than the Lambda duration, messages cycle between in-flight and visible repeatedly. The queue never reaches a drained state within the test window. Meanwhile, `inventory_records_written` may pass (records do exist — possibly duplicate ones) or may flap depending on timing.

Neither change alone produces this symptom: reducing the visibility timeout alone while keeping Lambda timeout also short would result in messages completing before the timeout expires; keeping the Lambda timeout at 60s with the correct 300s visibility timeout works correctly. Only the combination of a Lambda timeout exceeding the queue's visibility timeout causes the re-visibility loop.

- Wrong: `VisibilityTimeout: 10`
- Correct: `VisibilityTimeout: 300`

#### Observable Symptom

`csv_uploaded` passes. `inventory_records_written` may pass (records exist, possibly more than 2 due to duplicate writes) or may be intermittently unreliable. `queue_drained_terminal_state` fails consistently — at the 120-second assertion window, the queue shows non-zero `in_flight` or `visible` messages because Lambda invocations are continuously re-picking up messages that are re-appearing. The consumer Lambda logs show repeated START/END cycles for the same message bodies, with the same `product_id` and `location` values appearing multiple times.

#### Diagnostic Reasoning Path

Step 1 — Check queue depth during the wait window. The queue alternates between `visible=2, in_flight=0` and `visible=0, in_flight=2`. This oscillating pattern is distinct from a stuck queue (all visible) or a healthy queue (0/0 terminal).

Step 2 — Tail logs for SQSToDynamoDBFunction. Logs show successful invocations with `put_item` completing, but the same `product_id` and `location` combinations appear in multiple invocations at different timestamps. The function is being invoked repeatedly for the same messages.

Step 3 — The oscillating queue depth combined with repeated consumer invocations for identical data points to messages re-appearing after delivery. Check the SQS queue attributes via `ace_check_queue_depth` to obtain `ApproximateAgeOfOldestMessage` — it grows past 10 seconds, confirming the visibility timeout has already elapsed while messages are still in-flight. Inspect the queue configuration via `ace_describe_resource` on `InventoryUpdatesQueue` to read the `VisibilityTimeout` attribute.

Step 4 — Cross-reference the queue's `VisibilityTimeout` (10 s) against the Lambda's `Timeout` (60 s) via `ace_get_environment_variables` or `ace_describe_resource` on `SQSToDynamoDBFunction`. The Lambda timeout exceeds the visibility timeout, confirming messages become visible again before Lambda deletes them from the queue after successful processing.

#### Resolution

- `corpus/arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3/known_good.yaml` — Restore `InventoryUpdatesQueue.Properties.VisibilityTimeout` to `300`. The visibility timeout must be greater than the Lambda timeout (60 s) to ensure messages are not re-delivered while the consumer is still processing them. A template-only fix is sufficient.

No handler changes are required.

#### Difficulty

**Rating:** medium

The oscillating queue depth is a recognizable pattern for SQS/Lambda visibility timeout misconfiguration, but only if the model interprets the repeated consumer invocations in logs as a re-delivery signal rather than a normal retry. The misdirection is that records do appear in DynamoDB (the function succeeds — just repeatedly), making `inventory_records_written` pass while `queue_drained_terminal_state` fails.

---

### FAULT-06 — DLQ Redriving to Main Queue Creates Infinite Retry Loop on Handler Exception

**Class:** reliability
**Type:** coupled
**Chains with:** N/A
**Coupled properties:** `InventoryUpdatesDlq.Properties.RedrivePolicy` (DLQ is given a redrive policy that routes back to itself or to the main queue after 1 receipt), `SQSToDynamoDBFunction.Environment.Variables.DYNAMODB_TABLE_NAME` (set to wrong name causing repeated handler failures)
**Fails assertions:** `inventory_records_written`, `queue_drained_terminal_state`

#### Misconfiguration

`SQSToDynamoDBFunction` env var `DYNAMODB_TABLE_NAME` is set to `!Sub '${AWS::StackName}-inventory-staging'` (wrong suffix). Every consumer invocation raises `ResourceNotFoundException` for the non-existent table. With `maxReceiveCount: 5`, messages should dead-letter after 5 attempts.

However, `InventoryUpdatesDlq` is given a `RedrivePolicy` that points back to `InventoryUpdatesQueue` as its dead-letter target with `maxReceiveCount: 1`. In LocalStack, this is accepted by the SQS API. The result: messages flow from the main queue to the DLQ (after 5 failures), then are immediately moved back to the main queue (after 1 receipt at the DLQ level), creating a circular redrive loop. The main queue never drains and `inventory_records_written` never gets records written.

Neither change alone produces the loop: the wrong table name alone would cause messages to dead-letter to the DLQ and stay there (visible but not retried); the circular redrive policy alone (with a correct table name) would never activate because no failures would occur. Only together do they produce the infinite bounce.

- Wrong: `DYNAMODB_TABLE_NAME: !Sub '${AWS::StackName}-inventory-staging'`
- Correct: `DYNAMODB_TABLE_NAME: !Ref InventoryUpdatesTable`
- Wrong: `InventoryUpdatesDlq` gains `RedrivePolicy: {deadLetterTargetArn: !GetAtt InventoryUpdatesQueue.Arn, maxReceiveCount: 1}`
- Correct: `InventoryUpdatesDlq` has no `RedrivePolicy`

#### Observable Symptom

`csv_uploaded` passes. `inventory_records_written` fails with `records=0`. `queue_drained_terminal_state` fails — the queue alternates between having messages and appearing briefly empty as they cycle between the main queue and DLQ. Consumer Lambda logs show repeated failures for `ace-bench-stack-inventory-staging` table not found. Queue depth oscillates in a longer cycle (5 attempts at main queue, 1 attempt at DLQ, back to main queue) rather than the rapid oscillation of a visibility timeout issue.

#### Diagnostic Reasoning Path

Step 1 — Check queue depth for the main queue. It shows messages present intermittently. Checking the DLQ also shows messages intermittently, but neither accumulates permanently. This cycling pattern differs from a standard failure (DLQ fills up) and from a visibility timeout issue (rapid oscillation).

Step 2 — Tail consumer logs. Repeated `ResourceNotFoundException` for `ace-bench-stack-inventory-staging`. This immediately reveals the wrong table name. But the model must also explain why the DLQ does not accumulate messages after the main queue's `maxReceiveCount` is exhausted.

Step 3 — Check the DLQ depth after waiting a full retry cycle (5 main-queue attempts ≈ 5× visibility timeout). The DLQ depth briefly shows 2 messages, then drops back to 0. This transient DLQ depth means messages are leaving the DLQ — either being consumed (but there is no consumer mapping on the DLQ) or being redriven.

Step 4 — Inspect the DLQ's configuration via `ace_describe_resource` on `InventoryUpdatesDlq`. The DLQ itself has a `RedrivePolicy` with `deadLetterTargetArn` pointing back to the main queue ARN. This reveals the circular loop. The model now understands both faults: the wrong table name causes continuous failures, and the circular redrive policy prevents messages from ever being permanently dead-lettered.

#### Resolution

- `corpus/arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3/known_good.yaml` — Restore `SQSToDynamoDBFunction.Properties.Environment.Variables.DYNAMODB_TABLE_NAME` to `!Ref InventoryUpdatesTable`. Remove the `RedrivePolicy` from `InventoryUpdatesDlq`. Both are required: fixing only the table name stops failures but leaves the DLQ with a circular redrive that would activate on any future failure; removing only the redrive policy leaves the consumer writing to the wrong table. A template-only fix resolves the fault.

No handler changes are required.

#### Difficulty

**Rating:** very_hard

The circular redrive loop is non-obvious because messages appear to be moving correctly (they do reach the DLQ) but then disappear from the DLQ without being processed. A model must distinguish between three different oscillation patterns (visibility timeout, normal DLQ accumulation, circular redrive) and inspect the DLQ's own configuration — which is a step most diagnostic paths skip when the main queue consumer logs already show an obvious error.

---

### FAULT-07 — Handler Extracts Wrong Field Name, Writing Null Quantity to DynamoDB

**Class:** data_correctness
**Type:** coupled
**Chains with:** N/A
**Coupled properties:** `sqs-to-dynamodb/index.py` handler reads `body["qty"]` instead of `body["quantity"]`, and the `InventoryUpdatesTable` schema has no validation on the `quantity` attribute type (it is a non-key attribute, so a missing value causes `put_item` to succeed with a null/absent field)
**Fails assertions:** `inventory_records_written`

#### Misconfiguration

The `sqs-to-dynamodb` handler's `put_item` call uses `body["qty"]` to populate the `quantity` attribute. The CSV rows produced by `csv-to-sqs` use the header column `quantity` (matching the CSV `csv_body` in the functional test: `"product_id,location,quantity,update_date\n"`). So `body["qty"]` raises a `KeyError` in Python.

Simultaneously, the Lambda function's error handling is not explicit — a `KeyError` will propagate and cause the Lambda invocation to fail. However, for the fault to be subtle rather than immediately obvious from logs, the handler is also modified to silently coerce the missing key with `body.get("qty", "0")` — so no error is raised and `put_item` succeeds, writing `"quantity": {"N": "0"}` for every record. Records exist in the table (so `inventory_records_written` passes the count check), but the quantity field is always 0.

However, `inventory_records_written` only checks `len(items) >= expected_count` — it does not validate field values. The fault therefore passes `inventory_records_written` by count. To make the fault fail a primary assertion, the handler is additionally changed to use `body["qty"]` (without `.get()`) so a `KeyError` propagates and the Lambda invocation fails, causing messages to not be deleted from the queue and the queue to not drain. This fails `queue_drained_terminal_state` as the primary assertion.

The two coupled properties are: (1) `body["qty"]` (KeyError on missing key) in the handler, and (2) the CSV header `quantity` (not `qty`) in the functional test's CSV body — which is correct and cannot be changed, making the handler's field name the sole mutable side. The second coupled property is the Lambda's lack of a try/except around the field extraction, combined with SQS not deleting failed messages from the queue automatically (Lambda must complete successfully for SQS to delete the message).

- Wrong: `"quantity": {"N": str(body["qty"])}` in `sqs-to-dynamodb/index.py`
- Correct: `"quantity": {"N": str(body["quantity"])}` in `sqs-to-dynamodb/index.py`

#### Observable Symptom

`csv_uploaded` passes. `inventory_records_written` fails with `records=0` because the consumer Lambda fails on every invocation (KeyError). `queue_drained_terminal_state` fails because messages remain visible after the Lambda fails (SQS makes them visible again). Consumer Lambda CloudWatch Logs show `KeyError: 'qty'` on every invocation. The queue depth shows messages accumulating in visible state as the Lambda repeatedly fails and SQS returns them.

#### Diagnostic Reasoning Path

Step 1 — Check queue depth after CSV upload. Queue shows `visible=2, in_flight=0` oscillating — messages are being picked up (in-flight) then returned (visible) repeatedly. This indicates the consumer is failing to process them.

Step 2 — Tail consumer Lambda logs. Logs show `KeyError: 'qty'` on every invocation, clearly identifying the wrong field name in the handler code.

Step 3 — Inspect the handler code to identify the wrong key name (`qty` vs `quantity`). Cross-reference with the message format by examining the CSV parser handler — `csv-to-sqs/index.py` uses `csv.DictReader` which produces keys from the CSV header row. The functional test's CSV has `quantity` as the header, so the message body will have `quantity` not `qty`.

Step 4 — Confirm by checking the event source mapping is healthy via `ace_check_event_source` (enabled, correct ARN) to rule out any mapping-level issue, then verifying the actual message content in the queue by checking SQS message attributes. The root cause is definitively the handler field name mismatch.

#### Resolution

- `corpus/arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3/deployment/lambda/sqs-to-dynamodb/index.py` — Change `body["qty"]` to `body["quantity"]` in the `put_item` call's Item construction. A code-only fix is sufficient; no template change is required since the table schema, IAM permissions, and environment variables are all correct. A template-only fix is insufficient because the error lives in the handler's field extraction logic.

#### Difficulty

**Rating:** medium

The `KeyError: 'qty'` log message is explicit and directly names the wrong field, making the root cause straightforward to identify from logs alone. The challenge is that a model must trace the field name back to the CSV header (produced by the upstream handler using `csv.DictReader`) to confirm what the correct field name should be, rather than simply guessing `quantity` from context.

---

### FAULT-08 — CSV Parser Sends Messages With Wrong JSON Keys Due to Header BOM Strip Misconfiguration Combined With Wrong Env Var

**Class:** data_correctness
**Type:** chained
**Chains with:** FAULT-02 (prerequisite — FAULT-02's wrong SQS_QUEUE_URL must be resolved before this fault's symptom becomes observable, since FAULT-02 prevents any messages from reaching the queue at all)
**Coupled properties:** `csv-to-sqs/index.py` reads the CSV using `utf-8` (not `utf-8-sig`) causing a BOM prefix on the first column header, and `SQSToDynamoDBFunction.Environment.Variables.DYNAMODB_TABLE_NAME` is correct (this fault is handler-only)
**Fails assertions:** `inventory_records_written`, `queue_drained_terminal_state`

#### Misconfiguration

The `csv-to-sqs` handler currently reads the S3 object with `decode("utf-8-sig")`, which strips the BOM (`﻿`) from UTF-8-with-BOM CSV files. The fault changes this to `decode("utf-8")`. When the functional test uploads a CSV with a plain UTF-8 encoding (no BOM), this change has no effect — the test still passes. However, the functional test's CSV body is constructed as a Python string literal (`"product_id,location,quantity,update_date\n"`) and encoded with `.encode("utf-8")`, which does not add a BOM.

To make this fault observable, the functional test's CSV upload is not modified (it cannot be). Instead, the fault is: the handler changes `decode("utf-8-sig")` to `decode("utf-8")` AND the handler's `csv.DictReader` is changed to strip leading whitespace from keys using `str.lstrip()` — but the `lstrip()` call is applied to the value rather than the key, so the first column header name retains a leading space if the CSV has one. Since the test CSV does not have a space, this is a no-op for the test. This makes the fault non-triggering for the basic test scenario.

To make this scenario properly fail a primary assertion, the actual fault is: the handler is changed to use `response["Body"].read().decode("utf-8")` and then the CSV is produced by adding a `﻿` BOM prefix to the encoded bytes before upload — but the functional test cannot be modified. Therefore this scenario is redesigned:

**Redesigned:** The `csv-to-sqs` handler changes its field extraction to send the message body as `{"product_id": row["product_id"], "loc": row["location"], "quantity": row["quantity"], "update_date": row["update_date"]}` — renaming `location` to `loc` in the outgoing JSON. The `sqs-to-dynamodb` handler extracts `body["location"]`, which fails with `KeyError` because the message body now contains `loc` not `location`. This causes all consumer invocations to fail, messages to return to the queue, and `queue_drained_terminal_state` to fail.

The chaining: if FAULT-02 is also active (wrong `SQS_QUEUE_URL` in the CSV parser), no messages reach the main queue at all, and FAULT-08's symptom (consumer failing on `location` key) is completely invisible — there are no messages for the consumer to fail on. Only after FAULT-02 is resolved (correct queue URL restored) do messages reach the queue and the consumer fails on the renamed `loc` key.

- Wrong: `csv-to-sqs/index.py` sends `{"loc": row["location"], ...}` instead of `{"location": row["location"], ...}`
- Correct: `{"location": row["location"], ...}` (matching the key expected by the consumer)

#### Observable Symptom

After FAULT-02 is resolved: `csv_uploaded` passes. Messages reach the queue (depth increases after upload). `inventory_records_written` fails with `records=0`. `queue_drained_terminal_state` fails. Consumer Lambda logs show `KeyError: 'location'` on every invocation. The queue depth oscillates as messages are re-delivered.

While FAULT-02 is active: no messages reach the queue at all. FAULT-08's symptom (consumer KeyError) is completely masked because the consumer is never invoked.

#### Diagnostic Reasoning Path

Step 1 — After FAULT-02 is resolved and messages flow: check queue depth. Messages accumulate (visible=2). Consumer is invoked (logs show START records) but fails.

Step 2 — Tail consumer Lambda logs. `KeyError: 'location'` is the error. This points to the message body not having a `location` key.

Step 3 — Tail CSV parser Lambda logs. The parser ran successfully and `send_message_batch` returned no errors. The parser logged it sent N messages. This confirms the problem is in the message content, not the delivery.

Step 4 — Inspect the CSV parser handler code. The `MessageBody` construction uses `json.dumps(row)` where `row` is a `csv.DictReader` dict. But the row dict has `loc` as a key rather than `location`, because the handler maps it before serialization. Comparing the handler's outgoing message schema against the consumer's expected schema reveals the field name mismatch.

#### Resolution

- `corpus/arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3/deployment/lambda/csv-to-sqs/index.py` — Change the message body construction to preserve the original `location` key from the CSV row rather than renaming it to `loc`. The handler should pass `json.dumps(row)` directly (using the dict as-is from DictReader) without any key remapping. A template-only fix is insufficient because the error is in the handler's message serialization logic. A code-only fix (restoring the correct field name) is sufficient; no template changes are needed.

#### Difficulty

**Rating:** very_hard

This fault requires resolving FAULT-02 first before the symptom appears, meaning a model working through multiple faults sequentially must recognize that fixing the queue URL is a prerequisite to observing this consumer-level error. The chaining creates a natural ordering trap: a model that patches both faults simultaneously may not understand which one was masking the other.

---

### FAULT-09 — IAM Role Scopes DynamoDB Permission to Wrong Table ARN Suffix, Writes Silently Denied

**Class:** security
**Type:** coupled
**Chains with:** N/A
**Coupled properties:** `InventoryFunctionRole` inline policy `dynamodb:PutItem` Resource (scoped to `arn:aws:dynamodb:...table/${AWS::StackName}-inventory-archive`), `SQSToDynamoDBFunction.Environment.Variables.DYNAMODB_TABLE_NAME` (correctly set to `!Ref InventoryUpdatesTable`)
**Fails assertions:** `inventory_records_written`, `queue_drained_terminal_state`

#### Misconfiguration

The `dynamodb:PutItem` statement in `InventoryFunctionRole`'s inline policy is changed so its `Resource` is `!Sub 'arn:aws:dynamodb:${AWS::Region}:${AWS::AccountId}:table/${AWS::StackName}-inventory-archive'` instead of `!GetAtt InventoryUpdatesTable.Arn`. The table `ace-bench-stack-inventory-archive` does not exist.

`DYNAMODB_TABLE_NAME` env var still correctly points to `ace-bench-stack-inventory-updates` (the deployed table). So the consumer Lambda calls `put_item` on the correct table name, but the IAM policy only permits writes to the non-existent `inventory-archive` table. In LocalStack, this results in an `AccessDeniedException` on every `put_item` call.

The coupling: if only the IAM resource were wrong (env var correct), the consumer would fail with `AccessDeniedException` — detectable from logs. If only the env var were wrong (IAM correct), the consumer would fail with `ResourceNotFoundException` — also detectable. The fault combines correct env var with wrong IAM scope to produce `AccessDeniedException` on a legitimate table, creating confusion because the table name in the error matches the deployed table, yet the error is a permission denial — not a resource-not-found error.

- Wrong: `Resource: !Sub 'arn:aws:dynamodb:${AWS::Region}:${AWS::AccountId}:table/${AWS::StackName}-inventory-archive'`
- Correct: `Resource: !GetAtt InventoryUpdatesTable.Arn`

#### Observable Symptom

`csv_uploaded` passes. Messages reach the queue and the consumer is invoked. `inventory_records_written` fails with `records=0`. `queue_drained_terminal_state` fails. Consumer Lambda logs show `AccessDeniedException: User ... is not authorized to perform: dynamodb:PutItem on resource: arn:aws:dynamodb:us-east-1:000000000000:table/ace-bench-stack-inventory-updates with an explicit deny`. The table name in the error is correct — the deployed table — which is confusing because it appears the policy should cover it.

#### Diagnostic Reasoning Path

Step 1 — Consumer logs show `AccessDeniedException` for `dynamodb:PutItem` on `ace-bench-stack-inventory-updates`. The table name looks correct. A model might initially assume the table does not exist or there is a resource policy.

Step 2 — Verify the table exists and is ACTIVE via `ace_describe_resource` on `InventoryUpdatesTable`. The table is ACTIVE with the correct name. This eliminates resource-not-found as the cause.

Step 3 — Inspect the IAM role via `ace_get_iam_role` on `InventoryFunctionRole`. The inline policy shows `dynamodb:PutItem` with `Resource: arn:aws:dynamodb:us-east-1:000000000000:table/ace-bench-stack-inventory-archive`. This ARN ends in `inventory-archive`, not `inventory-updates`. The policy scope does not match the table the consumer is trying to write to.

Step 4 — Confirm via `ace_simulate_policy` on the role ARN with action `dynamodb:PutItem` and resource `arn:aws:dynamodb:us-east-1:000000000000:table/ace-bench-stack-inventory-updates`. The simulation returns `implicitDeny`, confirming the action is not permitted for the actual table despite the role appearing to have `dynamodb:PutItem` in its policy.

#### Resolution

- `corpus/arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3/known_good.yaml` — Restore the `dynamodb:PutItem` statement's `Resource` to `!GetAtt InventoryUpdatesTable.Arn`. The wrong ARN suffix (`inventory-archive`) must be corrected to reference the deployed table's ARN via the CloudFormation intrinsic. A template-only fix is sufficient; no handler changes are needed since the env var is correct.

#### Difficulty

**Rating:** hard

The `AccessDeniedException` error message names the correct table (`inventory-updates`), which a model may read as confirming the table name is correct and focus instead on resource policies or table-level permissions. The discrepancy between the error's table name and the policy's resource ARN requires side-by-side comparison of the error log and the IAM policy document, a step that is easy to skip if the model assumes the role's `dynamodb:PutItem` action is correctly scoped.

---

### FAULT-10 — S3 GetObject Permission Scoped to Wrong Bucket Prefix, CSV Parser Fails Silently on Throttled Re-invoke

**Class:** security
**Type:** coupled
**Chains with:** N/A
**Coupled properties:** `InventoryFunctionRole` inline policy `s3:GetObject` Resource (wrong bucket name suffix), `CSVProcessingToSQSFunction.Properties.ReservedConcurrentExecutions` (set to 0, throttling all invocations)
**Fails assertions:** `inventory_records_written`, `queue_drained_terminal_state`

#### Misconfiguration

The `s3:GetObject` statement's `Resource` is changed to `!Sub 'arn:aws:s3:::${AWS::StackName}-raw-uploads/*'` (wrong bucket name suffix — `raw-uploads` instead of `inventory-updates`). The actual bucket is `ace-bench-stack-inventory-updates`. Any `get_object` call from the parser would fail with `AccessDenied`.

Simultaneously, `CSVProcessingToSQSFunction` is given `ReservedConcurrentExecutions: 0`. This throttles all invocations of the CSV parser — S3 notifications are rejected with `TooManyRequestsException` before the function even starts. No CloudWatch Logs are produced for throttled invocations.

The coupling: if only the `ReservedConcurrentExecutions: 0` is present, the parser is throttled and no logs exist — the symptom looks like an S3 notification permission issue (CSVInvokePermission). If only the s3:GetObject resource is wrong, the parser is invoked, logs an `AccessDenied` on `get_object`, and the cause is visible in logs. Together, the throttle (concurrency=0) masks the GetObject permission error entirely — no invocations occur, so no AccessDenied log ever appears. After fixing the concurrency (setting it back to unreserved), the GetObject AccessDenied surfaces, requiring a second round of investigation.

- Wrong: `Resource: !Sub 'arn:aws:s3:::${AWS::StackName}-raw-uploads/*'` in s3:GetObject statement
- Correct: `Resource: !Sub 'arn:aws:s3:::${AWS::StackName}-inventory-updates/*'`
- Wrong: `ReservedConcurrentExecutions: 0` on CSVProcessingToSQSFunction
- Correct: no `ReservedConcurrentExecutions` property (unreserved)

#### Observable Symptom

`csv_uploaded` passes. Queue depth stays at 0. Consumer is never invoked. CSV parser has zero CloudWatch Log entries (throttled invocations produce no logs). Functional test fails on `inventory_records_written` and `queue_drained_terminal_state`. The bucket notification configuration is intact and correct — a model inspecting the notification would see it is configured, making the throttle the hidden blocking factor.

#### Diagnostic Reasoning Path

Step 1 — Queue depth is 0, consumer has no invocations, CSV parser has no log entries. The parser not logging anything suggests either it was never invoked or it was throttled before initialization. Check the event source mapping state — it is healthy. Check the S3 notification configuration — it is correct. No obvious connectivity break.

Step 2 — Inspect the CSV parser Lambda function configuration via `ace_describe_resource` on `CSVProcessingToSQSFunction`. The response includes `ReservedConcurrentExecutions: 0`. This is the throttle. A model should now understand why there are no invocations. Removing the reserved concurrency (setting it to unreserved) is the first fix.

Step 3 — After addressing the concurrency (template fix), the parser is invoked and logs now appear. Logs show `AccessDenied: s3:GetObject on arn:aws:s3:::ace-bench-stack-inventory-updates/inventory-xxxx.csv`. This reveals the second fault.

Step 4 — Inspect the IAM role via `ace_get_iam_role` on `InventoryFunctionRole`. The `s3:GetObject` statement's `Resource` ends in `raw-uploads/*` instead of `inventory-updates/*`. This is the permission mismatch. A `ace_simulate_policy` call confirms the action is denied for the actual bucket and key.

#### Resolution

- `corpus/arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3/known_good.yaml` — Remove `ReservedConcurrentExecutions: 0` from `CSVProcessingToSQSFunction.Properties` (or delete the property entirely to use unreserved concurrency). Fix the `s3:GetObject` `Resource` ARN suffix from `raw-uploads/*` to `inventory-updates/*`. Both changes are required: fixing only the concurrency reveals the s3 permission error but does not resolve the flow; fixing only the s3 permission while concurrency remains at 0 leaves the parser permanently throttled. A template-only fix is sufficient; no handler changes are required.

A template-only fix is sufficient. A code-only fix is insufficient.

#### Difficulty

**Rating:** very_hard

The concurrency=0 throttle produces no logs and is invisible from queue depth or notification configuration inspection — it requires examining the Lambda function's reserved concurrency configuration, which is not surfaced by the most common first-pass tools (`ace_check_queue_depth`, `ace_get_log_tail`). After fixing the throttle, a second fault surfaces that requires a separate IAM investigation. The two-phase nature of the diagnosis (fix throttle, then find IAM error) makes this the most multi-step scenario in the proposal.
