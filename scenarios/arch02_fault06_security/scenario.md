## System overview

A movie catalog ingestion and search service. Users submit movie records through a public HTTP ingest endpoint. Records flow through a streaming data pipeline into a search engine. A separate public search endpoint accepts text queries and returns fuzzy-matched movie results.

## What you have access to

A deployed instance of this system is running in your local environment. The CloudFormation template (`faulted.yaml`) and all supporting deployment files are available to you directly. Diagnostic tools are available via MCP to probe the running system. The system deployed successfully.

## Reported symptom

Submitting a movie record through the ingest endpoint returns 200. The data pipeline delivers records to the search engine successfully — direct inspection of the search index confirms documents are present. However, the search endpoint consistently returns a 403 error for any query. The ingest path is fully functional; only the query path fails with an authorization error from the search engine.

## What correct behavior looks like

After ingesting a movie record, querying the search endpoint with the movie's title returns a JSON array of matching results. The function that performs search queries must be authorized to query the search index at any document path under the search engine domain, not just a specific sub-path.
