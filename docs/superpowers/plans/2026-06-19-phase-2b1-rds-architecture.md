# Phase 2B-1 — RDS PostgreSQL Architecture (arch02) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first breadth-track corpus architecture — a serverless app on RDS PostgreSQL (arch02) — with three new RDS MCP diagnostic tools and four behavior-manifesting fault scenarios.

**Architecture:** `API Gateway → Lambda (in VPC) → RDS PostgreSQL`, with Secrets Manager holding DB credentials encrypted by a customer-managed KMS CMK, and a VPC + private subnets + security group governing connectivity. Reuses arch01's serverless scaffolding (handler vendoring pattern, scenario layout, functional-test harness) with DynamoDB swapped for RDS. arch01 is untouched.

**Tech Stack:** CloudFormation (LocalStack Ultimate), Python 3.11 Lambda handlers (vendored `psycopg2`), Node.js v22+ MCP server (`@aws-sdk/client-rds`, node `net`), pytest + `node:test`.

## Global Constraints

- LocalStack runs with `ENFORCE_IAM=1 IAM_SOFT_MODE=0`; any fixture/scenario creating Lambdas must define a real assumable role.
- AWS creds: accessKeyId=`test`, secretAccessKey=`test`, region=`us-east-1`, endpoint `http://localhost:4566`. IAM fake account `000000000000`.
- Prefer real AWS APIs; never use LocalStack-proprietary introspection (App Inspector, IAM Policy Streams).
- `fault_manifest.json` and `known_good.yaml` are NEVER exposed to the model.
- **Fault design principle (mandatory):** every fault must produce an observable behavioral symptom Pass-1 functional verification detects; `scenario.md` states only the symptom, never the cause. No posture-only faults (`PubliclyAccessible`, `0.0.0.0/0`).
- MCP tool files live in `harness/mcp_server/tools/` and are spread into `harness/mcp_server/index.js`. Each tool is `{ name, description, inputSchema, async handler(args) }` and returns a plain object (never throws).
- Corpus dir name: `corpus/arch_02_serverless_api_with_rds_postgres/`. Scenario dirs: `scenarios/arch02_fault0N_<class>/`.
- Node MCP tests: `node --test tests/test_mcp_server.js`. Spike + corpus run against a live LocalStack (`localstack start -d`).

---

## Task 1: De-risking spike (the gate)

Exploratory, not TDD. Validates the architecture's fault premises on the current LocalStack build before any corpus fan-out. **Do not start Task 3 until this passes.** Findings are recorded in this plan as inline notes under each step.

**Files:**
- Create: `scratch/spike_rds.mjs` (gitignored; `scratch/` is already in `.gitignore`)
- Create: `scratch/spike_rds_stack.yaml` (minimal CFN: VPC + subnet group + SG + RDS Postgres + KMS key + secret)

**Interfaces:**
- Consumes: nothing (standalone spike).
- Produces: a recorded decision per fault mechanism (primary vs fallback) and the arch02 X-Ray instrumentation decision, written as notes in Step 6 below. Tasks 3–4 read these notes.

- [ ] **Step 1: Confirm LocalStack is up with IAM enforcement**

Run:
```bash
localstack status services 2>/dev/null | grep -E "rds|kms|secretsmanager|ec2" || echo "start localstack first"
curl -s localhost:4566/_localstack/health | grep -o '"rds": "[a-z]*"'
```
Expected: `rds` service present. If LocalStack is not running: `localstack start -d` then wait until `localstack status services | grep -q running`.

- [ ] **Step 2: Write the minimal spike stack**

Create `scratch/spike_rds_stack.yaml` with: an `AWS::EC2::VPC`, one private `AWS::EC2::Subnet` (×2 in different AZs — RDS subnet groups require ≥2 AZs), an `AWS::EC2::SecurityGroup` (ingress tcp/5432 from the VPC CIDR), an `AWS::RDS::DBSubnetGroup`, an `AWS::KMS::Key`, an `AWS::SecretsManager::Secret` (username/password, `KmsKeyId` = the CMK), an `AWS::RDS::DBParameterGroup` (family `postgres15`), and an `AWS::RDS::DBInstance` (engine `postgres`, `DBInstanceClass: db.t3.micro`, `AllocatedStorage: 20`, `StorageEncrypted: true`, `KmsKeyId` = CMK, `MasterUsername`/`MasterUserPassword` from the secret via `{{resolve:secretsmanager:...}}`, `DBSubnetGroupName`, `VPCSecurityGroups`, `DBParameterGroupName`, `PubliclyAccessible: false`).

- [ ] **Step 3: Provisioning check**

Write `scratch/spike_rds.mjs` to deploy the stack (CreateStack + wait), then call `DescribeDBInstances`. Run:
```bash
node scratch/spike_rds.mjs provision
```
Expected: stack reaches `CREATE_COMPLETE`; instance reaches `available`; printout shows `Endpoint.Address`, `Endpoint.Port`, `PubliclyAccessible`, `StorageEncrypted`, `KmsKeyId`, `VpcSecurityGroups`, `DBParameterGroups`, `DBSubnetGroup`. **Record:** which of these attributes are populated (the tools depend on them).

- [ ] **Step 4: Enforcement checks (the three key risks)**

Extend `scratch/spike_rds.mjs` with three probes and run `node scratch/spike_rds.mjs enforce`:
1. **SG/VPC reachability:** attempt a TCP connect to `Endpoint.Address:Port` (node `net.connect`). Then create a second SG with NO 5432 ingress, modify the instance to use it, and attempt connect again. Record whether the blocked config actually refuses/times out.
2. **`max_connections`:** set the parameter group's `max_connections` to a low value (e.g. 5), reboot/apply, open N>5 concurrent `psycopg2` connections. Record whether connection N is refused.
3. **KMS `Decrypt`:** create a role WITHOUT `kms:Decrypt` on the CMK but WITH `secretsmanager:GetSecretValue`; from that role's context, call `GetSecretValue` on the KMS-encrypted secret. Record whether it fails with `AccessDeniedException`.

