## System overview

This system manages friendship relationships between players in a gaming platform. Players send friend requests and acceptances through a message queue. The system processes each message and updates relationship records accordingly. Under normal load, messages are processed exactly once and the system advances relationships through their expected states reliably.

## What you have access to

A deployed instance of this system is running in your local environment. The CloudFormation template (`faulted.yaml`) and all supporting deployment files are available to you directly. Diagnostic tools are available via MCP to probe the running system. The system deployed successfully.

## Reported symptom

The system behaves correctly on most runs but exhibits non-deterministic failures under any added latency. When the processing function takes longer than usual to complete, the same message is picked up and processed a second time before the first invocation finishes. This causes duplicate processing: two concurrent invocations attempt the same state transition, one succeeds and one silently fails its conditional check. Occasionally this race causes the final "Friends" state to never be confirmed because a duplicate invocation processed a stale message. Logs show multiple concurrent invocations of the same handler processing identical message bodies.

## What correct behavior looks like

Each message must be processed exactly once. The message queue must not make a message available to additional consumers while a handler invocation is still running. The queue's re-delivery window must be at least as long as the maximum time the processing function is allowed to run, so that a message only becomes visible again if the function failed to complete entirely.
