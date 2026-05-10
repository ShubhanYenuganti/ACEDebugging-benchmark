## System overview

This system accepts movie documents through a public HTTP endpoint, routes them through a streaming pipeline, and indexes them into a full-text search engine. A second public endpoint lets users perform fuzzy keyword searches across movie titles, directors, and actors. The ingest and search paths are decoupled — movies submitted through the ingest endpoint travel through an intermediate buffer before landing in the search index.

## What you have access to

A deployed instance of this system is running in your local environment. The CloudFormation template (`faulted.yaml`) and all supporting deployment files are available to you directly. Diagnostic tools are available via MCP to probe the running system. The system deployed successfully.

## Reported symptom

Every attempt to submit a movie document through the ingest endpoint fails with a server error (HTTP 500). The error is immediate — there is no timeout or delayed failure. The underlying streaming infrastructure is running and healthy. Search queries return no results because no documents have been successfully ingested.

## What correct behavior looks like

Submitting a movie document through the ingest endpoint should return HTTP 200 with a JSON body containing `{"status": "accepted"}`. The document should subsequently appear in search results within the 180-second observation window.