- [ ] **Step 5: X-Ray/psycopg2 capture probe (informational)**

In a throwaway Lambda (or local script) with `aws_xray_sdk` `dbapi2` patching enabled around a `psycopg2` query, emit a segment and call the existing `ace_get_trace`. Run `node scratch/spike_rds.mjs xray`. Record whether a nested SQL subsegment appears.

- [ ] **Step 6: Record findings + lock decisions**

Append a `## Task 1 findings` section to THIS plan file with, for each of: SG/VPC enforcement, `max_connections` enforcement, KMS Decrypt enforcement, psycopg2 X-Ray capture — the observed result and the resulting decision:
- fault01 mechanism = SG-ingress (if SG enforced) else wrong-endpoint/`VpcConfig`-removed.
- fault04 mechanism = `max_connections` (if enforced) else instance-class/CloudWatch.
- fault02 mechanism = KMS-Decrypt-missing (if enforced) else `GetSecretValue`-missing on role.
- arch02 X-Ray = instrument (if capture clean and worthwhile) else defer.

Commit this plan-file update:
```bash
git add docs/superpowers/plans/2026-06-19-phase-2b1-rds-architecture.md
git commit -m "docs(plan): record arch02 spike findings and locked fault mechanisms"
```

- [ ] **Step 7: Tear down the spike stack**

Run `node scratch/spike_rds.mjs teardown` (DeleteStack + wait). No commit (scratch is gitignored).

---

## Task 2: RDS MCP diagnostic tools

Adds `harness/mcp_server/tools/probe_rds.js` with three tools and wires it into `index.js`. TDD via `node:test`.

**Files:**
- Create: `harness/mcp_server/tools/probe_rds.js`
- Modify: `harness/mcp_server/index.js` (import + spread `probeRdsTools`)
- Modify: `harness/mcp_server/package.json` (add `@aws-sdk/client-rds` dependency)
- Test: `tests/test_mcp_server.js` (append a `before()`-seeded RDS block + tool tests)

**Interfaces:**
- Consumes: the `awsConfig` client pattern from `probe_extended.js`; the `tool(list, name)` helper and `before()` hook in `tests/test_mcp_server.js`.
- Produces: `export const probeRdsTools` — an array of three tools:
  - `ace_describe_db_instance({ db_instance_identifier })` → `{ identifier, status, engine, engine_version, instance_class, endpoint, port, publicly_accessible, storage_encrypted, kms_key_id, vpc_security_group_ids: string[], db_subnet_group, parameter_groups: string[], multi_az, master_username }` or `{ error }`.
  - `ace_describe_db_parameters({ db_parameter_group_name, parameter_names? })` → `{ parameters: [{ name, value, source, apply_type }] }` or `{ error }`.
  - `ace_check_db_connectivity({ host, port?, timeout_ms? })` → `{ host, port, reachable: boolean, outcome: "connected"|"refused"|"timeout"|"error", latency_ms, detail? }`.

- [ ] **Step 1: Add the RDS SDK dependency**

Run:
```bash
cd harness/mcp_server && npm install @aws-sdk/client-rds && cd -
```
Expected: `@aws-sdk/client-rds` appears in `harness/mcp_server/package.json` dependencies.

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_mcp_server.js`. First add the import near the other tool imports:
```javascript
import { probeRdsTools } from "../harness/mcp_server/tools/probe_rds.js";
```
Then add tests (no seeded RDS instance is required for these — they assert error handling and the connectivity probe against a known-closed port):
```javascript
test("probeRdsTools exposes the three RDS tools", () => {
  for (const n of ["ace_describe_db_instance", "ace_describe_db_parameters", "ace_check_db_connectivity"]) {
    assert.ok(tool(probeRdsTools, n), `missing ${n}`);
  }
});

test("ace_describe_db_instance: missing identifier returns error", async () => {
  const res = await tool(probeRdsTools, "ace_describe_db_instance").handler({});
  assert.ok(res.error);
});

test("ace_describe_db_instance: unknown identifier returns error", async () => {
  const res = await tool(probeRdsTools, "ace_describe_db_instance").handler({ db_instance_identifier: "nope-does-not-exist" });
  assert.ok(res.error);
});

test("ace_describe_db_parameters: missing group returns error", async () => {
  const res = await tool(probeRdsTools, "ace_describe_db_parameters").handler({});
  assert.ok(res.error);
});

test("ace_check_db_connectivity: missing host returns error", async () => {
  const res = await tool(probeRdsTools, "ace_check_db_connectivity").handler({});
  assert.ok(res.error);
});

test("ace_check_db_connectivity: closed port reports unreachable", async () => {
  const res = await tool(probeRdsTools, "ace_check_db_connectivity").handler({ host: "127.0.0.1", port: 1, timeout_ms: 500 });
  assert.equal(res.reachable, false);
  assert.ok(["refused", "timeout", "error"].includes(res.outcome));
});

test("ace_check_db_connectivity: open port reports reachable", async () => {
  // LocalStack edge port is always listening
  const res = await tool(probeRdsTools, "ace_check_db_connectivity").handler({ host: "127.0.0.1", port: 4566, timeout_ms: 1000 });
  assert.equal(res.reachable, true);
  assert.equal(res.outcome, "connected");
});
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `node --test tests/test_mcp_server.js 2>&1 | grep -A2 probeRdsTools`
Expected: FAIL — `Cannot find module '.../probe_rds.js'`.

- [ ] **Step 4: Implement `probe_rds.js`**

