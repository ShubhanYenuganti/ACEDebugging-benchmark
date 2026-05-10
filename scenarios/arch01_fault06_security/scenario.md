## System overview

This system manages friendship relationships between players in a gaming platform. Players send friend requests through a message queue. The system stores relationship records and reacts to record changes by creating reciprocal entries. A shared execution role governs which data sources all stream-processing functions are authorized to read.

## What you have access to

A deployed instance of this system is running in your local environment. The CloudFormation template (`faulted.yaml`) and all supporting deployment files are available to you directly. Diagnostic tools are available via MCP to probe the running system. The system deployed successfully.

## Reported symptom

After a friend request is submitted, the requester-side record appears correctly. However, the receiver-side Pending record is never created. The stream-processing handler responsible for creating the Pending record shows zero invocations in its logs — no errors, no activity at all. The dead-letter queue has zero messages. The event source mapping reports its state as enabled and its source configuration appears correct. There is no observable error signal anywhere in the system.

## What correct behavior looks like

After a friend request is submitted and the requester-side record is written, the stream-processing handler must be invoked to create the receiver-side Pending record. If a permissions error prevents the handler from reading the change stream, that failure must be retried and eventually routed to the dead-letter queue so it is observable. A configuration where failures are silently discarded with no retry and no dead-letter routing is incorrect.
