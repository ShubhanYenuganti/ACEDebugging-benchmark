## System overview

This system accepts job postings from an internal service and fans them out as ordered events to two downstream consumers. One consumer tracks the current state of each job in a structured data store, recording when a job is created and marking it as deleted when removed. A second consumer archives all job events to object storage for analytics and reporting. The producer publishes three events per job: creation, salary update, and deletion. Both consumers receive events through subscriptions on a shared ordered topic.

## What you have access to

A deployed instance of this system is running in your local environment. The CloudFormation template (`faulted.yaml`) and all supporting deployment files are available to you directly. Diagnostic tools are available via MCP to probe the running system. The system deployed successfully.

## Reported symptom

The analytics consumer is working correctly — objects appear in object storage after each producer invocation. However, the inventory data store never receives any records. After invoking the producer with a test job identifier, the inventory store remains empty. The inventory consumer's logs show that the function is being invoked and completing successfully with no errors — it processes the messages without raising any exceptions — but the data store is never written to.

## What correct behavior looks like

After a producer invocation with a given job identifier, the inventory data store should contain a record for that job with `markAsDeleted` set to `true`, reflecting that the system processed both the creation and deletion events in order.