Create `harness/mcp_server/tools/probe_rds.js`:
```javascript
import {
  RDSClient,
  DescribeDBInstancesCommand,
  DescribeDBParametersCommand,
} from "@aws-sdk/client-rds";
import net from "node:net";

const awsConfig = {
  endpoint: process.env.LOCALSTACK_ENDPOINT ?? "http://localhost:4566",
  region: "us-east-1",
  credentials: { accessKeyId: "test", secretAccessKey: "test" },
};

const rdsClient = new RDSClient(awsConfig);

export const probeRdsTools = [
  {
    name: "ace_describe_db_instance",
    description:
      "RDS DescribeDBInstances: return one DB instance's configuration — status, engine/version, instance class, endpoint host/port, publicly_accessible, storage_encrypted, kms_key_id, attached VPC security group IDs, DB subnet group, parameter group name(s), multi_az, master username. Use to diagnose connectivity (SG/subnet), security (encryption/exposure), and config faults.",
    inputSchema: {
      type: "object",
      properties: { db_instance_identifier: { type: "string" } },
      required: ["db_instance_identifier"],
    },
    async handler({ db_instance_identifier } = {}) {
      if (!db_instance_identifier) return { error: "db_instance_identifier is required" };
      try {
        const out = await rdsClient.send(
          new DescribeDBInstancesCommand({ DBInstanceIdentifier: db_instance_identifier })
        );
        const db = (out.DBInstances ?? [])[0];
        if (!db) return { error: `DB instance not found: ${db_instance_identifier}` };
        return {
          identifier: db.DBInstanceIdentifier,
          status: db.DBInstanceStatus ?? null,
          engine: db.Engine ?? null,
          engine_version: db.EngineVersion ?? null,
          instance_class: db.DBInstanceClass ?? null,
          endpoint: db.Endpoint?.Address ?? null,
          port: db.Endpoint?.Port ?? null,
          publicly_accessible: db.PubliclyAccessible ?? null,
          storage_encrypted: db.StorageEncrypted ?? null,
          kms_key_id: db.KmsKeyId ?? null,
          vpc_security_group_ids: (db.VpcSecurityGroups ?? []).map((g) => g.VpcSecurityGroupId),
          db_subnet_group: db.DBSubnetGroup?.DBSubnetGroupName ?? null,
          parameter_groups: (db.DBParameterGroups ?? []).map((p) => p.DBParameterGroupName),
          multi_az: db.MultiAZ ?? null,
          master_username: db.MasterUsername ?? null,
        };
      } catch (err) {
        return { error: String(err?.message ?? err) };
      }
    },
  },
  {
    name: "ace_describe_db_parameters",
    description:
      "RDS DescribeDBParameters: list parameters for a named DB parameter group (name, value, source, apply_type). Optionally filter to specific parameter_names (e.g. ['max_connections']). Use to diagnose parameter-group performance faults such as an undersized max_connections.",
    inputSchema: {
      type: "object",
      properties: {
        db_parameter_group_name: { type: "string" },
        parameter_names: { type: "array", items: { type: "string" } },
      },
      required: ["db_parameter_group_name"],
    },
    async handler({ db_parameter_group_name, parameter_names } = {}) {
      if (!db_parameter_group_name) return { error: "db_parameter_group_name is required" };
      try {
        const filter = new Set(parameter_names ?? []);
        const params = [];
        let marker;
        do {
          const out = await rdsClient.send(
            new DescribeDBParametersCommand({
              DBParameterGroupName: db_parameter_group_name,
              Marker: marker,
            })
          );
          for (const p of out.Parameters ?? []) {
            if (filter.size === 0 || filter.has(p.ParameterName)) {
              params.push({
                name: p.ParameterName,
                value: p.ParameterValue ?? null,
                source: p.Source ?? null,
                apply_type: p.ApplyType ?? null,
              });
            }
          }
          marker = out.Marker;
        } while (marker && params.length < 5000);
        return { parameters: params };
      } catch (err) {
        return { error: String(err?.message ?? err) };
      }
    },
  },
  {
    name: "ace_check_db_connectivity",
    description:
      "Open a raw TCP socket to a DB endpoint host:port and report whether it is reachable. outcome is 'connected', 'refused', 'timeout', or 'error'. Use to confirm a connectivity-class fault from the diagnostician's side (pair with ace_describe_security_group and ace_describe_db_instance).",
    inputSchema: {
      type: "object",
      properties: {
        host: { type: "string" },
        port: { type: "integer" },
        timeout_ms: { type: "integer" },
      },
      required: ["host"],
    },
    async handler({ host, port, timeout_ms } = {}) {
      if (!host) return { error: "host is required" };
      const p = port ?? 5432;
      const t = timeout_ms ?? 3000;
      const start = Date.now();
      return await new Promise((resolve) => {
        const sock = new net.Socket();
        let done = false;
        const finish = (outcome, detail) => {
          if (done) return;
          done = true;
          sock.destroy();
          resolve({
            host,
            port: p,
            reachable: outcome === "connected",
            outcome,
            latency_ms: Date.now() - start,
            ...(detail ? { detail } : {}),
          });
        };
        sock.setTimeout(t);
        sock.once("connect", () => finish("connected"));
        sock.once("timeout", () => finish("timeout"));
        sock.once("error", (e) => {
          const outcome = e.code === "ECONNREFUSED" ? "refused" : "error";
          finish(outcome, e.code ?? String(e.message ?? e));
        });
        sock.connect(p, host);
      });
    },
  },
];
```

- [ ] **Step 5: Wire into `index.js`**

