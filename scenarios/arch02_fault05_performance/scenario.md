## System overview

A movie catalog ingestion and search service. Users submit movie records (title, directors, actors, year, genres) through a public HTTP endpoint. Submitted records flow through a streaming data pipeline that buffers them before delivering to a search engine for indexing. A separate public HTTP endpoint accepts text search queries and returns matching movie records using fuzzy full-text matching.

## What you have access to

A deployed instance of this system is running in your local environment. The CloudFormation template (`faulted.yaml`) and all supporting deployment files are available to you directly. Diagnostic tools are available via MCP to probe the running system. The system deployed successfully.

## Reported symptom

Submitting a movie record through the ingest endpoint returns a 200 success response. The data stream shows records entering. However, searching for the ingested movie by title returns no results, even after waiting the full 3-minute test window. The search engine index appears empty despite successful ingest. The delivery pipeline shows active status and no errors, but the buffering window for record delivery is configured far beyond the test timeout — records will not reach the search index until the buffer flushes, which takes far longer than the test can wait.

## What correct behavior looks like

After submitting a movie record, the data pipeline should flush buffered records to the search index frequently enough that a search performed within a 3-minute window reliably finds the newly ingested movie. The buffer flush interval and size thresholds must both be reachable within the test timeout.
