## System overview

This system automatically processes inventory update files uploaded to a shared storage area. When a file arrives, a parsing function reads it and forwards each row as an individual message to a work queue. A separate consumer function picks up those messages and writes each inventory record into a database table. The pipeline is designed to be fully automated — a file upload is the only manual action required, and all records should appear in the database within two minutes.

## What you have access to

A deployed instance of this system is running in your local environment. The CloudFormation template (`faulted.yaml`) and all supporting deployment files are available to you directly. Diagnostic tools are available via MCP to probe the running system. The system deployed successfully.

## Reported symptom

An inventory file was uploaded successfully — the file is confirmed present in storage. However, after waiting two minutes, no records appear in the database and the work queue remains empty. The file parser shows no invocation records at all — not even a start entry in the logs. It appears the upload notification never reached the parser, even though the storage area and the parser are both correctly configured and reachable.

## What correct behavior looks like

Uploading a CSV file to the storage area should trigger the parser within seconds. The parser should read each row and send it to the work queue. The consumer should then drain the queue and write each row as a record in the database. At the end of the run, the database should contain one record per CSV row and the queue should be fully empty with no messages in flight.
