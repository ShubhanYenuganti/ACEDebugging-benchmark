# Traffic Flow — Order Processing Pipeline

## Architecture summary
A synchronous API accepts order submissions and acknowledges them
immediately. Orders are asynchronously processed by a background
function that persists the result to a data store.

## Correct end-to-end flow

1. **Client → API Gateway**
   A POST request arrives at `/orders` with a JSON body containing
   `order_id`, `item`, and `quantity`.

2. **API Gateway → Ingestion Lambda (ace-bench-ingestion)**
   API Gateway proxies the request to the ingestion function via
   AWS_PROXY integration. The function receives the full HTTP event.

3. **Ingestion Lambda → SQS (ace-bench-order-queue)**
   The ingestion function validates the payload, constructs a
   message containing the order fields, and sends it to the order
   queue via `sqs:SendMessage`. It then returns HTTP 200 to the
   caller with an acknowledgment body.

4. **SQS → Processor Lambda (ace-bench-processor)**
   The event source mapping polls the queue and triggers the
   processor function with a batch of up to 10 messages. The
   mapping must be Enabled: true for this trigger to fire.

5. **Processor Lambda → DynamoDB (ace-bench-orders)**
   The processor function deserialises each message, enriches
   the record with `status: fulfilled` and `processed_at`
   (ISO timestamp), and writes the full record to DynamoDB via
   `dynamodb:PutItem`. The partition key is `order_id`.

6. **Terminal state**
   A correctly processed order is readable in DynamoDB within
   seconds of submission. The record contains all fields from
   the original payload plus `status` and `processed_at`.

## Integration dependencies

| From | To | Mechanism | Required permission |
|---|---|---|---|
| API Gateway | Ingestion Lambda | AWS_PROXY integration | lambda:InvokeFunction (from API GW principal) |
| Ingestion Lambda | SQS | SDK call | sqs:SendMessage on queue ARN |
| SQS | Processor Lambda | EventSourceMapping | sqs:ReceiveMessage, sqs:DeleteMessage, sqs:GetQueueAttributes |
| Processor Lambda | DynamoDB | SDK call | dynamodb:PutItem on table ARN |

## What breaks at each hop

**Hop 2 broken:** API returns 5xx or timeout. Ingestion function not
invoked. Visible via `ace_invoke_endpoint` returning non-200.

**Hop 3 broken:** API returns 200 (ingestion ran) but queue depth
does not grow. Visible via `ace_check_queue_depth` returning zero
after a submission.

**Hop 4 broken:** Queue depth grows but processor never runs. Messages
accumulate. Visible via `ace_check_queue_depth` showing high
`messages_available` and zero `messages_in_flight` after wait,
and via `ace_check_event_source` returning no enabled mappings.

**Hop 5 broken:** Processor runs but write fails. Visible via
`ace_invoke_lambda` returning an error body, `ace_get_log_tail`
showing an exception, and `ace_read_table_item` returning null.