In `harness/mcp_server/index.js`, add the import alongside the others:
```javascript
import { probeRdsTools } from "./tools/probe_rds.js";
```
And add `...probeRdsTools` to the spread in the `for` loop:
```javascript
for (const tool of [...probeTools, ...probeExtendedTools, ...observeTools, ...observeExtendedTools, ...observeTracingTools, ...probeRdsTools, ...scoreTools]) {
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `node --test tests/test_mcp_server.js 2>&1 | tail -20`
Expected: all `probeRdsTools` / `ace_describe_db_*` / `ace_check_db_connectivity` tests PASS; no prior tests regress.

- [ ] **Step 7: Commit**

```bash
git add harness/mcp_server/tools/probe_rds.js harness/mcp_server/index.js harness/mcp_server/package.json harness/mcp_server/package-lock.json tests/test_mcp_server.js
git commit -m "feat(mcp): add RDS diagnostic tools (describe_db_instance, describe_db_parameters, check_db_connectivity)"
```

---

## Task 3: arch02 corpus (known-good)

Builds the working corpus architecture and proves it deploys clean and passes functional tests under IAM enforcement.

**Files:**
- Create: `corpus/arch_02_serverless_api_with_rds_postgres/known_good.yaml`
- Create: `corpus/arch_02_serverless_api_with_rds_postgres/functional_test.py`
- Create: `corpus/arch_02_serverless_api_with_rds_postgres/traffic_flow.md`
- Create: `corpus/arch_02_serverless_api_with_rds_postgres/deployment/lambda/api-handler/index.py`
- Create: `corpus/arch_02_serverless_api_with_rds_postgres/deployment/lambda/api-handler/db.py`
- Create: vendored `psycopg2` under the handler dir (see Step 3)

**Interfaces:**
- Consumes: the Task 1 findings (locked fault mechanisms inform which properties are "the correct value"); arch01's `functional_test.py` harness conventions (`emit_pass`/`emit_fail`/`finalize` from `harness.shared.functional_test_helpers`, `ASSERT pass|fail [name]` output, exit 0).
- Produces: a deployable `known_good.yaml` whose stack name is `ace-bench-stack`, exporting outputs `ApiBaseUrl`, `DbInstanceId`, `DbParameterGroupName`, `DbSecretArn`, `DbSecurityGroupId`, `KmsKeyId` (Task 4 faults and the functional test read these).

- [ ] **Step 1: Decide the domain + schema (single table)**

Domain: a minimal "orders" API. One Postgres table:
```sql
CREATE TABLE IF NOT EXISTS orders (
  order_id   TEXT PRIMARY KEY,
  customer   TEXT NOT NULL,
  amount     INTEGER NOT NULL,
  status     TEXT NOT NULL DEFAULT 'NEW',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```
API: `POST /orders` (create) and `GET /orders/{id}` (read). The handler creates the table on cold start (idempotent `CREATE TABLE IF NOT EXISTS`).

- [ ] **Step 2: Write the handler DB module**

Create `deployment/lambda/api-handler/db.py`:
```python
import json
import os

import boto3
import psycopg2

_conn = None


def _secret():
    arn = os.environ["DB_SECRET_ARN"]
    sm = boto3.client("secretsmanager", endpoint_url=os.environ.get("AWS_ENDPOINT_URL"))
    raw = sm.get_secret_value(SecretId=arn)["SecretString"]
    return json.loads(raw)


def get_conn():
    global _conn
    if _conn is not None and _conn.closed == 0:
        return _conn
    s = _secret()
    _conn = psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=int(os.environ.get("DB_PORT", "5432")),
        dbname=os.environ.get("DB_NAME", "postgres"),
        user=s["username"],
        password=s["password"],
        connect_timeout=5,
    )
    _conn.autocommit = True
    return _conn


