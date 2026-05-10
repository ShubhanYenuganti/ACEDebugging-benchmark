## System overview

A movie catalog ingestion and search service. Users submit movie records through a public HTTP ingest endpoint. Records flow through a data pipeline into a search engine. A public search endpoint accepts text queries and returns matching movie records.

## What you have access to

A deployed instance of this system is running in your local environment. The CloudFormation template (`faulted.yaml`) and all supporting deployment files are available to you directly. Diagnostic tools are available via MCP to probe the running system. The system deployed successfully.

## Reported symptom

Submitting a movie record to the ingest endpoint returns a 403 Forbidden response instead of the expected 200 with `{"status": "accepted"}`. No data reaches the pipeline. The endpoint URL is correct and the service is running. The error occurs for all callers regardless of request content.

## What correct behavior looks like

Any HTTP POST to the ingest endpoint with a JSON movie record body returns 200 with `{"status": "accepted"}`. The endpoint must be publicly accessible without authentication. Callers do not need to sign requests or provide credentials.
