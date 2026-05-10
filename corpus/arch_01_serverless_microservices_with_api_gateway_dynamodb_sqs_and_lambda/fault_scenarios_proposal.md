# Fault Scenario Proposal — arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda

## Architecture Fault Class Analysis

This architecture's richest fault surface lies in **connectivity** and **data correctness**. The event flow relies on a chain of implicit contracts: an SQS event source mapping with a specific batch window, three separate DynamoDB stream event source mappings each with distinct FilterCriteria JSON patterns, and a shared IAM role (`StreamHandlerRole`) whose policy must simultaneously serve four different Lambda functions. Any mismatch in the filter pattern JSON structure, stream view type, or key attribute names in a handler's write silently drops or misroutes data with no access-denied error.

**Security** faults are moderately rich but require care. The IAM policies are written with `!GetAtt FriendTable.Arn` resource scoping, meaning any scenario must involve the scope being subtly wrong rather than simply absent — for example, pointing to a different resource ARN or relying on a handler writing to a different table whose name is supplied via environment variable.

**Reliability** faults are viable around the DLQ configuration. `StreamHandlerRole` grants `sqs:SendMessage` on `StateHandleDLQ.Arn`, meaning any mismatch between the DLQ ARN in the `DestinationConfig` and the ARN granted in IAM silently drops failures rather than dead-lettering them. This interacts with retry configuration.

**Performance** faults are limited. The architecture has no Kinesis streams, no Step Functions, and no provisioned DynamoDB throughput. The one genuine performance fault opportunity is the interaction between the SQS `VisibilityTimeout` (120s) and the Lambda `Timeout` (120s) — exactly equal, meaning any handler slowdown causes messages to re-appear before Lambda finishes, producing duplicate processing and idempotency corruption. This is worth one scenario.

**Deprioritized:** Kinesis shard counts, Step Functions timeouts, and Firehose destination configuration are not present in this architecture and require no scenarios.

Fault classes proposed in order of natural richness: connectivity (3), data correctness (3), reliability (2), security (1), performance (1).

---

## Scenarios

### FAULT-01 — Stream Filter Drops Accepted Requests Due to OldImage State Mismatch

**Class:** connectivity
**Type:** coupled
**Chains with:** N/A
**Coupled properties:** `AcceptStateMapping.FilterCriteria.Filters[0].Pattern` (OldImage state value), `FriendTable.StreamSpecification.StreamViewType`
**Fails assertions:** `accept_terminal_state`

#### Misconfiguration

Two properties are simultaneously wrong:

1. `FriendTable.StreamSpecification.StreamViewType` is changed from `NEW_AND_OLD_IMAGES` to `NEW_IMAGE`. This means stream records for MODIFY events carry no `OldImage` field.

2. `AcceptStateMapping.FilterCriteria.Filters[0].Pattern` is changed to match both NewImage and OldImage fields:
   - Correct: `{"eventName":["MODIFY"],"dynamodb":{"NewImage":{"state":{"S":["Friends"]}},"OldImage":{"state":{"S":["Pending"]}}}}`
   - Wrong: the pattern is left as-is (still requires `OldImage.state = Pending`)

With `StreamViewType: NEW_IMAGE`, stream records for MODIFY events never include `OldImage`. The filter pattern requires `OldImage.state = Pending` to match. Since `OldImage` is absent from every record, the filter never matches and `AcceptStateHandlerFunction` is never invoked. The receiver-side record transitions to `Friends` (written by FrontHandlerFunction via `_accept`), but the requester-side record is never updated from `Requested` to `Friends`.

Neither property alone produces this full symptom: if `StreamViewType` is `NEW_AND_OLD_IMAGES` but the filter pattern requires OldImage, the filter correctly fires. If `StreamViewType` is `NEW_IMAGE` but the filter pattern requires only NewImage fields (no OldImage clause), the filter fires correctly on the NewImage content. Only the combination — view type strips OldImage while the filter requires it — causes silent filter failure.

#### Observable Symptom

The `accept_terminal_state` assertion fails. After sending an Accept action, the receiver-side record (`friend_id → player_id`) correctly transitions to `Friends` (written by FrontHandlerFunction), but the requester-side record (`player_id → friend_id`) remains in `Requested` state indefinitely. The `AcceptStateHandlerFunction` invocation metrics remain flat. No Lambda errors appear in logs for that function because it is never invoked, not because it failed.

#### Diagnostic Reasoning Path

Step 1 — Probe the system by sending a Request then an Accept action, then directly reading both table items. The receiver-side item shows `Friends`; the requester-side item shows `Requested`. This confirms FrontHandlerFunction processed the Accept correctly but some downstream step failed to update the requester side. The symptom isolates to hop 9 or 10 (stream → AcceptStateHandlerFunction or its write back to the table).

Step 2 — Check the AcceptStateHandlerFunction log tail. Logs show no invocations at all following the Accept action — not errors, but complete silence. This is the first surprising finding: the function was never called, yet no mapping is disabled. A model might wrongly conclude the mapping is misconfigured or disabled.

Step 3 — Inspect the AcceptStateMapping event source mapping via `ace_check_event_source`. The mapping reports `State: Enabled` with the correct `EventSourceArn` pointing to the FriendTable stream. The mapping looks healthy. This rules out the obvious "mapping disabled" hypothesis and forces deeper investigation.

Step 4 — Read the DynamoDB stream directly using `ace_get_stream_records` with the TableStreamArn. Examine the MODIFY record produced when the receiver-side item changed from Pending to Friends. The record shows a `new_image` field with `state: Friends` but `old_image: null`. This reveals the stream is not emitting OldImage data.

Step 5 — Inspect the FriendTable configuration using `ace_describe_resource` or `ace_describe_dynamo_stream`. The StreamViewType is `NEW_IMAGE` rather than `NEW_AND_OLD_IMAGES`. Cross-referencing this with the AcceptStateMapping filter pattern (which requires `OldImage.state = Pending`) explains why no records ever match: the OldImage is structurally absent, so the filter silently rejects every MODIFY record.

#### Resolution

Both files must be edited. A template-only fix is insufficient: the filter pattern in the template must also be updated or the StreamViewType restored; fixing only StreamViewType without verifying the filter pattern still works is correct in this case, but the scenario is designed so that a model must understand the interaction of both properties.

- `corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/known_good.yaml` — Restore `FriendTable.StreamSpecification.StreamViewType` to `NEW_AND_OLD_IMAGES`. This is the root infrastructure change. The filter pattern on `AcceptStateMapping` requires OldImage data; without NEW_AND_OLD_IMAGES, no filter match is possible.

#### Difficulty

**Rating:** hard

The function is never invoked (no logs at all), the mapping appears enabled and healthy, and the stream records appear valid at first glance — only direct inspection of stream record content and cross-referencing with the filter pattern reveals the structural mismatch between what the stream emits and what the filter expects.

---

### FAULT-02 — Request Handler Writes Reciprocal Record with Swapped Key Fields

**Class:** data_correctness
**Type:** coupled
**Chains with:** N/A
**Coupled properties:** `request-state-handler/index.py` `_create_pending` key assignment (requester_id/receiver_id argument order), `FriendTable.StreamSpecification.StreamViewType`
**Fails assertions:** `pending_record_created`, `accept_terminal_state`

#### Misconfiguration

Two properties are simultaneously wrong:

