# Scenario: arch08_fault08_connectivity

## System overview

A job lifecycle platform receives job creation and management commands and fans out ordered event notifications to two downstream processors: an analytics archiver that writes event records to object storage, and an inventory tracker that maintains the terminal state of each job in a database. Each processor consumes from a dedicated queue via an event source binding that polls the queue and invokes the processing function.

## What you have access to

A deployed instance of this system is running in your local environment. The CloudFormation template (`faulted.yaml`) and all supporting deployment files are available to you directly. Diagnostic tools are available via MCP to probe the running system. The system deployed successfully.

## Reported symptom

After a test job is submitted, the analytics archiver correctly writes records to object storage. Messages arrive at the inventory queue and the queue depth grows steadily, but the inventory processing function is never invoked — messages accumulate without being consumed. The inventory data store remains empty throughout the test window. Checking the event source bindings for the inventory processing function shows the binding is not in an active state.

## What correct behavior looks like

After a job is submitted, the inventory queue should be actively polled and messages should be delivered to the inventory processing function. The function should process creation and deletion events, write the initial job record, and mark it as deleted — leaving a terminal deletion marker on the database item.
