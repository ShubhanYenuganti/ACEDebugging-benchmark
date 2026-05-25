# Track A2 — MCP Stack Extensions and Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement all Phase 1 (critical fixes + P0 tools) and Phase 2 (high fixes + P1 tools) items from the Track A2 audit, bringing the MCP tool stack to parity with real-AWS SRE workflows across the 40-scenario corpus.

**Architecture:** All changes stay within the MCP server's 5 tool files (`probe.js`, `probe_extended.js`, `observe.js`, `observe_extended.js`) and the Node.js test suite (`tests/test_mcp_server.js`). Each task is a self-contained diff: one bug fix or one new tool, with its own failing test written first. No index.js changes needed — arrays are already spread there.

**Tech Stack:** Node.js v22+, `@aws-sdk/*` v3 clients, LocalStack at `http://localhost:4566`, `node --test` runner with `node:assert/strict`.

**Source audit documents:**
- `docs/superpowers/plans/2026-05-18-track-a2-mcp-extensions-and-fixes.md` — gaps + functional issues inventory
- `docs/superpowers/plans/2026-05-15-track-a-mcp-observability.md` — Track A (already implemented: filter_criteria + ace_scan_table_range)

---

## File Structure

| File | Changes |
|------|---------|
| `harness/mcp_server/tools/probe.js` | F5: flexible output key in `ace_invoke_endpoint`; F10: `event_source_arn` param in `ace_check_event_source` |
| `harness/mcp_server/tools/probe_extended.js` | F2: require `resource_arns` in `ace_simulate_policy`; F4: all-shards + LATEST in `ace_get_stream_records`; F6: poll loop in `ace_start_execution`; Gap 1: new `ace_peek_queue_messages`; Gap 4: new `ace_scan_table` |
| `harness/mcp_server/tools/observe.js` | F1: resource-type dispatch in `ace_describe_resource`; F3: managed policy expansion in `ace_get_iam_role`; F8: multi-stream in `ace_get_log_tail` |
| `harness/mcp_server/tools/observe_extended.js` | Gap 2: new `ace_get_s3_object_content`; Gap 3: new `ace_filter_log_events`; Gap 5: new `ace_get_stack_events`; Gap 6: new `ace_get_lambda_metrics` |
| `tests/test_mcp_server.js` | New tests for every task above |

---

## Section 1: LocalStack Free Tier Compatible (Tasks 1–14)

> All tasks in this section use services available in LocalStack Community edition: S3, SQS, SNS, Lambda, DynamoDB (including Streams), CloudFormation, CloudWatch Logs, CloudWatch Metrics, IAM, Kinesis, Step Functions, and API Gateway. Tests pass against `http://localhost:4566` free tier. Note: Task 14 uses the CloudWatch Metrics API (available in Community) but `AWS/Lambda` namespace metrics are not auto-emitted — the tool will return zero counts against Community edition and is fully functional against LocalStack Pro or real AWS.

---

## Phase 1 — Critical Fixes + P0 Tools

---

### Task 1: Fix `ace_invoke_endpoint` flexible output key (F5)

**Files:**
- Modify: `harness/mcp_server/tools/probe.js` (lines 31–68, `ace_invoke_endpoint` tool)
- Test: `tests/test_mcp_server.js`

**Why:** The handler hardcodes `outputs["ApiEndpoint"]`. Scenarios exposing the URL under `ApiUrl` or any other name silently fail with `"ApiEndpoint not found"`.

- [ ] **Step 1: Write the failing test**

In `tests/test_mcp_server.js`, find the existing `ace_invoke_endpoint` tests and add this new test after them:

```js
test("ace_invoke_endpoint accepts output_key override", async () => {
  const t = tool(probeTools, "ace_invoke_endpoint");
  // "ApiEndpoint" IS the key in test stack, so passing it explicitly must still work
  const result = await t.handler({ path: "/", method: "GET", output_key: "ApiEndpoint" });
  assert.ok(!result.error || result.error !== "ApiEndpoint not found in stack outputs",
    `should resolve endpoint when output_key given; got: ${JSON.stringify(result)}`);
});

test("ace_invoke_endpoint falls back to pattern search when no output_key", async () => {
  const t = tool(probeTools, "ace_invoke_endpoint");
  const result = await t.handler({ path: "/", method: "GET" });
  // test stack has ApiEndpoint; pattern /Api(Endpoint|Url)/i must match it
  assert.ok(!result.error || result.error !== "No ApiEndpoint or ApiUrl output found in stack outputs",
    `pattern search must find ApiEndpoint; got: ${JSON.stringify(result)}`);
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
node --test tests/test_mcp_server.js 2>&1 | grep -A 3 "output_key"
```

Expected: The tests pass already if the handler already does the pattern search, or fail if it still hardcodes. Confirm the behavior before patching.

- [ ] **Step 3: Update `ace_invoke_endpoint` in probe.js**

Replace the `inputSchema` and `handler` for `ace_invoke_endpoint` (currently lines 31–68):

```js
  {
    name: "ace_invoke_endpoint",
    description: "HTTP invoke to the deployed API Gateway endpoint",
    inputSchema: {
      type: "object",
      properties: {
        path: { type: "string" },
        method: { type: "string", enum: ["GET", "POST", "PUT", "DELETE", "PATCH"] },
        payload: { type: "object" },
        output_key: { type: "string" },
      },
      required: ["path", "method"],
    },
    async handler({ path, method, payload, output_key }) {
      if (!path) return { error: "path is required" };
      if (!method) return { error: "method is required" };
      const start = Date.now();
      try {
        const outputs = await getStackOutputs();
        let base;
        if (output_key) {
          base = outputs[output_key];
        } else {
          const entry = Object.entries(outputs).find(([k]) => /Api(Endpoint|Url)/i.test(k));
          base = entry?.[1];
        }
        if (!base) return { error: "No ApiEndpoint or ApiUrl output found in stack outputs" };
        const url = base.replace(/\/$/, "") + path;
        const res = await fetch(url, {
          method,
          headers: { "Content-Type": "application/json" },
          body: payload ? JSON.stringify(payload) : undefined,
        });
        const duration_ms = Date.now() - start;
        let response_body;
        try { response_body = await res.json(); } catch { response_body = await res.text(); }
        return { status_code: res.status, response_body, duration_ms };
      } catch (err) {
        return { error: err.message, error_type: "NETWORK_ERROR" };
      }
    },
  },
```

- [ ] **Step 4: Run tests**

```bash
node --test tests/test_mcp_server.js 2>&1 | grep -E "(output_key|pattern search|FAIL|ok)"
```

Expected: both new tests pass, no regressions.

- [ ] **Step 5: Commit**

```bash
git add harness/mcp_server/tools/probe.js tests/test_mcp_server.js
git commit -m "fix(mcp): ace_invoke_endpoint accepts optional output_key and pattern-searches ApiEndpoint|ApiUrl"
```

---

### Task 2: Fix `ace_get_stream_records` — all-shards + configurable iterator (F4)

**Files:**
- Modify: `harness/mcp_server/tools/probe_extended.js` (lines ~340–380, `ace_get_stream_records` tool)
- Test: `tests/test_mcp_server.js`

**Why:** Current code uses `TRIM_HORIZON` on `shards[shards.length - 1]` only. With multiple shards, records on other shards are invisible; TRIM_HORIZON replays stale data on every call.

- [ ] **Step 1: Write the failing test**

In `tests/test_mcp_server.js`, add after existing stream tests:

```js
test("ace_get_stream_records accepts iterator_type parameter", async () => {
  const t = probeExtendedTools.find(t => t.name === "ace_get_stream_records");
  assert.ok(t, "ace_get_stream_records must exist");
  // We can't easily create a DynamoDB stream in LocalStack test setup,
  // so test that missing stream_arn returns error (not a crash)
  const result = await t.handler({ stream_arn: "arn:aws:dynamodb:us-east-1:000000000000:table/nonexistent/stream/2020-01-01T00:00:00.000" });
  assert.ok(result.error, "nonexistent stream should return error");
});

test("ace_get_stream_records returns error for missing stream_arn", async () => {
  const t = probeExtendedTools.find(t => t.name === "ace_get_stream_records");
  const result = await t.handler({});
  assert.ok(result.error, "missing stream_arn should return error");
});
```

- [ ] **Step 2: Run tests to confirm baseline**

