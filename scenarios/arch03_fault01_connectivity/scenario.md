## System overview

This system is a serverless order management API backed by a PostgreSQL database. An API Gateway receives HTTP requests and proxies them to a Lambda function deployed inside a VPC. The Lambda function connects to an RDS PostgreSQL instance using credentials stored in Secrets Manager (encrypted by a KMS key). The stack was deployed successfully and all resources report a healthy state.

## What you have access to

- `faulted.yaml` — the deployed CloudFormation template
- `deployment/` — the Lambda function source code and dependencies
- MCP diagnostic tools including: `ace_describe_db_instance`, `ace_check_db_connectivity`, `ace_get_environment_variables`, `ace_get_log_tail`, `ace_filter_log_events`, `ace_get_stack_outputs`, `ace_describe_resource`

The system was deployed without errors (`CREATE_COMPLETE`). The RDS instance is available and the Secrets Manager secret exists.

## Reported symptom

Every call to `POST /orders` fails. The API returns a `502 Bad Gateway` or a `500` response. Lambda logs show a database connection error — the handler cannot reach the database host. The RDS instance itself appears healthy when inspected directly. No application-level errors are logged before the connection attempt.

## What correct behavior looks like

`POST /orders` with a valid JSON body (e.g. `{"customer": "alice", "amount": 100}`) should return HTTP `201` with a JSON body containing `order_id` and `"status": "NEW"`. A subsequent `GET /orders/{id}` should return the created order. Connection errors to the database must not occur when the database instance is running and reachable.
