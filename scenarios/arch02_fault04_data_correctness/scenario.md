## System overview

A movie catalog ingestion and search service. Users submit movie records (title, directors, actors, year, genres) through a public HTTP endpoint. Submitted records flow through a data pipeline that buffers and indexes them into a search engine. A separate public HTTP endpoint accepts text search queries and returns matching movie records using fuzzy full-text matching across title, directors, and actors fields.

## What you have access to

A deployed instance of this system is running in your local environment. The CloudFormation template (`faulted.yaml`) and all supporting deployment files are available to you directly. Diagnostic tools are available via MCP to probe the running system. The system deployed successfully.

## Reported symptom

Submitting a movie record through the ingest endpoint returns a success response. However, querying the search endpoint with any term consistently returns a 500 error. Logs from the search function show a connection error when it attempts to reach the search engine — the function is connecting to the wrong address entirely. The ingest side of the system appears to be working correctly, but the search endpoint is completely non-functional.

## What correct behavior looks like

Submitting a movie record through the ingest endpoint returns a 200 success response. After a short delay while the data pipeline processes and indexes the record, querying the search endpoint with the movie's title returns a JSON array containing the ingested movie with its title, directors, actors, year, and genres.