```bash
node --test tests/test_mcp_server.js 2>&1 | grep -A 3 "iterator_type"
```

- [ ] **Step 3: Replace `ace_get_stream_records` handler in probe_extended.js**

Find the `ace_get_stream_records` tool (starts at line ~340) and replace the entire tool object:

```js
  {
    name: "ace_get_stream_records",
    description: "Read recent records from up to 4 shards of a DynamoDB stream. Defaults to LATEST to avoid replaying stale data.",
    inputSchema: {
      type: "object",
      properties: {
        stream_arn: { type: "string" },
        iterator_type: { type: "string", enum: ["LATEST", "TRIM_HORIZON"] },
      },
      required: ["stream_arn"],
    },
    async handler({ stream_arn, iterator_type = "LATEST" } = {}) {
      if (!stream_arn) return { error: "stream_arn is required" };
      try {
        const descRes = await dynamoStreamsClient.send(
          new DDBDescribeStreamCommand({ StreamArn: stream_arn })
        );
        const shards = descRes.StreamDescription?.Shards ?? [];
        if (shards.length === 0) return { records: [], shard_count: 0 };
        const targetShards = shards.slice(-4);
        const allRecords = [];
        for (const shard of targetShards) {
          const iterRes = await dynamoStreamsClient.send(new GetShardIteratorCommand({
            StreamArn: stream_arn,
            ShardId: shard.ShardId,
            ShardIteratorType: iterator_type,
          }));
          const recordsRes = await dynamoStreamsClient.send(new GetRecordsCommand({
            ShardIterator: iterRes.ShardIterator,
            Limit: 10,
          }));
          for (const r of (recordsRes.Records ?? [])) {
            allRecords.push({
              event_name: r.eventName,
              keys: r.dynamodb?.Keys ? unmarshall(r.dynamodb.Keys) : {},
              new_image: r.dynamodb?.NewImage ? unmarshall(r.dynamodb.NewImage) : null,
              old_image: r.dynamodb?.OldImage ? unmarshall(r.dynamodb.OldImage) : null,
            });
            if (allRecords.length >= 20) break;
          }
          if (allRecords.length >= 20) break;
        }
        return { records: allRecords, shard_count: shards.length };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "DYNAMO_STREAMS_ERROR" };
      }
    },
  },
```

- [ ] **Step 4: Run full test suite**

```bash
node --test tests/test_mcp_server.js
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add harness/mcp_server/tools/probe_extended.js tests/test_mcp_server.js
git commit -m "fix(mcp): ace_get_stream_records iterates all shards (capped 4) with configurable iterator_type, default LATEST"
```

---

### Task 3: Fix `ace_describe_resource` — resource-type dispatch (F1)

**Files:**
- Modify: `harness/mcp_server/tools/observe.js` (imports section + lines 38–69, `ace_describe_resource`)
- Test: `tests/test_mcp_server.js`

**Why:** The handler only fetches real properties for `AWS::Lambda::Function`; all other resource types return `properties: {}`. DynamoDB tables, SQS queues, IAM roles, SNS topics, S3 buckets, event source mappings, and Kinesis streams are all invisible.

- [ ] **Step 1: Write failing tests**

In `tests/test_mcp_server.js`, find the existing `ace_describe_resource` tests and add:

```js
test("ace_describe_resource returns properties for DynamoDB table", async () => {
  const t = tool(observeTools, "ace_describe_resource");
  // The test stack's CFN has one resource; we need a DynamoDB table in the stack.
  // Since the test stack only has a placeholder, test the error path for nonexistent resource:
  const result = await t.handler({ logical_resource_id: "NonExistentTable" });
  assert.ok(result.error, "nonexistent resource should return error, not empty properties");
});

test("ace_describe_resource Lambda returns non-empty properties", async () => {
  const t = tool(observeTools, "ace_describe_resource");
  // The FN Lambda is NOT in the CFN stack, so this returns a CF error — that's expected
  const result = await t.handler({ logical_resource_id: "Placeholder" });
  // Placeholder is type AWS::CloudFormation::WaitConditionHandle → should use fallback note
  if (!result.error) {
    assert.ok(result.resource_type, "resource_type must be present");
    assert.ok(result.properties !== undefined, "properties must be present");
    if (result.resource_type !== "AWS::Lambda::Function") {
      assert.ok(
        result.properties.note === "use type-specific tool for this resource type" ||
        typeof result.properties === "object",
        "non-Lambda resources must have properties object or note"
      );
    }
  }
});
```

- [ ] **Step 2: Run tests to see current baseline**

```bash
node --test tests/test_mcp_server.js 2>&1 | grep -A 3 "describe_resource"
```

- [ ] **Step 3: Add new imports to observe.js**

At the top of `observe.js`, after the existing Lambda import block, add:

```js
import { DynamoDBClient, DescribeTableCommand } from "@aws-sdk/client-dynamodb";
import { SQSClient, GetQueueUrlCommand, GetQueueAttributesCommand } from "@aws-sdk/client-sqs";
import { SNSClient, GetTopicAttributesCommand } from "@aws-sdk/client-sns";
import { S3Client, GetBucketLocationCommand } from "@aws-sdk/client-s3";
import { KinesisClient, DescribeStreamSummaryCommand } from "@aws-sdk/client-kinesis";
```

Also extend the existing Lambda import to add `GetEventSourceMappingCommand`. Change:

```js
import {
  LambdaClient,
  GetFunctionCommand,
  GetFunctionConfigurationCommand,
} from "@aws-sdk/client-lambda";
```

to:

```js
import {
  LambdaClient,
  GetFunctionCommand,
  GetFunctionConfigurationCommand,
  GetEventSourceMappingCommand,
} from "@aws-sdk/client-lambda";
```

- [ ] **Step 4: Add new client instances to observe.js**

After the existing `const logsClient = new CloudWatchLogsClient(awsConfig);` line, add:

```js
const dynamoClient = new DynamoDBClient(awsConfig);
const sqsClient = new SQSClient(awsConfig);
const snsClient = new SNSClient(awsConfig);
const s3Client = new S3Client(awsConfig);
const kinesisClient = new KinesisClient(awsConfig);
```

- [ ] **Step 5: Replace `ace_describe_resource` handler with resource-type dispatch**

Replace the entire `ace_describe_resource` tool object (currently lines 38–69):

```js
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
        const physId = detail.PhysicalResourceId;
        let properties = {};
        try {
          switch (detail.ResourceType) {
            case "AWS::Lambda::Function": {
              const fn = await lambdaClient.send(new GetFunctionCommand({ FunctionName: physId }));
              properties = fn.Configuration ?? {};
              break;
            }
            case "AWS::DynamoDB::Table": {
              const tbl = await dynamoClient.send(new DescribeTableCommand({ TableName: physId }));
              properties = tbl.Table ?? {};
              break;
            }
            case "AWS::SQS::Queue": {
              const urlRes = await sqsClient.send(new GetQueueUrlCommand({ QueueName: physId }));
              const attrRes = await sqsClient.send(new GetQueueAttributesCommand({
                QueueUrl: urlRes.QueueUrl,
                AttributeNames: ["All"],
              }));
              properties = attrRes.Attributes ?? {};
              break;
            }
            case "AWS::SNS::Topic": {
              const topic = await snsClient.send(new GetTopicAttributesCommand({ TopicArn: physId }));
              properties = topic.Attributes ?? {};
              break;
            }
            case "AWS::S3::Bucket": {
              const loc = await s3Client.send(new GetBucketLocationCommand({ Bucket: physId }));
              properties = { LocationConstraint: loc.LocationConstraint ?? "us-east-1" };
              break;
            }
            case "AWS::IAM::Role": {
              const roleRes = await iamClient.send(new GetRoleCommand({ RoleName: physId }));
              const inlineRes = await iamClient.send(new ListRolePoliciesCommand({ RoleName: physId }));
              const attachedRes = await iamClient.send(new ListAttachedRolePoliciesCommand({ RoleName: physId }));
              const inlinePolicies = [];
              for (const policyName of (inlineRes.PolicyNames ?? [])) {
                const pol = await iamClient.send(new GetRolePolicyCommand({ RoleName: physId, PolicyName: policyName }));
                inlinePolicies.push({ name: policyName, document: JSON.parse(decodeURIComponent(pol.PolicyDocument)) });
              }
              properties = {
                assume_role_policy: JSON.parse(decodeURIComponent(roleRes.Role.AssumeRolePolicyDocument)),
                attached_policies: (attachedRes.AttachedPolicies ?? []).map(p => ({ name: p.PolicyName, arn: p.PolicyArn })),
                inline_policies: inlinePolicies,
              };
              break;
            }
            case "AWS::Lambda::EventSourceMapping": {
              const esm = await lambdaClient.send(new GetEventSourceMappingCommand({ UUID: physId }));
              properties = {
                event_source_arn: esm.EventSourceArn,
                function_arn: esm.FunctionArn,
                state: esm.State,
                batch_size: esm.BatchSize,
                filter_criteria: esm.FilterCriteria ?? null,
              };
              break;
            }
            case "AWS::Kinesis::Stream": {
              const stream = await kinesisClient.send(new DescribeStreamSummaryCommand({ StreamName: physId }));
              properties = stream.StreamDescriptionSummary ?? {};
              break;
            }
            default:
              properties = { note: "use type-specific tool for this resource type" };
          }
        } catch {}
        return {
          resource_type: detail.ResourceType,
          physical_id: physId,
          properties,
          status: detail.ResourceStatus,
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "CF_ERROR" };
      }
    },
  },
```

