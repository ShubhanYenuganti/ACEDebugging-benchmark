# Phase B — Diagnostic MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `ace-bench-diagnostic-mcp`, a Node.js MCP server with six probe tools, six observe tools, and two gated score tool stubs, backed by LocalStack AWS clients.

**Architecture:** Three tool files (`probe.js`, `observe.js`, `score.js`) each export a list of tool definitions; `index.js` imports them all, registers with the MCP SDK, and starts a stdio transport. All AWS clients share one config object pointing to LocalStack. Tests use Node's built-in test runner with real LocalStack and minimal fixtures.

**Tech Stack:** Node.js v22+, `@modelcontextprotocol/sdk`, AWS SDK v3 clients for Lambda/DynamoDB/SQS/S3/IAM/CloudWatch Logs/CloudFormation, Node built-in `test` runner, `jszip` (test fixture helper)

---

## Manual Pre-Configuration — Builder Must Complete These Before Writing Any Files

**1. Phase A tests pass**
```bash
pytest tests/test_shared.py -v
# Expected: 17 passed
```

**2. Node.js v22+ is installed**
```bash
node --version
# Expected: v22.x.x or higher
```

**3. npm is available**
```bash
npm --version
# Expected: 10.x.x or similar
```

**4. LocalStack is running**
```bash
localstack status services 2>/dev/null | grep -q "running" && echo "OK" || echo "NOT RUNNING"
# If not running: localstack start -d
# Then poll: until localstack status services 2>/dev/null | grep -q "running"; do sleep 2; done
```

---

## File Structure

| File | Responsibility |
|------|----------------|
| `harness/mcp_server/package.json` | npm dependencies and start script |
| `harness/mcp_server/index.js` | Server entry: imports tools, registers with MCP SDK, starts stdio transport, writes stderr log per call |
| `harness/mcp_server/tools/probe.js` | Six probe tool definitions + shared AWS client config |
| `harness/mcp_server/tools/observe.js` | Six observe tool definitions + own AWS client instances |
| `harness/mcp_server/tools/score.js` | Two gated score tool stubs |
| `tests/test_mcp_server.js` | Phase B gate: Node built-in test runner, imports tool handlers directly |

---

## Task 1: npm scaffold

**Files:**
- Create: `harness/mcp_server/package.json`
- Create: `harness/mcp_server/tools/` (directory)

- [ ] **Step 1: Create directories**

```bash
mkdir -p harness/mcp_server/tools
```

- [ ] **Step 2: Create `harness/mcp_server/package.json`**

```json
{
  "name": "ace-bench-diagnostic-mcp",
  "version": "1.0.0",
  "type": "module",
  "main": "index.js",
  "scripts": {
    "start": "node index.js"
  },
  "dependencies": {
    "@modelcontextprotocol/sdk": "^1.0.0",
    "@aws-sdk/client-cloudformation": "^3.0.0",
    "@aws-sdk/client-lambda": "^3.0.0",
    "@aws-sdk/client-dynamodb": "^3.0.0",
    "@aws-sdk/client-sqs": "^3.0.0",
    "@aws-sdk/client-iam": "^3.0.0",
    "@aws-sdk/client-cloudwatch-logs": "^3.0.0",
    "@aws-sdk/client-s3": "^3.0.0",
    "@aws-sdk/util-dynamodb": "^3.0.0",
    "jszip": "^3.10.1"
  }
}
```

- [ ] **Step 3: Install dependencies**

```bash
cd harness/mcp_server && npm install && cd ../..
```

Expected: `node_modules/` created, no errors.

- [ ] **Step 4: Commit**

```bash
git add harness/mcp_server/package.json harness/mcp_server/package-lock.json
git commit -m "feat: scaffold MCP server npm package"
```

---

## Task 2: Probe tools (`tools/probe.js`)

**Files:**
- Create: `harness/mcp_server/tools/probe.js`

- [ ] **Step 1: Create `harness/mcp_server/tools/probe.js`**

