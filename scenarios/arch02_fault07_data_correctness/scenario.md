## System overview

A movie catalog ingestion and search service. Users submit movie records through a public HTTP ingest endpoint. The ingest function receives each record and writes it to a data stream for downstream processing and indexing. A search endpoint returns fuzzy-matched movie results.

## What you have access to

A deployed instance of this system is running in your local environment. The CloudFormation template (`faulted.yaml`) and all supporting deployment files are available to you directly. Diagnostic tools are available via MCP to probe the running system. The system deployed successfully.

## Reported symptom

Submitting a movie record through the ingest endpoint returns a 500 error. The ingest function crashes at runtime. Logs show a key error: the function attempts to read a configuration value by a name that does not exist in its runtime environment. The configured environment provides the value under a different name than the function expects, causing an unhandled exception on every invocation.

## What correct behavior looks like

Submitting a movie record returns a 200 success response. The record is accepted, written to the data stream, and eventually indexed. Searching for the movie title returns the matching record. The environment variable name supplied by the deployment configuration must exactly match the name the ingest function reads at runtime.