def ensure_schema():
    with get_conn().cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS orders ("
            "order_id TEXT PRIMARY KEY, customer TEXT NOT NULL, amount INTEGER NOT NULL,"
            "status TEXT NOT NULL DEFAULT 'NEW', created_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
```

- [ ] **Step 3: Write the API handler + vendor psycopg2**

Create `deployment/lambda/api-handler/index.py`:
```python
import json
import uuid

import db


def _resp(status, body):
    return {"statusCode": status, "headers": {"Content-Type": "application/json"}, "body": json.dumps(body)}


def handler(event, context):
    db.ensure_schema()
    method = event.get("httpMethod")
    path_params = event.get("pathParameters") or {}
    if method == "POST":
        payload = json.loads(event.get("body") or "{}")
        order_id = str(uuid.uuid4())
        with db.get_conn().cursor() as cur:
            cur.execute(
                "INSERT INTO orders (order_id, customer, amount) VALUES (%s, %s, %s)",
                (order_id, payload["customer"], int(payload["amount"])),
            )
        return _resp(201, {"order_id": order_id, "status": "NEW"})
    if method == "GET":
        order_id = path_params.get("id")
        with db.get_conn().cursor() as cur:
            cur.execute("SELECT order_id, customer, amount, status FROM orders WHERE order_id = %s", (order_id,))
            row = cur.fetchone()
        if not row:
            return _resp(404, {"error": "not found"})
        return _resp(200, {"order_id": row[0], "customer": row[1], "amount": row[2], "status": row[3]})
    return _resp(405, {"error": "method not allowed"})
```
Vendor `psycopg2` (binary) and `boto3` is provided by the Lambda runtime — only vendor `psycopg2`:
```bash
cd corpus/arch_02_serverless_api_with_rds_postgres/deployment/lambda/api-handler
pip install --platform manylinux2014_x86_64 --target . --implementation cp --python-version 3.11 --only-binary=:all: psycopg2-binary
cd -
```
Expected: `psycopg2/` (or `psycopg2_binary*`) appears in the handler dir.

- [ ] **Step 4: Write `known_good.yaml`**

Create `corpus/arch_02_serverless_api_with_rds_postgres/known_good.yaml` with these resources (correct, fault-free):
- `Vpc` (`AWS::EC2::VPC`, CIDR `10.0.0.0/16`).
- `SubnetA`, `SubnetB` (`AWS::EC2::Subnet`, `10.0.1.0/24` / `10.0.2.0/24`, AZs `us-east-1a`/`us-east-1b`).
- `DbSubnetGroup` (`AWS::RDS::DBSubnetGroup`, both subnets).
- `DbSecurityGroup` (`AWS::EC2::SecurityGroup`) — ingress `tcp 5432` from `10.0.0.0/16` (CORRECT value; fault01 removes/narrows this).
- `DbKmsKey` (`AWS::KMS::Key`) — key policy grants the account root admin AND `kms:Decrypt`/`kms:DescribeKey` to `ApiHandlerRole` (CORRECT; fault02 removes the role grant).
- `DbSecret` (`AWS::SecretsManager::Secret`) — generated username/password JSON, `KmsKeyId: !Ref DbKmsKey`.
- `DbParameterGroup` (`AWS::RDS::DBParameterGroup`, family `postgres15`) — `max_connections` at a healthy value e.g. `100` (CORRECT; fault04 lowers it).
- `OrdersDb` (`AWS::RDS::DBInstance`) — `Engine: postgres`, `DBInstanceClass: db.t3.micro`, `AllocatedStorage: 20`, `StorageEncrypted: true`, `KmsKeyId: !Ref DbKmsKey`, master creds via `{{resolve:secretsmanager:${DbSecret}:SecretString:username/password}}`, `DBSubnetGroupName`, `VPCSecurityGroups: [!Ref DbSecurityGroup]`, `DBParameterGroupName: !Ref DbParameterGroup`, `PubliclyAccessible: false`.
- `ApiHandlerRole` (`AWS::IAM::Role`) — assumable by `lambda.amazonaws.com`; policies: `AWSLambdaVPCAccessExecutionRole` managed policy (ENIs), `secretsmanager:GetSecretValue` on `DbSecret` (CORRECT; fault03 corrupts the secret ARN), `kms:Decrypt` on `DbKmsKey`.
- `ApiHandlerFunction` (`AWS::Lambda::Function`) — Python 3.11, handler `index.handler`, `Role: !GetAtt ApiHandlerRole.Arn`, `VpcConfig` (both subnets + `DbSecurityGroup`), env: `DB_HOST: !GetAtt OrdersDb.Endpoint.Address`, `DB_PORT: !GetAtt OrdersDb.Endpoint.Port`, `DB_NAME: postgres`, `DB_SECRET_ARN: !Ref DbSecret`, `AWS_ENDPOINT_URL: http://localhost:4566`. Code from `deployment/lambda/api-handler/`.
- `Api` (`AWS::ApiGateway::RestApi`) + resources/methods for `POST /orders` and `GET /orders/{id}` + `AWS::ApiGateway::Deployment`/`Stage` (mirror arch01's API wiring).
- `Outputs`: `ApiBaseUrl`, `DbInstanceId` (`!Ref OrdersDb`), `DbParameterGroupName` (`!Ref DbParameterGroup`), `DbSecretArn` (`!Ref DbSecret`), `DbSecurityGroupId` (`!Ref DbSecurityGroup`), `KmsKeyId` (`!Ref DbKmsKey`).

- [ ] **Step 5: Deploy the known-good stack**

Run:
```bash
python -c "
import boto3, sys
cf=boto3.client('cloudformation',endpoint_url='http://localhost:4566',region_name='us-east-1',aws_access_key_id='test',aws_secret_access_key='test')
body=open('corpus/arch_02_serverless_api_with_rds_postgres/known_good.yaml').read()
cf.create_stack(StackName='ace-bench-stack',TemplateBody=body,Capabilities=['CAPABILITY_NAMED_IAM','CAPABILITY_AUTO_EXPAND'])
w=cf.get_waiter('stack_create_complete'); w.wait(StackName='ace-bench-stack'); print('CREATE_COMPLETE')
"
```
Expected: `CREATE_COMPLETE`. If it fails, inspect with `aws --endpoint-url=http://localhost:4566 cloudformation describe-stack-events --stack-name ace-bench-stack` and fix the template. (Use the existing deployment_handler conventions if a packaging step is needed for the Lambda zip — mirror how arch01 corpus is deployed in the runner.)

- [ ] **Step 6: Write `functional_test.py`**

Create `corpus/arch_02_serverless_api_with_rds_postgres/functional_test.py` mirroring arch01's harness. Primary assertions:
```python
# pseudo-structure — full code:
import json, sys, time, uuid
from urllib import request, error
import boto3
from harness.shared.functional_test_helpers import emit_fail, emit_pass, finalize

ENDPOINT="http://localhost:4566"; REGION="us-east-1"; CREDS={"aws_access_key_id":"test","aws_secret_access_key":"test"}
STACK="ace-bench-stack"

def client(s): return boto3.client(s, endpoint_url=ENDPOINT, region_name=REGION, **CREDS)

def output(key):
    st=client("cloudformation").describe_stacks(StackName=STACK)["Stacks"][0]
    return next(o["OutputValue"] for o in st["Outputs"] if o["OutputKey"]==key)

def main():
    base=output("ApiBaseUrl")
    # create
    body=json.dumps({"customer":"alice","amount":42}).encode()
    req=request.Request(f"{base}/orders", data=body, headers={"Content-Type":"application/json"}, method="POST")
    try:
        with request.urlopen(req, timeout=30) as r:
            created=json.loads(r.read()); ok_create=(r.status==201 and "order_id" in created)
    except error.HTTPError as e:
        ok_create=False; created={}; emit_fail("order_created", f"POST failed: {e.code}")
    if ok_create: emit_pass("order_created", "POST /orders returned 201 with order_id")
    # read back
    if ok_create:
        oid=created["order_id"]
        with request.urlopen(f"{base}/orders/{oid}", timeout=30) as r:
            got=json.loads(r.read())
        if r.status==200 and got.get("customer")=="alice" and got.get("amount")==42:
            emit_pass("order_readback", "GET returns the persisted order")
        else:
            emit_fail("order_readback", f"unexpected: {got}")
    # secondary: instance available
    db=client("rds").describe_db_instances(DBInstanceIdentifier=output("DbInstanceId"))["DBInstances"][0]
    (emit_pass if db["DBInstanceStatus"]=="available" else emit_fail)("db_available_secondary", db["DBInstanceStatus"])
    finalize()

if __name__=="__main__":
    main(); sys.exit(0)
```
Write the complete file (expand the pseudo-structure into real code; no placeholders).

- [ ] **Step 7: Run the functional test against the deployed known-good**

Run: `python corpus/arch_02_serverless_api_with_rds_postgres/functional_test.py`
Expected: `ASSERT pass order_created`, `ASSERT pass order_readback`, `ASSERT pass db_available_secondary`. All primary assertions pass — instrumentation/structure does not change behavior.

- [ ] **Step 8: Write `traffic_flow.md`**

Create `corpus/arch_02_serverless_api_with_rds_postgres/traffic_flow.md` describing: client → API Gateway → VPC Lambda → (Secrets Manager GetSecretValue, decrypted via KMS CMK) → psycopg2 TCP 5432 → RDS Postgres; and the read path. One short paragraph per hop.

- [ ] **Step 9: Tear down + commit**

```bash
python -c "import boto3;cf=boto3.client('cloudformation',endpoint_url='http://localhost:4566',region_name='us-east-1',aws_access_key_id='test',aws_secret_access_key='test');cf.delete_stack(StackName='ace-bench-stack');cf.get_waiter('stack_delete_complete').wait(StackName='ace-bench-stack');print('deleted')"
git add corpus/arch_02_serverless_api_with_rds_postgres
git commit -m "feat(corpus): add arch02 serverless API on RDS PostgreSQL (known-good)"
```

---

## Task 4: Four fault scenarios

Each scenario = a copy of the corpus deployment with one injected fault, a symptom-only `scenario.md`, a `fault_manifest.json` (never exposed), and a verified reproduction. Use the Task 1 locked mechanisms.

**Files (per scenario `scenarios/arch02_fault0N_<class>/`):**
- Create: `faulted.yaml` (corpus `known_good.yaml` with ONE injected fault)
- Create: `scenario.md` (symptom only)
- Create: `fault_manifest.json` (never exposed)
- Create: `deployment/lambda/api-handler/` (copy of corpus handler incl. vendored `psycopg2`)

**Interfaces:**
- Consumes: corpus `known_good.yaml` + handler (Task 3); the RDS tools (Task 2); Task 1 findings.
- Produces: four scenario dirs each reproducing its fault and diagnosable via the intended path; each `fault_manifest.json` carries `optimal_tool_calls`/`optimal_files_changed`/`optimal_lines_changed` measured here.

- [ ] **Step 1: Scaffold all four scenario dirs from the corpus**

```bash
CORP=corpus/arch_02_serverless_api_with_rds_postgres
for s in arch02_fault01_connectivity arch02_fault02_security arch02_fault03_credentials arch02_fault04_performance; do
  mkdir -p scenarios/$s
  cp $CORP/known_good.yaml scenarios/$s/faulted.yaml
  cp -r $CORP/deployment scenarios/$s/deployment
done
```

- [ ] **Step 2: Inject fault01 (connectivity)**

In `scenarios/arch02_fault01_connectivity/faulted.yaml`, apply the Task 1-locked mechanism:
- If SG enforced: remove the `DbSecurityGroup` ingress rule on `5432` (delete the `SecurityGroupIngress` entry).
- Fallback (SG not enforced): change `ApiHandlerFunction` env `DB_PORT` to a wrong port (e.g. `5433`), OR remove `VpcConfig`.
Record the exact `target_resource`/`target_property`/`original_value`/`injected_value` for the manifest.

- [ ] **Step 3: Inject fault02 (security — KMS Decrypt)**

In `scenarios/arch02_fault02_security/faulted.yaml`:
- Primary: remove the `kms:Decrypt` statement granting `ApiHandlerRole` on `DbKmsKey` (from the role policy AND/OR the key policy, matching what Task 1 found enforced).
- Fallback (KMS not enforced): remove `secretsmanager:GetSecretValue` from `ApiHandlerRole` while keeping the secret KMS-encrypted (symptom-equivalent).

- [ ] **Step 4: Inject fault03 (credentials)**

In `scenarios/arch02_fault03_credentials/faulted.yaml`: point `ApiHandlerFunction` env `DB_SECRET_ARN` at a wrong/nonexistent secret ARN, OR scope the role's `secretsmanager:GetSecretValue` Resource to a different secret ARN. (Distinct from fault02: here Decrypt is fine, the secret reference is wrong.)

- [ ] **Step 5: Inject fault04 (performance)**

In `scenarios/arch02_fault04_performance/faulted.yaml`:
- If `max_connections` enforced: set `DbParameterGroup` `max_connections` to a tiny value (e.g. `2`).
- Fallback: set `DBInstanceClass` to the smallest class and rely on a CloudWatch/latency-observable signal.

- [ ] **Step 6: Write symptom-only `scenario.md` for each**

For each scenario, write `scenario.md` with the arch01 structure: `## System overview`, `## What you have access to` (faulted.yaml + deployment files + MCP tools; system deployed successfully), `## Reported symptom` (behavioral symptom ONLY — e.g. fault01: "POST /orders hangs ~5s then returns 502; handler logs show a connection timeout to the database host"; fault02: "POST /orders returns 500; logs show an AccessDenied retrieving the database credentials"; fault03: "500 on every request; logs show the credentials secret cannot be found"; fault04: "intermittent 500s under light load; logs show 'too many clients already'"), and `## What correct behavior looks like` (the order is created and readable; failures must surface, not be masked by over-broad grants). **Never name the resource or property at fault.**

- [ ] **Step 7: Write `fault_manifest.json` for each**

Follow the arch01 schema exactly (`fault_id`, `fault_class`, `architecture`, `scenario_id`, `target_resource`, `target_property`, `injected_value`, `original_value`, `valid_fixes`, `invalid_patches`, `optimal_*` (filled in Step 9), `optimal_diagnostic_path`, `deployment_check: CREATE_COMPLETE`, `observability_check`, `observable_symptom`, `root_cause`, `corpus_path`, `functional_test_path`, `known_good_path`, `concurrency_probe_n`). For fault02 set `invalid_patches` = ["disable secret/storage encryption", "grant kms:* on *", "set key policy principal to *"]. For fault01 = ["open SG ingress to 0.0.0.0/0"].

- [ ] **Step 8: Verify each scenario reproduces + is diagnosable**

For each scenario: deploy `faulted.yaml` as `ace-bench-stack`, confirm `CREATE_COMPLETE`, run the corpus `functional_test.py`, and confirm the primary assertion FAILS (the symptom reproduces). Then walk the intended diagnosis path with the actual MCP tools and confirm it reveals the fault:
```bash
# example for fault01 after deploy:
node -e "import('./harness/mcp_server/tools/probe_rds.js').then(async m=>{const t=n=>m.probeRdsTools.find(x=>x.name===n);console.log(await t('ace_describe_db_instance').handler({db_instance_identifier:'<DbInstanceId output>'}));console.log(await t('ace_check_db_connectivity').handler({host:'<endpoint>',port:5432}));})"
```
Tear down between scenarios. If a scenario does NOT reproduce, switch to its Task 1 fallback mechanism and re-verify.

- [ ] **Step 9: Baseline `optimal_*` and finalize manifests**

For each scenario, set `optimal_files_changed`/`optimal_lines_changed` to the minimal fix (typically 1 file / 1-2 lines) and `optimal_tool_calls` to the count of MCP calls on the intended path (Step 8). Write these into each `fault_manifest.json`.

- [ ] **Step 10: Commit**

```bash
git add scenarios/arch02_fault01_connectivity scenarios/arch02_fault02_security scenarios/arch02_fault03_credentials scenarios/arch02_fault04_performance
git commit -m "feat(scenarios): add four arch02 RDS fault scenarios with manifests"
```

---

## Task 5: Documentation

Bring tool counts and architecture inventory in sync across the guides.

**Files:**
- Modify: `CLAUDE.md` (tool counts; Project Layout corpus/scenarios entries)
- Modify: `README.md` (Phase B tool inventory; repository layout)
- Modify: `RUN.md` (tool inventory; model-access count)

**Interfaces:**
- Consumes: the final tool list from Task 2 (3 new tools) and the arch02 corpus/scenarios from Tasks 3–4.
- Produces: consistent counts (diagnostic tools 58 → 61; the model-access count rises by 3 accordingly) and a documented arch02.

- [ ] **Step 1: Update `CLAUDE.md`**

Change the MCP server description from "56 diagnostic + 2 score tools across 27 LocalStack services" to "59 diagnostic + 2 score tools" (56 was the pre-2A diagnostic count; confirm the current number in the file and add 3) and add `harness/mcp_server/tools/probe_rds.js` (3 RDS tools) to the `tools/` listing. Add `corpus/arch_02_serverless_api_with_rds_postgres/` and the four `scenarios/arch02_fault0N_*` entries to the Project Layout.

- [ ] **Step 2: Update `README.md` and `RUN.md`**

Bump the diagnostic tool count by 3 and the model-access count by 3 in both files; add the three RDS tools to the tool tables; add arch02 to any architecture/corpus inventory.

- [ ] **Step 3: Verify counts are consistent**

Run:
```bash
grep -rEn "5[0-9]|6[0-9]" CLAUDE.md README.md RUN.md | grep -iE "tool|diagnostic|model access" | head
node -e "import('./harness/mcp_server/index.js').catch(()=>{})" 2>/dev/null || true
node -e "Promise.all([import('./harness/mcp_server/tools/probe.js'),import('./harness/mcp_server/tools/probe_extended.js'),import('./harness/mcp_server/tools/observe.js'),import('./harness/mcp_server/tools/observe_extended.js'),import('./harness/mcp_server/tools/observe_tracing.js'),import('./harness/mcp_server/tools/probe_rds.js'),import('./harness/mcp_server/tools/score.js')]).then(m=>{const c=m.reduce((a,x)=>a+Object.values(x).find(Array.isArray).length,0);console.log('total tools:',c);})"
```
Expected: the printed total equals diagnostic + score counts cited in the docs.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md README.md RUN.md
git commit -m "docs: document arch02 RDS architecture and RDS MCP tools (61 tools)"
```

---

## Task 1 findings

> Recorded 2026-06-19. LocalStack Pro 2026.5.4:6e0208279, edition=pro, IAM enforcement active (`ENFORCE_IAM=1 IAM_SOFT_MODE=0`).
> Spike stack: `ace-bench-spike-rds` (CFN), torn down after checks. Scripts in `scratch/` (gitignored).

### Check 1 — Provisioning (Step 3)

**Result: PASS — all attributes populated.**

Stack reached `CREATE_COMPLETE` in ~40 s. RDS instance `spike-rds-instance` reached `available` immediately. `DescribeDBInstances` returned all expected attributes:

| Attribute | Value observed | Populated? |
|---|---|---|
| `Endpoint.Address` | `localhost.localstack.cloud` | ✓ |
| `Endpoint.Port` | `4510` | ✓ |
| `PubliclyAccessible` | `false` | ✓ |
| `StorageEncrypted` | `true` | ✓ |
| `KmsKeyId` | UUID (CMK) | ✓ |
| `VpcSecurityGroups` | `[{sg-…, active}]` | ✓ |
| `DBParameterGroups` | `[{name, in-sync}]` | ✓ |
| `DBSubnetGroup` | subnet group name | ✓ |
| `EngineVersion` | `15` | ✓ |

All MCP tool attributes (needed by `ace_describe_db_instance` / Task 2 tools) are present.

### Check 2 — SG/VPC reachability enforcement (Step 4 probe 1)

**Result: NOT ENFORCED.**

- Good SG (tcp/5432 ingress from VPC CIDR): TCP connect to `localhost.localstack.cloud:4510` → `connected=true`.
- Blocked SG (no ingress): `ModifyDBInstance` applied the new SG (`DescribeDBInstances` confirmed the change), then TCP connect → **still `connected=true`**.
- LocalStack does not enforce EC2 security group rules for RDS TCP reachability; the endpoint is always accessible regardless of SG config.

**Locked decision — fault01 mechanism: FALLBACK.**
Fault01 (connectivity) will use the wrong-endpoint fallback: inject a wrong `DB_PORT` environment variable (e.g. `5433`) into the Lambda handler. This produces a genuine connection-refused symptom that Pass-1 functional verification detects. The injected property is `AWS::Lambda::Function > Properties > Environment > Variables > DB_PORT`, `original_value: "5432"`, `injected_value: "5433"`.

### Check 3 — `max_connections` enforcement (Step 4 probe 2)

**Result: NOT ENFORCED.**

- Parameter group contained `max_connections=100` with `ApplyMethod: pending-reboot`.
- `SHOW max_connections` from psycopg2 session: `100`.
- `ALTER SYSTEM SET max_connections = 3; SELECT pg_reload_conf()` succeeded but `SHOW max_connections` remained `100` — parameter did not take effect without a restart.
- Opened 20 concurrent psycopg2 connections: all succeeded. LocalStack does not enforce `max_connections` limits at the connection layer.

**Locked decision — fault04 mechanism: FALLBACK.**
Fault04 (performance) will use the instance-class fallback: downgrade `DBInstanceClass` from `db.t3.micro` to `db.t3.nano` (or equivalent smallest class). The behavioral symptom will be observable as high query latency / slow response in the functional test (Lambda handler reports latency > threshold). The injected property is `AWS::RDS::DBInstance > Properties > DBInstanceClass`, `original_value: "db.t3.micro"`, `injected_value: "db.t3.nano"`.

> Note: An alternative fallback is to set `max_connections` to a very low value in the parameter group AND require a reboot to apply it; but since LocalStack does not honor the parameter even after `pg_reload_conf`, this is unreliable. The instance-class fallback is the cleaner signal.

### Check 4 — KMS `kms:Decrypt` enforcement (Step 4 probe 3)

**Result: NOT ENFORCED.**

- Created IAM role `spike-no-decrypt-role` with ONLY `secretsmanager:GetSecretValue` (no `kms:Decrypt` on the CMK).
- Assumed the role; used temp credentials to call `GetSecretValue` on the CMK-encrypted secret.
- Result: **SUCCESS** — secret returned without error. LocalStack does not enforce `kms:Decrypt` as a prerequisite for SecretsManager decryption under IAM enforcement (`ENFORCE_IAM=1`).

**Locked decision — fault02 mechanism: FALLBACK.**
Fault02 (security) will remove `secretsmanager:GetSecretValue` from `ApiHandlerRole` entirely. The Lambda handler will receive `AccessDeniedException` from Secrets Manager (which LocalStack *does* enforce for IAM), preventing DB credential retrieval and causing a functional test failure. The injected change is removing the `secretsmanager:GetSecretValue` action from `ApiHandlerRole`'s inline policy, `target_resource: ApiHandlerRole`, `target_property: inline policy`, `original_value: allows GetSecretValue`, `injected_value: GetSecretValue removed`.

### Check 5 — psycopg2 / X-Ray subsegment capture (Step 5)

**Result: WORKS with `XRayTracedConn` wrapper.**

- `aws_xray_sdk` 2.15.0 installed. `aws_xray_sdk.ext.dbapi2` does NOT expose a `patch()` function or `XRayConnection` — the correct API is `XRayTracedConn` (wraps a psycopg2 connection) and `XRayTracedCursor`.
- Test: wrapped a live psycopg2 connection in `XRayTracedConn`, ran `SELECT 1 + 1` inside `xray_recorder.in_segment(...)`. Segment serialization showed **1 SQL subsegment** with `name=spike-db`, `sql={'database_type': 'PostgreSQL'}`.
- The `ace_get_trace` X-Ray tool already exists in `harness/mcp_server/tools/observe_tracing.js`. Capturing SQL subsegments is feasible.

**Locked decision — arch02 X-Ray instrumentation: INSTRUMENT.**
The arch02 Lambda handler will wrap its psycopg2 connection in `XRayTracedConn` (not `patch_all`) and use `xray_recorder.in_segment` / `xray_recorder.in_subsegment` for DB calls. This produces diagnosable SQL subsegments visible via `ace_get_trace`. No new MCP tool needed (existing `ace_get_trace` / `ace_get_trace_summaries` cover it).

### Summary — locked fault mechanisms for Tasks 3–4

| Fault | Class | Primary (not enforced) | **Locked mechanism** |
|---|---|---|---|
| fault01 | connectivity | SG ingress removal (NOT enforced) | **Wrong `DB_PORT` env var on Lambda (5433 instead of 5432)** |
| fault02 | security | `kms:Decrypt` removal (NOT enforced) | **Remove `secretsmanager:GetSecretValue` from `ApiHandlerRole`** |
| fault03 | credentials | N/A (always fallback) | **Wrong `DB_SECRET_ARN` env var on Lambda** |
| fault04 | performance | `max_connections` param (NOT enforced) | **Downgrade `DBInstanceClass` to smallest available** |

---

## Self-Review Notes (author)

- **Spec coverage:** Architecture → Task 3; fault set (4 classes, behavior-manifesting) → Task 4; new MCP tools (3, no new tool for security) → Task 2; KMS security redesign → Task 4 Step 3 + manifest invalid_patches; de-risking spike incl. KMS + psycopg2/X-Ray probe → Task 1; X-Ray-instrumentation-decided-by-spike → Task 1 Step 5/6; testing & sequencing + docs → Tasks 2/3/4/5. All spec sections map to a task.
- **Tool-count note:** the spec cites 58→61; CLAUDE.md currently phrases the diagnostic count differently (56+2). Task 5 Step 1 resolves to the actual current number + 3; the verify script in Step 3 is the source of truth.
- **Fallbacks** for every spike-risk fault (01/02/04) are inlined in Task 4 so a worker never blocks.
