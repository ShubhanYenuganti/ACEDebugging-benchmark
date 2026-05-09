# Serverless Microservices with API Gateway, DynamoDB, SQS, and Lambda

**Source file:** sample-microservices-apigateway-lambda-dynamodb-sqs.README.md
**AWS reference URL:** https://github.com/localstack/microservices-apigateway-lambda-dynamodb-sqs-sample

## Summary
This system manages friend relationships for a game backend through asynchronous commands and query endpoints. It accepts relationship actions such as request, accept, reject, and unfriend, records durable state transitions, and exposes read access for player-facing features. The design separates write-side command processing from read-side retrieval so backend producers can enqueue changes without blocking on every downstream transition. The outcome is consistent friend-state management that supports asynchronous workloads and observable terminal state.

## Repository extraction notes
The README in `localstack-samples/sample-microservices-apigateway-lambda-dynamodb-sqs` links to the implementation repository `localstack/microservices-apigateway-lambda-dynamodb-sqs-sample`. That repository has no hand-authored CloudFormation YAML; deployment is driven by `Makefile` through CDK commands (`cdklocal bootstrap` and `cdklocal deploy`). The original CDK stack is preserved under `implementation/`, while `known_good.yaml` is a standalone CloudFormation translation of the same observable flow for ACE corpus deployment.