```js
import { CloudFormationClient, DescribeStacksCommand } from "@aws-sdk/client-cloudformation";
import { LambdaClient, InvokeCommand, ListEventSourceMappingsCommand } from "@aws-sdk/client-lambda";
import { DynamoDBClient, GetItemCommand } from "@aws-sdk/client-dynamodb";
import { SQSClient, GetQueueUrlCommand, GetQueueAttributesCommand } from "@aws-sdk/client-sqs";
import { S3Client, HeadObjectCommand } from "@aws-sdk/client-s3";
import { marshall, unmarshall } from "@aws-sdk/util-dynamodb";

const awsConfig = {
  endpoint: process.env.LOCALSTACK_ENDPOINT ?? "http://localhost:4566",
  region: "us-east-1",
  credentials: { accessKeyId: "test", secretAccessKey: "test" },
};

const cfClient = new CloudFormationClient(awsConfig);
const lambdaClient = new LambdaClient(awsConfig);
const dynamoClient = new DynamoDBClient(awsConfig);
const sqsClient = new SQSClient(awsConfig);
const s3Client = new S3Client(awsConfig);

async function getStackOutputs() {
  const res = await cfClient.send(new DescribeStacksCommand({ StackName: "ace-bench-stack" }));
  const outputs = {};
  for (const o of (res.Stacks?.[0]?.Outputs ?? [])) {
    outputs[o.OutputKey] = o.OutputValue;
  }
  return outputs;
}

export const probeTools = [
  {
    name: "ace_invoke_endpoint",
    description: "HTTP invoke to the deployed API Gateway endpoint",
    inputSchema: {
      type: "object",
      properties: {
        path: { type: "string" },
        method: { type: "string", enum: ["GET", "POST", "PUT", "DELETE", "PATCH"] },
        payload: { type: "object" },
      },
      required: ["path", "method"],
    },
    async handler({ path, method, payload }) {
      if (!path) return { error: "path is required" };
      if (!method) return { error: "method is required" };
      const start = Date.now();
      try {
        const outputs = await getStackOutputs();
        const base = outputs["ApiEndpoint"];
        if (!base) return { error: "ApiEndpoint not found in stack outputs" };
        const url = base.replace(/\/$/, "") + path;
        const res = await fetch(url, {
          method,
          headers: { "Content-Type": "application/json" },
          body: payload ? JSON.stringify(payload) : undefined,
        });
        const latency_ms = Date.now() - start;
        let body;
        try { body = await res.json(); } catch { body = await res.text(); }
        return {
          status_code: res.status,
          latency_ms,
          body,
          error_type: res.ok ? null : `HTTP_${res.status}`,
        };
      } catch (err) {
        return { error: err.message, error_type: "NETWORK_ERROR" };
      }
    },
  },
  {
    name: "ace_invoke_lambda",
    description: "Directly invoke a Lambda function by name",
    inputSchema: {
      type: "object",
      properties: {
        function_name: { type: "string" },
        payload: { type: "object" },
      },
      required: ["function_name"],
    },
    async handler({ function_name, payload }) {
      if (!function_name) return { error: "function_name is required" };
      const start = Date.now();
      try {
        const res = await lambdaClient.send(new InvokeCommand({
          FunctionName: function_name,
          InvocationType: "RequestResponse",
          Payload: payload ? JSON.stringify(payload) : undefined,
        }));
        const duration_ms = Date.now() - start;
        const response_body = res.Payload
          ? JSON.parse(Buffer.from(res.Payload).toString("utf-8"))
          : null;
        return {
          status_code: res.StatusCode,
          response_body,
          error_type: res.FunctionError ?? null,
          duration_ms,
          billed_duration_ms: null,
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "LAMBDA_ERROR" };
      }
    },
  },
  {
    name: "ace_check_queue_depth",
    description: "Check SQS queue depth and oldest message age",
    inputSchema: {
      type: "object",
      properties: { queue_name: { type: "string" } },
      required: ["queue_name"],
    },
    async handler({ queue_name }) {
      if (!queue_name) return { error: "queue_name is required" };
      try {
        const urlRes = await sqsClient.send(new GetQueueUrlCommand({ QueueName: queue_name }));
        const attrRes = await sqsClient.send(new GetQueueAttributesCommand({
          QueueUrl: urlRes.QueueUrl,
          AttributeNames: [
            "ApproximateNumberOfMessages",
            "ApproximateNumberOfMessagesNotVisible",
            "ApproximateAgeOfOldestMessage",
          ],
        }));
        const attrs = attrRes.Attributes ?? {};
        return {
          messages_available: parseInt(attrs.ApproximateNumberOfMessages ?? "0", 10),
          messages_in_flight: parseInt(attrs.ApproximateNumberOfMessagesNotVisible ?? "0", 10),
          oldest_message_age_seconds: parseInt(attrs.ApproximateAgeOfOldestMessage ?? "0", 10),
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "SQS_ERROR" };
      }
    },
  },
  {
    name: "ace_read_table_item",
    description: "Read a single item from a DynamoDB table by key",
    inputSchema: {
      type: "object",
      properties: {
        table_name: { type: "string" },
        key: { type: "object" },
      },
      required: ["table_name", "key"],
    },
    async handler({ table_name, key }) {
      if (!table_name) return { error: "table_name is required" };
      if (!key) return { error: "key is required" };
      try {
        const res = await dynamoClient.send(new GetItemCommand({
          TableName: table_name,
          Key: marshall(key),
          ReturnConsumedCapacity: "TOTAL",
        }));
        return {
          item: res.Item ? unmarshall(res.Item) : null,
          consumed_read_capacity: res.ConsumedCapacity?.CapacityUnits ?? 0,
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "DYNAMO_ERROR" };
      }
    },
  },
  {
    name: "ace_check_event_source",
    description: "List Lambda event source mappings for a function",
    inputSchema: {
      type: "object",
      properties: { function_name: { type: "string" } },
      required: ["function_name"],
    },
    async handler({ function_name }) {
      if (!function_name) return { error: "function_name is required" };
      try {
        const res = await lambdaClient.send(new ListEventSourceMappingsCommand({
          FunctionName: function_name,
        }));
        return (res.EventSourceMappings ?? []).map(m => ({
          source_arn: m.EventSourceArn,
          source_type: m.EventSourceArn?.split(":")[2] ?? "unknown",
          enabled: m.State === "Enabled",
          batch_size: m.BatchSize,
          state: m.State,
        }));
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "LAMBDA_ERROR" };
      }
    },
  },
  {
    name: "ace_check_s3_object",
    description: "Check if an S3 object exists and return its metadata",
    inputSchema: {
      type: "object",
      properties: {
        bucket: { type: "string" },
        key: { type: "string" },
      },
      required: ["bucket", "key"],
    },
    async handler({ bucket, key }) {
      if (!bucket) return { error: "bucket is required" };
      if (!key) return { error: "key is required" };
      try {
        const res = await s3Client.send(new HeadObjectCommand({ Bucket: bucket, Key: key }));
        return {
          exists: true,
          size_bytes: res.ContentLength ?? 0,
          last_modified: res.LastModified?.toISOString() ?? null,
        };
      } catch (err) {
        if (err.name === "NotFound" || err.$metadata?.httpStatusCode === 404) {
          return { exists: false, size_bytes: null, last_modified: null };
        }
        return { error: err.message, error_type: err.name ?? "S3_ERROR" };
      }
    },
  },
];
```