1. In `corpus/.../deployment/lambda/request-state-handler/index.py`, the `_create_pending` call inside `handler` passes arguments in the wrong order:
   - Correct: `_create_pending(_s(image, "player_id"), _s(image, "friend_id"), timestamp)` — first arg is requester, second is receiver
   - Wrong: `_create_pending(_s(image, "friend_id"), _s(image, "player_id"), timestamp)` — arguments swapped
   
   In `_create_pending`, the function writes `player_id = receiver_id` and `friend_id = requester_id`. With swapped args, receiver_id receives `friend_id` from the stream record (the actual friend) and requester_id receives `player_id` (the actual player). The result is a Pending record written as `(friend_id → player_id, state=Pending)` — which is exactly the correct key for the Pending record. Wait — this scenario needs a different handler-level fault.

   Revised: The `_create_pending` function body uses the wrong attribute name for the item write. Instead of `"friend_id": requester_id`, it writes `"friend_id": receiver_id` (a duplicate of the hash key value):
   - Correct item: `{"player_id": receiver_id, "friend_id": requester_id, "state": "Pending"}`
   - Wrong item: `{"player_id": receiver_id, "friend_id": receiver_id, "state": "Pending"}` — self-referential record

2. `FriendTable.StreamSpecification.StreamViewType` is changed from `NEW_AND_OLD_IMAGES` to `KEYS_ONLY`. With KEYS_ONLY, stream records for INSERT events carry only the key fields (`player_id`, `friend_id`) in the NewImage — no `state` attribute. The RequestStateMapping filter requires `NewImage.state = Requested`. With KEYS_ONLY, NewImage contains only keys, so `state` is absent and the filter never matches.

With KEYS_ONLY alone: the filter silently blocks all INSERT events, so RequestStateHandlerFunction is never invoked. The `pending_record_created` assertion fails.
With the handler bug alone (self-referential write) and StreamViewType correct: RequestStateHandlerFunction is invoked, writes a self-referential Pending record `(friend_id → friend_id)`, and the functional test looking for `(friend_id, player_id, Pending)` finds nothing. `pending_record_created` fails.

Only the KEYS_ONLY StreamViewType is the dominant fault here — the handler bug is revealed after the infrastructure is fixed. This makes it a chained scenario instead.

**Revised design — pure data correctness, coupled, independent:**

**Class:** data_correctness
**Type:** coupled
**Chains with:** N/A
**Coupled properties:** `request-state-handler/index.py` item write (wrong sort key value), `read-handler/index.py` query (uses `KeyConditionExpression` string literal)
**Fails assertions:** `pending_record_created`, `accept_terminal_state`, `read_api_terminal_state`

#### Misconfiguration (revised)

Two properties are simultaneously wrong in handler code:

1. In `request-state-handler/index.py`, the `_create_pending` function writes the Pending record with the wrong sort key value. Instead of:
   ```python
   Item={
       "player_id": receiver_id,
       "friend_id": requester_id,   # correct
       ...
   }
   ```
   it writes:
   ```python
   Item={
       "player_id": receiver_id,
       "friend_id": receiver_id,    # wrong: self-referential
       ...
   }
   ```
   The Pending record is stored at `(receiver_id, receiver_id)` instead of `(receiver_id, requester_id)`. Since `player_id == friend_id` violates the natural uniqueness assumption, this self-referential record is written but never found by the functional test's lookup of `(friend_id, player_id, Pending)`.

2. In `read-handler/index.py`, the `handler` function uses a string literal `KeyConditionExpression` instead of the boto3 `Key` expression builder. The current code already uses a string (`"player_id = :player_id"`), which is valid — but the fault changes this to `"player_id = :pid"` while keeping the ExpressionAttributeValues key as `":player_id"`. This produces a `ValidationException: Value provided in ExpressionAttributeValues unused in expressions` error, causing the read handler to return a 502 or an error body.

With only the request-state-handler bug: the Pending record is at wrong keys, so `pending_record_created` fails. But the read API itself works (it can query records that do exist). The two bugs together cause `pending_record_created`, `accept_terminal_state` (since Accept requires a Pending record with correct keys), and `read_api_terminal_state` to all fail — a broader blast radius that makes root cause identification harder because the read API appears to be the final failure point.

With only the read-handler bug: `pending_record_created` and `accept_terminal_state` can pass (state machine works) but `read_api_terminal_state` fails. The model might focus solely on the read path and miss the upstream write bug.

#### Observable Symptom

The `pending_record_created` assertion fails — the receiver-side Pending record never appears at the expected `(friend_id, player_id)` key. Since no Pending record exists at the correct key, the subsequent Accept action by FrontHandlerFunction fails with `ConditionalCheckFailedException` (condition requires `state = Pending`), so `accept_terminal_state` also fails. The `read_api_terminal_state` assertion additionally fails because the read handler returns a ValidationException error for any query.

#### Diagnostic Reasoning Path

Step 1 — Run the functional flow manually: send a Request, wait, then directly call `ace_read_table_item` for `(friend_id, player_id)`. The item is not found. Call `ace_read_table_item` for `(player_id, friend_id)` — that item exists with state `Requested`. This confirms the requester-side write succeeded but the reciprocal Pending record is missing.

Step 2 — Check RequestStateHandlerFunction invocation via `ace_get_log_tail`. Logs show the function was invoked and completed without error (no exception logged). The handler ran but the Pending record is absent. This rules out mapping or IAM issues and points to a logic bug in what was written.

Step 3 — Use `ace_read_table_item` to probe the table for `(friend_id, friend_id)` — the self-referential key. This item exists with state `Pending`. This confirms the handler wrote to the wrong key: it set `friend_id = receiver_id` (the same as `player_id`) instead of `friend_id = requester_id`.

Step 4 — Separately probe the read API via `ace_invoke_endpoint` with a known `playerId`. The response is a 502 or error JSON. Check `ace_get_log_tail` for ReadHandlerFunction — logs show `ValidationException: Value provided in ExpressionAttributeValues unused`. The ExpressionAttributeValues key `:player_id` is not referenced in the expression `player_id = :pid`, causing a DynamoDB validation error.

#### Resolution

Both handler files must be edited. A template-only fix is insufficient because both bugs are in Lambda handler code with no template component to correct. A single-file fix is insufficient because fixing only the request-state-handler leaves the read API broken, and fixing only the read-handler leaves the Pending record at the wrong key.

- `corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/deployment/lambda/request-state-handler/index.py` — In `_create_pending`, change the Item write to use `"friend_id": requester_id` (not `receiver_id`). This places the Pending record at the correct composite key `(receiver_id, requester_id)`.
- `corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/deployment/lambda/read-handler/index.py` — In the `handler` function's `table.query` call, align the ExpressionAttributeValues key with the expression placeholder: use `":player_id"` in both the expression string and the values dict (or `:pid` in both). The mismatch causes DynamoDB to reject every query.

#### Difficulty

**Rating:** hard

The self-referential write bug in the request handler produces no error (DynamoDB happily writes a record with equal hash and sort key values), so logs show success at every step. The read handler bug appears at a completely separate layer, making it tempting to conclude there are two independent faults rather than recognizing both require code fixes.

---

### FAULT-03 — Silent DLQ Delivery Failure Masks Stream Handler Errors

**Class:** reliability
**Type:** coupled
**Chains with:** N/A
**Coupled properties:** `RequestStateMapping.DestinationConfig.OnFailure.Destination` (DLQ ARN value), `StreamHandlerRole` IAM policy `sqs:SendMessage` resource ARN
**Fails assertions:** `pending_record_created`

#### Misconfiguration

Two properties are simultaneously wrong:

1. `RequestStateMapping.DestinationConfig.OnFailure.Destination` in the template is changed to reference a non-existent or wrong SQS queue ARN — for example, a hardcoded ARN string `arn:aws:sqs:us-east-1:000000000000:wrong-dlq` instead of `!GetAtt StateHandleDLQ.Arn`. The mapping's failure destination points to an SQS queue that does not exist.

2. In `RequestStateHandlerFunction`'s handler code (`request-state-handler/index.py`), the `_create_pending` function's conditional exception swallowing is changed: instead of re-raising non-ConditionalCheckFailed errors, it silently swallows all exceptions:
   ```python
   except ClientError as exc:
       pass  # wrong: swallows all errors including AccessDeniedException
   ```