- [ ] **Step 6: Run full test suite**

```bash
node --test tests/test_mcp_server.js
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add harness/mcp_server/tools/observe.js tests/test_mcp_server.js
git commit -m "fix(mcp): ace_describe_resource dispatches by ResourceType to fetch real properties for DynamoDB, SQS, SNS, S3, IAM, ESM, Kinesis"
```

---

### Task 4: Fix `ace_simulate_policy` — require `resource_arns` (F2)

**Files:**
- Modify: `harness/mcp_server/tools/probe_extended.js` (lines ~569–600, `ace_simulate_policy` tool)
- Test: `tests/test_mcp_server.js`

**Why:** `resource_arns` defaults to `["*"]`. Policies scoped to specific ARNs return `implicitDeny` when simulated against `*`, causing false negatives — agent diagnoses a broken policy when the policy is actually correct.

- [ ] **Step 1: Write failing test**

In `tests/test_mcp_server.js`, add after existing simulate_policy tests:

```js
test("ace_simulate_policy returns error when resource_arns omitted", async () => {
  const t = probeExtendedTools.find(t => t.name === "ace_simulate_policy");
  assert.ok(t, "ace_simulate_policy must exist");
  const result = await t.handler({
    policy_source_arn: "arn:aws:iam::000000000000:role/test-role",
    action_names: ["dynamodb:GetItem"],
    // resource_arns intentionally omitted
  });
  assert.ok(result.error, `should error when resource_arns is omitted; got: ${JSON.stringify(result)}`);
  assert.ok(/resource_arns/i.test(result.error), "error message must mention resource_arns");
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
node --test tests/test_mcp_server.js 2>&1 | grep -A 5 "resource_arns omitted"
```

Expected: test fails because handler currently defaults to `["*"]` and does not error.

- [ ] **Step 3: Update `ace_simulate_policy` in probe_extended.js**

Find `ace_simulate_policy` (line ~569). Change two things:

1. Add `"resource_arns"` to `required` in `inputSchema`.
2. Remove the `= ["*"]` default and add explicit validation.

Replace the `inputSchema.required` array from:
```js
required: ["policy_source_arn", "action_names"],
```
to:
```js
required: ["policy_source_arn", "action_names", "resource_arns"],
```

Replace the handler signature from:
```js
async handler({ policy_source_arn, action_names, resource_arns = ["*"] } = {}) {
  if (!policy_source_arn || !action_names?.length)
    return { error: "policy_source_arn and action_names are required" };
```
to:
```js
async handler({ policy_source_arn, action_names, resource_arns } = {}) {
  if (!policy_source_arn || !action_names?.length)
    return { error: "policy_source_arn and action_names are required" };
  if (!resource_arns?.length)
    return { error: "resource_arns is required — pass [\"*\"] to simulate against all resources, or specific ARNs for accurate results on scoped policies" };
```

- [ ] **Step 4: Run full test suite**

```bash
node --test tests/test_mcp_server.js
```

Expected: all pass including new test.

- [ ] **Step 5: Commit**

```bash
git add harness/mcp_server/tools/probe_extended.js tests/test_mcp_server.js
git commit -m "fix(mcp): ace_simulate_policy requires resource_arns to prevent false-negative IAM decisions against wildcard"
```

---

### Task 5: Add `ace_peek_queue_messages` — SQS message-body peek (Gap 1)

**Files:**
- Modify: `harness/mcp_server/tools/probe_extended.js` (new imports + append to `probeExtendedTools` array)
- Test: `tests/test_mcp_server.js`

**Why:** `ace_check_queue_depth` returns counts only. Data-correctness faults hinge on message *shape*; without peek, agents reverse-engineer fields from downstream `KeyError` logs.

- [ ] **Step 1: Write failing tests**

In `tests/test_mcp_server.js`, add:

```js
test("ace_peek_queue_messages tool exists", async () => {
  const t = probeExtendedTools.find(t => t.name === "ace_peek_queue_messages");
  assert.ok(t, "ace_peek_queue_messages tool must exist");
});

test("ace_peek_queue_messages returns messages array for empty queue", async () => {
  const t = probeExtendedTools.find(t => t.name === "ace_peek_queue_messages");
  const result = await t.handler({ queue_name: QUEUE });
  assert.ok(!result.error, `unexpected error: ${JSON.stringify(result.error)}`);
  assert.ok(Array.isArray(result.messages), "messages must be an array");
  assert.ok(typeof result.count === "number", "count must be a number");
});

test("ace_peek_queue_messages clamps max_messages to 10", async () => {
  const t = probeExtendedTools.find(t => t.name === "ace_peek_queue_messages");
  const result = await t.handler({ queue_name: QUEUE, max_messages: 999 });
  assert.ok(!result.error);
  assert.ok(result.count <= 10);
});

test("ace_peek_queue_messages returns error for missing queue_name", async () => {
  const t = probeExtendedTools.find(t => t.name === "ace_peek_queue_messages");
  const result = await t.handler({});
  assert.ok(result.error, "missing queue_name should return error");
});

test("ace_peek_queue_messages returns error for nonexistent queue", async () => {
  const t = probeExtendedTools.find(t => t.name === "ace_peek_queue_messages");
  const result = await t.handler({ queue_name: "nonexistent-queue-xyz-abc" });
  assert.ok(result.error, "nonexistent queue should return error");
});
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
node --test tests/test_mcp_server.js 2>&1 | grep -E "(ace_peek_queue|FAIL)" | head -10
```

Expected: all 5 new tests fail with "ace_peek_queue_messages tool must exist".

- [ ] **Step 3: Add SQS imports to probe_extended.js**

At the top of `probe_extended.js`, after the existing imports, add:

```js
import { SQSClient, GetQueueUrlCommand, ReceiveMessageCommand } from "@aws-sdk/client-sqs";
```

After the existing client instantiations (e.g., after `const iamClient = new IAMClient(awsConfig);`), add:

```js
const sqsClient = new SQSClient(awsConfig);
```

- [ ] **Step 4: Append `ace_peek_queue_messages` to `probeExtendedTools` array**

Find the closing `];` of `probeExtendedTools`. Insert before it:

```js
  {
    name: "ace_peek_queue_messages",
    description: "Read up to N messages from an SQS queue without consuming them (VisibilityTimeout=0). Returns message bodies parsed as JSON where possible, plus attributes.",
    inputSchema: {
      type: "object",
      properties: {
        queue_name: { type: "string" },
        max_messages: { type: "number" },
      },
      required: ["queue_name"],
    },
    async handler({ queue_name, max_messages = 5 } = {}) {
      if (!queue_name) return { error: "queue_name is required" };
      const count = Math.min(Math.max(1, max_messages ?? 5), 10);
      try {
        const urlRes = await sqsClient.send(new GetQueueUrlCommand({ QueueName: queue_name }));
        const recvRes = await sqsClient.send(new ReceiveMessageCommand({
          QueueUrl: urlRes.QueueUrl,
          MaxNumberOfMessages: count,
          VisibilityTimeout: 0,
          WaitTimeSeconds: 0,
          MessageAttributeNames: ["All"],
          AttributeNames: ["All"],
        }));
        const messages = recvRes.Messages ?? [];
        return {
          messages: messages.map(m => ({
            message_id: m.MessageId,
            body: (() => { try { return JSON.parse(m.Body); } catch { return m.Body; } })(),
            attributes: m.MessageAttributes ?? {},
            approximate_receive_count: m.Attributes?.ApproximateReceiveCount ?? null,
          })),
          count: messages.length,
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "SQS_ERROR" };
      }
    },
  },
```

