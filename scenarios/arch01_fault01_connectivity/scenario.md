## System overview

This system manages friendship relationships between players in a gaming platform. Players send friend requests and accept them through a message queue. When a request arrives, the system records it and then automatically creates a mirrored "pending" entry for the receiving player. When the receiving player accepts, the system updates both sides of the relationship to "Friends." The entire relationship lifecycle — requested, pending, accepted — is driven by database change events that trigger downstream handlers.

## What you have access to

A deployed instance of this system is running in your local environment. The CloudFormation template (`faulted.yaml`) and all supporting deployment files are available to you directly. Diagnostic tools are available via MCP to probe the running system. The system deployed successfully.

## Reported symptom

Friend requests are submitted and the requester-side record reaches "Requested" state correctly. The receiving player's "Pending" record is created. However, after the receiving player sends an Accept action, the requester-side record never transitions to "Friends" — it stays stuck in "Requested" indefinitely. The read API shows the receiver's record as "Friends" but the requester's record remains "Requested."

## What correct behavior looks like

After a complete Request → Accept cycle, both sides of the relationship should show "Friends" state. The requester-side record (keyed by the original requester's ID) must transition from "Requested" to "Friends" when the accept is processed. The read API should return "Friends" for both directions of the relationship.
