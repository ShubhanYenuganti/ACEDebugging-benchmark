## System overview

A movie catalog ingestion and search service. Users submit movie records through a public HTTP ingest endpoint. Records flow through a data pipeline into a search engine. A public search endpoint accepts text queries and returns matching movie records by parsing the search engine's response.

## What you have access to

A deployed instance of this system is running in your local environment. The CloudFormation template (`faulted.yaml`) and all supporting deployment files are available to you directly. Diagnostic tools are available via MCP to probe the running system. The system deployed successfully.

## Reported symptom

Submitting a movie record succeeds. Direct inspection of the search index confirms the document is indexed. The search endpoint responds with 200 but always returns an empty array (`[]`), even for exact title queries on documents that are confirmed to be present in the index. The search engine responds with results, but the function that calls it fails to extract them from the response — it reads from a field that does not exist in the search engine's response structure, silently returning nothing.

## What correct behavior looks like

Querying the search endpoint with a movie's title returns a JSON array containing the matching movie record with its title, directors, actors, year, and genres. The search function must correctly extract results from the search engine's response by reading from the right top-level response field.
