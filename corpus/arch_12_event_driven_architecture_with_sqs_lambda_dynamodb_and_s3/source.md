# Event-driven architecture with SQS, Lambda, DynamoDB, and S3

**Source file:** sample-sqs-lambda-dynamodb.README.md
**AWS reference URL:** https://github.com/localstack-samples/sample-sqs-lambda-dynamodb

## Summary
This system processes inventory update files asynchronously. A source file is uploaded, parsed into individual inventory update messages, and then consumed by a worker that records each update for later inspection. The design separates file ingestion from record persistence so bulk uploads can be buffered and processed in batches. The outcome is an inventory table populated from CSV uploads without a synchronous application server in the write path.

## Repository extraction notes
The repository is a Python CDK application. The original CDK stack and Lambda handlers are preserved under `implementation/`. `known_good.yaml` is a standalone CloudFormation translation with inline Python handlers because the Lambda code only requires standard library modules and `boto3`.
