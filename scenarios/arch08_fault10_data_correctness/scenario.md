# Scenario: arch08_fault10_data_correctness

## System overview

A job lifecycle platform receives job creation and management commands and fans out ordered event notifications to two downstream processors: an analytics archiver that writes event records to object storage, and an inventory tracker that maintains the terminal state of each job in a database. The inventory tracker writes an initial record when a job creation event is received, then marks the record as deleted when a deletion event arrives.

## What you have access to

A deployed instance of this system is running in your local environment. The CloudFormation template (`faulted.yaml`) and all supporting deployment files are available to you directly. Diagnostic tools are available via MCP to probe the running system. The system deployed successfully.

## Reported symptom

After a test job is submitted, the analytics archiver correctly writes records to object storage. The inventory queue receives messages and the processing function is invoked. However, the inventory data store contains no record for the test job after the test window elapses. The processing function logs show errors from the database — either the target table does not exist, or the write fails with a key schema validation error. Correcting just the environment configuration or just the handler code alone does not fully resolve the problem — both must be fixed together.

## What correct behavior looks like

After a job is submitted, the inventory tracker should successfully resolve both the correct target table and the correct key attribute, write the initial job record using the right primary key name, and mark the record as deleted. The database item should be queryable by the test job identifier and have a terminal deletion marker present.