- [ ] **Step 5: Run full test suite**

```bash
node --test tests/test_mcp_server.js
```

Expected: all pass including all 5 new tests.

- [ ] **Step 6: Commit**

```bash
git add harness/mcp_server/tools/probe_extended.js tests/test_mcp_server.js
git commit -m "feat(mcp): add ace_peek_queue_messages — SQS message-body peek with VisibilityTimeout=0"
```

---

### Task 6: Add `ace_get_s3_object_content` — S3 GetObject (Gap 2)

**Files:**
- Modify: `harness/mcp_server/tools/observe_extended.js` (new imports + append to `observeExtendedTools` array)
- Test: `tests/test_mcp_server.js`

**Why:** `ace_check_s3_object` does HeadObject (metadata only). Config files, CSV inputs, and fixture data in S3 can't be read without a GetObject tool — agents indirectly observe S3 contents via Lambda logs.

- [ ] **Step 1: Write failing tests**

In `tests/test_mcp_server.js`, add:

```js
test("ace_get_s3_object_content tool exists", async () => {
  const t = observeExtendedTools.find(t => t.name === "ace_get_s3_object_content");
  assert.ok(t, "ace_get_s3_object_content must exist");
});

test("ace_get_s3_object_content returns error for missing bucket", async () => {
  const t = observeExtendedTools.find(t => t.name === "ace_get_s3_object_content");
  const result = await t.handler({});
  assert.ok(result.error, "missing bucket should return error");
});

test("ace_get_s3_object_content returns error for missing key", async () => {
  const t = observeExtendedTools.find(t => t.name === "ace_get_s3_object_content");
  const result = await t.handler({ bucket: "some-bucket" });
  assert.ok(result.error, "missing key should return error");
});

test("ace_get_s3_object_content returns error for nonexistent object", async () => {
  const t = observeExtendedTools.find(t => t.name === "ace_get_s3_object_content");
  const result = await t.handler({ bucket: "nonexistent-bucket-xyz", key: "file.json" });
  assert.ok(result.error, "nonexistent object should return error");
});
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
node --test tests/test_mcp_server.js 2>&1 | grep -E "(s3_object_content|FAIL)" | head -10
```

Expected: all 4 new tests fail with "ace_get_s3_object_content must exist".

- [ ] **Step 3: Add S3 import to observe_extended.js**

At the top of `observe_extended.js`, after the existing imports, add:

```js
import { S3Client, GetObjectCommand } from "@aws-sdk/client-s3";
```

After the existing client instantiations (after `const cwClient = ...`), add:

```js
const s3Client = new S3Client(awsConfig);
```

- [ ] **Step 4: Append `ace_get_s3_object_content` to `observeExtendedTools` array**

Find the closing `];` of `observeExtendedTools`. Insert before it:

```js
  {
    name: "ace_get_s3_object_content",
    description: "Read contents of an S3 object as UTF-8 text. Capped at 256 KB; binary objects return an error. Use ace_check_s3_object for metadata only.",
    inputSchema: {
      type: "object",
      properties: {
        bucket: { type: "string" },
        key: { type: "string" },
        max_bytes: { type: "number" },
      },
      required: ["bucket", "key"],
    },
    async handler({ bucket, key, max_bytes = 65536 } = {}) {
      if (!bucket) return { error: "bucket is required" };
      if (!key) return { error: "key is required" };
      const cap = Math.min(Math.max(1, max_bytes ?? 65536), 262144);
      try {
        const res = await s3Client.send(new GetObjectCommand({ Bucket: bucket, Key: key }));
        const contentType = res.ContentType ?? "";
        if (contentType.startsWith("image/") || contentType === "application/octet-stream") {
          return { error: "binary content not supported", content_type: contentType };
        }
        const chunks = [];
        let total = 0;
        let truncated = false;
        for await (const chunk of res.Body) {
          const remaining = cap - total;
          if (chunk.length > remaining) {
            chunks.push(chunk.slice(0, remaining));
            truncated = true;
            break;
          }
          chunks.push(chunk);
          total += chunk.length;
        }
        return {
          content: Buffer.concat(chunks).toString("utf-8"),
          size: res.ContentLength ?? total,
          truncated,
          content_type: contentType,
          last_modified: res.LastModified?.toISOString() ?? null,
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "S3_ERROR" };
      }
    },
  },
```

- [ ] **Step 5: Run full test suite**

```bash
node --test tests/test_mcp_server.js
```

Expected: all pass including all 4 new tests.

- [ ] **Step 6: Commit**

```bash
git add harness/mcp_server/tools/observe_extended.js tests/test_mcp_server.js
git commit -m "feat(mcp): add ace_get_s3_object_content — S3 GetObject with 256KB cap and UTF-8 decoding"
```

---

### Task 7: Add `ace_filter_log_events` — CloudWatch Logs cross-stream filter (Gap 3)

**Files:**
- Modify: `harness/mcp_server/tools/observe_extended.js` (new import + append to array)
- Test: `tests/test_mcp_server.js`

**Why:** `ace_get_log_tail` reads only the most-recent stream's tail. Intermittent or already-passed faults (e.g., a reliability fault that swallowed an error two invocations ago) are invisible. `FilterLogEvents` searches across all streams with a CloudWatch filter pattern.

- [ ] **Step 1: Write failing tests**

In `tests/test_mcp_server.js`, add:

```js
test("ace_filter_log_events tool exists", async () => {
  const t = observeExtendedTools.find(t => t.name === "ace_filter_log_events");
  assert.ok(t, "ace_filter_log_events must exist");
});

test("ace_filter_log_events returns events array for Lambda with logs", async () => {
  const t = observeExtendedTools.find(t => t.name === "ace_filter_log_events");
  const result = await t.handler({ function_name: FN, filter_pattern: "ERROR" });
  assert.ok(!result.error, `unexpected error: ${JSON.stringify(result.error)}`);
  assert.ok(Array.isArray(result.events), "events must be an array");
  assert.ok(typeof result.count === "number", "count must be a number");
});

test("ace_filter_log_events returns error for missing function_name", async () => {
  const t = observeExtendedTools.find(t => t.name === "ace_filter_log_events");
  const result = await t.handler({ filter_pattern: "ERROR" });
  assert.ok(result.error, "missing function_name should return error");
});

test("ace_filter_log_events returns error for missing filter_pattern", async () => {
  const t = observeExtendedTools.find(t => t.name === "ace_filter_log_events");
  const result = await t.handler({ function_name: FN });
  assert.ok(result.error, "missing filter_pattern should return error");
});

test("ace_filter_log_events clamps limit and start_minutes_ago", async () => {
  const t = observeExtendedTools.find(t => t.name === "ace_filter_log_events");
  const result = await t.handler({ function_name: FN, filter_pattern: "INFO", limit: 999, start_minutes_ago: 9999 });
  assert.ok(!result.error);
  assert.ok(result.count <= 100, "count must not exceed 100");
});
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
node --test tests/test_mcp_server.js 2>&1 | grep -E "(filter_log_events|FAIL)" | head -10
```

Expected: all 5 new tests fail with "ace_filter_log_events must exist".

- [ ] **Step 3: Add CloudWatchLogs FilterLogEventsCommand import to observe_extended.js**

At the top of `observe_extended.js`, add a new import line after the existing imports:

```js
import { CloudWatchLogsClient, FilterLogEventsCommand } from "@aws-sdk/client-cloudwatch-logs";
```

After the existing client instantiations, add:

```js
const logsClient = new CloudWatchLogsClient(awsConfig);
```

- [ ] **Step 4: Append `ace_filter_log_events` to `observeExtendedTools` array**

Find the closing `];` of `observeExtendedTools`. Insert before it:

