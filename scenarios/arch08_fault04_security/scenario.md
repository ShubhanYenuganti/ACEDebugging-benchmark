# Scenario: arch08_fault04_security

## System overview

A job lifecycle platform receives job creation and management commands and fans out ordered event notifications to two downstream processors: an analytics archiver that writes event records to object storage, and an inventory tracker that maintains the terminal state of each job in a database. The inventory tracker processes three event types per job — creation, salary update (which it ignores), and deletion — writing the initial record on creation and marking the record as deleted on deletion.

## What you have access to

A deployed instance of this system is running in your local environment. The CloudFormation template (`faulted.yaml`) and all supporting deployment files are available to you directly. Diagnostic tools are available via MCP to probe the running system. The system deployed successfully.

## Reported symptom

After a test job is submitted, the analytics archiver correctly writes records to object storage. The inventory queue receives messages and drains normally — the processing function is invoked. However, the inventory data store contains no record for the test job after the test window elapses. The processing function logs show access denial errors when attempting to write to the database.

## What correct behavior looks like

After a job is submitted, the inventory tracker should successfully write the initial job record to the database when processing the creation event, then mark the record as deleted when processing the deletion event. The database item should exist with a terminal deletion marker after the full event sequence is processed.
