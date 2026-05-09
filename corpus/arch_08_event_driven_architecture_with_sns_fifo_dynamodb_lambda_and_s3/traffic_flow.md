# Traffic Flow - Event-driven architecture with SNS FIFO, DynamoDB, Lambda, and S3

## Architecture summary
An event producer publishes ordered job events to a FIFO topic. Analytics and inventory consumers receive filtered copies through FIFO queues and persist their own views.

## Correct end-to-end flow

1. **TestOrScheduler** -> **AntiCorruptionFunction**
   Mechanism: event trigger
   Required permission: lambda:InvokeFunction on arn:aws:lambda:${AWS::Region}:${AWS::AccountId}:function:${AntiCorruptionFunction}
   The producer function creates job-created, job-updated, and job-deleted events for one job.

2. **AntiCorruptionFunction** -> **JobEventsTopic**
   Mechanism: SDK call
   Required permission: sns:Publish on arn:aws:sns:${AWS::Region}:${AWS::AccountId}:${JobEventsTopic}
   The function publishes each event to the FIFO topic with a message group and deduplication ID.

3. **JobEventsTopic** -> **AnalyticsJobEventsQueue**
   Mechanism: event trigger
   Required permission: sqs:SendMessage on arn:aws:sqs:${AWS::Region}:${AWS::AccountId}:${AnalyticsJobEventsQueue}
   SNS fanout sends every raw job event to the analytics FIFO queue.

4. **AnalyticsJobEventsQueue** -> **AnalyticsFunction**
   Mechanism: polling
   Required permission: lambda:InvokeFunction on arn:aws:lambda:${AWS::Region}:${AWS::AccountId}:function:${AnalyticsFunction}
   The analytics Lambda consumes queued events in batches.

5. **AnalyticsFunction** -> **AnalyticsBucket**
   Mechanism: SDK call
   Required permission: s3:PutObject on arn:aws:s3:::${AnalyticsBucket}/*
   The function stores a JSON record of consumed events for long-term analytics.

6. **JobEventsTopic** -> **InventoryJobEventsQueue**
   Mechanism: event trigger
   Required permission: sqs:SendMessage on arn:aws:sqs:${AWS::Region}:${AWS::AccountId}:${InventoryJobEventsQueue}
   SNS fanout delivers only inventory-relevant `JobCreated` and `JobDeleted` events to the inventory FIFO queue.

7. **InventoryJobEventsQueue** -> **InventoryFunction**
   Mechanism: polling
   Required permission: lambda:InvokeFunction on arn:aws:lambda:${AWS::Region}:${AWS::AccountId}:function:${InventoryFunction}
   The inventory Lambda consumes filtered job events.

8. **InventoryFunction** -> **InventoryTable**
   Mechanism: SDK call
   Required permission: dynamodb:PutItem or dynamodb:UpdateItem on arn:aws:dynamodb:${AWS::Region}:${AWS::AccountId}:table/${InventoryTable}
   Job-created events create inventory records and job-deleted events mark records as deleted.

## Integration dependencies

| From | To | Mechanism | Required permission |
|---|---|---|---|
| TestOrScheduler | AntiCorruptionFunction | event trigger | lambda:InvokeFunction |
| AntiCorruptionFunction | JobEventsTopic | SDK call | sns:Publish |
| JobEventsTopic | AnalyticsJobEventsQueue | event trigger | sqs:SendMessage |
| AnalyticsJobEventsQueue | AnalyticsFunction | polling | lambda:InvokeFunction |
| AnalyticsFunction | AnalyticsBucket | SDK call | s3:PutObject |
| JobEventsTopic | InventoryJobEventsQueue | event trigger | sqs:SendMessage |
| InventoryJobEventsQueue | InventoryFunction | polling | lambda:InvokeFunction |
| InventoryFunction | InventoryTable | SDK call | dynamodb:PutItem, dynamodb:UpdateItem |

## What breaks at each hop

**Hop 1 broken:** Invocations return Lambda errors and no SNS publish calls are visible for the producer.

**Hop 2 broken:** The producer logs `AuthorizationError` or `NotFound` for `sns:Publish`, and both subscribed queues remain empty.

**Hop 3 broken:** Analytics queue depth stays at zero after publish while SNS delivery failure metrics rise.

**Hop 4 broken:** Analytics queue visible messages increase while `AnalyticsFunction` invocations remain flat.

**Hop 5 broken:** Analytics Lambda logs S3 `PutObject` errors and no analytics object appears in the bucket.

**Hop 6 broken:** Inventory queue receives no `JobCreated` or `JobDeleted` messages and SNS filter/delivery metrics show no matches or delivery failures.

**Hop 7 broken:** Inventory queue visible messages increase while `InventoryFunction` invocations remain flat.

**Hop 8 broken:** Inventory Lambda logs DynamoDB write errors and the expected job item is absent or lacks the deletion marker.