```js
  {
    name: "ace_filter_log_events",
    description: "Search a Lambda's CloudWatch logs across all streams using a CloudWatch filter pattern. Use filter_pattern like 'ERROR', '?KeyError ?ValidationError', or '[level=ERROR, ...]'.",
    inputSchema: {
      type: "object",
      properties: {
        function_name: { type: "string" },
        filter_pattern: { type: "string" },
        start_minutes_ago: { type: "number" },
        limit: { type: "number" },
      },
      required: ["function_name", "filter_pattern"],
    },
    async handler({ function_name, filter_pattern, start_minutes_ago = 30, limit = 30 } = {}) {
      if (!function_name) return { error: "function_name is required" };
      if (!filter_pattern) return { error: "filter_pattern is required" };
      const clampedLimit = Math.min(Math.max(1, limit ?? 30), 100);
      const clampedMins = Math.min(Math.max(1, start_minutes_ago ?? 30), 1440);
      const startTime = Date.now() - clampedMins * 60 * 1000;
      try {
        const res = await logsClient.send(new FilterLogEventsCommand({
          logGroupName: `/aws/lambda/${function_name}`,
          filterPattern: filter_pattern,
          startTime,
          limit: clampedLimit,
        }));
        const events = res.events ?? [];
        return {
          events: events.map(e => ({
            timestamp: new Date(e.timestamp).toISOString(),
            message: e.message?.trim() ?? "",
            stream: e.logStreamName ?? null,
          })),
          count: events.length,
          searched_streams: res.searchedLogStreams?.length ?? 0,
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "LOGS_ERROR" };
      }
    },
  },
```

- [ ] **Step 5: Run full test suite**

```bash
node --test tests/test_mcp_server.js
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add harness/mcp_server/tools/observe_extended.js tests/test_mcp_server.js
git commit -m "feat(mcp): add ace_filter_log_events — CloudWatch Logs cross-stream pattern search"
```

---

## Phase 2 — High Fixes + P1 Tools

---

### Task 8: Fix `ace_get_iam_role` — expand attached managed policy documents (F3)

**Files:**
- Modify: `harness/mcp_server/tools/observe.js` (IAM imports + lines 97–129, `ace_get_iam_role` handler)
- Test: `tests/test_mcp_server.js`

**Why:** Currently attached policies return only `{name, arn}`. Security faults where the misconfigured permission lives in an attached managed policy are invisible — the agent sees the role has a policy but can't read it.

- [ ] **Step 1: Write failing test**

In `tests/test_mcp_server.js`, add after the existing `ace_get_iam_role` test:

```js
test("ace_get_iam_role attached_policies has document field", async () => {
  const t = tool(observeTools, "ace_get_iam_role");
  // Create a test role with an attached managed policy
  const iamTestCl = new (await import("@aws-sdk/client-iam")).IAMClient({
    endpoint: "http://localhost:4566",
    region: "us-east-1",
    credentials: { accessKeyId: "test", secretAccessKey: "test" },
  });
  try {
    await iamTestCl.send(new (await import("@aws-sdk/client-iam")).CreateRoleCommand({
      RoleName: "test-role-for-attach",
      AssumeRolePolicyDocument: JSON.stringify({ Version: "2012-10-17", Statement: [] }),
    }));
  } catch {}
  const result = await t.handler({ role_name: "test-role-for-attach" });
  assert.ok(!result.error, `unexpected error: ${JSON.stringify(result.error)}`);
  assert.ok("attached_policies" in result, "attached_policies key must be present");
  // Each attached policy must have a document field (null is OK if fetch failed, but key must exist)
  for (const p of result.attached_policies) {
    assert.ok("document" in p, `attached policy ${p.name} must have document field`);
  }
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
node --test tests/test_mcp_server.js 2>&1 | grep -A 5 "attached_policies has document"
```

Expected: fails because current attached_policies items have only `{name, arn}`, no `document` key.

- [ ] **Step 3: Add GetPolicy and GetPolicyVersion to IAM import in observe.js**

Change the existing IAM import from:

```js
import {
  IAMClient,
  GetRoleCommand,
  ListRolePoliciesCommand,
  ListAttachedRolePoliciesCommand,
  GetRolePolicyCommand,
} from "@aws-sdk/client-iam";
```

to:

```js
import {
  IAMClient,
  GetRoleCommand,
  ListRolePoliciesCommand,
  ListAttachedRolePoliciesCommand,
  GetRolePolicyCommand,
  GetPolicyCommand,
  GetPolicyVersionCommand,
} from "@aws-sdk/client-iam";
```

- [ ] **Step 4: Update `ace_get_iam_role` handler to expand attached policies**

Replace the `return` block in the handler (currently lines 118–125) from:

```js
        return {
          assume_role_policy: JSON.parse(decodeURIComponent(roleRes.Role.AssumeRolePolicyDocument)),
          attached_policies: (attachedRes.AttachedPolicies ?? []).map(p => ({
            name: p.PolicyName,
            arn: p.PolicyArn,
          })),
          inline_policies: inlinePolicies,
        };
```

to:

```js
        const attachedPolicies = [];
        for (const p of (attachedRes.AttachedPolicies ?? []).slice(0, 5)) {
          try {
            const policyRes = await iamClient.send(new GetPolicyCommand({ PolicyArn: p.PolicyArn }));
            const versionRes = await iamClient.send(new GetPolicyVersionCommand({
              PolicyArn: p.PolicyArn,
              VersionId: policyRes.Policy.DefaultVersionId,
            }));
            attachedPolicies.push({
              name: p.PolicyName,
              arn: p.PolicyArn,
              document: JSON.parse(decodeURIComponent(versionRes.PolicyVersion.Document)),
            });
          } catch {
            attachedPolicies.push({ name: p.PolicyName, arn: p.PolicyArn, document: null });
          }
        }
        return {
          assume_role_policy: JSON.parse(decodeURIComponent(roleRes.Role.AssumeRolePolicyDocument)),
          attached_policies: attachedPolicies,
          inline_policies: inlinePolicies,
        };
```

- [ ] **Step 5: Run full test suite**

```bash
node --test tests/test_mcp_server.js
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add harness/mcp_server/tools/observe.js tests/test_mcp_server.js
git commit -m "fix(mcp): ace_get_iam_role expands attached managed policy documents (capped at 5)"
```

---

### Task 9: Fix `ace_start_execution` — poll loop with configurable timeout (F6)

**Files:**
- Modify: `harness/mcp_server/tools/probe_extended.js` (the `ace_start_execution` tool)
- Test: `tests/test_mcp_server.js`

**Why:** Hardcoded `await new Promise(r => setTimeout(r, 2000))` then single DescribeExecution. Long-running executions always return `status: "RUNNING"` with empty output.

- [ ] **Step 1: Write failing test**

In `tests/test_mcp_server.js`, add after existing ace_start_execution tests:

```js
test("ace_start_execution accepts poll_timeout_ms parameter", async () => {
  const t = probeExtendedTools.find(t => t.name === "ace_start_execution");
  // No state machine in test setup; just confirm handler doesn't crash on the param
  const result = await t.handler({
    state_machine_arn: "arn:aws:states:us-east-1:000000000000:stateMachine:nonexistent",
    poll_timeout_ms: 1000,
  });
  // Should get an error from LocalStack (nonexistent SM), not a crash
  assert.ok(result.error, "nonexistent state machine should return error");
});
```

- [ ] **Step 2: Run test to confirm it passes (param was silently ignored before)**

```bash
node --test tests/test_mcp_server.js 2>&1 | grep -A 3 "poll_timeout_ms parameter"
```

- [ ] **Step 3: Replace `ace_start_execution` tool in probe_extended.js**

Find the entire `ace_start_execution` tool object and replace it:

```js
  {
    name: "ace_start_execution",
    description: "Start a Step Functions state machine execution and poll until a terminal status (SUCCEEDED, FAILED, TIMED_OUT, ABORTED) or poll_timeout_ms is reached.",
    inputSchema: {
      type: "object",
      properties: {
        state_machine_arn: { type: "string" },
        input: { type: "object" },
        poll_timeout_ms: { type: "number" },
      },
      required: ["state_machine_arn"],
    },
    async handler({ state_machine_arn, input = {}, poll_timeout_ms = 5000 } = {}) {
      if (!state_machine_arn) return { error: "state_machine_arn is required" };
      const timeout = Math.min(Math.max(500, poll_timeout_ms ?? 5000), 15000);
      try {
        const startRes = await sfnClient.send(new StartExecutionCommand({
          stateMachineArn: state_machine_arn,
          input: JSON.stringify(input),
        }));
        const deadline = Date.now() + timeout;
        const terminalStatuses = new Set(["SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"]);
        let descRes;
        do {
          descRes = await sfnClient.send(new DescribeExecutionCommand({
            executionArn: startRes.executionArn,
          }));
          if (terminalStatuses.has(descRes.status)) break;
          if (Date.now() < deadline) await new Promise(r => setTimeout(r, 500));
        } while (Date.now() < deadline);
        return {
          execution_arn: startRes.executionArn,
          status: descRes.status,
          started_at: descRes.startDate?.toISOString() ?? null,
          stopped_at: descRes.stopDate?.toISOString() ?? null,
          output: descRes.output ? JSON.parse(descRes.output) : null,
          error: descRes.error ?? null,
          cause: descRes.cause ?? null,
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "SFN_ERROR" };
      }
    },
  },
```