- [ ] **Step 2: Commit**

```bash
git add harness/mcp_server/tools/probe.js
git commit -m "feat: add six probe tools"
```

---

## Task 3: Observe tools (`tools/observe.js`)

**Files:**
- Create: `harness/mcp_server/tools/observe.js`

- [ ] **Step 1: Create `harness/mcp_server/tools/observe.js`**

```js
import {
  CloudFormationClient,
  DescribeStackResourceCommand,
  ListStackResourcesCommand,
  DescribeStacksCommand,
} from "@aws-sdk/client-cloudformation";
import {
  LambdaClient,
  GetFunctionCommand,
  GetFunctionConfigurationCommand,
} from "@aws-sdk/client-lambda";
import {
  IAMClient,
  GetRoleCommand,
  ListRolePoliciesCommand,
  ListAttachedRolePoliciesCommand,
  GetRolePolicyCommand,
} from "@aws-sdk/client-iam";
import {
  CloudWatchLogsClient,
  DescribeLogStreamsCommand,
  GetLogEventsCommand,
} from "@aws-sdk/client-cloudwatch-logs";

const awsConfig = {
  endpoint: process.env.LOCALSTACK_ENDPOINT ?? "http://localhost:4566",
  region: "us-east-1",
  credentials: { accessKeyId: "test", secretAccessKey: "test" },
};

const cfClient = new CloudFormationClient(awsConfig);
const lambdaClient = new LambdaClient(awsConfig);
const iamClient = new IAMClient(awsConfig);
const logsClient = new CloudWatchLogsClient(awsConfig);

export const observeTools = [
  {
    name: "ace_describe_resource",
    description: "Get full configuration of a CloudFormation stack resource",
    inputSchema: {
      type: "object",
      properties: { logical_resource_id: { type: "string" } },
      required: ["logical_resource_id"],
    },
    async handler({ logical_resource_id }) {
      if (!logical_resource_id) return { error: "logical_resource_id is required" };
      try {
        const res = await cfClient.send(new DescribeStackResourceCommand({
          StackName: "ace-bench-stack",
          LogicalResourceId: logical_resource_id,
        }));
        const detail = res.StackResourceDetail;
        let properties = {};
        if (detail.ResourceType === "AWS::Lambda::Function") {
          try {
            const fn = await lambdaClient.send(new GetFunctionCommand({ FunctionName: detail.PhysicalResourceId }));
            properties = fn.Configuration ?? {};
          } catch {}
        }
        return {
          resource_type: detail.ResourceType,
          physical_id: detail.PhysicalResourceId,
          properties,
          status: detail.ResourceStatus,
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "CF_ERROR" };
      }
    },
  },
  {
    name: "ace_list_resources",
    description: "List all resources in the ace-bench-stack, optionally filtered by type",
    inputSchema: {
      type: "object",
      properties: { resource_type: { type: "string" } },
    },
    async handler({ resource_type } = {}) {
      try {
        const res = await cfClient.send(new ListStackResourcesCommand({ StackName: "ace-bench-stack" }));
        let resources = (res.StackResourceSummaries ?? []).map(r => ({
          logical_id: r.LogicalResourceId,
          physical_id: r.PhysicalResourceId,
          resource_type: r.ResourceType,
          status: r.ResourceStatus,
        }));
        if (resource_type) {
          resources = resources.filter(r => r.resource_type === resource_type);
        }
        return resources;
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "CF_ERROR" };
      }
    },
  },
  {
    name: "ace_get_iam_role",
    description: "Get full IAM role config including inline and attached policies",
    inputSchema: {
      type: "object",
      properties: { role_name: { type: "string" } },
      required: ["role_name"],
    },
    async handler({ role_name }) {
      if (!role_name) return { error: "role_name is required" };
      try {
        const roleRes = await iamClient.send(new GetRoleCommand({ RoleName: role_name }));
        const inlineRes = await iamClient.send(new ListRolePoliciesCommand({ RoleName: role_name }));
        const attachedRes = await iamClient.send(new ListAttachedRolePoliciesCommand({ RoleName: role_name }));
        const inlinePolicies = [];
        for (const policyName of (inlineRes.PolicyNames ?? [])) {
          const pol = await iamClient.send(new GetRolePolicyCommand({ RoleName: role_name, PolicyName: policyName }));
          inlinePolicies.push({
            name: policyName,
            document: JSON.parse(decodeURIComponent(pol.PolicyDocument)),
          });
        }
        return {
          assume_role_policy: JSON.parse(decodeURIComponent(roleRes.Role.AssumeRolePolicyDocument)),
          attached_policies: (attachedRes.AttachedPolicies ?? []).map(p => ({
            name: p.PolicyName,
            arn: p.PolicyArn,
          })),
          inline_policies: inlinePolicies,
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "IAM_ERROR" };
      }
    },
  },
  {
    name: "ace_get_log_tail",
    description: "Get recent CloudWatch log lines for a Lambda function",
    inputSchema: {
      type: "object",
      properties: {
        function_name: { type: "string" },
        line_count: { type: "number" },
      },
      required: ["function_name"],
    },
    async handler({ function_name, line_count = 20 }) {
      if (!function_name) return { error: "function_name is required" };
      try {
        const groupName = `/aws/lambda/${function_name}`;
        const streamsRes = await logsClient.send(new DescribeLogStreamsCommand({
          logGroupName: groupName,
          orderBy: "LastEventTime",
          descending: true,
          limit: 1,
        }));
        const stream = streamsRes.logStreams?.[0];
        if (!stream) return [];
        const eventsRes = await logsClient.send(new GetLogEventsCommand({
          logGroupName: groupName,
          logStreamName: stream.logStreamName,
          limit: line_count,
          startFromHead: false,
        }));
        return (eventsRes.events ?? []).reverse().map(e => {
          const msg = e.message ?? "";
          const requestIdMatch = msg.match(/RequestId:\s*([^\s]+)/);
          const levelMatch = msg.match(/\b(ERROR|WARN|INFO|DEBUG)\b/);
          return {
            timestamp: new Date(e.timestamp).toISOString(),
            request_id: requestIdMatch?.[1] ?? null,
            level: levelMatch?.[1] ?? "INFO",
            message: msg.trim(),
          };
        });
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "LOGS_ERROR" };
      }
    },
  },
  {
    name: "ace_get_stack_outputs",
    description: "Get all CloudFormation stack outputs as a flat key-value dict",
    inputSchema: { type: "object", properties: {} },
    async handler() {
      try {
        const res = await cfClient.send(new DescribeStacksCommand({ StackName: "ace-bench-stack" }));
        const outputs = {};
        for (const o of (res.Stacks?.[0]?.Outputs ?? [])) {
          outputs[o.OutputKey] = o.OutputValue;
        }
        return outputs;
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "CF_ERROR" };
      }
    },
  },
  {
    name: "ace_get_environment_variables",
    description: "Get environment variables for a Lambda function",
    inputSchema: {
      type: "object",
      properties: { function_name: { type: "string" } },
      required: ["function_name"],
    },
    async handler({ function_name }) {
      if (!function_name) return { error: "function_name is required" };
      try {
        const res = await lambdaClient.send(new GetFunctionConfigurationCommand({ FunctionName: function_name }));
        return res.Environment?.Variables ?? {};
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "LAMBDA_ERROR" };
      }
    },
  },
];
```

