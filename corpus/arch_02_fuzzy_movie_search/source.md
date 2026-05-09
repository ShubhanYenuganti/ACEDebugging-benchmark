# Fuzzy Movie Search

**Source file:** sample-fuzzy-movie-search-lambda-kinesis-elasticsearch.README.md
**AWS reference URL:** https://github.com/localstack/fuzzy-movie-search

## Summary
This application hosts a small movie search experience backed by an asynchronous ingestion pipeline. New movie documents are submitted through a public ingestion endpoint, streamed into a search index, and later queried through a separate search endpoint used by the website. The design separates data loading from user search so writes can be buffered while read traffic performs fuzzy matching over indexed movie metadata. The outcome is a static website and API pair that can search movie titles, directors, and actors even when the user misspells a query.

## Repository extraction notes
The repository deploys with Terraform through `run.sh`/`Makefile` and has no direct CloudFormation template. The original Terraform files and Lambda source files are preserved under `implementation/`. `known_good.yaml` is a standalone CloudFormation translation of the Terraform graph: Lambda function URLs, Kinesis stream, Firehose delivery to Elasticsearch, S3 skipped-document bucket, and static website bucket.
