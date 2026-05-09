# Event-driven architecture with SNS FIFO, DynamoDB, Lambda, and S3

**Source file:** sample-sam-sns-fifo-dynamodb-lambda.README.md
**AWS reference URL:** https://github.com/localstack/event-driven-architecture-with-amazon-sns-fifo

## Summary
This system converts raw job-change activity into ordered business events for downstream services. Analytics consumers retain an event history, while inventory consumers keep an operational view of currently available jobs. The design lets independent services react to the same ordered event stream without coupling their storage concerns. The outcome is a recruiting workflow where job creation and deletion events are processed consistently for both reporting and inventory state.

## Repository extraction notes
The repository is a SAM application with Python handlers and `boto3`-only requirements. `known_good.yaml` translates the SAM template into plain CloudFormation with inline Lambda code so it is deployable without a SAM build artifact bucket. The original SAM template and service source folders are preserved under `implementation/`.