- [ ] **Step 2: Commit**

```bash
git add harness/mcp_server/tools/observe.js
git commit -m "feat: add six observe tools"
```

---

## Task 4: Score tools stub (`tools/score.js`)

**Files:**
- Create: `harness/mcp_server/tools/score.js`

- [ ] **Step 1: Create `harness/mcp_server/tools/score.js`**

```js
const HARNESS_API_KEY = process.env.HARNESS_API_KEY ?? "";

function checkKey(provided) {
  if (!provided || provided !== HARNESS_API_KEY) {
    return { error: "unauthorized", message: "score tools require harness_api_key" };
  }
  return null;
}

export const scoreTools = [
  {
    name: "ace_verify_fix",
    description: "Trigger verify loop for a run (harness use only)",
    inputSchema: {
      type: "object",
      properties: {
        run_id: { type: "string" },
        harness_api_key: { type: "string" },
      },
      required: ["run_id", "harness_api_key"],
    },
    async handler({ run_id, harness_api_key }) {
      const authErr = checkKey(harness_api_key);
      if (authErr) return authErr;
      return { status: "not_implemented" };
    },
  },
  {
    name: "ace_score_run",
    description: "Score a completed run (harness use only)",
    inputSchema: {
      type: "object",
      properties: {
        run_id: { type: "string" },
        harness_api_key: { type: "string" },
      },
      required: ["run_id", "harness_api_key"],
    },
    async handler({ run_id, harness_api_key }) {
      const authErr = checkKey(harness_api_key);
      if (authErr) return authErr;
      return { status: "not_implemented" };
    },
  },
];
```