Without both faults: if only the DLQ destination is wrong, Lambda cannot deliver failures to the DLQ, but the handler itself raises exceptions on DynamoDB errors and Lambda's retry mechanism handles them. With only the code bug (swallowing all errors): failures are silently dropped but the handler returns success, so the batchItemFailures list is empty and no DLQ delivery is attempted — the behavior appears identical to the DLQ destination being wrong.

Combined: if `request-state-handler/index.py` also has a missing DynamoDB permission or environment variable fault introduced alongside, the handler silently swallows the AccessDeniedException, returns `batchItemFailures: []`, Lambda marks the batch as successful, and no DLQ delivery is even attempted. The DLQ destination being wrong is then completely irrelevant — but a model inspecting the DestinationConfig will find a wrong ARN and may conclude that is the sole root cause, fix it, and still observe the same failure because the handler swallows errors.

**Revised to require template-only fix to be insufficient:**

The combined scenario: `request-state-handler/index.py` swallows all ClientError exceptions (not just ConditionalCheckFailed), and `FRIEND_TABLE` env var in `RequestStateHandlerFunction` is set to a wrong table name `wrong-table-name` (not `!Ref FriendTable`). The handler calls `table.put_item(...)` which raises `ResourceNotFoundException` (table not found), then silently swallows it and returns `batchItemFailures: []`. Lambda marks the batch successful. Nothing reaches the DLQ.

- Fix the env var in template only: the handler now correctly references the table but still swallows all exceptions — any future error (e.g., permission issue) would be silently lost. Actually, with correct table name, the handler works fine.

Let me revise this properly.

---

**FAULT-03 — Revised:**

### FAULT-03 — DLQ Failure Silencing via Wrong Table Env Var and All-Error Swallow in Request Handler

**Class:** reliability
**Type:** coupled
**Chains with:** N/A
**Coupled properties:** `RequestStateHandlerFunction.Environment.Variables.FRIEND_TABLE` (wrong table name), `request-state-handler/index.py` exception handler (swallows all ClientError)
**Fails assertions:** `pending_record_created`

#### Misconfiguration

Two properties are simultaneously wrong:

1. In `known_good.yaml`, `RequestStateHandlerFunction.Environment.Variables.FRIEND_TABLE` is changed from `!Ref FriendTable` (which resolves to the stack-namespaced table name like `ace-bench-stack-friend`) to a hardcoded wrong value `friend-table` — a table that does not exist in LocalStack.

2. In `request-state-handler/index.py`, the exception handler in `_create_pending` is changed from:
   ```python
   except ClientError as exc:
       if not _is_conditional(exc):
           raise
   ```
   to:
   ```python
   except ClientError as exc:
       pass  # swallows ALL ClientError including ResourceNotFoundException
   ```
   This swallows every DynamoDB exception, including the `ResourceNotFoundException` that occurs when `FRIEND_TABLE` points to a non-existent table.

With only the wrong `FRIEND_TABLE` env var and correct exception handling: the handler raises `ResourceNotFoundException`, Lambda sees a function error, marks the record in `batchItemFailures`, and after `MaximumRetryAttempts: 1` exhausted, attempts DLQ delivery. The system is broken but the failure path is observable via DLQ depth and Lambda error logs.

With only the all-error-swallow and correct table name: the handler works normally for all valid records and silently ignores `ConditionalCheckFailedException` as intended. No observable difference from correct behavior.

Combined: the handler is invoked (stream mapping is healthy), calls `table.put_item(...)` on the wrong table, gets `ResourceNotFoundException`, silently swallows it, returns `batchItemFailures: []`. Lambda marks the batch successful. No DLQ delivery is attempted. No error metrics appear. The function's log shows invocations completing without error. The Pending record is never written.

#### Observable Symptom

The `pending_record_created` assertion times out. The requester-side `Requested` record exists in the table. The `RequestStateHandlerFunction` invocation metrics show successful invocations (no errors). The DLQ depth remains zero. CloudWatch shows no Lambda errors for the function. The system appears healthy at every observable layer.

#### Diagnostic Reasoning Path

Step 1 — Verify the requester-side record exists via `ace_read_table_item`. It does. Verify the receiver-side Pending record. It does not. The stream handler must be involved — check `ace_get_log_tail` for RequestStateHandlerFunction. Logs show invocations completing successfully (no error messages). This is the first misdirection: the function ran and appeared to succeed.

Step 2 — Check the SQS DLQ depth via `ace_check_queue_depth` for the StateHandleDLQ. Depth is zero — no failures were routed there. This rules out the "failure happened but DLQ delivery also failed" hypothesis and deepens the mystery: the function succeeded but wrote nothing.

Step 3 — Inspect the RequestStateHandlerFunction environment variables via `ace_get_environment_variables`. The `FRIEND_TABLE` value is `friend-table`, not the stack-namespaced table name. Cross-reference with the actual table name from `ace_get_stack_outputs` (which returns the real `TableName`). The mismatch is now visible: the handler is querying a non-existent table.

Step 4 — But why is there no error in logs? Inspect the handler code via the agent's file read tool. The `_create_pending` function's `except ClientError` block uses `pass` — it swallows all errors. The `ResourceNotFoundException` from the wrong table name is silently discarded. Both the wrong env var and the error-swallowing must be fixed together: fix only the table name and the handler works; fix only the error swallowing but leave the wrong table name and the handler raises unhandled errors, causing Lambda to retry and DLQ.

#### Resolution

Both files must be edited. A template-only fix is insufficient: restoring the correct `FRIEND_TABLE` value without fixing the exception swallow leaves the system working but with a latent reliability defect (any future DynamoDB error would be silently lost). A code-only fix is insufficient: fixing the exception swallow without correcting `FRIEND_TABLE` causes Lambda errors and DLQ routing but never writes the Pending record.

- `corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/known_good.yaml` — Restore `RequestStateHandlerFunction.Environment.Variables.FRIEND_TABLE` to `!Ref FriendTable` so the handler targets the correct stack-namespaced table.
- `corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/deployment/lambda/request-state-handler/index.py` — Restore the exception handler in `_create_pending` to re-raise non-ConditionalCheckFailed errors: `if not _is_conditional(exc): raise`. This ensures that infrastructure misconfigurations produce observable errors rather than silent success.

#### Difficulty

**Rating:** very_hard

The function completes successfully in every invocation metric, the DLQ is empty, and no Lambda error appears anywhere — the system presents a perfect illusion of health. A model must connect three separate observations (missing Pending record + successful invocations + wrong env var) before discovering the error-swallowing code is the reason the wrong table reference is invisible.

---

### FAULT-04 — Accept Handler Updates Wrong Relationship Direction Due to Field Extraction Bug

**Class:** data_correctness
**Type:** coupled
**Chains with:** N/A
**Coupled properties:** `accept-state-handler/index.py` field extraction from NewImage (player_id/friend_id argument order to `_accept_reverse`), `AcceptStateMapping.FilterCriteria.Filters[0].Pattern` (OldImage state requirement)
**Fails assertions:** `accept_terminal_state`

#### Misconfiguration

Two properties are simultaneously wrong:

1. In `accept-state-handler/index.py`, the `handler` function extracts fields from NewImage and calls `_accept_reverse` with swapped arguments:
   - Correct: `_accept_reverse(_s(image, "player_id"), _s(image, "friend_id"), timestamp)` — updates `(friend_id → player_id)` from Requested to Friends
   - Wrong: `_accept_reverse(_s(image, "friend_id"), _s(image, "player_id"), timestamp)` — calls `_accept_reverse(friend_id_value, player_id_value)` which then tries to update `Key={"player_id": player_id_value, "friend_id": friend_id_value}` — the receiver-side record, not the requester-side record

   Inside `_accept_reverse`, the Key is `{"player_id": friend_id, "friend_id": player_id}` — which is exactly the same item that FrontHandlerFunction already updated to `Friends`. The `ConditionExpression` requires `state = Requested`, which fails because that item is now `Friends`. The update is silently swallowed by the `_is_conditional` check. The requester-side item (`player_id → friend_id`, state=`Requested`) is never updated.

