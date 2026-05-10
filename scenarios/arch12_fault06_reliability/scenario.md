# Scenario: arch12_fault06_reliability

## System overview

This system automatically processes inventory update files uploaded to a shared storage area. When a file arrives, a parsing function reads it and forwards each row as an individual message to a work queue. A separate consumer function picks up those messages and writes each inventory record into a database table. The pipeline is designed to be fully automated — a file upload is the only manual action required, and all records should appear in the database within two minutes.

## What you have access to

A deployed instance of this system is running in your local environment. The CloudFormation template (`faulted.yaml`) and all supporting deployment files are available to you directly. Diagnostic tools are available via MCP to probe the running system. The system deployed successfully.

## Reported symptom

An inventory file was uploaded and the parser ran successfully. The consumer function is being invoked and its logs show repeated errors — the target database does not exist. Messages appear to move to the dead-letter queue after repeated failures, but then disappear from there too and reappear in the main queue. Neither queue accumulates messages permanently. No records are written to the database and the queue never reaches a stable empty state.

## What correct behavior looks like

Uploading a file should trigger the parser, which sends each row as a message to the work queue. The consumer should drain the queue and write each row as a record in the database. At the end of the run, the database should contain one record per CSV row and the queue should be fully empty.