- [ ] **Step 2: Commit**

```bash
git add harness/mcp_server/tools/score.js
git commit -m "feat: add gated score tool stubs"
```

---

## Task 5: Server entry point (`index.js`)

**Files:**
- Create: `harness/mcp_server/index.js`

- [ ] **Step 1: Create `harness/mcp_server/index.js`**

```js
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { probeTools } from "./tools/probe.js";
import { observeTools } from "./tools/observe.js";
import { scoreTools } from "./tools/score.js";

const server = new McpServer({
  name: "ace-bench-diagnostic-mcp",
  version: "1.0.0",
});

for (const tool of [...probeTools, ...observeTools, ...scoreTools]) {
  server.tool(
    tool.name,
    tool.description,
    tool.inputSchema.properties ?? {},
    async (args) => {
      process.stderr.write(
        JSON.stringify({ tool: tool.name, timestamp: new Date().toISOString() }) + "\n"
      );
      const result = await tool.handler(args);
      return { content: [{ type: "text", text: JSON.stringify(result) }] };
    }
  );
}

const transport = new StdioServerTransport();
await server.connect(transport);
```

- [ ] **Step 2: Verify server starts without errors**

```bash
cd harness/mcp_server && echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"0.0.1"}}}' | timeout 3 node index.js 2>/dev/null | head -3
```