2. `AcceptStateMapping.FilterCriteria.Filters[0].Pattern` is changed to remove the OldImage clause, making it:
   `{"eventName":["MODIFY"],"dynamodb":{"NewImage":{"state":{"S":["Friends"]}}}}`
   
   This makes the filter broader — it now matches ANY modification that sets state=Friends, not just Pending→Friends transitions. This means if the requester-side record is somehow updated to Friends by another path, it would trigger AcceptStateHandlerFunction again (double-processing). More importantly, this change masks the real bug: with the original filter that requires OldImage.state=Pending, only one MODIFY record (the Pending→Friends transition) triggers the handler. With the broader filter, both the FrontHandlerFunction's update AND any future update that sets Friends would trigger it — but with the swapped arguments, neither invocation ever finds a record in `Requested` state at the wrong key it's trying to update.

Neither fault alone produces the same symptom: if only the arguments are swapped (filter is correct), the handler is invoked once on the Pending→Friends transition and tries to update the wrong record, silently failing. If only the filter is broadened (arguments correct), the handler correctly updates `(friend_id → player_id)` on the Pending→Friends event and possibly again on any other Friends transition — a mild reliability issue but not a correctness failure for the primary flow.

Combined: the broader filter potentially invokes the handler on more events, but the swapped arguments ensure none of these invocations update the correct record. The broader filter also makes it harder to reason about which specific event triggered the handler, because there may be multiple invocations visible in logs.

#### Observable Symptom

The `accept_terminal_state` assertion fails. The receiver-side record (`friend_id → player_id`) shows `Friends`. The requester-side record (`player_id → friend_id`) remains `Requested`. AcceptStateHandlerFunction logs show invocations that completed without error — the handler ran, attempted an update, silently caught a `ConditionalCheckFailedException`, and returned success. No error metric is emitted.

#### Diagnostic Reasoning Path

Step 1 — Read both table items. Receiver-side: `Friends`. Requester-side: `Requested`. The accept flow broke at the reciprocal update (hop 10). Check AcceptStateHandlerFunction log tail — invocations appear, no errors logged. This rules out mapping or IAM issues.

Step 2 — Inspect the AcceptStateMapping event source mapping via `ace_check_event_source`. The FilterCriteria pattern does not require OldImage (it was broadened). A model might flag this as suspicious — the original known-good pattern required OldImage — but with LocalStack the mapping is enabled and records are clearly being processed (logs show invocations).

Step 3 — Directly invoke AcceptStateHandlerFunction with a synthetic stream record via `ace_invoke_lambda`, passing a MODIFY record with NewImage `{"player_id": "A", "friend_id": "B", "state": "Friends"}` and OldImage `{"player_id": "A", "friend_id": "B", "state": "Pending"}`. Observe what DynamoDB call the handler makes. The handler attempts to update `Key={"player_id": "B", "friend_id": "A"}` (swapped) with condition `state = Requested`. If the item at that key is not in `Requested` state, the update silently does nothing.

Step 4 — Read the handler source code. In the `handler` function, the call is `_accept_reverse(_s(image, "friend_id"), _s(image, "player_id"), timestamp)` — arguments are swapped relative to the field names. `_accept_reverse` builds the Key as `{"player_id": friend_id, "friend_id": player_id}` — which is the receiver-side item, not the requester-side item. The fix requires swapping the arguments back to the correct order.

#### Resolution

Both files must be edited. A template-only fix is insufficient: the broadened filter pattern alone does not cause the wrong record to be updated — that is a handler code bug. A code-only fix is insufficient: with the swapped arguments fixed but the filter broadened, the handler may be invoked on unintended events (any Friends transition, not just Pending→Friends), causing duplicate updates and potential idempotency issues.

- `corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/known_good.yaml` — Restore `AcceptStateMapping.FilterCriteria.Filters[0].Pattern` to `{"eventName":["MODIFY"],"dynamodb":{"NewImage":{"state":{"S":["Friends"]}},"OldImage":{"state":{"S":["Pending"]}}}}`. This ensures the handler is only invoked on Pending→Friends transitions, not on any Friends write.
- `corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/deployment/lambda/accept-state-handler/index.py` — In the `handler` function, restore argument order to `_accept_reverse(_s(image, "player_id"), _s(image, "friend_id"), timestamp)`. This ensures the handler updates `Key={"player_id": friend_id, "friend_id": player_id}` (the requester-side record) rather than the receiver-side record.

#### Difficulty

**Rating:** hard

The handler invokes successfully and silently catches a ConditionalCheckFailedException on the wrong record — the error is expected and swallowed by design, making the bug invisible in logs. A model must reason about what the conditional update's condition expression implies about which item the handler believes it is updating.

---

### FAULT-05 — SQS Visibility Timeout Equal to Lambda Timeout Causes Duplicate Processing and State Corruption

**Class:** performance
**Type:** coupled
**Chains with:** N/A
**Coupled properties:** `FrontQueue.VisibilityTimeout` (equal to Lambda timeout), `FrontHandlerFunction.Timeout` (equal to queue visibility timeout)
**Fails assertions:** `request_record_written`, `accept_terminal_state`

#### Misconfiguration

Two properties are simultaneously misconfigured in a way that neither alone produces the symptom:

1. `FrontQueue.VisibilityTimeout` is changed from `120` to `30` seconds. This means messages become re-visible to other consumers after 30 seconds if not deleted.

2. `FrontHandlerFunction.Timeout` is left at `120` seconds (unchanged) — or optionally kept at 120 to emphasize the Lambda is slower than the visibility timeout.

The correct configuration has VisibilityTimeout at 120s matching the Lambda timeout, giving Lambda enough time to finish before a message re-appears. With VisibilityTimeout at 30s and Lambda timeout at 120s, if Lambda takes longer than 30 seconds to process a batch (e.g., under any DynamoDB latency or cold start), the message becomes visible again before Lambda finishes. A second invocation picks up the same message and attempts the same `put_item` with `ConditionExpression="attribute_not_exists(player_id)"`. The condition fails for the duplicate, which is silently swallowed. However, if the Accept message is duplicated and processed twice, the second `_accept` call (updating Pending→Friends) may run against a record already in `Friends` state, causing a `ConditionalCheckFailedException` which is silently swallowed. The receiver-side Accept completes but without reliable idempotency guarantees.

More critically: when the Accept message is re-delivered and FrontHandlerFunction processes it again, the `_accept` function requires `state = Pending`. If the first invocation already updated it to `Friends`, the second invocation's condition fails silently. But if both invocations run concurrently (race condition), one succeeds and one fails — and the DynamoDB stream may emit two MODIFY events with conflicting states, causing AcceptStateHandlerFunction to attempt two reverse updates, the second of which finds `state != Requested` and silently fails. Net result: the system is non-deterministically correct.

Under functional test load (sequential, single player pair), this manifests as: the `request_record_written` assertion eventually passes (first processing wins), but `accept_terminal_state` may time out if the duplicate Accept processing's ConditionalCheckFailed race prevents the requester-side update from completing within the test's 120-second window.

Neither fault alone: VisibilityTimeout=30s with Lambda timeout=30s means Lambda finishes before messages re-appear (no duplicates). Lambda timeout=120s with VisibilityTimeout=120s (correct) means Lambda finishes before messages re-appear. Only VisibilityTimeout < Lambda timeout creates the re-delivery window.

#### Observable Symptom

The test is non-deterministic. On cold starts or under any added latency, `accept_terminal_state` fails because duplicate Accept processing causes the AcceptStateHandlerFunction's update to find the requester-side record already in `Friends` state (updated by a duplicate invocation of AcceptStateHandlerFunction triggered by the duplicate stream event). The `request_record_written` assertion may also fail on rare occasions if the duplicate Request processing races to insert the same item twice and both invocations see `attribute_not_exists` simultaneously. CloudWatch metrics show multiple concurrent invocations of FrontHandlerFunction for the same message.

