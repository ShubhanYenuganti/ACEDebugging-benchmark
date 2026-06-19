## System overview

This system is a serverless order management API backed by a PostgreSQL database. An API Gateway receives HTTP requests and proxies them to a Lambda function deployed inside a VPC. The Lambda function connects to an RDS PostgreSQL instance using credentials stored in Secrets Manager (encrypted by a KMS key). The stack was deployed successfully and all resources report a healthy state.

## What you have access to

- `faulted.yaml` — the deployed CloudFormation template
- `deployment/` — the Lambda function source code and dependencies
- MCP diagnostic tools including: `ace_get_environment_variables`, `ace_describe_secret`, `ace_get_stack_outputs`, `ace_get_log_tail`, `ace_filter_log_events`, `ace_describe_resource`, `ace_get_iam_role`

The system was deployed without errors (`CREATE_COMPLETE`). The RDS instance is available. A Secrets Manager secret with valid database credentials exists in the account.

## Reported symptom

Every call to `POST /orders` returns HTTP `500`. Lambda logs show a `ResourceNotFoundException` when attempting to retrieve the database credentials — the secret cannot be found. The Lambda execution role has the necessary permissions to call Secrets Manager. The database instance itself is healthy.

## What correct behavior looks like

`POST /orders` with a valid JSON body (e.g. `{"customer": "alice", "amount": 100}`) should return HTTP `201` with a JSON body containing `order_id` and `"status": "NEW"`. The Lambda function must successfully locate and retrieve the database credentials secret on every invocation. A `ResourceNotFoundException` from Secrets Manager must not occur during normal operation.