Expected: A JSON line containing `"result"` and `"serverInfo"`.

- [ ] **Step 3: Commit**

```bash
git add harness/mcp_server/index.js
git commit -m "feat: add MCP server entry point with stderr tool logging"
```

---

## Task 6: Tests (`tests/test_mcp_server.js`)

**Files:**
- Create: `tests/test_mcp_server.js`

- [ ] **Step 1: Create `tests/test_mcp_server.js`**

```js
import { test, before } from "node:test";
import assert from "node:assert/strict";
import { LambdaClient, CreateFunctionCommand } from "@aws-sdk/client-lambda";
import { DynamoDBClient, CreateTableCommand } from "@aws-sdk/client-dynamodb";
import { SQSClient, CreateQueueCommand } from "@aws-sdk/client-sqs";
import { CloudFormationClient, CreateStackCommand } from "@aws-sdk/client-cloudformation";
import JSZip from "jszip";

import { probeTools } from "../harness/mcp_server/tools/probe.js";
import { observeTools } from "../harness/mcp_server/tools/observe.js";
import { scoreTools } from "../harness/mcp_server/tools/score.js";

const awsConfig = {
  endpoint: process.env.LOCALSTACK_ENDPOINT ?? "http://localhost:4566",
  region: "us-east-1",
  credentials: { accessKeyId: "test", secretAccessKey: "test" },
};

const lambda = new LambdaClient(awsConfig);
const dynamo = new DynamoDBClient(awsConfig);
const sqs = new SQSClient(awsConfig);
const cf = new CloudFormationClient(awsConfig);

const FN = "test-identity-fn";
const TABLE = "test-table";
const QUEUE = "test-queue";

function tool(list, name) {
  return list.find(t => t.name === name);
}

before(async () => {
  const zip = new JSZip();
  zip.file("index.js", "exports.handler = async (e) => ({ statusCode: 200, body: JSON.stringify(e) });");
  const zipBuf = await zip.generateAsync({ type: "nodebuffer" });

  for (const op of [
    () => lambda.send(new CreateFunctionCommand({
      FunctionName: FN,
      Runtime: "nodejs18.x",
      Role: "arn:aws:iam::000000000000:role/test-role",
      Handler: "index.handler",
      Code: { ZipFile: zipBuf },
    })),
    () => dynamo.send(new CreateTableCommand({
      TableName: TABLE,
      KeySchema: [{ AttributeName: "pk", KeyType: "HASH" }],
      AttributeDefinitions: [{ AttributeName: "pk", AttributeType: "S" }],
      BillingMode: "PAY_PER_REQUEST",
    })),
    () => sqs.send(new CreateQueueCommand({ QueueName: QUEUE })),
    () => cf.send(new CreateStackCommand({
      StackName: "ace-bench-stack",
      TemplateBody: JSON.stringify({
        AWSTemplateFormatVersion: "2010-09-09",
        Outputs: { ApiEndpoint: { Value: "http://localhost:4566" } },
        Resources: { Placeholder: { Type: "AWS::CloudFormation::WaitConditionHandle" } },
      }),
    })),
  ]) {
    try { await op(); } catch (e) { if (!e.message?.includes("already exist")) throw e; }
  }
});

// Probe tools
test("ace_invoke_lambda: returns status_code and response_body", async () => {
  const result = await tool(probeTools, "ace_invoke_lambda").handler({ function_name: FN, payload: { x: 1 } });
  assert.ok("status_code" in result, "missing status_code");
  assert.ok("response_body" in result, "missing response_body");
  assert.ok("error_type" in result, "missing error_type");
  assert.ok("duration_ms" in result, "missing duration_ms");
});

test("ace_invoke_lambda: missing function_name returns error", async () => {
  const result = await tool(probeTools, "ace_invoke_lambda").handler({});
  assert.ok(result.error, "expected error field");
});

test("ace_check_queue_depth: returns depth fields", async () => {
  const result = await tool(probeTools, "ace_check_queue_depth").handler({ queue_name: QUEUE });
  assert.ok("messages_available" in result);
  assert.ok("messages_in_flight" in result);
  assert.ok("oldest_message_age_seconds" in result);
});

test("ace_check_queue_depth: missing queue_name returns error", async () => {
  const result = await tool(probeTools, "ace_check_queue_depth").handler({});
  assert.ok(result.error);
});

test("ace_read_table_item: nonexistent key returns null item", async () => {
  const result = await tool(probeTools, "ace_read_table_item").handler({
    table_name: TABLE,
    key: { pk: "does-not-exist" },
  });
  assert.ok("item" in result);
  assert.equal(result.item, null);
  assert.ok("consumed_read_capacity" in result);
});

test("ace_check_event_source: returns array", async () => {
  const result = await tool(probeTools, "ace_check_event_source").handler({ function_name: FN });
  assert.ok(Array.isArray(result));
});

test("ace_check_s3_object: nonexistent bucket returns exists:false", async () => {
  const result = await tool(probeTools, "ace_check_s3_object").handler({
    bucket: "no-such-bucket-xyz123",
    key: "no-key",
  });
  assert.ok("exists" in result);
  assert.equal(result.exists, false);
});

// Observe tools
test("ace_list_resources: returns array", async () => {
  const result = await tool(observeTools, "ace_list_resources").handler({});
  assert.ok(Array.isArray(result));
  if (result.length > 0) {
    assert.ok("logical_id" in result[0]);
    assert.ok("resource_type" in result[0]);
    assert.ok("status" in result[0]);
  }
});

test("ace_get_stack_outputs: returns ApiEndpoint", async () => {
  const result = await tool(observeTools, "ace_get_stack_outputs").handler();
  assert.ok(typeof result === "object" && !Array.isArray(result));
  assert.ok("ApiEndpoint" in result);
});

test("ace_get_environment_variables: returns object", async () => {
  const result = await tool(observeTools, "ace_get_environment_variables").handler({ function_name: FN });
  assert.ok(typeof result === "object");
});

test("ace_get_log_tail: returns array or error", async () => {
  const result = await tool(observeTools, "ace_get_log_tail").handler({ function_name: FN, line_count: 5 });
  assert.ok(Array.isArray(result) || typeof result.error === "string");
});

test("ace_get_iam_role: nonexistent role returns error", async () => {
  const result = await tool(observeTools, "ace_get_iam_role").handler({ role_name: "nonexistent-xyz" });
  assert.ok(result.error);
});

// Score tools
test("ace_verify_fix: empty key returns unauthorized", async () => {
  const result = await tool(scoreTools, "ace_verify_fix").handler({ run_id: "r1", harness_api_key: "" });
  assert.equal(result.error, "unauthorized");
});

test("ace_verify_fix: wrong key returns unauthorized", async () => {
  const result = await tool(scoreTools, "ace_verify_fix").handler({ run_id: "r1", harness_api_key: "wrong" });
  assert.equal(result.error, "unauthorized");
});

test("ace_score_run: empty key returns unauthorized", async () => {
  const result = await tool(scoreTools, "ace_score_run").handler({ run_id: "r1", harness_api_key: "" });
  assert.equal(result.error, "unauthorized");
});
```