#### Diagnostic Reasoning Path

Step 1 — Check FrontHandlerFunction logs via `ace_get_log_tail`. Multiple invocations appear for the same test run's player IDs. Two or more invocation records process the same message body (same `player_id`, `friend_id`, `friend_action`). This confirms duplicate processing is occurring.

Step 2 — Check the FrontQueue attributes via `ace_check_queue_depth` and inspect the queue's `VisibilityTimeout` attribute. The visibility timeout is 30 seconds. Note that `FrontHandlerFunction`'s timeout is 120 seconds (visible via `ace_describe_resource` or `ace_get_environment_variables`). The imbalance is now visible: a message can re-appear 30 seconds after delivery even though Lambda may still be processing it.

Step 3 — Confirm by examining the DynamoDB stream records via `ace_get_stream_records`. Multiple INSERT or MODIFY events appear for the same item keys, confirming that FrontHandlerFunction wrote the same transition multiple times. The duplicates were silently handled by ConditionalCheckFailedException in the handler but the stream still emitted the first successful write's event.

Step 4 — Verify by checking `ace_describe_resource` for FrontHandlerFunction. The Timeout is 120 seconds. The VisibilityTimeout of 30 seconds is shorter than this timeout, creating a guaranteed re-delivery window for any Lambda invocation approaching or exceeding 30 seconds. The fix requires both values to be aligned: VisibilityTimeout must be at least as long as Lambda's maximum execution time.

#### Resolution

Both template properties must be corrected. Fixing only the queue visibility timeout without considering Lambda timeout leaves the system fragile if Lambda timeout is later increased. Fixing only the Lambda timeout downward to 30 seconds would make Lambda more likely to time out on slow DynamoDB operations.

- `corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/known_good.yaml` — Restore `FrontQueue.VisibilityTimeout` to `120`. This ensures the queue does not re-expose messages to other pollers while Lambda is still processing the current batch within its 120-second timeout budget.

#### Difficulty

**Rating:** medium

The symptom is non-deterministic and may not reproduce on every test run, but duplicate invocation records in CloudWatch logs are a clear fingerprint once found. The configuration mismatch between VisibilityTimeout and Lambda Timeout is a well-known antipattern, making this medium difficulty despite the non-determinism.

---

### FAULT-06 — StreamHandlerRole IAM Policy Grants Stream Read on Wrong ARN, Silently Blocks All Stream Handlers

**Class:** security
**Type:** coupled
**Chains with:** N/A
**Coupled properties:** `StreamHandlerRole` IAM policy `dynamodb:DescribeStream`/`GetRecords`/`GetShardIterator`/`ListStreams` Resource ARN, `RequestStateHandlerFunction.Environment.Variables.FRIEND_TABLE`
**Fails assertions:** `pending_record_created`, `accept_terminal_state`

#### Misconfiguration

Two properties are simultaneously wrong:

1. In `known_good.yaml`, the `StreamHandlerRole` IAM policy statement for DynamoDB stream read permissions changes the `Resource` from `!GetAtt FriendTable.StreamArn` to `!GetAtt FriendTable.Arn` — the table ARN rather than the stream ARN. Both are valid ARNs in the template and look syntactically identical except for the `.StreamArn` vs `.Arn` suffix. The table ARN looks like `arn:aws:dynamodb:us-east-1:000000000000:table/ace-bench-stack-friend`. The stream ARN looks like `arn:aws:dynamodb:us-east-1:000000000000:table/ace-bench-stack-friend/stream/2026-01-01T00:00:00.000`. Granting stream read operations on the table ARN (not the stream ARN) causes IAM to deny `dynamodb:GetRecords` when the Lambda event source mapping tries to poll the stream. However, in LocalStack, IAM enforcement is partial — stream polling by the event source mapping may still succeed because LocalStack's Lambda-SQS/DynamoDB integration bypasses some IAM checks at the polling layer. The denial manifests at the function invocation level: the function cannot call `DescribeStream` or `GetRecords` itself if it tries to do so directly, but the ESM polling is done by the Lambda service, not the function.

   Revised: the Resource for stream read is `!GetAtt FriendTable.Arn` (table ARN) instead of the stream ARN. Additionally, the DynamoDB write permission (`dynamodb:PutItem`, `dynamodb:UpdateItem`, `dynamodb:DeleteItem`) Resource is changed from `!GetAtt FriendTable.Arn` to `arn:aws:dynamodb:${AWS::Region}:${AWS::AccountId}:table/*` — a wildcard that appears permissive but in LocalStack is sufficient. This combination makes the policy look broadly permissive on write and restrictively wrong on stream read.

2. The `FRIEND_TABLE` environment variable in `RequestStateHandlerFunction` is correct. But a second misconfiguration is introduced: `RequestStateMapping.StartingPosition` is changed from `LATEST` to `TRIM_HORIZON`. This causes the mapping to read from the beginning of the stream shard, potentially re-processing old records from previous test runs. Combined with the IAM stream ARN issue, the mapping may attempt to process old records, some of which trigger the handler with stale data, causing spurious `ConditionalCheckFailedException` errors that are silently swallowed — making the system appear to be processing but not producing the expected state.

   This coupling: with only the wrong stream ARN in IAM, the event source mapping may fail to poll (LocalStack behavior varies), and the mapping enters `Disabled` or error state — visible via `ace_check_event_source`. With only `TRIM_HORIZON`, old records are reprocessed but the current test's records are also processed correctly. Combined: TRIM_HORIZON causes the mapping to read many old stream records first, and the IAM issue causes intermittent failures in processing those records, generating noise in logs that distracts from the current test's records never being processed cleanly.

**Simplified and tightened:**

**Class:** security
**Type:** coupled
**Chains with:** N/A
**Coupled properties:** `StreamHandlerRole` inline policy stream read `Resource` (wrong ARN type — table ARN instead of stream ARN), `RequestStateMapping.MaximumRetryAttempts` (set to 0 instead of 1)
**Fails assertions:** `pending_record_created`, `accept_terminal_state`

#### Misconfiguration

Two properties are simultaneously wrong:

1. In `known_good.yaml`, `StreamHandlerRole`'s inline policy statement for `dynamodb:DescribeStream`, `dynamodb:GetRecords`, `dynamodb:GetShardIterator`, `dynamodb:ListStreams` changes its `Resource` from `!GetAtt FriendTable.StreamArn` to `!GetAtt FriendTable.Arn`. The stream operations are now authorized only for the table ARN, not the stream ARN. In LocalStack with IAM enforcement enabled, the event source mapping's polling call for `GetRecords` against the stream ARN is denied — `AccessDeniedException`. The ESM polling layer sees this as a function invocation failure and marks the record batch as failed.

2. `RequestStateMapping.MaximumRetryAttempts` is changed from `1` to `0`. With zero retries, any batch that fails (including due to IAM denial at the ESM polling layer) is immediately discarded rather than retried. Combined with the IAM denial, the ESM fails to poll records, immediately discards them (zero retries), and never invokes RequestStateHandlerFunction. The DLQ also never receives anything because the failure occurs at the polling layer before invocation, and `DestinationConfig.OnFailure` applies to invocation failures, not polling permission failures.

Neither fault alone: with correct IAM and `MaximumRetryAttempts=0`: any transient invocation failure is immediately discarded, but if the handler runs without error, records are processed correctly. With wrong IAM stream ARN and `MaximumRetryAttempts=1`: after one retry, records are sent to the DLQ, which has depth > 0 — observable via `ace_check_queue_depth`. Combined: zero retries prevent DLQ routing, and the IAM denial prevents polling — DLQ depth remains 0, handler logs are empty, the mapping appears enabled.

#### Observable Symptom

