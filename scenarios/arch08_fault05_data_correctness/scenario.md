# Scenario: arch08_fault05_data_correctness

## System overview

A job lifecycle platform receives job creation and management commands and fans out ordered event notifications to two downstream processors: an analytics archiver that writes event records to object storage, and an inventory tracker that maintains the terminal state of each job in a database. The event publisher encodes job event payloads as structured data and sends them to a shared ordered topic. The inventory tracker reads these payloads to extract the job identifier and event type, then updates the database accordingly.

## What you have access to

A deployed instance of this system is running in your local environment. The CloudFormation template (`faulted.yaml`) and all supporting deployment files are available to you directly. Diagnostic tools are available via MCP to probe the running system. The system deployed successfully.

## Reported symptom

After a test job is submitted, the analytics archiver correctly writes records to object storage. The inventory queue receives messages and the processing function is invoked. However, the inventory data store contains no record for the test job after the test window elapses. The processing function logs show type errors — the code attempts to access a field on a value that is a string rather than a structured object.

## What correct behavior looks like

After a job is submitted, the inventory tracker should receive the creation and deletion events, extract the job identifier and event type from the payload, write the initial job record to the database, and mark the record as deleted — leaving a terminal deletion marker on the database item.
