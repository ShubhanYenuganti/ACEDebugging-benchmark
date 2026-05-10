# Scenario: arch12_fault05_reliability

## System overview

This system automatically processes inventory update files uploaded to a shared storage area. When a file arrives, a parsing function reads it and forwards each row as an individual message to a work queue. A separate consumer function picks up those messages and writes each inventory record into a database table. The pipeline is designed to be fully automated — a file upload is the only manual action required, and all records should appear in the database within two minutes.

## What you have access to

A deployed instance of this system is running in your local environment. The CloudFormation template (`faulted.yaml`) and all supporting deployment files are available to you directly. Diagnostic tools are available via MCP to probe the running system. The system deployed successfully.

## Reported symptom

An inventory file was uploaded successfully. Records do appear in the database — but the work queue never fully empties. The queue oscillates between showing messages in-flight and showing them visible again, and the consumer function logs show repeated invocations processing the same data multiple times. The queue is still active at the end of the two-minute test window.

## What correct behavior looks like

Uploading a file should trigger the parser, which sends each row as a message to the work queue. The consumer should drain the queue, writing each row exactly once to the database. At the end of the run, the queue should be fully empty with zero messages visible and zero in flight.