`pending_record_created` fails. RequestStateHandlerFunction logs are empty — no invocations at all. The DLQ depth is zero. The RequestStateMapping reports `State: Enabled` with correct ARN. The stream itself is healthy (shards exist, records are being written by FrontHandlerFunction). The system presents no observable error signal anywhere.

#### Diagnostic Reasoning Path

Step 1 — Verify the requester-side `Requested` item exists (it does). Verify the receiver-side Pending item (it does not). Check RequestStateHandlerFunction logs — empty, no invocations since the test started. This points to a mapping-level or IAM-level failure preventing invocation.

Step 2 — Check the RequestStateMapping via `ace_check_event_source`. State is `Enabled`, EventSourceArn matches the table stream ARN. The mapping looks healthy. Check `ace_get_stream_records` for the stream — INSERT records are present in the stream for the Requested item. Records exist in the stream but the handler is never invoked.

Step 3 — Check the StreamHandlerRole IAM policy via `ace_get_iam_role`. The stream read statement's Resource is the table ARN (`arn:aws:dynamodb:...:table/ace-bench-stack-friend`), not the stream ARN (`arn:aws:dynamodb:...:table/ace-bench-stack-friend/stream/...`). Stream operations require the stream ARN as the resource. This is the IAM misconfiguration.

Step 4 — Check the RequestStateMapping `MaximumRetryAttempts`. Via `ace_check_event_source` or `ace_describe_resource`, the retry count is 0. With zero retries and IAM denial at the polling layer, failed batches are immediately discarded — no DLQ routing, no invocation attempts. If retry were set to 1, the DLQ would have received the failed batch, providing an observable signal. Both properties must be corrected: restore the stream ARN in the IAM resource, and restore `MaximumRetryAttempts` to 1.

#### Resolution

Only the template file requires editing. A template-only fix is sufficient here. No handler code changes are needed.

- `corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/known_good.yaml` — In `StreamHandlerRole`'s inline policy, restore the stream read statement's `Resource` from `!GetAtt FriendTable.Arn` to `!GetAtt FriendTable.StreamArn`. This grants `GetRecords`, `DescribeStream`, `GetShardIterator`, and `ListStreams` on the stream ARN, which is required for the event source mapping to poll records.
- `corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/known_good.yaml` — Restore `RequestStateMapping.MaximumRetryAttempts` from `0` to `1`. This ensures that if a polling or invocation failure occurs, the batch is retried once and failed batches are routed to the DLQ for observability, rather than silently discarded.

#### Difficulty

**Rating:** hard

The mapping is enabled, stream records exist, DLQ is empty, and the handler has zero log entries — every observable signal points to health. The IAM resource ARN difference (table vs. stream) is visually subtle, and the zero-retry setting eliminates the one observable artifact (DLQ depth) that would normally signal a polling failure.

---

### FAULT-07 — Front Queue Mapping Batch Window Skips Functional Test Messages via Filter Criteria

**Class:** connectivity
**Type:** coupled
**Chains with:** N/A
**Coupled properties:** `FrontQueueMapping.FilterCriteria` (introduced filter that rejects all messages with `friend_action: Request`), `FrontQueueMapping.BatchSize`
**Fails assertions:** `request_record_written`, `pending_record_created`, `accept_terminal_state`, `read_api_terminal_state`

#### Misconfiguration

Two properties are simultaneously wrong:

1. `FrontQueueMapping` in `known_good.yaml` has a `FilterCriteria` block added (it has none in the correct template). The filter pattern is:
   ```json
   {"body":{"friend_action":["Accept","Reject","Unfriend"]}}
   ```
   This filter passes only Accept, Reject, and Unfriend actions — and silently discards all `Request` action messages. Since the functional test sends a `Request` action first, this message is silently dropped by the ESM filter before FrontHandlerFunction is ever invoked.

2. `FrontQueueMapping.BatchSize` is changed from `5` to `1`. With BatchSize=1, each message is processed individually. This makes it impossible for a model to hypothesize that messages are being batched and delayed — each message is polled immediately. The BatchSize=1 change appears to improve responsiveness and is a plausible "optimization" that a model might not flag as suspicious.

Neither fault alone: with only the filter (BatchSize=5): Request messages are silently dropped. The symptom is the same. With only BatchSize=1 and no filter: all messages including Request are processed normally. The combined scenario makes it harder to reason: BatchSize=1 means the model cannot attribute the silence to "messages waiting to form a batch," and the filter's presence requires inspecting FilterCriteria (which `ace_check_event_source` does not surface — it only shows State and EventSourceArn).

The key diagnostic blind spot: `ace_check_event_source` shows the mapping is `Enabled` with the correct ARN and BatchSize=1, but does NOT show FilterCriteria. A model must use `ace_describe_resource` or read the CloudFormation template to discover the filter.

#### Observable Symptom

`request_record_written` fails — the requester-side Requested item never appears. FrontHandlerFunction logs show zero invocations after the Request message is sent. The queue depth initially shows 1 message, then returns to 0 (the message was consumed by the ESM and discarded by the filter — it does not stay in the queue). The handler is simply never called.

#### Diagnostic Reasoning Path

Step 1 — Send the Request message and check `ace_check_queue_depth` immediately after. The queue shows 0 messages (the ESM consumed the message but the filter discarded it before invocation). Check FrontHandlerFunction logs — no invocations. The message was consumed but the handler was not invoked.

Step 2 — Check the FrontQueueMapping via `ace_check_event_source`. State: `Enabled`, EventSourceArn: correct queue ARN, BatchSize: 1. All looks healthy. The tool does not reveal FilterCriteria.

Step 3 — Use `ace_describe_resource` on `FrontQueueMapping`. The full CloudFormation resource properties are returned, which include the `FilterCriteria` block. The pattern `{"body":{"friend_action":["Accept","Reject","Unfriend"]}}` is visible — `Request` is not in the allowed list. This explains why the message was consumed (ESM polled it) but never delivered to the handler (filter rejected it).

Step 4 — Verify by checking `ace_invoke_lambda` directly with a synthetic SQS event carrying a Request message. The handler runs correctly and writes the Requested item. This confirms the handler itself is healthy — only the ESM filter is blocking delivery.

#### Resolution

Only the template file requires editing. No handler code changes are needed.

- `corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/known_good.yaml` — Remove the `FilterCriteria` block from `FrontQueueMapping` entirely (the correct template has no filter on the front queue). Restore `BatchSize` to `5`. Without a filter, all `friend_action` values including `Request` are delivered to FrontHandlerFunction.

#### Difficulty

**Rating:** medium

The queue drains to zero (the message is consumed by the ESM), which eliminates the "messages stuck in queue" hypothesis immediately. The handler has no invocations, which points to the ESM layer. The diagnostic challenge is that `ace_check_event_source` does not surface FilterCriteria — requiring a less obvious tool call to discover the filter.

---

### FAULT-08 — Read API Returns 502 Due to Missing Lambda Permission and Wrong Stage Name

**Class:** connectivity
**Type:** chained
**Chains with:** FAULT-07 (prerequisite — FAULT-07 must be resolved before the read API failure becomes the primary observable symptom)
**Coupled properties:** `ReadApiPermission.SourceArn` (wrong stage wildcard), `ReadApiStage.StageName` (changed to `v1` instead of `prod`)
**Fails assertions:** `read_api_terminal_state`

#### Misconfiguration

Two properties are simultaneously wrong:

1. `ReadApiStage.StageName` is changed from `prod` to `v1`. API Gateway deploys the stage under the URL path `/v1/` instead of `/prod/`. The stack output `ApiUrl` is computed as `!Sub 'http://${ReadApi}.execute-api.localhost.localstack.cloud:4566/prod'` — it still hardcodes `/prod`, which now points to a non-existent stage. The functional test uses the `ApiUrl` output, so all requests go to `/prod/...` which returns 404.

