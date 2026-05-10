## System overview

This system accepts movie documents through a public HTTP endpoint, routes them through a streaming pipeline, and indexes them into a full-text search engine. A second public endpoint lets users perform fuzzy keyword searches across movie titles, directors, and actors. The ingest and search paths are decoupled — movies submitted through the ingest endpoint travel through an intermediate buffer before landing in the search index.

## What you have access to

A deployed instance of this system is running in your local environment. The CloudFormation template (`faulted.yaml`) and all supporting deployment files are available to you directly. Diagnostic tools are available via MCP to probe the running system. The system deployed successfully.

## Reported symptom

The ingest endpoint accepts documents and returns success responses. Records accumulate in the streaming buffer — metrics confirm messages are arriving. However, after waiting the full observation window (180 seconds), documents never appear in the search index. The delivery pipeline reports an active status, yet nothing flows through to the search engine. Search queries return zero results.

## What correct behavior looks like

After a movie document is submitted through the ingest endpoint, the document should travel through the streaming buffer and appear in the search index within the observation window. The delivery pipeline should actively poll the buffer and push records to the search engine. Search queries should return the indexed movie.
