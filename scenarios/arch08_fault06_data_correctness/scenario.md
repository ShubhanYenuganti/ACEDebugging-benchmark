# Scenario: arch08_fault06_data_correctness

## System overview

A job lifecycle platform receives job creation and management commands and fans out ordered event notifications to two downstream processors: an analytics archiver that writes event records to object storage, and an inventory tracker that maintains the terminal state of each job in a database. The inventory tracker processes creation and deletion events — writing an initial record on creation and marking the record as deleted when a deletion event arrives.

## What you have access to

A deployed instance of this system is running in your local environment. The CloudFormation template (`faulted.yaml`) and all supporting deployment files are available to you directly. Diagnostic tools are available via MCP to probe the running system. The system deployed successfully.

## Reported symptom

After a test job is submitted, the analytics archiver correctly writes records to object storage. The inventory tracker receives all messages, the processing function is invoked successfully for every record, and no errors appear in the logs. The database item for the test job is created. However, the terminal deletion marker is not found on the item — the item exists in the database but the expected `markAsDeleted` attribute is absent or set to an unexpected value.

## What correct behavior looks like

After a job is submitted, the inventory tracker should write the initial job record on the creation event, and then update the record to set `markAsDeleted` to `true` when the deletion event is processed. The final database item should have `markAsDeleted: true` present at the top level.