2. `ReadApiPermission.SourceArn` remains `arn:aws:execute-api:${AWS::Region}:${AWS::AccountId}:${ReadApi}/*/GET/*` — this is a wildcard that covers all stages. So the Lambda permission is not the issue. However, to make the permission a coupled property: the `SourceArn` is changed to `arn:aws:execute-api:${AWS::Region}:${AWS::AccountId}:${ReadApi}/prod/GET/*` — hardcoded to the `prod` stage. Since the stage is now `v1`, the `/prod/` path is gone, and the hardcoded permission for `/prod/` is irrelevant. Even if the client knew to call `/v1/`, the Lambda permission only authorizes `apigateway.amazonaws.com` for the `/prod/` source path, not `/v1/`. So requests to `/v1/` return 403 (Lambda permission denied by API Gateway).

Neither fault alone: with only `StageName=v1` and correct permission (`/*/GET/*`): the `ApiUrl` points to `/prod/` which doesn't exist (404), but if a client guessed the `/v1/` path, the wildcard permission would authorize it. With only the wrong `SourceArn` (`/prod/GET/*`) and `StageName=prod` (correct): the URL works and the permission matches `/prod/` — no fault. Combined: the stage is `v1`, the URL output points to `/prod/` (404), and even if discovered at `/v1/`, the permission only allows `/prod/` (403).

**Chains with FAULT-07:** While FAULT-07 is active (Request messages are filtered and never processed), the state machine never advances past the first step. The functional test fails at `request_record_written`, and `read_api_terminal_state` is never reached. The read API fault (FAULT-08) is completely masked — there is no Friends state to query, so a model cannot distinguish "read API broken" from "no data to read." Only after FAULT-07 is resolved (Request messages processed, state machine advancing to Friends) does the read API failure become observable as the terminal assertion failure.

#### Observable Symptom

After FAULT-07 is resolved, `request_record_written`, `pending_record_created`, and `accept_terminal_state` all pass — both records reach `Friends` state in DynamoDB. But `read_api_terminal_state` fails. The `ApiUrl` output points to a `/prod/` path that returns 404. If a model discovers the correct `/v1/` path and calls it, API Gateway returns 403 (Lambda permission not granted for that stage path).

#### Diagnostic Reasoning Path

Step 1 — Check the stack outputs via `ace_get_stack_outputs`. `ApiUrl` is `http://<api-id>.execute-api.localhost.localstack.cloud:4566/prod`. Call `ace_invoke_endpoint` or `http_get_json` against `/friends/{playerId}` — returns 404. This is unexpected given that DynamoDB has the correct Friends records.

Step 2 — Inspect the `ReadApiStage` resource via `ace_describe_resource`. The stage name is `v1`, not `prod`. The URL `/prod/` does not exist because the deployed stage is `/v1/`. This explains the 404.

Step 3 — Try the correct stage path `/v1/friends/{playerId}` via `ace_invoke_endpoint`. The response is 403. This means the stage exists but Lambda permission denies invocation.

Step 4 — Inspect `ReadApiPermission` via `ace_describe_resource`. The `SourceArn` is `arn:aws:execute-api:.../prod/GET/*` — hardcoded to the `prod` stage. Since the actual stage is `v1`, API Gateway's invocation of Lambda carries a source ARN with `/v1/` in it, which does not match the permission's `/prod/` pattern. Both the stage name and the permission SourceArn must be corrected to match.

#### Resolution

Only the template file requires editing. No handler code changes are needed.

- `corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/known_good.yaml` — Restore `ReadApiStage.StageName` from `v1` to `prod`. This makes the stage accessible at the URL path the `ApiUrl` output references.
- `corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/known_good.yaml` — Restore `ReadApiPermission.SourceArn` to use the wildcard `${ReadApi}/*/GET/*` pattern, or ensure it matches the actual stage name. Using `*` for the stage segment ensures correctness regardless of future stage renames.

#### Difficulty

**Rating:** hard

The symptom (read API 404) appears only after FAULT-07 is resolved, requiring the model to track which faults have been fixed and which remain. The two-fault combination in FAULT-08 itself (stage name mismatch + permission stage scope mismatch) requires two separate observations — one from the stage resource and one from the Lambda permission — before the full picture is clear.

---

### FAULT-09 — Accept State Handler Invoked on Wrong Stream Event Due to Incorrect Filter Insertion Point

