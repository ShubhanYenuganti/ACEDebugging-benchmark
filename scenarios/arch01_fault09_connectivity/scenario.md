## System overview

This system manages friendship relationships between players in a gaming platform. Players send friend requests and acceptances through a message queue. Record changes are streamed to separate handlers that react to specific state transitions — one handler creates reciprocal pending entries when a new request is recorded, and a different handler promotes both records to the final "Friends" state when a request is accepted.

## What you have access to

A deployed instance of this system is running in your local environment. The CloudFormation template (`faulted.yaml`) and all supporting deployment files are available to you directly. Diagnostic tools are available via MCP to probe the running system. The system deployed successfully.

## Reported symptom

After a friend request is submitted, the receiver-side Pending record is never created. Logs reveal an unexpected pattern: the handler responsible for promoting records to "Friends" state (the accept handler) fires immediately after the request is submitted — before any accept action is sent — while the handler that should be creating the Pending record (the request handler) shows zero invocations. The accept handler completes without error but produces no visible output.

## What correct behavior looks like

When a new request record is inserted, only the request-processing handler must fire. When a record transitions to "Friends" state, only the accept-processing handler must fire. Each handler must be wired to exactly the event type it is designed to process. No handler should fire on events intended for a different handler.
