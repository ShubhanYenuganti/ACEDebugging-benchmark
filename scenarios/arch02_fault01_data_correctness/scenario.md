## System overview

This system accepts movie documents through a public HTTP endpoint, routes them through a streaming pipeline, and indexes them into a full-text search engine. A second public endpoint lets users perform fuzzy keyword searches across movie titles, directors, and actors. The ingest and search paths are decoupled — movies submitted through the ingest endpoint travel through an intermediate buffer before landing in the search index.

## What you have access to

A deployed instance of this system is running in your local environment. The CloudFormation template (`faulted.yaml`) and all supporting deployment files are available to you directly. Diagnostic tools are available via MCP to probe the running system. The system deployed successfully.

## Reported symptom

The ingest endpoint accepts movie documents and returns a success response. However, after waiting the full observation window, searching for a just-ingested movie returns zero results — the search endpoint consistently responds with an empty list even for titles that were recently submitted. Direct queries to the underlying search engine also return no results for the expected index.

## What correct behavior looks like

After a movie document is submitted through the ingest endpoint, the document should appear in the search index within the observation window (approximately 60–180 seconds). The search endpoint should return the document when queried by title fragment. Both the direct search engine query and the search endpoint should return the indexed movie.
