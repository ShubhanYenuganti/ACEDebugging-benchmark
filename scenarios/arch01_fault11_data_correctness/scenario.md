# Scenario: arch01_fault11_data_correctness

## Architecture
Serverless friend microservices on AWS: SQS → FrontHandlerFunction (Lambda) → DynamoDB (FriendTable) → DynamoDB Streams → multiple state-handler Lambdas → DynamoDB. An API Gateway (ReadApi) exposes friend relationship reads.

## Observed Symptom
After a player sends a `Request` friend action:
- The **requester-side** DynamoDB record is written correctly with state `Requested`.
- The **receiver-side** record is **never written**. The receiver does not see an incoming request; no `Pending` record appears in DynamoDB for their side of the relationship.
- All infrastructure resources deploy successfully (`CREATE_COMPLETE`).
- Logs and metrics show `RequestStateHandlerFunction` is being invoked (it receives the DynamoDB stream event), but the downstream operation fails silently.

The `pending_record_created` functional test assertion fails: the receiver-side item never reaches `Pending` state within the wait timeout.

## What Is Working
- SQS ingestion and FrontHandlerFunction delivery are healthy.
- The requester-side `Requested` record is written correctly by FrontHandlerFunction.
- DynamoDB stream events on the `Requested` INSERT are delivered to RequestStateHandlerFunction.
- All other state handlers (AcceptStateHandlerFunction, RejectStateHandlerFunction) appear unaffected.

## What to Diagnose
Determine why `RequestStateHandlerFunction` fails to write the receiver-side `Pending` record. The function is invoked, reaches the downstream DynamoDB call, but that call does not succeed. Identify the specific operation and resource that is faulting, and determine what configuration change is needed to restore the reciprocal write.

X-Ray tracing is active on all Lambda functions. Trace data is available via `ace_get_trace_summaries` and `ace_get_trace`.
