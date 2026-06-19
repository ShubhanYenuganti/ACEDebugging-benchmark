## System overview

This system is a serverless order management API backed by a PostgreSQL database. An API Gateway receives HTTP requests and proxies them to a Lambda function deployed inside a VPC. The Lambda function connects to an RDS PostgreSQL instance using credentials stored in Secrets Manager (encrypted by a KMS key). The stack was deployed successfully and all resources report a healthy state.

## What you have access to

- `faulted.yaml` — the deployed CloudFormation template
- `deployment/` — the Lambda function source code and dependencies
- MCP diagnostic tools including: `ace_get_iam_role`, `ace_simulate_policy`, `ace_get_environment_variables`, `ace_get_log_tail`, `ace_filter_log_events`, `ace_get_stack_outputs`, `ace_describe_secret`, `ace_describe_resource`

The system was deployed without errors (`CREATE_COMPLETE`). The RDS instance is available and the Secrets Manager secret exists with valid credentials.

## Reported symptom

Every call to `POST /orders` returns HTTP `500`. Lambda logs show an `AccessDeniedException` when attempting to retrieve the database credentials. The database instance itself is healthy and reachable from the VPC. The secret exists and contains correct credentials.

## What correct behavior looks like

`POST /orders` with a valid JSON body (e.g. `{"customer": "alice", "amount": 100}`) should return HTTP `201` with a JSON body containing `order_id` and `"status": "NEW"`. The Lambda function must be able to retrieve its credentials without authorization errors. Overly broad IAM grants (e.g. wildcard actions or resources) are not acceptable fixes.