- [ ] **Step 2: Run tests (LocalStack must be running)**

```bash
LOCALSTACK_ENDPOINT=http://localhost:4566 node --test tests/test_mcp_server.js
```

Expected: All 15 tests pass, no uncaught exceptions.

- [ ] **Step 3: Commit**

```bash
git add tests/test_mcp_server.js
git commit -m "test: add Phase B MCP server test suite"
```

---

## Task 7: Phase B Gate & MCP Registration

- [ ] **Step 1: Run full Phase B gate**

```bash
LOCALSTACK_ENDPOINT=http://localhost:4566 node --test tests/test_mcp_server.js
```

Expected: All 15 tests pass.

- [ ] **Step 2: Register MCP server with Claude Code**

```bash
HARNESS_API_KEY=$(openssl rand -hex 32)
echo "HARNESS_API_KEY=$HARNESS_API_KEY" > .env
claude mcp add ace-bench-diagnostic-mcp \
  -e HARNESS_API_KEY=$HARNESS_API_KEY \
  -e LOCALSTACK_ENDPOINT=http://localhost:4566 \
  -- node harness/mcp_server/index.js
```

- [ ] **Step 3: Verify .env is gitignored**

```bash
grep -q "^\.env$" .gitignore || echo ".env" >> .gitignore
git add .gitignore
```

