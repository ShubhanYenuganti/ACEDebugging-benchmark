# Scenario: arch12_fault04_reliability

## System overview

This system automatically processes inventory update files uploaded to a shared storage area. When a file arrives, a parsing function reads it and forwards each row as an individual message to a work queue. A separate consumer function picks up those messages and writes each inventory record into a database table. The pipeline is designed to be fully automated — a file upload is the only manual action required, and all records should appear in the database within two minutes.

## What you have access to

A deployed instance of this system is running in your local environment. The CloudFormation template (`faulted.yaml`) and all supporting deployment files are available to you directly. Diagnostic tools are available via MCP to probe the running system. The system deployed successfully.

## Reported symptom

An inventory file was uploaded successfully. The work queue received messages and the consumer function was invoked. However, no records were written to the database. The consumer function logs show repeated errors indicating the target database does not exist. After a period of retries, the messages vanished from both the main queue and the dead-letter queue without any records being written — they silently disappeared instead of accumulating in the dead-letter queue as expected.

## What correct behavior looks like

Uploading a file to the storage area should trigger the parser, which sends each row to the work queue. The consumer should drain the queue and write each row as a record in the database. At the end of the run, the database should contain one record per CSV row and the queue should be fully empty with no messages in flight.
