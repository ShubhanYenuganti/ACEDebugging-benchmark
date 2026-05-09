# Traffic Flow - Serverless Microservices with API Gateway, DynamoDB, SQS, and Lambda

## Architecture summary
The system processes friend relationship commands asynchronously and exposes current relationship state through read endpoints. State transitions create mirrored records so each player can query their view of the relationship.

## Correct end-to-end flow

1. **GameBackendProducer** -> **FrontQueue**
   Mechanism: SDK call
   Required permission: sqs:SendMessage on arn:aws:sqs:${AWS::Region}:${AWS::AccountId}:${FrontQueue}
   A backend producer submits a friend action message containing `player_id`, `friend_id`, and `friend_action`.

2. **FrontQueue** -> **FrontHandlerFunction**
   Mechanism: polling
   Required permission: lambda:InvokeFunction on arn:aws:lambda:${AWS::Region}:${AWS::AccountId}:function:${FrontHandlerFunction}
   The event source mapping polls queued messages and invokes the command handler in batches.

3. **FrontHandlerFunction** -> **FriendTable**
   Mechanism: SDK call
   Required permission: dynamodb:PutItem or dynamodb:UpdateItem on arn:aws:dynamodb:${AWS::Region}:${AWS::AccountId}:table/${FriendTable}
   For a request action, the handler writes the requester's relationship record with state `Requested`; for accept, reject, and unfriend actions it updates or deletes the caller's relationship record.

4. **FriendTable Stream** -> **RequestStateHandlerFunction**
   Mechanism: event trigger
   Required permission: lambda:InvokeFunction on arn:aws:lambda:${AWS::Region}:${AWS::AccountId}:function:${RequestStateHandlerFunction}
   Inserted `Requested` records trigger creation of the reciprocal receiver-side `Pending` record.

5. **RequestStateHandlerFunction** -> **FriendTable**
   Mechanism: SDK call
   Required permission: dynamodb:PutItem on arn:aws:dynamodb:${AWS::Region}:${AWS::AccountId}:table/${FriendTable}
   The handler writes the reverse relationship item so the receiving player can see the incoming request.

6. **GameBackendProducer** -> **FrontQueue**
   Mechanism: SDK call
   Required permission: sqs:SendMessage on arn:aws:sqs:${AWS::Region}:${AWS::AccountId}:${FrontQueue}
   A backend producer submits an accept action from the receiver to the requester.

7. **FrontQueue** -> **FrontHandlerFunction**
   Mechanism: polling
   Required permission: lambda:InvokeFunction on arn:aws:lambda:${AWS::Region}:${AWS::AccountId}:function:${FrontHandlerFunction}
   The same queue mapping invokes the command handler for the accept message.

8. **FrontHandlerFunction** -> **FriendTable**
   Mechanism: SDK call
   Required permission: dynamodb:UpdateItem on arn:aws:dynamodb:${AWS::Region}:${AWS::AccountId}:table/${FriendTable}
   The handler changes the receiver-side record from `Pending` to `Friends`.

9. **FriendTable Stream** -> **AcceptStateHandlerFunction**
   Mechanism: event trigger
   Required permission: lambda:InvokeFunction on arn:aws:lambda:${AWS::Region}:${AWS::AccountId}:function:${AcceptStateHandlerFunction}
   A stream modification from `Pending` to `Friends` triggers the reciprocal accept handler.

10. **AcceptStateHandlerFunction** -> **FriendTable**
    Mechanism: SDK call
    Required permission: dynamodb:UpdateItem on arn:aws:dynamodb:${AWS::Region}:${AWS::AccountId}:table/${FriendTable}
    The handler changes the requester-side record from `Requested` to `Friends`.

11. **GameClient** -> **ReadApi (GET /friends/{playerId})**
    Mechanism: HTTP proxy
    Required permission: execute-api:Invoke on arn:aws:execute-api:${AWS::Region}:${AWS::AccountId}:${ReadApi}/*/GET/friends/*
    A client requests the current relationship list for a player.

12. **ReadApi** -> **ReadHandlerFunction**
    Mechanism: event trigger
    Required permission: lambda:InvokeFunction on arn:aws:lambda:${AWS::Region}:${AWS::AccountId}:function:${ReadHandlerFunction}
    API Gateway forwards the read request to the query handler.

13. **ReadHandlerFunction** -> **FriendTable**
    Mechanism: SDK call
    Required permission: dynamodb:Query on arn:aws:dynamodb:${AWS::Region}:${AWS::AccountId}:table/${FriendTable}
    The handler queries all relationship records for the requested player and returns them to the caller.

## Integration dependencies

| From | To | Mechanism | Required permission |
|---|---|---|---|
| GameBackendProducer | FrontQueue | SDK call | sqs:SendMessage |
| FrontQueue | FrontHandlerFunction | polling | lambda:InvokeFunction |
| FrontHandlerFunction | FriendTable | SDK call | dynamodb:PutItem, dynamodb:UpdateItem, dynamodb:DeleteItem |
| FriendTable Stream | RequestStateHandlerFunction | event trigger | lambda:InvokeFunction |
| RequestStateHandlerFunction | FriendTable | SDK call | dynamodb:PutItem |
| GameBackendProducer | FrontQueue | SDK call | sqs:SendMessage |
| FrontQueue | FrontHandlerFunction | polling | lambda:InvokeFunction |
| FrontHandlerFunction | FriendTable | SDK call | dynamodb:UpdateItem |
| FriendTable Stream | AcceptStateHandlerFunction | event trigger | lambda:InvokeFunction |
| AcceptStateHandlerFunction | FriendTable | SDK call | dynamodb:UpdateItem |
| GameClient | ReadApi (GET /friends/{playerId}) | HTTP proxy | execute-api:Invoke |
| ReadApi | ReadHandlerFunction | event trigger | lambda:InvokeFunction |
| ReadHandlerFunction | FriendTable | SDK call | dynamodb:Query |

## What breaks at each hop

**Hop 1 broken:** `SendMessage` returns an authorization or queue-not-found error and `ApproximateNumberOfMessagesVisible` does not increase.

**Hop 2 broken:** Queue depth remains nonzero while Lambda invocation metrics for `FrontHandlerFunction` stay flat.

**Hop 3 broken:** `FrontHandlerFunction` logs `AccessDeniedException`, `ConditionalCheckFailedException`, or DynamoDB write errors and no `Requested` item appears.

**Hop 4 broken:** The `Requested` item exists but `RequestStateHandlerFunction` invocation metrics remain flat for table stream records.

**Hop 5 broken:** The requester item exists but the receiver-side `Pending` item never appears; handler logs show DynamoDB write or conditional errors.

**Hop 6 broken:** The accept action cannot be enqueued and SQS API calls return authorization or queue URL errors.

**Hop 7 broken:** Accept messages remain visible in the queue while `FrontHandlerFunction` invocation metrics do not increase.

**Hop 8 broken:** The receiver-side item remains `Pending`; handler logs show conditional update failures or DynamoDB access errors.

**Hop 9 broken:** The receiver item becomes `Friends` but `AcceptStateHandlerFunction` invocations remain flat for table stream modifications.

**Hop 10 broken:** The requester-side item remains `Requested` while the receiver-side item is `Friends`, indicating reciprocal update failure.

**Hop 11 broken:** The client receives HTTP 403/404/5xx from the read endpoint and API Gateway access metrics show failed requests.

**Hop 12 broken:** API Gateway returns 502 and execution logs indicate a Lambda integration permission or invocation failure.

**Hop 13 broken:** The read endpoint returns an empty or error response despite table records existing; `ReadHandlerFunction` logs show DynamoDB query failures.