**Class:** connectivity
**Type:** chained
**Chains with:** FAULT-01 (prerequisite — while FAULT-01 is active, stream view type is wrong and AcceptStateMapping fires on no records at all; only after FAULT-01 is fixed does this fault's symptom become visible)
**Coupled properties:** `AcceptStateMapping.FilterCriteria.Filters[0].Pattern` (matches INSERT events instead of MODIFY), `RequestStateMapping.FilterCriteria.Filters[0].Pattern` (also matches MODIFY events to accept-state semantics)
**Fails assertions:** `accept_terminal_state`

#### Misconfiguration

Two filter patterns are simultaneously wrong in a way that creates a crossed-wire scenario between RequestStateMapping and AcceptStateMapping:

1. `RequestStateMapping.FilterCriteria.Filters[0].Pattern` is changed from:
   `{"eventName":["INSERT"],"dynamodb":{"NewImage":{"state":{"S":["Requested"]}}}}`
   to:
   `{"eventName":["MODIFY"],"dynamodb":{"NewImage":{"state":{"S":["Requested"]}}}}`
   
   RequestStateHandlerFunction now fires on MODIFY events where state=Requested — but there are no such MODIFY events in normal flow (items transition FROM Requested, not TO Requested). This effectively disables RequestStateHandlerFunction without disabling the mapping.

2. `AcceptStateMapping.FilterCriteria.Filters[0].Pattern` is changed from:
   `{"eventName":["MODIFY"],"dynamodb":{"NewImage":{"state":{"S":["Friends"]}},"OldImage":{"state":{"S":["Pending"]}}}}`
   to:
   `{"eventName":["INSERT"],"dynamodb":{"NewImage":{"state":{"S":["Requested"]}}}}`
   
   AcceptStateHandlerFunction now fires on INSERT events where state=Requested — exactly the events that RequestStateHandlerFunction should handle. AcceptStateHandlerFunction tries to run `_accept_reverse` on a Requested INSERT event, but `_accept_reverse` attempts to update the reverse record from Requested to Friends, and conditions it on `state = Requested`. The reverse record at `(friend_id → player_id)` does not exist yet (it was never created because RequestStateHandlerFunction wasn't invoked). The ConditionalCheckFailedException is silently swallowed.

Combined effect: INSERT/Requested events trigger AcceptStateHandlerFunction (wrong handler), which silently fails to update a non-existent reverse record. RequestStateHandlerFunction never fires (it listens for MODIFY/Requested which never occurs). No Pending record is ever created.

**Chains with FAULT-01:** FAULT-01 sets StreamViewType to NEW_IMAGE, which prevents OldImage-dependent filters from matching. While FAULT-01 is active, AcceptStateMapping's original filter (requiring OldImage) would also fail — but in this chained scenario, AcceptStateMapping's filter was ALSO changed (to INSERT/Requested), so FAULT-01 and FAULT-09 interact differently. Specifically, with FAULT-01 active (NEW_IMAGE), the crossed-wire filters still fire (INSERT/Requested doesn't need OldImage), but AcceptStateHandlerFunction still fails silently. Only after FAULT-01 is fixed (StreamViewType restored) does the diagnostic picture become clearer — because now a model can use `ace_get_stream_records` to see both OldImage and NewImage, making it possible to reason about what each filter should be matching.

#### Observable Symptom

`pending_record_created` fails (no Pending record, because RequestStateHandlerFunction is never invoked on INSERT events). AcceptStateHandlerFunction logs show invocations that complete without error (it fired on the INSERT event but silently failed the conditional update). RequestStateHandlerFunction logs show zero invocations. The DLQ is empty.

#### Diagnostic Reasoning Path

Step 1 — Check both stream handler function logs. AcceptStateHandlerFunction shows invocations (surprising — the accept handler ran before any accept action was sent). RequestStateHandlerFunction shows no invocations. This immediately signals that the filters are crossed — the wrong handler is firing on the INSERT event.

Step 2 — Compare the filter patterns of RequestStateMapping and AcceptStateMapping via `ace_describe_resource` for each. AcceptStateMapping's filter is `eventName=INSERT + state=Requested` — clearly wrong for an accept handler. RequestStateMapping's filter is `eventName=MODIFY + state=Requested` — no MODIFY events with state=Requested exist in the flow. The inversion is now fully visible.

Step 3 — Verify by checking stream records via `ace_get_stream_records`. INSERT records show state=Requested in NewImage. These should match RequestStateMapping's filter but instead match AcceptStateMapping's crossed filter. Cross-referencing the two filter patterns with the stream record event types makes the intended swap unambiguous.

#### Resolution

Only the template file requires editing.

- `corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/known_good.yaml` — Restore `RequestStateMapping.FilterCriteria.Filters[0].Pattern` to `{"eventName":["INSERT"],"dynamodb":{"NewImage":{"state":{"S":["Requested"]}}}}`.
- `corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/known_good.yaml` — Restore `AcceptStateMapping.FilterCriteria.Filters[0].Pattern` to `{"eventName":["MODIFY"],"dynamodb":{"NewImage":{"state":{"S":["Friends"]}},"OldImage":{"state":{"S":["Pending"]}}}}`.

#### Difficulty

**Rating:** medium

The crossed-wire symptom (accept handler fires before any accept action) is a distinctive fingerprint that narrows the search space quickly. The difficulty lies in recognizing the filter patterns must be inspected via `ace_describe_resource` rather than `ace_check_event_source`, and in understanding why AcceptStateHandlerFunction fires silently without producing the expected output.

---

### FAULT-10 — Front Handler Writes Request Record to Wrong Table via Misconfigured Env Var and Broken Conditional

**Class:** data_correctness
**Type:** coupled
**Chains with:** N/A
**Coupled properties:** `FrontHandlerFunction.Environment.Variables.FRIEND_TABLE` (wrong table name pointing to a non-existent table), `front-handler/index.py` `_request` function ConditionExpression attribute name
**Fails assertions:** `request_record_written`, `pending_record_created`, `accept_terminal_state`, `read_api_terminal_state`

#### Misconfiguration

Two properties are simultaneously wrong:

1. In `known_good.yaml`, `FrontHandlerFunction.Environment.Variables.FRIEND_TABLE` is changed from `!Ref FriendTable` to `!Ref FrontQueue`. This resolves to the SQS queue URL (e.g., `http://sqs.us-east-1.localhost.localstack.cloud:4566/000000000000/ace-bench-stack-front-queue`) rather than the DynamoDB table name. When boto3 creates a DynamoDB Table resource with this URL as the table name, any `put_item` call raises a `ResourceNotFoundException` or `ValidationException` because the string is not a valid DynamoDB table name format.

2. In `front-handler/index.py`, the `_request` function's `ConditionExpression` uses a wrong attribute name in the condition:
   - Correct: `ConditionExpression="attribute_not_exists(player_id)"`
   - Wrong: `ConditionExpression="attribute_not_exists(pid)"` — `pid` is not an attribute name in the table schema
   
   With the correct `FRIEND_TABLE`, this wrong condition would still allow the `put_item` to succeed (DynamoDB evaluates `attribute_not_exists(pid)` as `True` since `pid` doesn't exist on any item, so the condition passes). The item is written, but for EVERY Request — including duplicates — because the guard condition no longer prevents re-insertion. However, since the item being written is correct, the functional test passes. The condition bug is dormant when `FRIEND_TABLE` is correct.

   With the wrong `FRIEND_TABLE` (queue URL), the `put_item` raises an exception before the condition is even evaluated. The handler catches the exception in the `_request` function but `_request` does not have a try/except — the exception propagates to the `handler` function's per-record try/except, which appends the record to `batchItemFailures`. Lambda's ESM retries after `MaximumRetryAttempts` (none configured for FrontQueueMapping — it defaults to the SQS redrive policy). The message is retried indefinitely, the queue fills up with repeated attempts, and the handler never succeeds.

Neither fault alone: with only the wrong `FRIEND_TABLE` (queue URL) and correct condition: the handler always fails with `ResourceNotFoundException`, the message retries, the test times out waiting for `request_record_written`. With only the wrong condition (`attribute_not_exists(pid)`) and correct table name: all Request actions succeed (condition always True), but duplicate requests would both write — a latent idempotency issue, not a primary assertion failure during the single-pair test.

Combined: the wrong `FRIEND_TABLE` causes complete failure. The condition bug is a latent defect that is exposed only after the table name is corrected — it doesn't prevent the item write but breaks deduplication semantics. A model fixing only the table name restores basic functionality but leaves the condition bug, which may cause idempotency failures under concurrent load (not caught by the sequential functional test). The scenario requires the model to fix both because the condition bug is the reason a template-only fix is insufficient.

#### Observable Symptom

`request_record_written` fails — the Requested item never appears in the table. FrontHandlerFunction logs show errors: each invocation logs a `ResourceNotFoundException` or `ValidationException` for the `put_item` call with the queue URL as the table name, and returns a `batchItemFailures` list containing the message's `messageId`. The queue depth grows as the ESM retries the message.

#### Diagnostic Reasoning Path

Step 1 — Check FrontHandlerFunction log tail via `ace_get_log_tail`. Errors appear: `ResourceNotFoundException: Requested resource not found` or `Invalid table name`. The error message contains the queue URL string being used as the table name.

Step 2 — Check the FrontHandlerFunction environment variables via `ace_get_environment_variables`. `FRIEND_TABLE` value is the SQS queue URL (starting with `http://` or matching the queue URL format). This is clearly wrong — the value should be the table name string, not a URL.

Step 3 — Cross-reference with the stack outputs via `ace_get_stack_outputs`. `TableName` is the correct table name; `QueueUrl` is the queue URL. The `FRIEND_TABLE` value matches `QueueUrl`, not `TableName`. This confirms the CloudFormation `!Ref` points to the wrong resource.

Step 4 — After identifying the env var bug, examine the handler source code. The `_request` function's `ConditionExpression` uses `attribute_not_exists(pid)` — `pid` is not a valid attribute in the table schema. The correct attribute is `player_id`. With the table name fixed, the function runs but the condition `attribute_not_exists(pid)` always evaluates to True (since `pid` never exists), so every request is written even if a duplicate — a silent idempotency defect. Both the template (env var) and handler code (condition expression) must be fixed.

#### Resolution

Both files must be edited. A template-only fix is insufficient: restoring `FRIEND_TABLE` to `!Ref FriendTable` makes the function work for the sequential test, but the broken condition expression `attribute_not_exists(pid)` silently accepts all duplicate requests. A code-only fix is insufficient: fixing the condition but leaving `FRIEND_TABLE` as the queue URL causes all requests to fail with `ResourceNotFoundException`.

- `corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/known_good.yaml` — Restore `FrontHandlerFunction.Environment.Variables.FRIEND_TABLE` from `!Ref FrontQueue` to `!Ref FriendTable`. This provides the correct DynamoDB table name to the handler.
- `corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/deployment/lambda/front-handler/index.py` — In the `_request` function, restore `ConditionExpression="attribute_not_exists(player_id)"`. The condition must reference the actual HASH key attribute name (`player_id`) to correctly prevent duplicate Request inserts.

#### Difficulty

**Rating:** medium

The error message in logs directly contains the queue URL as the table name, making the wrong env var highly visible. The latent condition bug is subtler — it requires reading the handler code and recognizing that `pid` is not a schema attribute, and understanding that the condition always evaluating True constitutes a correctness defect even when the primary test passes.