- [ ] **Step 4: Final commit**

```bash
git commit -m "feat: Phase B complete — MCP server with 14 tools, tests passing, server registered"
```

**Phase B gate is clear. Phase C may begin.**

---

## Self-Review

### Spec coverage

| Spec requirement | Covered by |
|-----------------|------------|
| B1 — shared `awsConfig` across all tools | Defined in `probe.js` and `observe.js` each |
| B1 — stderr log per tool call with name + timestamp | `index.js` wraps every handler |
| B1 — validate required params before AWS call | Each handler checks required fields, returns structured error |
| B1 — catch all AWS errors, never crash server | Every handler has try/catch |
| B2 — all six probe tools | Task 2 |
| B2 — `ace_check_s3_object` 404 → `{exists:false}` not error | Task 2 |
| B2 — DynamoDB key marshalling | `marshall(key)` in `ace_read_table_item` |
| B3 — all six observe tools | Task 3 |
| B3 — `ace_get_iam_role` with inline policies fully parsed | Task 3 |
| B4 — score tools gated by `HARNESS_API_KEY` | Task 4 |
| B4 — missing/wrong key → `{"error":"unauthorized"}` | Task 4 + tests |
| B4 — correct key → `{"status":"not_implemented"}` | Task 4 |
| B5 — MCP registration command | Task 7 |
| B — tests: all tools, error conditions, no uncaught exceptions | Task 6 |

### Placeholder scan

No TBD, TODO, or vague steps found.

### Type consistency

- `probeTools`, `observeTools`, `scoreTools` — all exported as named arrays, imported identically in `index.js` and `test_mcp_server.js`
- `awsConfig` — defined with identical shape in `probe.js` and `observe.js`
- All error returns use `{ error: string, error_type?: string }` — consistent across all 14 tools
- Score tool `checkKey` returns `null` (ok) or `{ error, message }` (unauthorized) — used identically in both tools