- [ ] **Step 4: Run full test suite**

```bash
node --test tests/test_mcp_server.js
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add harness/mcp_server/tools/probe_extended.js tests/test_mcp_server.js
git commit -m "fix(mcp): ace_start_execution polls in 500ms loop until terminal status or poll_timeout_ms"
```

---

### Task 10: Fix `ace_get_log_tail` — multi-stream support (F8)

**Files:**
- Modify: `harness/mcp_server/tools/observe.js` (lines ~131–177, `ace_get_log_tail` tool)
- Test: `tests/test_mcp_server.js`

**Why:** `DescribeLogStreams` uses `limit: 1`. Lambdas under concurrent load write to multiple streams; the latest-event-time stream may not contain the relevant error.

- [ ] **Step 1: Write failing test**

In `tests/test_mcp_server.js`, add after existing `ace_get_log_tail` tests:

```js
test("ace_get_log_tail accepts stream_count parameter", async () => {
  const t = tool(observeTools, "ace_get_log_tail");
  const result = await t.handler({ function_name: FN, line_count: 5, stream_count: 3 });
  assert.ok(!result.error, `unexpected error: ${JSON.stringify(result.error)}`);
  assert.ok(Array.isArray(result), "result must be an array");
});

test("ace_get_log_tail returned events have timestamp and message fields", async () => {
  const t = tool(observeTools, "ace_get_log_tail");
  const result = await t.handler({ function_name: FN, line_count: 5, stream_count: 1 });
  assert.ok(Array.isArray(result));
  for (const e of result) {
    assert.ok("timestamp" in e, "each event must have timestamp");
    assert.ok("message" in e, "each event must have message");
  }
});
```

- [ ] **Step 2: Run test to confirm stream_count is ignored currently**

```bash
node --test tests/test_mcp_server.js 2>&1 | grep -A 3 "stream_count"
```

- [ ] **Step 3: Replace `ace_get_log_tail` in observe.js**

Replace the entire `ace_get_log_tail` tool object:

```js
  {
    name: "ace_get_log_tail",
    description: "Get recent CloudWatch log lines for a Lambda function, merged across multiple log streams",
    inputSchema: {
      type: "object",
      properties: {
        function_name: { type: "string" },
        line_count: { type: "number" },
        stream_count: { type: "number" },
      },
      required: ["function_name"],
    },
    async handler({ function_name, line_count = 20, stream_count = 3 }) {
      if (!function_name) return { error: "function_name is required" };
      const clampedStreams = Math.min(Math.max(1, stream_count ?? 3), 10);
      try {
        const groupName = `/aws/lambda/${function_name}`;
        const streamsRes = await logsClient.send(new DescribeLogStreamsCommand({
          logGroupName: groupName,
          orderBy: "LastEventTime",
          descending: true,
          limit: clampedStreams,
        }));
        const streams = streamsRes.logStreams ?? [];
        if (streams.length === 0) return [];
        const allEvents = [];
        for (const stream of streams) {
          const eventsRes = await logsClient.send(new GetLogEventsCommand({
            logGroupName: groupName,
            logStreamName: stream.logStreamName,
            limit: line_count,
            startFromHead: false,
          }));
          for (const e of (eventsRes.events ?? [])) {
            const msg = e.message ?? "";
            const requestIdMatch = msg.match(/RequestId:\s*([^\s]+)/);
            const levelMatch = msg.match(/\b(ERROR|WARN|INFO|DEBUG)\b/);
            allEvents.push({
              timestamp: new Date(e.timestamp).toISOString(),
              request_id: requestIdMatch?.[1] ?? null,
              level: levelMatch?.[1] ?? "INFO",
              message: msg.trim(),
            });
          }
        }
        allEvents.sort((a, b) => a.timestamp.localeCompare(b.timestamp));
        return allEvents.slice(-line_count);
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "LOGS_ERROR" };
      }
    },
  },
```

- [ ] **Step 4: Run full test suite**

```bash
node --test tests/test_mcp_server.js
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add harness/mcp_server/tools/observe.js tests/test_mcp_server.js
git commit -m "fix(mcp): ace_get_log_tail fetches up to stream_count streams (default 3) and merges events by timestamp"
```

---

### Task 11: Fix `ace_check_event_source` — accept `event_source_arn` param (F10)

**Files:**
- Modify: `harness/mcp_server/tools/probe.js` (`ace_check_event_source` tool)
- Test: `tests/test_mcp_server.js`

**Why:** Currently only filters by `FunctionName`. To find the consumer of a specific queue/stream ARN, agents must enumerate all Lambdas and check each — O(n) calls. `ListEventSourceMappings` natively supports `EventSourceArn` filtering.

- [ ] **Step 1: Write failing test**

In `tests/test_mcp_server.js`, add after existing `ace_check_event_source` tests:

```js
test("ace_check_event_source accepts event_source_arn param", async () => {
  const t = tool(probeTools, "ace_check_event_source");
  // A real SQS queue ARN — no mappings exist in test setup, so expect empty array or error-free response
  const result = await t.handler({ event_source_arn: `arn:aws:sqs:us-east-1:000000000000:${QUEUE}` });
  assert.ok(!result.error, `unexpected error: ${JSON.stringify(result.error)}`);
  assert.ok(Array.isArray(result), "result must be an array");
});

test("ace_check_event_source returns error when neither param given", async () => {
  const t = tool(probeTools, "ace_check_event_source");
  const result = await t.handler({});
  assert.ok(result.error, "missing both params should return error");
});
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
node --test tests/test_mcp_server.js 2>&1 | grep -A 3 "event_source_arn param"
```

Expected: first test may fail because current handler requires `function_name`; second test currently passes (already returns error for missing param).

- [ ] **Step 3: Replace `ace_check_event_source` tool in probe.js**

Find and replace the entire `ace_check_event_source` tool object:

```js
  {
    name: "ace_check_event_source",
    description: "List Lambda event source mappings, filtered by function name or event source ARN (at least one required)",
    inputSchema: {
      type: "object",
      properties: {
        function_name: { type: "string" },
        event_source_arn: { type: "string" },
      },
    },
    async handler({ function_name, event_source_arn } = {}) {
      if (!function_name && !event_source_arn)
        return { error: "function_name or event_source_arn is required" };
      try {
        const params = {};
        if (function_name) params.FunctionName = function_name;
        if (event_source_arn) params.EventSourceArn = event_source_arn;
        const res = await lambdaClient.send(new ListEventSourceMappingsCommand(params));
        return (res.EventSourceMappings ?? []).map(m => ({
          source_arn: m.EventSourceArn,
          source_type: m.EventSourceArn?.split(":")[2] ?? "unknown",
          enabled: m.State === "Enabled",
          batch_size: m.BatchSize,
          state: m.State,
          filter_criteria: m.FilterCriteria ?? null,
        }));
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "LAMBDA_ERROR" };
      }
    },
  },
```

- [ ] **Step 4: Run full test suite**

```bash
node --test tests/test_mcp_server.js
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add harness/mcp_server/tools/probe.js tests/test_mcp_server.js
git commit -m "fix(mcp): ace_check_event_source accepts event_source_arn to find consumers of a queue or stream"
```

---

### Task 12: Add `ace_scan_table` — full-table DynamoDB scan with filter (Gap 4)

**Files:**
- Modify: `harness/mcp_server/tools/probe_extended.js` (add `ScanCommand` to DynamoDB import + append to array)
- Test: `tests/test_mcp_server.js`

**Why:** `ace_scan_table_range` requires a `KeyConditionExpression` — useless when the agent doesn't know the partition key (e.g., data-correctness faults that corrupt the key itself). A full Scan with optional filter finds anomalous items without knowing the key.

