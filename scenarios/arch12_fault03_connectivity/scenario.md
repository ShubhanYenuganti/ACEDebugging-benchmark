## System overview

This system automatically processes inventory update files uploaded to a shared storage area. When a file arrives, a parsing function reads it and forwards each row as an individual message to a work queue. A separate consumer function picks up those messages and writes each inventory record into a database table. The pipeline is designed to be fully automated — a file upload is the only manual action required, and all records should appear in the database within two minutes.

## What you have access to

A deployed instance of this system is running in your local environment. The CloudFormation template (`faulted.yaml`) and all supporting deployment files are available to you directly. Diagnostic tools are available via MCP to probe the running system. The system deployed successfully.

## Reported symptom

An inventory file was uploaded and the parser ran successfully — messages were sent to the work queue. The queue initially shows messages present. However, the consumer function is never invoked despite the queue-to-consumer binding appearing to be enabled and healthy. After several minutes the messages disappear from the main queue and appear in the dead-letter queue without any consumer ever processing them. No records are written to the database.

## What correct behavior looks like

Uploading a CSV file to the storage area should trigger the parser within seconds. The parser should read each row and send it to the work queue. The consumer should then drain the queue and write each row as a record in the database. At the end of the run, the database should contain one record per CSV row and the queue should be fully empty with no messages in flight.
