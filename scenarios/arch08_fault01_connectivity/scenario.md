## System overview

This system accepts job postings from an internal service and fans them out as ordered events to two downstream consumers. One consumer tracks the current state of each job in a structured data store, recording when a job is created and marking it as deleted when removed. A second consumer archives all job events to object storage for analytics and reporting. The producer publishes three events per job: creation, salary update, and deletion. Both consumers are configured to receive events from a shared ordered topic, but the inventory consumer applies a subscription filter so it only receives creation and deletion events — not salary updates.

## What you have access to

A deployed instance of this system is running in your local environment. The CloudFormation template (`faulted.yaml`) and all supporting deployment files are available to you directly. Diagnostic tools are available via MCP to probe the running system. The system deployed successfully.

## Reported symptom

The analytics consumer is working correctly — objects appear in object storage after each producer invocation. However, the inventory data store never receives any records. After invoking the producer with a test job identifier, the inventory store remains empty for that job. No errors appear in the inventory consumer's logs. The subscription delivering events to the inventory consumer appears healthy at the topic level, but zero messages are arriving in the inventory queue.

## What correct behavior looks like

After a producer invocation with a given job identifier, the inventory data store should contain a record for that job with `markAsDeleted` set to `true`, reflecting that the system processed both the creation and deletion events in order. The inventory queue should receive exactly the creation and deletion events (not the salary update event) for each job.
