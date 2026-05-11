# Scenario: arch12_fault10_security

## System overview

This system automates inventory record management. When a CSV file containing inventory data is uploaded to object storage, a processing function is triggered automatically. The processor reads the file, parses each row, and sends individual records as messages to a work queue. A separate consumer function picks up those messages and writes each record to a persistent database table. The end-to-end flow is: file upload → parser triggered → messages enqueued → consumer invoked → records written to database.

## What you have access to

A deployed instance of this system is running in your local environment. The CloudFormation template (`faulted.yaml`) and all supporting deployment files are available to you directly. Diagnostic tools are available via MCP to probe the running system. The system deployed successfully.

## Reported symptom

After uploading a CSV file with inventory data, the file is acknowledged as received. However, no messages appear in the work queue and the consumer function is never invoked. The queue depth remains at zero. There are no error logs anywhere — the parser function produces no log output at all after the upload, not even a start entry. The system appears completely silent after the upload completes.

## What correct behavior looks like

A CSV upload should trigger the parser, which sends each row as a message to the work queue. The consumer should receive each message and write a corresponding record to the database. After processing completes, the queue should be empty and the database should contain one record per CSV row.
