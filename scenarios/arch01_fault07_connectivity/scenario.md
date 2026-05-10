## System overview

This system manages friendship relationships between players in a gaming platform. Players send friend requests and other relationship actions through a message queue. A processing function reads messages from the queue and applies the corresponding state change to the relationship store. All action types — requests, acceptances, rejections, and unfriend actions — flow through this same queue.

## What you have access to

A deployed instance of this system is running in your local environment. The CloudFormation template (`faulted.yaml`) and all supporting deployment files are available to you directly. Diagnostic tools are available via MCP to probe the running system. The system deployed successfully.

## Reported symptom

When a Request action is sent, the message is consumed from the queue — the queue depth returns to zero immediately — but the requester-side record never appears in the data store. The queue processing function shows zero invocations in its logs after the message is sent. The function itself is healthy and responds correctly when invoked directly with a synthetic Request message.

## What correct behavior looks like

All friend action types, including Request, must be delivered to the processing function for handling. No action type should be silently filtered out before reaching the function. The queue-to-function connection must pass all messages regardless of their action field value.