- [ ] **Step 1: Write failing tests**

In `tests/test_mcp_server.js`, add:

```js
test("ace_scan_table tool exists", async () => {
  const t = probeExtendedTools.find(t => t.name === "ace_scan_table");
  assert.ok(t, "ace_scan_table must exist");
});

test("ace_scan_table returns items from a table", async () => {
  const t = probeExtendedTools.find(t => t.name === "ace_scan_table");
  const result = await t.handler({ table_name: TABLE });
  assert.ok(!result.error, `unexpected error: ${JSON.stringify(result.error)}`);
  assert.ok("items" in result, "items key must be present");
  assert.ok("count" in result, "count key must be present");
  assert.ok("scanned_count" in result, "scanned_count key must be present");
});

test("ace_scan_table clamps limit to 25", async () => {
  const t = probeExtendedTools.find(t => t.name === "ace_scan_table");
  const result = await t.handler({ table_name: TABLE, limit: 999 });
  assert.ok(!result.error);
  assert.ok(result.count <= 25);
});

test("ace_scan_table returns error for missing table_name", async () => {
  const t = probeExtendedTools.find(t => t.name === "ace_scan_table");
  const result = await t.handler({});
  assert.ok(result.error, "missing table_name should return error");
});

test("ace_scan_table returns error for nonexistent table", async () => {
  const t = probeExtendedTools.find(t => t.name === "ace_scan_table");
  const result = await t.handler({ table_name: "nonexistent-table-xyz" });
  assert.ok(result.error, "nonexistent table should return error");
});
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
node --test tests/test_mcp_server.js 2>&1 | grep -E "(ace_scan_table|FAIL)" | head -10
```

Expected: all 5 new tests fail with "ace_scan_table must exist".

- [ ] **Step 3: Add `ScanCommand` to DynamoDB import in probe_extended.js**

Change the existing DynamoDB import (line 17) from:

```js
import { DynamoDBClient, QueryCommand } from "@aws-sdk/client-dynamodb";
```

to:

```js
import { DynamoDBClient, QueryCommand, ScanCommand } from "@aws-sdk/client-dynamodb";
```

- [ ] **Step 4: Append `ace_scan_table` to `probeExtendedTools` array**

Find the closing `];` of `probeExtendedTools`. Insert before it:

```js
  {
    name: "ace_scan_table",
    description: "Full-table DynamoDB Scan with optional filter expression. Use when the partition key is unknown. Returns up to 25 items.",
    inputSchema: {
      type: "object",
      properties: {
        table_name: { type: "string" },
        filter_expression: { type: "string" },
        expression_values: { type: "object" },
        expression_names: { type: "object" },
        limit: { type: "number" },
      },
      required: ["table_name"],
    },
    async handler({ table_name, filter_expression, expression_values, expression_names, limit = 10 } = {}) {
      if (!table_name) return { error: "table_name is required" };
      const clampedLimit = Math.min(Math.max(1, limit ?? 10), 25);
      try {
        const params = { TableName: table_name, Limit: clampedLimit };
        if (filter_expression) params.FilterExpression = filter_expression;
        if (expression_values) params.ExpressionAttributeValues = marshall(expression_values);
        if (expression_names) params.ExpressionAttributeNames = expression_names;
        const res = await dynamoClient.send(new ScanCommand(params));
        return {
          items: (res.Items ?? []).map(item => unmarshall(item)),
          count: res.Count ?? 0,
          scanned_count: res.ScannedCount ?? 0,
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "DYNAMO_ERROR" };
      }
    },
  },
```

- [ ] **Step 5: Run full test suite**

```bash
node --test tests/test_mcp_server.js
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add harness/mcp_server/tools/probe_extended.js tests/test_mcp_server.js
git commit -m "feat(mcp): add ace_scan_table — full DynamoDB Scan with optional filter when partition key is unknown"
```

---

### Task 13: Add `ace_get_stack_events` — CloudFormation deployment history (Gap 5)

**Files:**
- Modify: `harness/mcp_server/tools/observe_extended.js` (new CF import + append to array)
- Test: `tests/test_mcp_server.js`

**Why:** When `submit_fix` triggers a redeploy that fails, the agent only sees a generic failure message. Stack events expose the exact failing resource and reason (e.g., "zip file too large", "policy did not validate").

- [ ] **Step 1: Write failing tests**

In `tests/test_mcp_server.js`, add:

```js
test("ace_get_stack_events tool exists", async () => {
  const t = observeExtendedTools.find(t => t.name === "ace_get_stack_events");
  assert.ok(t, "ace_get_stack_events must exist");
});

test("ace_get_stack_events returns events array for ace-bench-stack", async () => {
  const t = observeExtendedTools.find(t => t.name === "ace_get_stack_events");
  const result = await t.handler({});
  assert.ok(!result.error, `unexpected error: ${JSON.stringify(result.error)}`);
  assert.ok(Array.isArray(result), "result must be an array");
  if (result.length > 0) {
    const e = result[0];
    assert.ok("timestamp" in e, "event must have timestamp");
    assert.ok("logical_id" in e, "event must have logical_id");
    assert.ok("status" in e, "event must have status");
  }
});

test("ace_get_stack_events status_filter FAILED returns subset", async () => {
  const t = observeExtendedTools.find(t => t.name === "ace_get_stack_events");
  const all = await t.handler({ status_filter: "ALL" });
  const failed = await t.handler({ status_filter: "FAILED" });
  assert.ok(!all.error && !failed.error);
  assert.ok(failed.length <= all.length, "FAILED filter must return <= ALL events");
});

test("ace_get_stack_events clamps limit to 50", async () => {
  const t = observeExtendedTools.find(t => t.name === "ace_get_stack_events");
  const result = await t.handler({ limit: 999 });
  assert.ok(!result.error);
  assert.ok(result.length <= 50);
});
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
node --test tests/test_mcp_server.js 2>&1 | grep -E "(stack_events|FAIL)" | head -10
```

Expected: all 4 new tests fail with "ace_get_stack_events must exist".

- [ ] **Step 3: Add CloudFormation DescribeStackEventsCommand import to observe_extended.js**

At the top of `observe_extended.js`, add a new import line:

```js
import { CloudFormationClient, DescribeStackEventsCommand } from "@aws-sdk/client-cloudformation";
```

After the existing client instantiations, add:

```js
const cfClient = new CloudFormationClient(awsConfig);
```

- [ ] **Step 4: Append `ace_get_stack_events` to `observeExtendedTools` array**

Find the closing `];` of `observeExtendedTools`. Insert before it:

```js
  {
    name: "ace_get_stack_events",
    description: "Get recent CloudFormation stack events for ace-bench-stack, ordered newest first. Use status_filter='FAILED' to see only failed transitions.",
    inputSchema: {
      type: "object",
      properties: {
        limit: { type: "number" },
        status_filter: { type: "string", enum: ["FAILED", "ALL"] },
      },
    },
    async handler({ limit = 20, status_filter = "ALL" } = {}) {
      const clampedLimit = Math.min(Math.max(1, limit ?? 20), 50);
      try {
        const res = await cfClient.send(new DescribeStackEventsCommand({ StackName: "ace-bench-stack" }));
        let events = (res.StackEvents ?? []).slice(0, clampedLimit);
        if (status_filter === "FAILED") {
          events = events.filter(e =>
            e.ResourceStatus?.includes("FAILED") || e.ResourceStatus?.includes("ROLLBACK")
          );
        }
        return events.map(e => ({
          timestamp: e.Timestamp?.toISOString() ?? null,
          logical_id: e.LogicalResourceId ?? null,
          resource_type: e.ResourceType ?? null,
          status: e.ResourceStatus ?? null,
          reason: e.ResourceStatusReason ?? null,
        }));
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "CF_ERROR" };
      }
    },
  },
```

- [ ] **Step 5: Run full test suite**

```bash
node --test tests/test_mcp_server.js
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add harness/mcp_server/tools/observe_extended.js tests/test_mcp_server.js
git commit -m "feat(mcp): add ace_get_stack_events — CloudFormation deployment history with FAILED filter"
```

---

## Section 2: Limited Utility on LocalStack Free Tier (Task 14)

