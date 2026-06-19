# Traffic Flow — arch03: Serverless API on RDS PostgreSQL

## Write Path: POST /orders

A client sends `POST /orders` with a JSON body (`{ "customer": "...", "amount": ... }`) to the
Amazon API Gateway regional endpoint. API Gateway forwards the request as an AWS Lambda Proxy
integration event (JSON envelope carrying HTTP method, headers, path parameters, and raw body)
to the `ApiHandlerFunction` Lambda function.

The Lambda function (Python 3.11, VPC-attached to `SubnetA`/`SubnetB`) cold-starts by opening a
fresh psycopg2 TCP connection to the RDS Postgres instance. Before connecting it calls
`secretsmanager:GetSecretValue` using the `DB_SECRET_ARN` environment variable; Secrets Manager
decrypts the stored JSON credential blob using the `DbKmsKey` KMS CMK (`kms:Decrypt`), and returns
`{ "username": "...", "password": "..." }`. The Lambda then connects to the RDS endpoint at
`DB_HOST:DB_PORT` (both resolved dynamically from `!GetAtt OrdersDb.Endpoint.*` at deploy time)
using the retrieved credentials, runs a `CREATE TABLE IF NOT EXISTS orders (...)` to ensure the
schema exists, and executes `INSERT INTO orders ...` to persist the new order.

The Lambda returns `{ "statusCode": 201, "body": "{ \"order_id\": \"<uuid>\", \"status\": \"NEW\" }" }`
which API Gateway passes through as the HTTP response to the client. The psycopg2 connection is
closed at the end of every invocation.

## Read Path: GET /orders/{id}

A client sends `GET /orders/{id}` to the API Gateway endpoint with the order UUID as a path
parameter. API Gateway routes it via the `{id}` resource to the same `ApiHandlerFunction`
Lambda integration.

The Lambda repeats the same connection flow — calls Secrets Manager (decrypted by KMS CMK) to
obtain credentials, opens a fresh psycopg2 connection to the RDS endpoint — then runs
`SELECT order_id, customer, amount, status FROM orders WHERE order_id = %s`. If a matching row
is found it is serialized to JSON and returned as `200 OK`; if no row matches, `404 Not Found`
is returned. The connection is closed before the Lambda handler returns.

## Security Boundary

All data in transit between the Lambda and RDS flows inside the VPC through `DbSecurityGroup`,
which allows TCP 5432 ingress only from within the `10.0.0.0/16` CIDR — the Lambda's VPC-attached
network interface originates traffic from that range. Data at rest in the RDS instance and in the
Secrets Manager secret are encrypted using the customer-managed `DbKmsKey` KMS key. The
`ApiHandlerRole` IAM role carries the minimum permissions needed: `secretsmanager:GetSecretValue`
on the specific secret ARN, `kms:Decrypt` on the CMK ARN, and the
`AWSLambdaVPCAccessExecutionRole` managed policy for VPC ENI management and CloudWatch Logs.
