## System overview

This system is a serverless order management API backed by a PostgreSQL database. An API Gateway receives HTTP requests and proxies them to a Lambda function deployed inside a VPC. The Lambda function connects to an RDS PostgreSQL instance using credentials stored in Secrets Manager (encrypted by a KMS key). The stack was deployed successfully and all resources report a healthy state.

## What you have access to

- `faulted.yaml` — the deployed CloudFormation template
- `deployment/` — the Lambda function source code and dependencies
- MCP diagnostic tools including: `ace_get_lambda_metrics`, `ace_get_log_tail`, `ace_filter_log_events`, `ace_get_environment_variables`, `ace_describe_resource`, `ace_get_stack_outputs`, `ace_describe_db_instance`

The system was deployed without errors (`CREATE_COMPLETE`). The RDS instance is available, the secret exists, and the IAM role has correct permissions.

## Reported symptom

Every call to `POST /orders` fails with a `502 Bad Gateway` or a timeout error. Lambda logs show `Task timed out after 1.00 seconds`. No application-level code is reached — the function terminates before completing the database round-trip. The RDS instance, IAM role, and credentials are all healthy.

## What correct behavior looks like

`POST /orders` with a valid JSON body (e.g. `{"customer": "alice", "amount": 100}`) should return HTTP `201` with a JSON body containing `order_id` and `"status": "NEW"` within a reasonable time. The Lambda function must be given enough execution time to complete the full round-trip: credential retrieval, database connection, and query execution. Invocations must not time out under normal single-request load.