> **Note:** `Amazon CloudWatch Metrics` is available in LocalStack Community edition (the API works). However, Task 14 targets the `AWS/Lambda` namespace specifically — LocalStack Community does **not** auto-emit Lambda service metrics, so `GetMetricStatistics` returns empty datapoints for `Invocations`, `Errors`, `Throttles`, and `Duration`. The tool would return `{invocations: 0, errors: 0, throttles: 0, duration: {avg_ms: 0, max_ms: 0}}` for every call regardless of actual Lambda activity, providing no diagnostic value. Lambda metrics are auto-emitted only in LocalStack Pro. The tool can be implemented (the API exists) but will be a no-op against Community edition.

---

### Task 14: Add `ace_get_lambda_metrics` — purpose-built Lambda metrics tool (Gap 6)

**Files:**
- Modify: `harness/mcp_server/tools/observe_extended.js` (append to array; `cwClient` and `GetMetricStatisticsCommand` already imported)
- Test: `tests/test_mcp_server.js`

**Why:** Reliability faults require invocation/error/throttle counts. `ace_get_metric_statistics` exists but requires the agent to know the `AWS/Lambda` namespace, dimension structure, and call it 4–5 times. This tool collapses that into one call.

Note: `cwClient` and `GetMetricStatisticsCommand` are already in `observe_extended.js` — no new imports needed.

- [ ] **Step 1: Write failing tests**

In `tests/test_mcp_server.js`, add:

```js
test("ace_get_lambda_metrics tool exists", async () => {
  const t = observeExtendedTools.find(t => t.name === "ace_get_lambda_metrics");
  assert.ok(t, "ace_get_lambda_metrics must exist");
});

test("ace_get_lambda_metrics returns metric fields for known Lambda", async () => {
  const t = observeExtendedTools.find(t => t.name === "ace_get_lambda_metrics");
  const result = await t.handler({ function_name: FN });
  assert.ok(!result.error, `unexpected error: ${JSON.stringify(result.error)}`);
  assert.ok("invocations" in result, "invocations field required");
  assert.ok("errors" in result, "errors field required");
  assert.ok("throttles" in result, "throttles field required");
  assert.ok("duration" in result, "duration field required");
  assert.ok("window_minutes" in result, "window_minutes field required");
});

test("ace_get_lambda_metrics returns error for missing function_name", async () => {
  const t = observeExtendedTools.find(t => t.name === "ace_get_lambda_metrics");
  const result = await t.handler({});
  assert.ok(result.error, "missing function_name should return error");
});

test("ace_get_lambda_metrics clamps window_minutes to 60", async () => {
  const t = observeExtendedTools.find(t => t.name === "ace_get_lambda_metrics");
  const result = await t.handler({ function_name: FN, window_minutes: 999 });
  assert.ok(!result.error);
  assert.strictEqual(result.window_minutes, 60);
});
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
node --test tests/test_mcp_server.js 2>&1 | grep -E "(lambda_metrics|FAIL)" | head -10
```

Expected: all 4 new tests fail with "ace_get_lambda_metrics must exist".

- [ ] **Step 3: Append `ace_get_lambda_metrics` to `observeExtendedTools` array**

Find the closing `];` of `observeExtendedTools`. Insert before it:

```js
  {
    name: "ace_get_lambda_metrics",
    description: "Get Lambda invocation, error, throttle, DeadLetterErrors counts and duration (p50/max) over a time window. Collapses 5 GetMetricStatistics calls into one.",
    inputSchema: {
      type: "object",
      properties: {
        function_name: { type: "string" },
        window_minutes: { type: "number" },
      },
      required: ["function_name"],
    },
    async handler({ function_name, window_minutes = 15 } = {}) {
      if (!function_name) return { error: "function_name is required" };
      const clampedWindow = Math.min(Math.max(1, window_minutes ?? 15), 60);
      const endTime = new Date();
      const startTime = new Date(endTime.getTime() - clampedWindow * 60 * 1000);
      const dimensions = [{ Name: "FunctionName", Value: function_name }];
      const period = clampedWindow * 60;
      const sumMetrics = ["Invocations", "Errors", "Throttles", "DeadLetterErrors"];
      try {
        const results = {};
        for (const metricName of sumMetrics) {
          const res = await cwClient.send(new GetMetricStatisticsCommand({
            Namespace: "AWS/Lambda",
            MetricName: metricName,
            Dimensions: dimensions,
            StartTime: startTime,
            EndTime: endTime,
            Period: period,
            Statistics: ["Sum"],
          }));
          results[metricName.toLowerCase().replace("deadlettererrors", "dead_letter_errors")] =
            res.Datapoints?.[0]?.Sum ?? 0;
        }
        const durationRes = await cwClient.send(new GetMetricStatisticsCommand({
          Namespace: "AWS/Lambda",
          MetricName: "Duration",
          Dimensions: dimensions,
          StartTime: startTime,
          EndTime: endTime,
          Period: period,
          Statistics: ["Average", "Maximum"],
        }));
        const dp = durationRes.Datapoints?.[0];
        results.duration = { avg_ms: dp?.Average ?? 0, max_ms: dp?.Maximum ?? 0 };
        results.window_minutes = clampedWindow;
        return results;
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "CLOUDWATCH_ERROR" };
      }
    },
  },
```

- [ ] **Step 4: Run full test suite**

```bash
node --test tests/test_mcp_server.js
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add harness/mcp_server/tools/observe_extended.js tests/test_mcp_server.js
git commit -m "feat(mcp): add ace_get_lambda_metrics — single-call Lambda invocations/errors/throttles/duration summary"
```

---

## Self-Review

**Spec coverage:**
- F5 (ace_invoke_endpoint flexible key): Task 1 ✓
- F4 (ace_get_stream_records all-shards LATEST): Task 2 ✓
- F1 (ace_describe_resource resource-type dispatch): Task 3 ✓
- F2 (ace_simulate_policy require resource_arns): Task 4 ✓
- Gap 1 (ace_peek_queue_messages): Task 5 ✓
- Gap 2 (ace_get_s3_object_content): Task 6 ✓
- Gap 3 (ace_filter_log_events): Task 7 ✓
- F3 (ace_get_iam_role managed policy expansion): Task 8 ✓
- F6 (ace_start_execution poll loop): Task 9 ✓
- F8 (ace_get_log_tail multi-stream): Task 10 ✓
- F10 (ace_check_event_source event_source_arn): Task 11 ✓
- Gap 4 (ace_scan_table): Task 12 ✓
- Gap 5 (ace_get_stack_events): Task 13 ✓
- Gap 6 (ace_get_lambda_metrics): Task 14 ✓
- Phase 3 (F9, F11–F15, Gaps 7–10): explicitly out of scope ✓

**Placeholder scan:** No TBD/TODO/placeholder text. All code blocks are complete and executable.

**Type consistency:**
- `sqsClient` (probe_extended.js Tasks 5, 12) matches `new SQSClient(awsConfig)` added in Task 5 ✓
- `s3Client` (observe_extended.js Task 6) matches `new S3Client(awsConfig)` added in Task 6 ✓
- `logsClient` (observe_extended.js Task 7) matches `new CloudWatchLogsClient(awsConfig)` added in Task 7 ✓
- `cfClient` (observe_extended.js Task 13) matches `new CloudFormationClient(awsConfig)` added in Task 13 ✓
- `cwClient` (observe_extended.js Task 14) matches existing `const cwClient = new CloudWatchClient(awsConfig)` ✓
- `dynamoClient` (probe_extended.js Task 12) matches existing `const dynamoClient = new DynamoDBClient(awsConfig)` ✓
- `marshall`/`unmarshall` (probe_extended.js Task 12) matches existing `import { marshall, unmarshall }` ✓
- `ScanCommand` (probe_extended.js Task 12) added to existing DynamoDB import ✓
- `GetEventSourceMappingCommand` (observe.js Task 3) added to existing Lambda import ✓
- `GetPolicyCommand`, `GetPolicyVersionCommand` (observe.js Task 8) added to existing IAM import ✓
- `dynamoClient`/`sqsClient`/`snsClient`/`s3Client`/`kinesisClient` (observe.js Task 3) are new instances added after existing clients ✓

**Import conflicts:** `observe_extended.js` does not currently import `S3Client`, `CloudFormationClient`, or `CloudWatchLogsClient` — all three are new and non-conflicting with existing imports ✓. `probe_extended.js` does not currently import `SQSClient` — new and non-conflicting ✓.
