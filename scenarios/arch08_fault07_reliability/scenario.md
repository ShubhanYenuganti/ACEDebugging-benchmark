# Scenario: arch08_fault07_reliability

## System overview

A job lifecycle platform receives job creation and management commands and fans out ordered event notifications to two downstream processors: an analytics archiver that writes event records to object storage, and an inventory tracker that maintains the terminal state of each job in a database. The inventory tracker is configured with a redrive policy that moves messages to a dead-letter queue after a defined number of failed receive attempts.

## What you have access to

A deployed instance of this system is running in your local environment. The CloudFormation template (`faulted.yaml`) and all supporting deployment files are available to you directly. Diagnostic tools are available via MCP to probe the running system. The system deployed successfully.

## Reported symptom

After a test job is submitted, the analytics archiver correctly writes records to object storage. The inventory queue initially receives messages, but they quickly disappear from the main queue and accumulate in the dead-letter queue without being processed. The inventory data store contains no record for the test job. There may be no Lambda errors in the logs, or only a single invocation attempt visible before all messages are dead-lettered.

## What correct behavior looks like

After a job is submitted, the inventory tracker should receive the creation and deletion events, successfully process each event, write the initial job record to the database, and mark the record as deleted. Messages should be deleted from the queue after successful processing and should not appear in the dead-letter queue during normal operation.
