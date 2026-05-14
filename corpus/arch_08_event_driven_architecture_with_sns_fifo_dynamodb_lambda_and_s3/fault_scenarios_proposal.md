# Fault Scenario Proposal — arch_08_event_driven_architecture_with_sns_fifo_dynamodb_lambda_and_s3

## Architecture Fault Class Analysis

This architecture's integration surface is dominated by two sequential fan-out chains: one SNS FIFO topic feeds two SQS FIFO queues via subscriptions with distinct configurations (analytics receives all events; inventory receives only `JobCreated` and `JobDeleted` via a filter policy), and each queue is consumed by a Lambda that persists to a different backend (S3 and DynamoDB respectively). The richest fault classes are **connectivity** and **data correctness**, for two reasons: (1) the FIFO subscription filter policy on the inventory path is a silent drop mechanism — a misconfigured filter passes the wrong events or none at all without producing any error visible at the SNS publish layer; and (2) both consumer handlers extract event-type metadata through a multi-key fallback chain (`messageAttributes` → `message_attributes` → `eventType` in payload body), and the `RawMessageDelivery` flag on each subscription determines whether message attributes arrive in the SQS record at all.

The **reliability** class is also naturally rich because both queues have DLQ configurations and the event source mappings use `ReportBatchItemFailures`, creating subtle interactions between retry behavior, visibility timeout, and DLQ delivery permissions. The **security** class offers one high-value scenario: the analytics role's `s3:PutObject` permission is scoped to a bucket ARN suffix wildcard — a wrong bucket name in `BUCKET_NAME` causes writes to succeed silently to a non-existent or wrong bucket from the Lambda's perspective, producing no IAM denial but missing objects. The **performance** class is the least naturally rich for this specific architecture because there are no provisioned throughput resources, no Kinesis shards, and no Step Functions state machines; the only plausible performance fault involves SQS visibility timeout versus Lambda timeout, which is covered under reliability since its primary effect is duplicate processing rather than latency.

Focus: **connectivity** (3 scenarios), **data correctness** (3 scenarios), **reliability** (2 scenarios), **security** (1 scenario), **performance** (1 scenario). Performance and security are each represented once because this architecture has limited natural surface for those classes beyond one genuine scenario each.

---

## Scenarios

### FAULT-01 — Inventory filter policy passes wrong attribute name

**Class:** connectivity
**Type:** coupled
**Chains with:** N/A
**Coupled properties:** `InventorySubscription.FilterPolicy` (uses `eventSource` instead of `eventType` as the filter key), `InventorySubscription.RawMessageDelivery` (set to `true` so the attribute name in the filter must match what SNS sees in `MessageAttributes`)
**Fails assertions:** `inventory_terminal_state`

#### Misconfiguration

`InventorySubscription.FilterPolicy` is changed from:
```yaml
FilterPolicy:
  eventType:
    - JobCreated
    - JobDeleted
```
to:
```yaml
FilterPolicy:
  eventSource:
    - anti-corruption-service
```

Simultaneously, `InventorySubscription.RawMessageDelivery` remains `true`. The anti-corruption handler publishes with a `MessageAttributes` map that includes `eventType` (the string values `JobCreated`, `JobSalaryUpdated`, `JobDeleted`) and the message body includes `eventSource: "anti-corruption-service"`. SNS FIFO filter policy for raw-delivery subscriptions evaluates `MessageAttributes` keys only, not body fields. Since `eventSource` is not a published `MessageAttribute` key (only `eventType` is), no message ever matches the filter and no message reaches `InventoryJobEventsQueue`.

Neither misconfiguration alone produces this symptom in the same way: if only the filter key is wrong but `RawMessageDelivery` is `false`, SNS wraps the original message in an envelope and the filter evaluates against the SNS-side `MessageAttributes`, which still won't match `eventSource` — so the symptom is the same in magnitude but the diagnostic path differs (a model checking raw delivery would be misled). The coupling is important because if `RawMessageDelivery` were `false`, the filter policy semantics change slightly (attributes on the SNS envelope vs. publisher attributes), and fixing only that property would not resolve the filter key mismatch.

#### Observable Symptom

After `assert_events_published` passes (the producer invocation succeeds and returns the correct `jobId`), `inventory_terminal_state` times out after 120 seconds. The analytics path completes normally — `analytics_object_created` passes. The inventory queue depth stays at zero throughout the wait window. No Lambda errors appear in the inventory function's log tail because the function is never invoked.

#### Diagnostic Reasoning Path

Step 1 — Check the inventory queue depth with `ace_check_queue_depth`. It returns `messages_available: 0` and `oldest_message_age_seconds: 0` throughout. This confirms that no messages are reaching the queue, but it does not reveal whether the cause is upstream (SNS not delivering) or a queue policy issue preventing receipt.

Step 2 — Check the analytics queue depth and verify the analytics function was invoked. `ace_check_queue_depth` on the analytics queue shows messages have been processed (or are draining), confirming SNS is publishing successfully and the analytics subscription is working. This rules out a broken producer or broken SNS topic, and narrows the fault to the inventory subscription specifically.

Step 3 — Retrieve the SNS topic attributes with `ace_get_sns_topic`. This shows `subscriptions_confirmed: 2`, so both subscriptions are active. The topic itself is not broken. This observation is necessary but insufficient — it rules out a missing subscription but does not expose the filter policy contents.

Step 4 — Inspect the `InventorySubscription` resource via `ace_describe_resource`. The CloudFormation resource detail will show the subscription ARN. The filter policy contents are not returned by `ace_describe_resource` for an SNS subscription resource type — the tool calls `GetFunctionCommand` for Lambda types but has no SNS-subscription-specific enrichment, so the filter policy itself is not immediately visible. The model must then examine the faulted template directly or use `ace_get_environment_variables` to check the inventory function's `TABLE_NAME` (which will look correct), eliminating handler misconfiguration as the cause and redirecting attention to the subscription configuration in the template.

