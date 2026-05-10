## System overview

This system manages friendship relationships between players in a gaming platform. Players send friend requests through a message queue. When a request arrives, the system creates a "pending" entry for the receiving player to act on. If processing fails, failed items are routed to a dead-letter queue for investigation.

## What you have access to

A deployed instance of this system is running in your local environment. The CloudFormation template (`faulted.yaml`) and all supporting deployment files are available to you directly. Diagnostic tools are available via MCP to probe the running system. The system deployed successfully.

## Reported symptom

Friend requests are submitted and the requester-side record reaches "Requested" state. However, the receiver-side "Pending" record is never created. The request handler appears to run successfully — logs show no errors, the event source mapping reports the batch as processed, and no messages appear in the dead-letter queue. The failure is completely silent: the handler reports success even though no data was written.

## What correct behavior looks like

After a Request is sent, a "Pending" record must appear for the receiving player. If the handler encounters an infrastructure error it cannot recover from, that error should propagate so the batch item is retried and eventually routed to the dead-letter queue. Silent success-reporting on write failure is incorrect behavior.
