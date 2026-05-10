## System overview

A movie catalog ingestion and search service. Users submit movie records through a public HTTP ingest endpoint. Records flow through a streaming pipeline into a search engine. Documents that cannot be delivered to the search engine are written to a backup storage bucket for later inspection. A public search endpoint accepts text queries and returns matching movie records.

## What you have access to

A deployed instance of this system is running in your local environment. The CloudFormation template (`faulted.yaml`) and all supporting deployment files are available to you directly. Diagnostic tools are available via MCP to probe the running system. The system deployed successfully.

## Reported symptom

Submitting a movie record succeeds and returns 200. Under normal conditions documents reach the search engine and searches return results. However, when the pipeline needs to write a skipped document to the backup storage location — for example when the search engine is temporarily unavailable — the write fails silently with an access denied error. Skipped documents are permanently lost rather than saved for later reprocessing. The pipeline component's delivery role does not have permission to write to the actual backup bucket.

## What correct behavior looks like

When a document cannot be delivered to the search engine, the pipeline writes it to the designated backup storage bucket. The pipeline component's role must have write permissions to the correct backup bucket. Skipped documents must be recoverable from that bucket.