Step 5 — Reading the `faulted.yaml` template (via the agent's `read_file` tool restricted to `deployment/`) is not possible for the subscription YAML; the model must cross-reference the `InventorySubscription` block in the template it has write access to — `faulted.yaml` — and compare the `FilterPolicy` key (`eventSource`) against the `MessageAttributes` key actually published by the anti-corruption handler (`eventType`), which requires reading the handler code and matching attribute names.

#### Resolution

- `corpus/arch_08_event_driven_architecture_with_sns_fifo_dynamodb_lambda_and_s3/known_good.yaml` — Change `InventorySubscription.FilterPolicy` from `eventSource: [anti-corruption-service]` back to `eventType: [JobCreated, JobDeleted]`. This restores the filter key to match the `MessageAttributes` key published by the anti-corruption function.

A template-only fix is sufficient here (the handler code does not need changing), but only if the correct attribute key is restored — changing the filter values without restoring the correct key would still block all messages.

**Rating:** hard

The symptom (empty inventory queue) is identical to a broken queue policy, a missing subscription, or a Lambda ESM disabled — all of which a model will check first and find correct. Only after ruling out those simpler causes does the filter policy key mismatch become the candidate, and the tool set does not directly expose subscription filter policy contents, requiring the model to read the template directly.

---

### FAULT-02 — Analytics subscription raw delivery disabled causes message attribute loss in inventory path

**Class:** connectivity
**Type:** coupled
**Chains with:** N/A
**Coupled properties:** `InventorySubscription.RawMessageDelivery` (changed from `true` to `false`), `InventoryFunction` handler's `_event_type` fallback logic (relies on `messageAttributes.eventType.stringValue` being present in the SQS record body)
**Fails assertions:** `inventory_terminal_state`

#### Misconfiguration

`InventorySubscription.RawMessageDelivery` is changed from `true` to `false` in `faulted.yaml`. When SNS delivers to an SQS queue with `RawMessageDelivery: false`, the SQS message body is a JSON envelope: `{"Type":"Notification","MessageId":"...","Message":"<original JSON string>","MessageAttributes":{...}}`. The SQS record that Lambda receives has `body` equal to this SNS envelope, not the raw message JSON.

The inventory handler parses `record["body"]` directly with `json.loads()`, expecting the raw payload fields (`jobId`, `eventType`, `eventSource`, etc.) at the top level. With the SNS envelope wrapping, `json.loads(record["body"])` produces the envelope object, and `payload.get("jobId")` returns `None`. The `_event_type` function attempts `record.get("messageAttributes")` (which is on the SQS record object, not the parsed body) and falls back to `payload.get("eventType")` — but `payload` is now the envelope, where `"eventType"` is a nested object under `"MessageAttributes"`, not a top-level string. So `_event_type` returns `None` and neither `if` branch in the handler fires. The handler silently processes all records without writing anything to DynamoDB.

Neither misconfiguration alone produces the full silent-skip outcome: if only `RawMessageDelivery` is `false` but the handler were updated to unwrap the SNS envelope, it would work. If the handler fallback were broken but `RawMessageDelivery` remained `true`, messages would arrive as raw JSON and the primary fallback `payload.get("eventType")` would succeed. Only the combination produces silent no-op processing.

#### Observable Symptom

`events_published` passes. `analytics_object_created` passes. The inventory function is invoked (the ESM is enabled and the queue receives messages), but `inventory_terminal_state` times out. The DynamoDB table has no item for the test job ID. The inventory function's log tail shows no errors — the handler returns `{}` successfully for each record without writing anything.

#### Diagnostic Reasoning Path

Step 1 — Check the inventory queue with `ace_check_queue_depth`. After waiting, the queue is draining (messages_available approaches zero), and the Lambda ESM is enabled per `ace_check_event_source`. This rules out a connectivity break and confirms the function is being invoked.

Step 2 — Check the DynamoDB table with `ace_read_table_item` using the test job ID. The item is absent. Check `ace_get_log_tail` for the inventory function — logs show successful invocations returning `{}` with no error messages. This is the critical misdirection: the function appears healthy but writes nothing.

Step 3 — Invoke the inventory function directly with `ace_invoke_lambda` using a synthetic SQS-shaped payload with a raw message body (mimicking correct delivery). The function correctly processes the item and writes to DynamoDB. This narrows the fault to what the actual SQS payload looks like versus the synthetic payload — specifically the `body` field format.

Step 4 — Check the inventory function's environment variables with `ace_get_environment_variables` — `TABLE_NAME` is correct. Check the IAM role with `ace_get_iam_role` for `InventoryRole` — `dynamodb:PutItem` and `dynamodb:UpdateItem` are present and scoped to the correct table ARN. These checks are correct and do not reveal the root cause.

Step 5 — Examine the `InventorySubscription` resource configuration in `faulted.yaml` and compare `RawMessageDelivery` against the known-good value. The `RawMessageDelivery: false` flag means the SQS body is an SNS envelope, not the raw message. Cross-referencing this with the handler's `json.loads(record["body"])` and `payload.get("eventType")` call makes the root cause deducible: the envelope body does not expose `eventType` at the top level.

#### Resolution

- `corpus/arch_08_event_driven_architecture_with_sns_fifo_dynamodb_lambda_and_s3/known_good.yaml` — Restore `InventorySubscription.RawMessageDelivery` to `true`. This ensures SQS message bodies contain the raw publisher JSON, not the SNS envelope.

A template-only fix is sufficient because the handler code is correct for raw delivery — the `_event_type` fallback correctly handles the attribute lookup when the raw message arrives. A code-only fix (updating the handler to unwrap the SNS envelope) would also technically work but would leave the template misconfigured and break for future format assumptions.

**Rating:** hard

The function logs show healthy invocations with no errors, so the model must distinguish between "function is broken" and "function receives malformed input and silently no-ops." The misdirection from the successful `ace_invoke_lambda` with a synthetic payload is key — it confirms the code itself is not the issue, but requires the model to reason about what the live SQS payload actually contains.

---

### FAULT-03 — Analytics ESM batch window causes event source mapping to stall under test message count

**Class:** performance
**Type:** coupled
**Chains with:** N/A
**Coupled properties:** `AnalyticsEventSourceMapping.MaximumBatchingWindowInSeconds` (added, set to 120), `AnalyticsJobEventsQueue.VisibilityTimeout` (reduced from 60 to 30 seconds)
**Fails assertions:** `analytics_object_created`

#### Misconfiguration

`AnalyticsEventSourceMapping` is modified to add `MaximumBatchingWindowInSeconds: 120`, which instructs the Lambda poller to wait up to 120 seconds accumulating messages before triggering the function. The functional test publishes exactly 3 messages (one per event type) and then waits up to 120 seconds for an S3 object to appear. With a 120-second batching window, the poller will not fire within the test's 120-second deadline — it will trigger only after the window expires, which is at or beyond the timeout boundary.

Simultaneously, `AnalyticsJobEventsQueue.VisibilityTimeout` is reduced from 60 to 30 seconds. This makes the symptom harder to distinguish from a Lambda timeout issue: if a model attempts to probe by checking queue depth, it will see messages appearing to be in-flight briefly and then cycling back to visible (because the visibility timeout expires before the batch window does, making the messages visible again). This cycling creates the false appearance of repeated failed Lambda invocations rather than a batching stall.

Neither misconfiguration alone produces the `analytics_object_created` timeout: with only the 120-second batch window and a correct visibility timeout of 60 seconds, a model could observe that no invocations have happened yet and correctly diagnose the batch window. With only the reduced visibility timeout and no batch window, the function would be invoked promptly but messages might re-appear if processing were slow (which it is not here). Only the combination produces the cycling messages + no S3 object pattern that misleads toward Lambda timeout or DLQ exhaustion hypotheses.

#### Observable Symptom

`events_published` passes. `analytics_object_created` times out after 120 seconds. The analytics queue shows messages cycling between `messages_available` and `messages_in_flight` with roughly 30-second cycles, suggesting the function is being invoked but failing — but in fact no invocations occur within the window. No analytics objects appear in S3. The inventory path may or may not complete (depending on whether the inventory function's separate ESM is unaffected — it is, since only the analytics ESM is faulted).

#### Diagnostic Reasoning Path

Step 1 — Check `ace_check_queue_depth` on the analytics queue. Messages are present and cycling (alternating between available and in-flight counts at 30-second intervals). This strongly suggests Lambda invocations are occurring but failing, pointing toward a Lambda error hypothesis.

Step 2 — Check `ace_get_log_tail` for the analytics function. No log entries appear at all — the function has not been invoked. This is surprising given the queue cycling behavior, and contradicts the Lambda-failure hypothesis.

Step 3 — Check `ace_check_event_source` for the analytics function. The ESM is shown as Enabled with `batch_size: 10`. The tool does not surface `MaximumBatchingWindowInSeconds` — it only reports `source_arn`, `enabled`, `batch_size`, and `state`. This blind spot is critical: the batch window is invisible to this tool.

Step 4 — Check the analytics function IAM role with `ace_get_iam_role` and environment variables with `ace_get_environment_variables`. Both look correct. Check the queue visibility timeout via `ace_check_queue_depth` attributes — the `ace_check_queue_depth` tool returns `messages_in_flight` count but not the visibility timeout value. The visibility timeout is not directly exposed by the available probe tools.

Step 5 — Examine the `faulted.yaml` template directly via the agent's read access to `faulted.yaml`. The `AnalyticsJobEventsQueue.VisibilityTimeout: 30` and `AnalyticsEventSourceMapping.MaximumBatchingWindowInSeconds: 120` become visible. Cross-referencing: a batch window of 120 seconds combined with a test timeout of 120 seconds means the function will never fire within the test window. The reduced visibility timeout explains the queue cycling without actual invocations.

#### Resolution

- `corpus/arch_08_event_driven_architecture_with_sns_fifo_dynamodb_lambda_and_s3/known_good.yaml` — Remove `MaximumBatchingWindowInSeconds` from `AnalyticsEventSourceMapping` (or set it to 0) and restore `AnalyticsJobEventsQueue.VisibilityTimeout` to 60 seconds. Both changes are required: restoring only the visibility timeout leaves the 120-second batch window intact and `analytics_object_created` still times out. Restoring only the batch window leaves visibility timeout at 30 seconds, which is shorter than the Lambda timeout of 20 seconds, risking message re-delivery during processing.

A template-only fix is insufficient for a complete resolution if handler code changes are also needed — but in this scenario the handler code is correct, so the template-only fix is sufficient. However, both properties in the template must be corrected; fixing only one is insufficient.

**Rating:** hard

The `ace_check_event_source` tool does not expose `MaximumBatchingWindowInSeconds`, so the model cannot discover the batch window without reading the template. The queue cycling pattern — caused by the short visibility timeout — creates a strong false signal of repeated Lambda failures, leading the model to inspect IAM, S3 permissions, and function logs before the true cause becomes deducible.

---

### FAULT-04 — InventoryRole PutItem permission scoped to wrong table ARN, masked by UpdateItem succeeding on pre-seeded item

**Class:** security
**Type:** coupled
**Chains with:** N/A
**Coupled properties:** `InventoryRole` policy statement for `dynamodb:PutItem` (resource ARN changed to the analytics bucket ARN instead of the inventory table ARN), `InventoryFunction` environment variable `TABLE_NAME` (correct value — this is the masking property)
**Fails assertions:** `inventory_terminal_state`

#### Misconfiguration

The `InventoryRole` inline policy is changed so that the `dynamodb:PutItem` action is scoped to a wrong resource:

```yaml
# Faulted:
- Effect: Allow
  Action:
    - dynamodb:PutItem
    - dynamodb:UpdateItem
  Resource: !GetAtt AnalyticsBucket.Arn   # wrong: S3 bucket ARN, not DynamoDB table ARN
```

The `TABLE_NAME` environment variable on `InventoryFunction` remains correct (pointing to the right table name). When a `JobCreated` event arrives, the handler calls `dynamodb.put_item(TableName=..., Item={...})`. DynamoDB evaluates the caller's policy against the table's ARN — the `PutItem` is denied because the role only grants `PutItem` on the S3 bucket ARN (which is semantically meaningless for DynamoDB but is what is authorized). The call raises `AccessDeniedException`.

However, `dynamodb:UpdateItem` is also scoped to the same wrong ARN. So when a `JobDeleted` event arrives and the handler calls `dynamodb.update_item`, that too is denied. The `JobCreated` record is never written, so the `UpdateItem` for `JobDeleted` would fail even if permissions were correct (the item doesn't exist to update). The functional test checks `inventory_terminal_state` which requires both: the item exists AND `markAsDeleted` is `True`. Neither condition is met.

The coupling: `TABLE_NAME` is correct (so `ace_get_environment_variables` returns a correct-looking value and does not reveal the fault), while the IAM resource scope is wrong. A model checking env vars first will find no issue. A model checking the IAM role will see `dynamodb:PutItem` and `dynamodb:UpdateItem` listed — but must also check the resource ARN, which is a bucket ARN rather than a table ARN.

#### Observable Symptom

`events_published` passes. `analytics_object_created` passes (the analytics path is unaffected). `inventory_terminal_state` times out. The inventory queue drains normally (messages are received and deleted by the Lambda poller after each invocation). The inventory function's log tail shows `AccessDeniedException` errors on `PutItem` calls. No item exists in the DynamoDB table for the test job ID.

#### Diagnostic Reasoning Path

Step 1 — Check `ace_read_table_item` for the test job ID — the item is absent. Check `ace_check_queue_depth` — the inventory queue is empty (messages have been processed and deleted). This combination suggests the function is being invoked but not writing to DynamoDB.

Step 2 — Check `ace_get_log_tail` for the inventory function. `AccessDeniedException` errors appear for `PutItem` on the DynamoDB table. This looks like a straightforward IAM issue. The natural next move is to check the IAM role.

Step 3 — Check `ace_get_iam_role` for `InventoryRole`. The policy document lists `dynamodb:PutItem` and `dynamodb:UpdateItem` — both actions appear to be present. A model that stops here and reads only the `Action` list will conclude the role is correctly configured. Only if the model also reads the `Resource` field will it notice the ARN is a bucket ARN (`arn:aws:s3:::...`) rather than a DynamoDB table ARN (`arn:aws:dynamodb:...`).

Step 4 — Check `ace_get_environment_variables` for the inventory function — `TABLE_NAME` is correct. This eliminates a wrong table name as the cause. The model must reconcile: correct table name in env var, correct actions in IAM, `AccessDeniedException` on PutItem. The only remaining possibility is the resource ARN in the IAM policy.

Step 5 — Re-examine the `Resource` field in the IAM policy statement returned by `ace_get_iam_role`. The ARN format (`arn:aws:s3:::stack-analytics`) is recognizable as an S3 ARN, not a DynamoDB ARN. Cross-referencing with `ace_get_stack_outputs` (which shows the `AnalyticsBucketName`) confirms the resource ARN points to the S3 bucket, not the inventory table.

#### Resolution

- `corpus/arch_08_event_driven_architecture_with_sns_fifo_dynamodb_lambda_and_s3/known_good.yaml` — Restore the `InventoryRole` policy `Resource` for `dynamodb:PutItem` and `dynamodb:UpdateItem` to `!GetAtt InventoryTable.Arn`. This ensures DynamoDB API calls are authorized against the correct table ARN.

A template-only fix is sufficient — the handler code correctly uses `TABLE_NAME` env var which is already correct. No code change is needed.

**Rating:** medium

The `AccessDeniedException` in logs provides a clear signal that IAM is the problem, making this easier than scenarios with no error output. The difficulty lies in the model checking the IAM `Action` list (which looks correct) and stopping before reading the `Resource` ARN — a common pattern of partial inspection.

---

### FAULT-05 — InventoryFunction handler extracts jobId from wrong payload level when event body is double-encoded

**Class:** data_correctness
**Type:** coupled
**Chains with:** N/A
**Coupled properties:** `AntiCorruptionFunction` handler (publishes message body as double-JSON-encoded string — `json.dumps(json.dumps({...}))`), `InventoryFunction` handler (parses `record["body"]` once — correct for single encoding — then accesses `payload["jobId"]` which is now a string of JSON, not a dict)
**Fails assertions:** `inventory_terminal_state`

#### Misconfiguration

The anti-corruption handler's `_publish` function is changed so that the `Message` parameter is double-encoded:

```python
# Faulted:
Message=json.dumps(json.dumps({
    "id": message_id,
    "jobId": job_id,
    ...
})),
```

The outer `json.dumps` encodes the entire dict as a JSON string, and the inner `json.dumps` encodes it again, so `Message` is a JSON string whose value is another JSON string (i.e., `"\"{ \\\"jobId\\\": \\\"...\\\", ... }\""` — a string literal in JSON). When this reaches the inventory handler, `json.loads(record["body"])` successfully parses the outer string and returns a Python string object (not a dict). Accessing `payload["jobId"]` raises `TypeError: string indices must be integers` because `payload` is a string, not a dict.

The `_event_type` function also fails because `payload.get(...)` doesn't exist on a string. The handler raises an exception on each record, triggering retries. After `maxReceiveCount: 3` retries, the message moves to the inventory DLQ. No DynamoDB writes occur.

The coupling: if only the handler is double-encoding but the DLQ were removed, messages would be lost silently. If only the double-encoding is present but the retry/DLQ configuration is correct (as it is in the known good), the messages exhaust retries and move to DLQ — the DLQ is not surfaced by any primary assertion. But the functional test times out on `inventory_terminal_state` because the item is never written. The handler code change is the root cause; the DLQ being silently absorbing failures is the masking property.

#### Observable Symptom

`events_published` passes (the producer invocation succeeds — the anti-corruption function returns `{"jobId": job_id}` correctly, it just publishes a malformed message body). `analytics_object_created` may pass or fail depending on whether the analytics handler also double-encodes (it does not — only anti-corruption is faulted; however `analytics` uses `json.loads(record["body"])` and the outer decode returns a Python string, causing an error there too). Wait — let me reconsider: both analytics and inventory receive the same messages from the topic, so the analytics handler will also fail to process. `analytics_object_created` will also fail.

Actually: `analytics_object_created` will time out AND `inventory_terminal_state` will time out. The analytics function's `json.loads(record["body"])` returns a string; then `body["Records"].append(json.loads(record["body"]))` — wait, analytics does `json.loads(record["body"])` and appends the result to `body["Records"]`. If the result is a string, it will append a string to the list. The S3 write will succeed (it just writes `{"Records": ["<json string>"]}` to S3), so `analytics_object_created` may still pass because an object is created — just with incorrect content. The analytics assertion only checks for object existence, not content. So `analytics_object_created` passes, and only `inventory_terminal_state` fails.

For the inventory path: the handler tries `payload["jobId"]` where `payload` is a string → `TypeError` → Lambda error → retries → DLQ.

**Fails assertions:** `inventory_terminal_state`

#### Diagnostic Reasoning Path

Step 1 — `ace_read_table_item` confirms the item is absent. `ace_check_queue_depth` on the inventory queue shows it is empty (messages moved to DLQ after exhausting retries). The ESM is enabled.

Step 2 — `ace_get_log_tail` for the inventory function shows `TypeError: string indices must be integers` on repeated invocations. This points to a Python type error in the handler — the body is being accessed as a string rather than a dict.

Step 3 — `ace_invoke_lambda` with a correctly-shaped synthetic SQS record (single-encoded JSON body) successfully writes to DynamoDB. This confirms the handler logic itself is correct for proper input, narrowing the fault to the actual message content reaching the function from SQS.

Step 4 — `ace_check_queue_depth` on the inventory DLQ shows messages have accumulated. Examining the DLQ messages (via `ace_read_table_item` is not applicable; the model must reason from the error pattern) confirms messages are being rejected. The model now knows the live SQS record body is malformed from the inventory handler's perspective.

Step 5 — Read the anti-corruption handler code (via the agent's `read_file` on `deployment/lambda/anti-corruption/index.py`). The double `json.dumps` call is visible. Cross-referencing with how the inventory handler parses `record["body"]` makes the double-encoding fault deducible.

#### Resolution

- `corpus/arch_08_event_driven_architecture_with_sns_fifo_dynamodb_lambda_and_s3/deployment/lambda/anti-corruption/index.py` — Remove the outer `json.dumps` so that `Message` is a single JSON-encoded dict. The inner encoding must remain; only the outer redundant encoding must be removed.

A template-only fix is insufficient — the template has no control over the message encoding in the handler. A code-only fix (removing double encoding in anti-corruption handler) is sufficient; no template change is needed.

**Rating:** medium

The `TypeError` in logs is a direct signal that points at string/dict confusion, making this easier to localize than silent failures. The diagnostic challenge is recognizing that the message content (produced by a different Lambda) is the source, rather than the consuming handler itself — requiring the model to read the producer's code.

---

### FAULT-06 — InventoryFunction writes markAsDeleted under wrong attribute name, terminal state check misses it

**Class:** data_correctness
**Type:** coupled
**Chains with:** N/A
**Coupled properties:** `InventoryFunction` handler `UpdateExpression` (uses `:m` alias but `ExpressionAttributeValues` maps `:m` to `{"BOOL": True}` while the `UpdateExpression` writes to `markAsDeleted = :m` — fault changes this to `mark_as_deleted = :m`), functional test assertion `assert_inventory_terminal_state` checks `item.get("markAsDeleted", {}).get("BOOL")`
**Fails assertions:** `inventory_terminal_state`

#### Misconfiguration

The inventory handler's `update_item` call is changed so that the `UpdateExpression` uses a different attribute name:

```python
# Faulted:
UpdateExpression="SET mark_as_deleted = :m",
ExpressionAttributeValues={":m": {"BOOL": True}},
```

The correct form is `SET markAsDeleted = :m`. With the faulted form, `update_item` succeeds (DynamoDB accepts any attribute name in an `UpdateExpression`) and writes a new attribute `mark_as_deleted` (snake_case) with value `True` to the item. The functional test reads the item and checks `item.get("markAsDeleted", {}).get("BOOL")` — which returns `None` because the attribute is stored as `mark_as_deleted`, not `markAsDeleted`. The item exists in DynamoDB (the `JobCreated` handler correctly wrote `id`, `eventCreated`, `eventSource`, `eventDetails`) but the deletion marker attribute is stored under the wrong name.

The coupling: if the `UpdateExpression` used the wrong attribute name but the functional test were also checking `mark_as_deleted`, the test would pass. If the functional test checks `markAsDeleted` but the expression is correct, the test passes. Only the combination of the wrong attribute name in the handler and the correct attribute name in the test assertion produces the failure. A template change alone cannot fix this — it requires a handler code change.

#### Observable Symptom

`events_published` passes. `analytics_object_created` passes. `inventory_terminal_state` times out. When `ace_read_table_item` is called during investigation, the item *does* exist in the table for the test job ID — it has `id`, `eventCreated`, `eventSource`, `eventDetails`, and `mark_as_deleted: true` — but `markAsDeleted` is absent. The functional test's predicate `item.get("markAsDeleted", {}).get("BOOL")` returns `None` → `False`, so the wait loop never exits successfully.

#### Diagnostic Reasoning Path

Step 1 — Check `ace_read_table_item` for the test job ID during the wait window. The item exists and contains expected fields. This immediately rules out the `JobCreated` path as broken. The item just does not have the `markAsDeleted` attribute the test expects.

Step 2 — `ace_get_log_tail` for the inventory function shows successful invocations with no errors for both `JobCreated` and `JobDeleted` events. The DynamoDB calls return without exception. This is the misdirection: the function appears fully healthy.

Step 3 — `ace_invoke_lambda` with a synthetic `JobDeleted` SQS record for the same job ID, then `ace_read_table_item` again. The item now has `mark_as_deleted: true` — but the `markAsDeleted` attribute (camelCase) is still absent. This confirms the handler is writing the wrong attribute name.

Step 4 — Read the inventory handler source code (`deployment/lambda/inventory/index.py`). The `UpdateExpression="SET mark_as_deleted = :m"` is visible and clearly uses snake_case where camelCase is expected. Cross-referencing with the functional test assertion `item.get("markAsDeleted", ...)` makes the mismatch deducible.

#### Resolution

- `corpus/arch_08_event_driven_architecture_with_sns_fifo_dynamodb_lambda_and_s3/deployment/lambda/inventory/index.py` — Change `UpdateExpression="SET mark_as_deleted = :m"` to `UpdateExpression="SET markAsDeleted = :m"`. This ensures the attribute name matches what the functional test checks.

A template-only fix is insufficient — the wrong attribute name is in the handler code and no template property controls DynamoDB attribute names in `UpdateExpression` strings. A code-only fix is sufficient; no template change is needed.

**Rating:** medium

The item exists and the function logs show no errors, but `inventory_terminal_state` still fails — this creates an initially puzzling symptom. The diagnostic shortcut is `ace_read_table_item` revealing the item contents, which immediately exposes the snake_case attribute name.

---

### FAULT-07 — Analytics DLQ lacks SQS send permission on AnalyticsRole, causing failed records to be silently dropped

**Class:** reliability
**Type:** coupled
**Chains with:** N/A
**Coupled properties:** `AnalyticsRole` policy (missing `sqs:SendMessage` on `AnalyticsJobEventsQueueDLQ.Arn`), `AnalyticsEventSourceMapping.FunctionResponseTypes` (includes `ReportBatchItemFailures` — fault changes this to remove the array, disabling partial-batch reporting)
**Fails assertions:** `analytics_object_created`

#### Misconfiguration

Two simultaneous changes:

1. `AnalyticsRole` inline policy removes the `sqs:SendMessage` permission for `AnalyticsJobEventsQueueDLQ.Arn`. The analytics Lambda's execution role cannot send messages to its own DLQ.

2. `AnalyticsEventSourceMapping.FunctionResponseTypes` is changed from `[ReportBatchItemFailures]` to `[]` (empty list — or the property is removed entirely). With `ReportBatchItemFailures` disabled, the Lambda service treats any non-thrown completion as full-batch success and deletes all records from the queue, regardless of whether individual records were processed.

The interaction: in the known-good state, if the analytics function raises an exception, the SQS event source triggers a retry up to `maxReceiveCount: 3`, then attempts to send failed messages to the DLQ. Without `sqs:SendMessage` on the DLQ ARN, the DLQ delivery fails silently — but this alone doesn't cause `analytics_object_created` to fail unless the function actually throws. With `FunctionResponseTypes` empty, the function must throw an exception to trigger DLQ routing at all (since `ReportBatchItemFailures` partial reporting is gone). But the analytics function does not throw on normal messages — it processes them and writes to S3.

Wait — this scenario needs a fault that causes `analytics_object_created` to fail. Let me reconsider: I need the analytics function to fail to write to S3. The coupled fault should be: the analytics function handler raises on a specific message shape AND the DLQ permission is missing so failures are silently dropped.

Revised: The analytics handler is changed to intentionally raise when processing a `JobSalaryUpdated` event type (which the anti-corruption function publishes). Combined with missing DLQ `sqs:SendMessage` on `AnalyticsRole`, the failed record is neither retried-to-DLQ successfully nor partial-batch-reported. The entire batch fails, retries 3 times, then attempts DLQ delivery which silently fails (no `sqs:SendMessage`). The S3 object is never written.

Actually, the analytics handler does not filter by event type — it writes all records to S3 as-is. The simplest coupled fault is: (1) raise if `BUCKET_NAME` env var has a trailing space (wrong value with whitespace), and (2) `FunctionResponseTypes` is emptied. But that's a data correctness fault not reliability.

Let me re-scope: The DLQ permission missing is the reliability fault. The coupling is: `AnalyticsJobEventsQueueDLQ` has `sqs:SendMessage` removed from `AnalyticsRole` AND `AnalyticsRole` has `s3:PutObject` removed from the policy (so the function always fails to write). With both: function always fails → retries → DLQ delivery attempt → DLQ delivery fails silently (no permission) → messages lost → `analytics_object_created` fails. With only the S3 permission removed, messages would exhaust retries and go to DLQ successfully (permission present) — observable via `ace_check_queue_depth` on DLQ. With only the DLQ permission removed, the function succeeds at S3 writes and the DLQ is never triggered. Only both together produce the silent-drop failure.

**Coupled properties:** `AnalyticsRole` policy (missing `s3:PutObject` on `AnalyticsBucket.Arn/*` — so analytics writes fail), `AnalyticsRole` policy (missing `sqs:SendMessage` on `AnalyticsJobEventsQueueDLQ.Arn` — so DLQ delivery silently fails after retries exhausted)
**Fails assertions:** `analytics_object_created`

#### Misconfiguration (revised)

`AnalyticsRole` inline policy is changed in two places:
1. The `s3:PutObject` statement's `Resource` is changed from `!Sub '${AnalyticsBucket.Arn}/*'` to `!Sub '${AnalyticsBucket.Arn}'` (missing the `/*` suffix), causing all `put_object` calls to be denied with `AccessDeniedException` (bucket-level ARN does not grant object-level `s3:PutObject`).
2. The DLQ send permission is entirely absent from the role — `sqs:SendMessage` is only granted on `AnalyticsJobEventsQueue.Arn`, not on `AnalyticsJobEventsQueueDLQ.Arn`.

With `s3:PutObject` denied, the analytics function throws `ClientError` on every invocation. The SQS event source retries the batch up to `maxReceiveCount: 3` times. After exhaustion, the SQS queue's `RedrivePolicy` attempts to send messages to `AnalyticsJobEventsQueueDLQ`. This attempt fails silently from the Lambda event source's perspective — Lambda itself doesn't perform the DLQ send (SQS does), but the SQS queue's redrive requires that the queue's own permissions allow sending to the DLQ, which is governed by the `AnalyticsQueuePolicy`. The `AnalyticsQueuePolicy` in the known good grants `sqs:SendMessage` only from the SNS topic source ARN — it does not govern Lambda→DLQ routing. DLQ routing is queue-service-side and does not require the Lambda execution role to have `sqs:SendMessage` on the DLQ.

I need to reconsider the DLQ routing model: in SQS, the redrive to DLQ is performed by the SQS service itself after `maxReceiveCount` is exceeded. Lambda does not send to the DLQ — SQS does. So the Lambda role's `sqs:SendMessage` permission on the DLQ is irrelevant for SQS DLQ routing. The `OnFailure` destination in the Lambda ESM (if configured) would require Lambda to have permission to the destination. Since `DestinationConfig` is not used in this architecture, the DLQ routing is SQS-service-side and always works regardless of Lambda role permissions.

This means the DLQ permission coupling scenario I described is not valid for this architecture. Let me replace this with a valid reliability scenario.

**Revised FAULT-07:**

**Class:** reliability
**Type:** coupled
**Chains with:** N/A
**Coupled properties:** `AnalyticsEventSourceMapping.FunctionResponseTypes` (changed from `[ReportBatchItemFailures]` to `[]`), `AnalyticsEventSourceMapping.BisectBatchOnFunctionError` (added as `true`) — but this introduces partial processing that causes duplicate writes. Actually let me use a cleaner coupling.

**Final FAULT-07:**

**Class:** reliability  
**Type:** coupled
**Chains with:** N/A
**Coupled properties:** `AnalyticsJobEventsQueue.VisibilityTimeout` (changed from 60 to 5 seconds — shorter than the analytics Lambda timeout of 20 seconds), `AnalyticsEventSourceMapping.FunctionResponseTypes` (changed from `[ReportBatchItemFailures]` to `[]` — disabling partial-batch failure reporting)
**Fails assertions:** `analytics_object_created`

With a 5-second visibility timeout and a Lambda timeout of 20 seconds: if the analytics function takes longer than 5 seconds to run (possible under LocalStack load), messages become visible again while the function is still processing. Another Lambda invocation picks them up and attempts a concurrent S3 write. If both writes use `uuid.uuid4()` as the key (which they do — the handler generates a fresh UUID per invocation), both writes may succeed, creating duplicate analytics objects. However, the more important failure mode is: the SQS event source marks the batch as failed when the function's execution context times out (if Lambda timeout hits before S3 write completes), and with `FunctionResponseTypes: []`, all records in the batch are re-enqueued. With the 5-second visibility timeout, re-enqueued messages immediately become available and the cycle repeats, creating an infinite retry loop that never produces a stable S3 write within the 120-second test window.

For this to reliably fail `analytics_object_created`, the Lambda must consistently fail to complete within 5 seconds. Under LocalStack this is plausible for cold starts. The more reliable failure mode is: the 5-second visibility timeout means messages re-appear before the Lambda function completes its first invocation, leading to concurrent duplicate processing that, combined with no `ReportBatchItemFailures`, produces a chaotic retry pattern where no single object is durably written before being re-processed.

Actually, this scenario is somewhat environment-dependent (timing). Let me pick a more deterministic reliability fault.

**Final FAULT-07 (definitive):**

**Class:** reliability  
**Type:** coupled
**Chains with:** N/A
**Coupled properties:** `InventoryJobEventsQueue.RedrivePolicy.maxReceiveCount` (changed from 3 to 1 — messages DLQ after a single processing attempt), `InventoryEventSourceMapping.FunctionResponseTypes` (changed from `[ReportBatchItemFailures]` to `[]` — any exception in the batch treats the whole batch as failed, triggering immediate DLQ routing after just 1 attempt)
**Fails assertions:** `inventory_terminal_state`

#### Misconfiguration (definitive)

`InventoryJobEventsQueue.RedrivePolicy.maxReceiveCount` is changed from `3` to `1`, meaning any message that is received and not deleted within the visibility timeout will be moved to `InventoryJobEventsQueueDLQ` after a single receive. Simultaneously, `InventoryEventSourceMapping.FunctionResponseTypes` is emptied, disabling `ReportBatchItemFailures`. With `ReportBatchItemFailures` disabled, if any record in a batch raises an exception, the entire batch is treated as failed — the Lambda does not delete the messages from SQS, and SQS counts the entire batch as one receive. With `maxReceiveCount: 1`, any batch that contains even one problematic record immediately exhausts the redrive limit after the first receive attempt, and all messages in the batch move to the DLQ.

The inventory handler processes `JobCreated` and `JobDeleted` events. If `JobCreated` and `JobDeleted` arrive in the same batch (possible with `BatchSize: 10`), and if the `JobDeleted` record happens to arrive before `JobCreated` is committed (due to batch processing order), `update_item` on a non-existent item will succeed in DynamoDB (it creates the item with only `id` and `markAsDeleted`) — but then `put_item` for `JobCreated` would overwrite it, removing `markAsDeleted`. This ordering issue is masked in the known good by retries. With `maxReceiveCount: 1`, a batch that triggers any Lambda error (e.g., a cold start timeout, a transient DynamoDB error, or the ordering-dependent conditional check failure) is immediately dead-lettered.

More deterministically: with `FunctionResponseTypes: []`, if the Lambda raises an exception on any record, the entire batch is failed. The inventory handler has no try/except — any DynamoDB error (throttle, transient error) causes the whole batch to fail. With `maxReceiveCount: 1`, the first failure immediately DLQs all messages. The `JobCreated` and `JobDeleted` events are never successfully processed and the item is never written to or marked deleted.

The coupling: with `maxReceiveCount: 3` and no `ReportBatchItemFailures`, a transient DynamoDB error is retried 3 times — usually enough for eventual success. With `maxReceiveCount: 1` and `ReportBatchItemFailures`, a failed record can be isolated and the successful records committed — so individual record failures don't DLQ the whole batch. Only the combination of `maxReceiveCount: 1` AND no `ReportBatchItemFailures` causes any batch-level error to immediately dead-letter all messages.

#### Observable Symptom

`events_published` passes. `analytics_object_created` passes. `inventory_terminal_state` times out. The inventory queue depth reaches zero quickly (messages are DLQ'd after one failed receive). The inventory DLQ accumulates messages. No item exists in the DynamoDB table.

#### Diagnostic Reasoning Path

Step 1 — `ace_read_table_item` — item absent. `ace_check_queue_depth` on the inventory queue — empty. ESM is enabled. No apparent processing activity.

Step 2 — `ace_get_log_tail` for the inventory function — may show a Lambda error (if one occurred) or may show no invocations at all (if the single-receive-count was exhausted before Lambda was invoked a second time). This ambiguity is the first misdirection.

Step 3 — `ace_check_queue_depth` on the inventory DLQ — messages are present. This confirms messages were DLQ'd rather than successfully processed. The model now knows: messages reached the queue, Lambda was invoked at least once (or the SQS service determined the receive count was exceeded), and messages ended up in DLQ.

Step 4 — `ace_get_iam_role` for `InventoryRole` — correct permissions. `ace_get_environment_variables` — `TABLE_NAME` correct. These checks are clean and don't reveal the fault.

Step 5 — Read `faulted.yaml` to examine `InventoryJobEventsQueue.RedrivePolicy.maxReceiveCount` (value: 1) and `InventoryEventSourceMapping.FunctionResponseTypes` (empty list). The `maxReceiveCount: 1` means any receive-without-delete immediately exhausts the redrive. Combined with no `ReportBatchItemFailures`, the first invocation failure or timeout kills the entire batch with no retry opportunity. This makes the root cause deducible.

#### Resolution

- `corpus/arch_08_event_driven_architecture_with_sns_fifo_dynamodb_lambda_and_s3/known_good.yaml` — Restore `InventoryJobEventsQueue.RedrivePolicy.maxReceiveCount` to `3` and restore `InventoryEventSourceMapping.FunctionResponseTypes` to `[ReportBatchItemFailures]`. Both changes are required: with only `maxReceiveCount: 3` restored but `ReportBatchItemFailures` still absent, a transient error on one record fails the entire batch and retries it as a unit — eventually succeeding but with duplicate-processing risk. With only `ReportBatchItemFailures` restored but `maxReceiveCount: 1`, a single failed batch still DLQs all messages after one attempt.

A template-only fix is sufficient — both properties are in the template. No handler code changes are needed.

**Rating:** hard

The DLQ accumulation is a strong diagnostic signal, but connecting `maxReceiveCount: 1` to `FunctionResponseTypes: []` as a coupled failure requires reading both the queue and the ESM configuration. The ambiguity in whether Lambda was invoked at all (depending on timing) creates additional diagnostic noise.

---

### FAULT-08 — AntiCorruptionFunction publishes with wrong MessageGroupId format, breaking FIFO ordering deduplication in inventory path

**Class:** data_correctness
**Type:** chained
**Chains with:** FAULT-07 (prerequisite — while the inventory DLQ immediately consumes messages due to FAULT-07, the ordering violation in FAULT-08 is invisible; only after FAULT-07 is resolved and messages begin flowing does FAULT-08's symptom — `markAsDeleted` appearing without a prior `id` record — become observable)
**Coupled properties:** `AntiCorruptionFunction` handler `MessageGroupId` (changed from `f"JOB-{job_id}"` to `"GLOBAL-EVENTS"` — all events share one group, collapsing FIFO ordering guarantee to a single shared group), `InventorySubscription.FilterPolicy` (correct — this is what makes the symptom non-obvious, since only `JobCreated` and `JobDeleted` reach inventory, and their relative ordering in the single group is now undefined)
**Fails assertions:** `inventory_terminal_state`

#### Misconfiguration

The anti-corruption handler's `_publish` function is changed so that all events share the same `MessageGroupId`:

```python
# Faulted:
MessageGroupId="GLOBAL-EVENTS",  # was f"JOB-{job_id}"
```

In SNS FIFO, `MessageGroupId` determines the ordered group. When all three events (`JobCreated`, `JobSalaryUpdated`, `JobDeleted`) share `"GLOBAL-EVENTS"`, they are placed in a single ordered group and delivered strictly in publish order. This seems correct. However, the deduplication behavior changes: `MessageDeduplicationId` is unique per publish (a fresh UUID), so deduplication is not the issue. The ordering within the single group is preserved.

The actual failure is more subtle: with a per-job `MessageGroupId` (`f"JOB-{job_id}"`), different jobs can be processed in parallel by separate consumers (different message groups are independent). With `"GLOBAL-EVENTS"`, all messages from all jobs share one group — only one consumer can process messages from this group at a time (FIFO guarantee), creating a serialization bottleneck. Under the functional test's single-job scenario, this doesn't cause a failure.

This scenario as stated doesn't produce a `primary assertion` failure for a single-job test. I need to reconsider.

Let me replace FAULT-08 with a genuinely chained scenario that produces a clear primary assertion failure.

**Revised FAULT-08:**

**Class:** data_correctness
**Type:** chained
**Chains with:** FAULT-01 (prerequisite — while FAULT-01 is present, the inventory queue receives no messages at all, masking FAULT-08's symptom entirely; once FAULT-01 is fixed and messages flow to inventory, FAULT-08's symptom — items written with wrong `id` key value — becomes observable)
**Coupled properties:** `AntiCorruptionFunction` handler `_publish` (changed to publish `jobId` as an integer cast — `int(job_id.split('-')[1], 16)` — so the inventory handler receives a numeric string jobId in the payload), `InventoryFunction` handler `put_item` call (uses `payload["jobId"]` directly as the DynamoDB `id` key value — the numeric jobId causes a type mismatch with the functional test's lookup using the original string job_id)
**Fails assertions:** `inventory_terminal_state`

Actually this is getting complicated and the chaining logic isn't clean. Let me write a simpler, clean chained scenario.

**Final FAULT-08 (definitive):**

**Class:** connectivity  
**Type:** chained  
**Chains with:** FAULT-01 (prerequisite — while FAULT-01's filter policy fault is present, no messages reach the inventory queue at all, so FAULT-08's symptom of the inventory ESM being disabled is completely masked; once FAULT-01 is fixed and messages flow to the queue, the disabled ESM becomes the new blocker)  
**Coupled properties:** `InventoryEventSourceMapping` state (disabled — `StartingPosition` is set to an invalid value causing the mapping to enter an error state, effectively disabling it), `InventoryJobEventsQueue.VisibilityTimeout` (reduced to 5 seconds — so messages accumulate rapidly in the queue, providing a false signal of queue depth growth that a model might mistake for a consumer processing slowly rather than not processing at all)
**Fails assertions:** `inventory_terminal_state`, `event_sources_enabled_secondary`

Note: `event_sources_enabled_secondary` is a secondary assertion, so the primary failing assertion is `inventory_terminal_state` only.

**Chains with:** FAULT-01 — while FAULT-01 (wrong filter policy key) is present, the inventory queue is empty and the disabled ESM symptom is invisible: there are no messages to not-consume. The model would check `ace_check_event_source` and see the ESM as disabled, but without any queue depth to explain why it matters, this finding might be attributed to deployment configuration rather than a fault. Only after FAULT-01 is fixed and messages begin accumulating in the inventory queue does the disabled ESM become the clear blocking fault.

#### Misconfiguration

`InventoryEventSourceMapping` is modified to set `Enabled: false` explicitly (or equivalently, the `StartingPosition` property is removed and replaced with an invalid value causing the mapping to fail activation). `InventoryJobEventsQueue.VisibilityTimeout` is reduced from 60 to 5 seconds, causing messages to rapidly cycle back to visible and inflate the apparent queue depth.

The chaining: while FAULT-01 is active (filter policy uses `eventSource` key), the inventory queue receives zero messages. A model checking `ace_check_event_source` for the inventory function would see the ESM as disabled — but since the queue has no messages, this might appear to be a deliberate operational pause rather than a fault. The model's attention is consumed by why the queue is empty (FAULT-01's filter) and the disabled ESM is attributed to the same root cause or overlooked. Only after FAULT-01 is resolved (filter key corrected), messages begin flowing to the inventory queue — and then the disabled ESM becomes the blocking issue that prevents `inventory_terminal_state` from ever passing.

#### Observable Symptom (after FAULT-01 is resolved)

The inventory queue depth grows steadily (messages accumulate because the ESM is disabled and no consumer is polling). `inventory_terminal_state` times out. `event_sources_enabled_secondary` fails. The analytics path is unaffected.

#### Diagnostic Reasoning Path

Step 1 — After FAULT-01 is fixed, `ace_check_queue_depth` on the inventory queue shows growing `messages_available`. This confirms messages are now reaching the queue.

Step 2 — `ace_check_event_source` for the inventory function returns `state: "Disabled"` (or an error state). This is the clear diagnostic finding — the ESM is not consuming messages from the queue.

Step 3 — `ace_get_log_tail` for the inventory function shows no recent invocations, consistent with the disabled ESM.

Step 4 — Read `faulted.yaml` to confirm `InventoryEventSourceMapping.Enabled: false`. Restoring it to `true` (or removing the explicit `false`) resolves the fault.

#### Resolution

- `corpus/arch_08_event_driven_architecture_with_sns_fifo_dynamodb_lambda_and_s3/known_good.yaml` — Set `InventoryEventSourceMapping.Enabled` to `true` (or remove the explicit `false` to restore default enabled state). Also restore `InventoryJobEventsQueue.VisibilityTimeout` to 60 seconds to eliminate the false queue-depth cycling signal.

A template-only fix is sufficient. No handler changes are needed.

**Rating:** medium (after FAULT-01 is resolved)

Once the chained prerequisite is resolved, the disabled ESM is quickly discoverable via `ace_check_event_source`. The difficulty lies in recognizing that this is a second independent fault masking behind the first, rather than a continuation of the filter policy issue.

---

### FAULT-09 — Analytics handler writes to wrong bucket name from environment variable pointing to inventory table name

**Class:** data_correctness
**Type:** coupled
**Chains with:** N/A
**Coupled properties:** `AnalyticsFunction` environment variable `BUCKET_NAME` (set to `!Ref InventoryTable` instead of `!Ref AnalyticsBucket` — points to the DynamoDB table's physical name, not the S3 bucket name), `AnalyticsRole` policy `s3:PutObject` resource (correct ARN using `AnalyticsBucket.Arn/*` — so the role is authorized to write to the real analytics bucket, but the function tries to write to a bucket named after the DynamoDB table, which does not exist)
**Fails assertions:** `analytics_object_created`

#### Misconfiguration

`AnalyticsFunction.Environment.Variables.BUCKET_NAME` is changed from `!Ref AnalyticsBucket` to `!Ref InventoryTable`. CloudFormation resolves `!Ref InventoryTable` to the DynamoDB table's physical name (e.g., `ace-bench-stack-inventory`). The S3 bucket is named `ace-bench-stack-analytics`. The analytics handler calls `s3.put_object(Bucket=os.environ["BUCKET_NAME"], Key=key, Body=...)` — `BUCKET_NAME` is now `ace-bench-stack-inventory`, which is not an S3 bucket. The call raises `NoSuchBucket` error.

The `AnalyticsRole` policy still grants `s3:PutObject` on `${AnalyticsBucket.Arn}/*` — the actual analytics bucket. The IAM check succeeds (the request is authorized for the correct bucket), but the target bucket name in the API call doesn't exist as an S3 bucket. The error is a resource-not-found error, not an IAM denial.

The coupling: if only `BUCKET_NAME` is wrong but the role also granted `s3:PutObject` on `*`, the error would still be `NoSuchBucket` but the diagnostic path would not require checking IAM. With the specific resource scoping (`AnalyticsBucket.Arn/*`), a model checking IAM first will see the role grants `s3:PutObject` on the analytics bucket — which looks correct — and must then check the actual `BUCKET_NAME` env var to find the mismatch. The masking property is the correct-looking IAM policy that scopes to the right bucket, causing the model to clear IAM as a suspect before finding the env var pointing to the wrong resource.

#### Observable Symptom

`events_published` passes. `analytics_object_created` times out — no S3 objects appear in the analytics bucket. The analytics function's log tail shows `NoSuchBucket` (or `The specified bucket does not exist`) errors on every invocation. The analytics queue drains slowly (retries exhaust redrive count, then messages go to analytics DLQ).

#### Diagnostic Reasoning Path

Step 1 — `ace_check_queue_depth` on the analytics queue — messages are present but draining. `ace_get_log_tail` for the analytics function — `NoSuchBucket` errors. This immediately points to an S3 bucket name issue.

Step 2 — `ace_get_iam_role` for `AnalyticsRole` — `s3:PutObject` is granted on `arn:aws:s3:::ace-bench-stack-analytics/*`. This looks correct and does not reveal the fault. A model might conclude the IAM policy is right and move on.

Step 3 — `ace_get_environment_variables` for the analytics function — `BUCKET_NAME` is `ace-bench-stack-inventory`. This does not match the analytics bucket name (`ace-bench-stack-analytics`) and does not match an S3 bucket at all.

Step 4 — Cross-reference `ace_get_stack_outputs` — `AnalyticsBucketName` is `ace-bench-stack-analytics` and `InventoryTableName` is `ace-bench-stack-inventory`. The `BUCKET_NAME` value matches the inventory table name, confirming the CloudFormation template uses `!Ref InventoryTable` instead of `!Ref AnalyticsBucket` for the analytics function's env var.

#### Resolution

- `corpus/arch_08_event_driven_architecture_with_sns_fifo_dynamodb_lambda_and_s3/known_good.yaml` — Change `AnalyticsFunction.Environment.Variables.BUCKET_NAME` from `!Ref InventoryTable` to `!Ref AnalyticsBucket`. This ensures the env var resolves to the S3 bucket name, not the DynamoDB table name.

A template-only fix is sufficient. The handler code correctly reads `os.environ["BUCKET_NAME"]` — only the wrong template reference needs correction.

**Rating:** medium

The `NoSuchBucket` error in logs is a direct diagnostic signal. The difficulty lies in the model checking IAM first (which looks correct) before checking the environment variable, and in recognizing that `BUCKET_NAME` resolves to a DynamoDB table name (a non-obvious cross-resource `!Ref` mistake).

---

### FAULT-10 — InventoryFunction and AntiCorruptionFunction require simultaneous handler and template fix to restore end-to-end flow

**Class:** data_correctness
**Type:** coupled
**Chains with:** N/A
**Coupled properties:** `AntiCorruptionFunction` handler (publishes `eventType` as a `MessageAttribute` with key `event_type` instead of `eventType`), `InventorySubscription.FilterPolicy` (filter key is `eventType` — correct — but the published attribute key is `event_type`, so no inventory messages match the filter)
**Fails assertions:** `inventory_terminal_state`

#### Misconfiguration

The anti-corruption handler's `_publish` function is changed to use `event_type` (snake_case) as the `MessageAttributes` key:

```python
# Faulted:
MessageAttributes={
    "event_type": {        # was "eventType"
        "DataType": "String",
        "StringValue": event_type,
    }
}
```

The `InventorySubscription.FilterPolicy` remains `eventType: [JobCreated, JobDeleted]` — filtering on the `eventType` attribute key. SNS evaluates the filter against the published `MessageAttributes`. Since the publisher now uses `event_type` (no match for `eventType`), no message matches the inventory subscription filter. Zero messages reach `InventoryJobEventsQueue`.

The coupling with the handler: the `InventoryFunction` handler's `_event_type` function also tries `attrs.get("event_type")` as a fallback — so if messages did reach the inventory function with the `event_type` key, the handler would still correctly extract the event type. But since the filter blocks all messages before they reach SQS, the handler is never invoked. This means:
- Template-only fix (correcting the filter policy to `event_type`): messages now flow through, and the inventory handler correctly processes them via its `event_type` fallback. This works.
- Code-only fix (correcting the handler to use `eventType`): the filter policy matches again and messages flow. This also works.

Wait — if either fix alone resolves the symptom, this doesn't satisfy the constraint that both a template-only and code-only fix must be insufficient. Let me adjust.

**Adjusted coupling:** The handler is also changed so that the body field `eventType` is renamed to `event_type`:

```python
# Faulted body:
Message=json.dumps({
    "id": message_id,
    "jobId": job_id,
    "eventCreated": str(datetime.utcnow()),
    "event_type": event_type,      # was "eventType"
    "eventSource": "anti-corruption-service",
    "eventDetails": details,
}),
MessageAttributes={
    "event_type": {                 # was "eventType"
        "DataType": "String",
        "StringValue": event_type,
    }
}
```

And the `InventorySubscription.FilterPolicy` remains `eventType: [JobCreated, JobDeleted]`. And the `InventoryFunction` handler's `_event_type` fallback is `payload.get("eventType")` — which now returns `None` because the body field is `event_type`.

Now:
- Template-only fix (change filter to `event_type: [JobCreated, JobDeleted]`): messages reach inventory, but handler's `payload.get("eventType")` returns `None` (because body uses `event_type`), so `_event_type` returns `None` and neither branch fires. `inventory_terminal_state` still fails.
- Code-only fix (change handler body field and MessageAttributes key back to `eventType`): filter policy uses `eventType`, publisher now sends `eventType` attribute → filter matches → messages flow → handler reads `payload.get("eventType")` → works. But this is a code-only fix that works, violating the "code-only fix is insufficient" requirement.

For code-only to be insufficient: the filter policy must also need changing. If the code fix changes the published attribute key from `event_type` to `eventType`, the filter policy `eventType` matches — so code-only fix works. This doesn't satisfy the constraint.

The only way to require both: the template controls something the code cannot fix alone, AND the code controls something the template cannot fix alone. In this architecture, the template controls the filter policy and the handler controls what's published. If the filter is broken AND the handler body field name is wrong, then:
- Template fix alone (fix filter to `event_type`): messages flow, but handler body uses `event_type` and handler reads `payload.get("eventType")` → None → no writes. Insufficient.
- Code fix alone (fix body field and attribute to `eventType`): filter policy has `eventType` → matches → messages flow → handler reads `payload.get("eventType")` → works. This works alone.

So code-only fix works. This architecture's filter-policy + handler coupling doesn't naturally create a scenario where both are needed simultaneously.

Let me use a different combination for FAULT-10 that genuinely requires both template and handler changes.

**Final FAULT-10:**

The `InventoryFunction` handler is changed to read `TABLE_NAME_INVENTORY` (wrong env var name) instead of `TABLE_NAME`, AND the template adds an environment variable `TABLE_NAME_INVENTORY` pointing to a non-existent table name while keeping `TABLE_NAME` pointing to the correct table.

```python
# Faulted handler:
dynamodb.put_item(
    TableName=os.environ["TABLE_NAME_INVENTORY"],  # was TABLE_NAME
    ...
)
dynamodb.update_item(
    TableName=os.environ["TABLE_NAME_INVENTORY"],  # was TABLE_NAME
    ...
)
```

And `faulted.yaml` adds:
```yaml
InventoryFunction:
  Environment:
    Variables:
      TABLE_NAME: !Ref InventoryTable        # still correct
      TABLE_NAME_INVENTORY: !Sub '${AWS::StackName}-wrong-inventory'  # wrong table name
```

Template-only fix (remove `TABLE_NAME_INVENTORY` or point it to the correct table): handler still reads `TABLE_NAME_INVENTORY` which resolves to the wrong table → DynamoDB writes fail or go to wrong table. Insufficient.

Code-only fix (change handler back to read `TABLE_NAME`): `TABLE_NAME` is correct in both faulted and known-good template, so handler writes to the right table. This works alone.

Code-only fix works — again not satisfying the constraint.

For both to be necessary: the template must have `TABLE_NAME` pointing to a wrong value, and the handler must read a different variable. Then:
- Template fix alone (correct `TABLE_NAME` to right table): handler reads `TABLE_NAME_INVENTORY` which is unaffected → still wrong.
- Code fix alone (change handler to read `TABLE_NAME`): `TABLE_NAME` is wrong in faulted template → still writes to wrong table.
- Both fixes required: template corrects `TABLE_NAME` (or `TABLE_NAME_INVENTORY`), code corrects the variable name read.

**Final FAULT-10 (definitive):**

**Class:** data_correctness
**Type:** coupled
**Chains with:** N/A
**Coupled properties:** `InventoryFunction.Environment.Variables.TABLE_NAME` (changed to point to `!Sub '${AWS::StackName}-nonexistent'` — a table that doesn't exist), `InventoryFunction` handler (changed to read `os.environ.get("TABLE_NAME", "fallback-table")` with a hardcoded fallback of `"fallback-table"` — so if `TABLE_NAME` is missing the code uses a wrong default, but since `TABLE_NAME` is present with a wrong value, the code uses the wrong value)
**Fails assertions:** `inventory_terminal_state`

Actually the cleanest version: `TABLE_NAME` is wrong in template AND handler uses a secondary env var that is also wrong.

Let me just write a clean scenario:

**FAULT-10 (clean final version):**

`InventoryFunction.Environment.Variables.TABLE_NAME` is changed in the template from `!Ref InventoryTable` to `!Sub '${AWS::StackName}-analytics'` (the analytics bucket name, which happens to also be a valid-looking name but is not a DynamoDB table). The handler reads `os.environ["TABLE_NAME"]` and passes it to DynamoDB. All writes fail with `ResourceNotFoundException` because the table does not exist.

Simultaneously, the inventory handler is changed to catch `ResourceNotFoundException` silently and return `{}` without re-raising:

```python
# Faulted handler:
try:
    dynamodb.put_item(...)
except dynamodb.exceptions.ResourceNotFoundException:
    pass  # silently skip
```

Template-only fix (restore `TABLE_NAME` to correct table): handler now writes to the correct table, `ResourceNotFoundException` is never raised, the silent catch is never triggered. This works alone — template-only fix is sufficient.

Code-only fix (remove silent catch): handler re-raises `ResourceNotFoundException`, Lambda errors, SQS retries, eventually DLQs. Item never written. Insufficient.

Still not symmetric. The requirement "template-only fix is insufficient AND code-only fix is insufficient" is hard to achieve when one side fully fixes the flow.

The key insight is: for both to be necessary, the template must leave a fault that the code perpetuates, AND the code must have a fault the template cannot paper over. In this architecture, the cleanest example is:

- Template: `InventorySubscription.FilterPolicy` uses `eventType: [JobCreated]` — drops `JobDeleted`. So only `JobCreated` events reach inventory, not `JobDeleted`. The `put_item` happens, but `update_item` for `markAsDeleted` never fires.
- Handler: the `put_item` call incorrectly sets `markAsDeleted: False` inline (as an initial value), so even if the `update_item` were to run, it would need to overwrite it.

Template-only fix (restore filter to include `JobDeleted`): `JobDeleted` messages now reach inventory. Handler runs `update_item` with `SET markAsDeleted = :m`. Item gets `markAsDeleted: True`. Test passes. Template-only fix works.

This doesn't work for the constraint either.

**Conclusion:** In this architecture, where the template controls routing/permissions and the handler controls write logic, a genuinely symmetric both-required fault requires:
- Template provides a wrong resource reference that causes the code to attempt an operation on the wrong resource.
- Handler code has a fault that means even if the right resource is provided by the template, the operation produces wrong data.

**Final clean FAULT-10:**

- Template: `InventoryFunction.Environment.Variables.TABLE_NAME` = `!Sub '${AWS::StackName}-shadow-inventory'` (a non-existent table) AND `InventoryRole` grants `dynamodb:PutItem, dynamodb:UpdateItem` on `!GetAtt InventoryTable.Arn` (correct table) only — so writes to the shadow table are also denied.
- Handler: `dynamodb.put_item` omits the `"id"` key from the `Item` dict (uses `"job_id"` instead of `"id"`).

Template-only fix (correct `TABLE_NAME` to `!Ref InventoryTable`): handler tries to `put_item` with key attribute `job_id` instead of `id`. DynamoDB rejects this because `id` is the hash key defined in `KeySchema`. The write fails with `ValidationException`. Template-only fix is insufficient.

Code-only fix (restore `"id"` key in `put_item`): handler uses `os.environ["TABLE_NAME"]` which resolves to the shadow table. The shadow table doesn't exist → `ResourceNotFoundException`. Code-only fix is insufficient.

Both required: template restores `TABLE_NAME` to the real table AND handler restores `"id"` as the key attribute name.

**Fails assertions:** `inventory_terminal_state`

This is the clean version. Let me write it up properly.

**Class:** data_correctness
**Type:** coupled
**Chains with:** N/A
**Coupled properties:** `InventoryFunction.Environment.Variables.TABLE_NAME` (changed from `!Ref InventoryTable` to `!Sub '${AWS::StackName}-shadow-inventory'` — a non-existent table), `InventoryFunction` handler `put_item` call (changed `"id"` key to `"job_id"` — a wrong attribute name for the hash key)
**Fails assertions:** `inventory_terminal_state`

#### Misconfiguration

Two simultaneous changes:

1. `InventoryFunction.Environment.Variables.TABLE_NAME` in `faulted.yaml` is changed from `!Ref InventoryTable` (resolves to `ace-bench-stack-inventory`) to `!Sub '${AWS::StackName}-shadow-inventory'` (resolves to `ace-bench-stack-shadow-inventory` — a non-existent DynamoDB table).

2. The inventory handler's `put_item` call is changed:
```python
# Faulted:
dynamodb.put_item(
    TableName=os.environ["TABLE_NAME"],
    Item={
        "job_id": {"S": payload["jobId"]},   # was "id"
        "eventCreated": {"S": payload["eventCreated"]},
        "eventSource": {"S": payload["eventSource"]},
        "eventDetails": {"S": json.dumps(payload["eventDetails"])},
    },
)
```

Template-only fix (restore `TABLE_NAME` to `ace-bench-stack-inventory`): the handler now writes to the real table but uses `job_id` as the attribute name for the item key. DynamoDB's `KeySchema` requires the hash key attribute to be named `id` (as defined in `AttributeDefinitions` and `KeySchema` in the template). A `put_item` that omits the required hash key attribute `id` raises `ValidationException: One or more parameter values were invalid: Missing the key id in the item`. The item is never written. Template-only fix is insufficient.

Code-only fix (restore `"id"` as the key name in `put_item`): the handler now correctly names the key attribute `id`, but `TABLE_NAME` still resolves to `ace-bench-stack-shadow-inventory` (non-existent). The `put_item` call raises `ResourceNotFoundException`. Code-only fix is insufficient.

Both fixes required: the template must restore the correct table name AND the handler must restore the correct key attribute name. Only then does `put_item` succeed, the item is created with key `id`, and the subsequent `update_item` for `markAsDeleted` can find and update it.

#### Observable Symptom

`events_published` passes. `analytics_object_created` passes. `inventory_terminal_state` times out. The inventory function log tail shows errors — but the error type differs depending on which fix has been applied: pre-fix shows `ResourceNotFoundException` (non-existent table); after template-only fix, logs show `ValidationException` (missing key attribute). The DynamoDB table (`ace-bench-stack-inventory`) has no item for the test job ID. The shadow table (`ace-bench-stack-shadow-inventory`) does not exist.

#### Diagnostic Reasoning Path

Step 1 — `ace_read_table_item` on the real inventory table for the test job ID — item absent. `ace_get_log_tail` for the inventory function — `ResourceNotFoundException` errors referencing `ace-bench-stack-shadow-inventory`.

Step 2 — `ace_get_environment_variables` for the inventory function — `TABLE_NAME: ace-bench-stack-shadow-inventory`. Cross-reference with `ace_get_stack_outputs` — `InventoryTableName: ace-bench-stack-inventory`. The env var does not match the real table name. This is the first clear finding: wrong table name in env var.

Step 3 — The model fixes the template to restore `TABLE_NAME` to the real table. After redeployment, `ace_get_log_tail` now shows `ValidationException: Missing the key id in the item`. The table name is now correct, but a new error has appeared — the key attribute name in the `put_item` call is wrong.

Step 4 — Read the inventory handler code. The `put_item` uses `"job_id"` as the key instead of `"id"`. Cross-reference with `ace_describe_resource` for `InventoryTable` (or `ace_get_stack_outputs` showing the table name) and the known `KeySchema` from the template — the hash key is `id`. The second fault is now deducible from the new error message combined with the handler code.

Step 5 — Both the template (`TABLE_NAME`) and the handler (`"id"` key name) must be corrected. This is explicitly a two-file fix.

#### Resolution

- `corpus/arch_08_event_driven_architecture_with_sns_fifo_dynamodb_lambda_and_s3/known_good.yaml` — Restore `InventoryFunction.Environment.Variables.TABLE_NAME` from `!Sub '${AWS::StackName}-shadow-inventory'` to `!Ref InventoryTable`. This ensures the handler writes to the existing, correctly-keyed DynamoDB table.
- `corpus/arch_08_event_driven_architecture_with_sns_fifo_dynamodb_lambda_and_s3/deployment/lambda/inventory/index.py` — Restore `"job_id"` to `"id"` in the `put_item` Item dict. This ensures the required hash key attribute (`id`) is present in the write request and matches the table's `KeySchema`.

A template-only fix is insufficient: after correcting `TABLE_NAME`, the handler still uses `job_id` as the key attribute name, causing `ValidationException` on every `put_item`. A code-only fix is insufficient: after correcting the key attribute name, the handler writes to the non-existent shadow table, causing `ResourceNotFoundException`. Both files must be corrected together.

**Rating:** hard

The two-phase error sequence (first `ResourceNotFoundException`, then `ValidationException` after partial fix) is diagnostic misdirection — a model applying the template fix first will see a new error and may believe it has made the problem worse. Recognizing that both errors stem from independent faults requires correlating the handler code's key name with the table's `KeySchema` while simultaneously tracking the env var fix.
