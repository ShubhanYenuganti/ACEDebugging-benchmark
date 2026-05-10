## System overview

This system manages friendship relationships between players in a gaming platform. Players send friend requests through a message queue. A processing function reads each message and writes the initial relationship record. The write uses a guard condition to prevent duplicate records from being inserted if the same request is processed more than once.

## What you have access to

A deployed instance of this system is running in your local environment. The CloudFormation template (`faulted.yaml`) and all supporting deployment files are available to you directly. Diagnostic tools are available via MCP to probe the running system. The system deployed successfully.

## Reported symptom

When a Request action is sent, the processing function logs errors on every invocation. The error message contains a long URL string in place of a data store table name, indicating the function is attempting to write to the wrong destination. The message is retried repeatedly but never succeeds. The queue depth grows as retries accumulate.

## What correct behavior looks like

The processing function must receive the correct data store table name as a configuration value. It must successfully write the initial relationship record using the correct identifier for the record. The guard condition must reference the actual key attribute name used in the table schema to prevent duplicate inserts correctly.
