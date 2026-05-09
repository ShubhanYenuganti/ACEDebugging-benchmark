# Traffic Flow - Event-driven architecture with SQS, Lambda, DynamoDB, and S3

## Architecture summary
CSV inventory files are uploaded to object storage, parsed into queue messages, and persisted as inventory update records.

## Correct end-to-end flow

1. **InventoryUploader** -> **InventoryUpdatesBucket**
   Mechanism: SDK call
   Required permission: s3:PutObject on arn:aws:s3:::${InventoryUpdatesBucket}/*
   A CSV file containing inventory update rows is uploaded to the source bucket.

2. **InventoryUpdatesBucket** -> **CSVProcessingToSQSFunction**
   Mechanism: event trigger
   Required permission: lambda:InvokeFunction on arn:aws:lambda:${AWS::Region}:${AWS::AccountId}:function:${CSVProcessingToSQSFunction}
   S3 object-created notification invokes the parser Lambda.

3. **CSVProcessingToSQSFunction** -> **InventoryUpdatesQueue**
   Mechanism: SDK call
   Required permission: sqs:SendMessageBatch on arn:aws:sqs:${AWS::Region}:${AWS::AccountId}:${InventoryUpdatesQueue}
   The parser reads the CSV object and sends each row as a JSON queue message.

4. **InventoryUpdatesQueue** -> **SQSToDynamoDBFunction**
   Mechanism: polling
   Required permission: lambda:InvokeFunction on arn:aws:lambda:${AWS::Region}:${AWS::AccountId}:function:${SQSToDynamoDBFunction}
   The consumer Lambda is invoked for queued inventory update batches.

5. **SQSToDynamoDBFunction** -> **InventoryUpdatesTable**
   Mechanism: SDK call
   Required permission: dynamodb:PutItem on arn:aws:dynamodb:${AWS::Region}:${AWS::AccountId}:table/${InventoryUpdatesTable}
   Each queued row is written to the inventory updates table with product, location, quantity, and date fields.

## Integration dependencies

| From | To | Mechanism | Required permission |
|---|---|---|---|
| InventoryUploader | InventoryUpdatesBucket | SDK call | s3:PutObject |
| InventoryUpdatesBucket | CSVProcessingToSQSFunction | event trigger | lambda:InvokeFunction |
| CSVProcessingToSQSFunction | InventoryUpdatesQueue | SDK call | sqs:SendMessageBatch |
| InventoryUpdatesQueue | SQSToDynamoDBFunction | polling | lambda:InvokeFunction |
| SQSToDynamoDBFunction | InventoryUpdatesTable | SDK call | dynamodb:PutItem |

## What breaks at each hop

**Hop 1 broken:** Upload calls return S3 access errors and the source object is absent from the bucket.

**Hop 2 broken:** The CSV object exists but parser Lambda invocation metrics remain flat.

**Hop 3 broken:** Parser logs SQS send errors and queue depth never increases after upload.

**Hop 4 broken:** Queue visible messages increase while consumer Lambda invocation metrics stay flat.

**Hop 5 broken:** Consumer Lambda logs DynamoDB write errors and expected product records never appear in the table.
