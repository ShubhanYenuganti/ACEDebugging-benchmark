# Scenario: arch08_fault09_data_correctness

## System overview

A job lifecycle platform receives job creation and management commands and fans out ordered event notifications to two downstream processors: an analytics archiver that writes event records to object storage, and an inventory tracker that maintains the terminal state of each job in a database. The analytics archiver reads the target storage location from an environment variable and writes event data to that location on each invocation.

## What you have access to

A deployed instance of this system is running in your local environment. The CloudFormation template (`faulted.yaml`) and all supporting deployment files are available to you directly. Diagnostic tools are available via MCP to probe the running system. The system deployed successfully.

## Reported symptom

After a test job is submitted, the inventory tracker correctly processes events and writes the expected terminal state to the database. The analytics queue receives messages and drains, indicating the analytics processing function is being invoked. However, no objects appear in object storage, and the analytics function logs show errors indicating the target storage location does not exist.

## What correct behavior looks like

After a job is submitted, the analytics archiver should be invoked, successfully resolve the target storage location from its environment, and write at least one object to that location within the test window. The object should be readable and contain the event records from the producer invocation.
