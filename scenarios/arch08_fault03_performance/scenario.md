# Scenario: arch08_fault03_performance

## System overview

A job lifecycle platform receives job creation and management commands and fans out ordered event notifications to two downstream processors: an analytics archiver that writes event records to object storage, and an inventory tracker that maintains the terminal state of each job in a database. Both processors consume from dedicated queues fed by a shared ordered topic.

## What you have access to

A deployed instance of this system is running in your local environment. The CloudFormation template (`faulted.yaml`) and all supporting deployment files are available to you directly. Diagnostic tools are available via MCP to probe the running system. The system deployed successfully.

## Reported symptom

After a test job is submitted, messages arrive at both the analytics queue and the inventory queue. The inventory tracker processes its messages correctly and writes the expected terminal state to the database. However, the analytics archiver never writes any objects to storage within the test window. Checking the analytics queue shows messages accumulating and cycling — they repeatedly become visible and then go back to in-flight — but the analytics processing function is never invoked.

## What correct behavior looks like

After a job is submitted, both downstream processors successfully handle their messages within the test window. The analytics archiver should be invoked by its event source and write at least one object to object storage. The inventory tracker should write the job record and mark it as deleted in the database.
