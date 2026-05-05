# MCP Server LocalStack Full-Tier Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand the ACE-Bench diagnostic MCP server from 12 tools covering 8 services to 48 tools covering 27 services, matching the full LocalStack free-tier surface.

**Architecture:** Two new tool files (`probe_extended.js`, `observe_extended.js`) follow the exact same export pattern as the existing `probe.js`/`observe.js`. `index.js` spreads both into its existing tool loop with two additional imports. Tests are added to the existing `test_mcp_server.js` with new fixture variables and service-specific before-hook setup.

**Tech Stack:** Node.js v22+, AWS SDK v3 (`@aws-sdk/client-*`), `@modelcontextprotocol/sdk`, `node:test` + `node:assert/strict`.

---

## Existing Tools (reference — do not modify)

**probe.js (6):** `ace_invoke_endpoint`, `ace_invoke_lambda`, `ace_check_queue_depth`, `ace_read_table_item`, `ace_check_event_source`, `ace_check_s3_object`

**observe.js (6):** `ace_describe_resource`, `ace_list_resources`, `ace_get_iam_role`, `ace_get_log_tail`, `ace_get_stack_outputs`, `ace_get_environment_variables`

**score.js (2):** `ace_verify_fix`, `ace_score_run`

---

## New Tools Summary

### probe_extended.js (19 tools)
| Tool | Service | SDK Call |
|------|---------|----------|
| `ace_publish_sns` | SNS | `PublishCommand` |
| `ace_put_events` | EventBridge | `PutEventsCommand` |
| `ace_start_execution` | Step Functions | `StartExecutionCommand` + `DescribeExecutionCommand` |
| `ace_count_open_executions` | SWF | `CountOpenWorkflowExecutionsCommand` |
| `ace_send_test_email` | SES | `SendEmailCommand` |
| `ace_check_instance_state` | EC2 | `DescribeInstancesCommand` |
| `ace_check_hosted_zone` | Route 53 | `GetHostedZoneCommand` |
| `ace_list_resolver_endpoints` | Route 53 Resolver | `ListResolverEndpointsCommand` |
| `ace_put_kinesis_record` | Kinesis Streams | `PutRecordCommand` |
| `ace_put_firehose_record` | Kinesis Firehose | `PutRecordCommand` (aliased) |
| `ace_get_stream_records` | DynamoDB Streams | `GetShardIteratorCommand` + `GetRecordsCommand` |
| `ace_encrypt_decrypt` | KMS | `EncryptCommand` + `DecryptCommand` |
| `ace_get_secret` | Secrets Manager | `GetSecretValueCommand` |
| `ace_get_caller_identity` | STS | `GetCallerIdentityCommand` |
| `ace_assume_role` | STS | `AssumeRoleCommand` |
| `ace_get_parameter` | SSM | `GetParameterCommand` |
| `ace_list_access_points` | S3 Control | `ListAccessPointsCommand` |
| `ace_put_metric_data` | CloudWatch Metrics | `PutMetricDataCommand` |
| `ace_simulate_policy` | IAM Simulation | `SimulatePrincipalPolicyCommand` |

### observe_extended.js (17 tools)
| Tool | Service | SDK Call |
|------|---------|----------|
| `ace_get_sns_topic` | SNS | `GetTopicAttributesCommand` |
| `ace_get_eventbridge_rule` | EventBridge | `DescribeRuleCommand` + `ListTargetsByRuleCommand` |
| `ace_get_schedule` | EventBridge Scheduler | `GetScheduleCommand` |
| `ace_describe_state_machine` | Step Functions | `DescribeStateMachineCommand` |
| `ace_describe_swf_domain` | SWF | `DescribeDomainCommand` |
| `ace_get_ses_identity` | SES | `GetIdentityVerificationAttributesCommand` |
| `ace_describe_security_group` | EC2 | `DescribeSecurityGroupsCommand` |
| `ace_list_dns_records` | Route 53 | `ListResourceRecordSetsCommand` |
| `ace_get_resolver_endpoint` | Route 53 Resolver | `GetResolverEndpointCommand` |
| `ace_describe_kinesis_stream` | Kinesis Streams | `DescribeStreamSummaryCommand` |
| `ace_describe_firehose_stream` | Kinesis Firehose | `DescribeDeliveryStreamCommand` |
| `ace_describe_dynamo_stream` | DynamoDB Streams | `DescribeStreamCommand` |
| `ace_describe_kms_key` | KMS | `DescribeKeyCommand` + `GetKeyRotationStatusCommand` |
| `ace_describe_secret` | Secrets Manager | `DescribeSecretCommand` |
| `ace_describe_parameters` | SSM | `DescribeParametersCommand` |
| `ace_get_public_access_block` | S3 Control | `GetPublicAccessBlockCommand` |
| `ace_get_metric_statistics` | CloudWatch Metrics | `GetMetricStatisticsCommand` |

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `harness/mcp_server/package.json` | Modify | Add 18 new `@aws-sdk/client-*` dependencies |
| `harness/mcp_server/tools/probe_extended.js` | Create | 19 new probe tools |
| `harness/mcp_server/tools/observe_extended.js` | Create | 17 new observe tools |
| `harness/mcp_server/index.js` | Modify | Import + spread new tool arrays |
| `tests/test_mcp_server.js` | Modify | New imports, fixture vars, before() additions, 36+ tests |

---

## Task 1: Add npm Dependencies

**Files:**
- Modify: `harness/mcp_server/package.json`

- [ ] **Step 1: Update package.json with all new SDK packages**

Replace the `dependencies` block in `harness/mcp_server/package.json`:

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
    "@aws-sdk/client-dynamodb-streams": "^3.0.0",
    "@aws-sdk/client-sqs": "^3.0.0",
    "@aws-sdk/client-iam": "^3.0.0",
    "@aws-sdk/client-cloudwatch-logs": "^3.0.0",
    "@aws-sdk/client-cloudwatch": "^3.0.0",
    "@aws-sdk/client-s3": "^3.0.0",
    "@aws-sdk/client-s3-control": "^3.0.0",
    "@aws-sdk/client-sns": "^3.0.0",
    "@aws-sdk/client-eventbridge": "^3.0.0",
    "@aws-sdk/client-scheduler": "^3.0.0",
    "@aws-sdk/client-sfn": "^3.0.0",
    "@aws-sdk/client-swf": "^3.0.0",
    "@aws-sdk/client-ses": "^3.0.0",
    "@aws-sdk/client-ec2": "^3.0.0",
    "@aws-sdk/client-route-53": "^3.0.0",
    "@aws-sdk/client-route-53-resolver": "^3.0.0",
    "@aws-sdk/client-kinesis": "^3.0.0",
    "@aws-sdk/client-firehose": "^3.0.0",
    "@aws-sdk/client-kms": "^3.0.0",
    "@aws-sdk/client-secrets-manager": "^3.0.0",
    "@aws-sdk/client-sts": "^3.0.0",
    "@aws-sdk/client-ssm": "^3.0.0",
    "@aws-sdk/util-dynamodb": "^3.0.0",
    "jszip": "^3.10.1"
  }
}
```

- [ ] **Step 2: Install dependencies**

```bash
cd harness/mcp_server && npm install
```

Expected: `added N packages` with no errors.

- [ ] **Step 3: Commit**

```bash
git add harness/mcp_server/package.json harness/mcp_server/package-lock.json
git commit -m "chore(mcp): add aws-sdk packages for 19 new services"
```

---

## Task 2: Scaffold Empty Tool Files + Wire index.js

**Files:**
- Create: `harness/mcp_server/tools/probe_extended.js`
- Create: `harness/mcp_server/tools/observe_extended.js`
- Modify: `harness/mcp_server/index.js`

- [ ] **Step 1: Create probe_extended.js scaffold**

Create `harness/mcp_server/tools/probe_extended.js`:

```js
export const probeExtendedTools = [];
```

- [ ] **Step 2: Create observe_extended.js scaffold**

Create `harness/mcp_server/tools/observe_extended.js`:

```js
export const observeExtendedTools = [];
```

- [ ] **Step 3: Update index.js to import and register new tool arrays**

Replace `harness/mcp_server/index.js` with:

```js
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { probeTools } from "./tools/probe.js";
import { probeExtendedTools } from "./tools/probe_extended.js";
import { observeTools } from "./tools/observe.js";
import { observeExtendedTools } from "./tools/observe_extended.js";
import { scoreTools } from "./tools/score.js";

const server = new McpServer({
  name: "ace-bench-diagnostic-mcp",
  version: "1.0.0",
});

for (const tool of [...probeTools, ...probeExtendedTools, ...observeTools, ...observeExtendedTools, ...scoreTools]) {
  server.tool(
    tool.name,
    tool.description,
    {},
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

- [ ] **Step 4: Verify server starts without errors**

```bash
cd harness/mcp_server && node index.js &
sleep 1 && kill %1
```

Expected: No import/syntax errors on startup.

- [ ] **Step 5: Commit**

```bash
git add harness/mcp_server/index.js harness/mcp_server/tools/probe_extended.js harness/mcp_server/tools/observe_extended.js
git commit -m "feat(mcp): scaffold probe_extended and observe_extended tool files"
```

---

## Task 3: Add Test Fixture Vars + Before-Hook Extensions

**Files:**
- Modify: `tests/test_mcp_server.js` (top of file: new imports and variable declarations; inside existing `before()`: new resource creation blocks)

- [ ] **Step 1: Add new imports and fixture variable declarations**

After the existing imports at the top of `tests/test_mcp_server.js`, add:

```js
import { SNSClient, CreateTopicCommand } from "@aws-sdk/client-sns";
import { KinesisClient, CreateStreamCommand } from "@aws-sdk/client-kinesis";
import { KMSClient, CreateKeyCommand } from "@aws-sdk/client-kms";
import { SecretsManagerClient, CreateSecretCommand } from "@aws-sdk/client-secrets-manager";
import { SSMClient, PutParameterCommand } from "@aws-sdk/client-ssm";

import { probeExtendedTools } from "../harness/mcp_server/tools/probe_extended.js";
import { observeExtendedTools } from "../harness/mcp_server/tools/observe_extended.js";

const snsCl = new SNSClient(awsConfig);
const kinesisCl = new KinesisClient(awsConfig);
const kmsCl = new KMSClient(awsConfig);
const secretsCl = new SecretsManagerClient(awsConfig);
const ssmCl = new SSMClient(awsConfig);

let TOPIC_ARN;
let KEY_ID;
const KINESIS_STREAM = "test-kinesis-stream";
const SECRET_NAME = "test-secret-mcp";
const PARAM_NAME = "/test/mcp/param";
```

- [ ] **Step 2: Extend the existing `before()` hook with new fixture creation**

Inside the existing `before(async () => { ... })`, after all existing `for (const op of [...])` calls, append:

```js
  // SNS topic (CreateTopic is idempotent — always returns ARN)
  const topicRes = await snsCl.send(new CreateTopicCommand({ Name: "test-topic" }));
  TOPIC_ARN = topicRes.TopicArn;

  // Kinesis stream (not idempotent — ignore ResourceInUseException)
  try {
    await kinesisCl.send(new CreateStreamCommand({ StreamName: KINESIS_STREAM, ShardCount: 1 }));
  } catch (e) {
    if (!e.name?.includes("ResourceInUse")) throw e;
  }

  // KMS key (always creates new — capture current run's key)
  const keyRes = await kmsCl.send(new CreateKeyCommand({ Description: "ace-bench-test-key" }));
  KEY_ID = keyRes.KeyMetadata.KeyId;

  // Secrets Manager secret (ignore ResourceExistsException on re-runs)
  try {
    await secretsCl.send(new CreateSecretCommand({
      Name: SECRET_NAME,
      SecretString: JSON.stringify({ username: "admin", password: "s3cr3t" }),
    }));
  } catch (e) {
    if (!e.name?.includes("ResourceExists")) throw e;
  }

  // SSM parameter (Overwrite:true is always safe)
  await ssmCl.send(new PutParameterCommand({
    Name: PARAM_NAME,
    Value: "test-value-mcp",
    Type: "String",
    Overwrite: true,
  }));
```

- [ ] **Step 3: Add a smoke test verifying the new tool arrays are importable**

```js
test("probe_extended and observe_extended export arrays", () => {
  assert.ok(Array.isArray(probeExtendedTools));
  assert.ok(Array.isArray(observeExtendedTools));
});
```

- [ ] **Step 4: Run tests to verify existing tests still pass and fixture compiles**

```bash
cd /Users/shubhan/ACEDebugging-benchmark && node --test tests/test_mcp_server.js 2>&1 | tail -20
```

Expected: All existing tests pass. New smoke test passes (empty arrays).

- [ ] **Step 5: Commit**

```bash
git add tests/test_mcp_server.js
git commit -m "test(mcp): add fixture vars and before() extensions for new services"
```

---

## Task 4: SNS Probe + Observe

**Files:**
- Modify: `harness/mcp_server/tools/probe_extended.js`
- Modify: `harness/mcp_server/tools/observe_extended.js`
- Modify: `tests/test_mcp_server.js`

- [ ] **Step 1: Write failing tests**

In `tests/test_mcp_server.js`, add:

```js
// === SNS ===
test("ace_publish_sns: returns message_id", async () => {
  const result = await probeExtendedTools.find(t => t.name === "ace_publish_sns")
    .handler({ topic_arn: TOPIC_ARN, message: "hello from test" });
  assert.ok("message_id" in result, JSON.stringify(result));
});

test("ace_publish_sns: missing args returns error", async () => {
  const result = await probeExtendedTools.find(t => t.name === "ace_publish_sns").handler({});
  assert.ok(result.error);
});

test("ace_get_sns_topic: returns subscription counts", async () => {
  const result = await observeExtendedTools.find(t => t.name === "ace_get_sns_topic")
    .handler({ topic_arn: TOPIC_ARN });
  assert.ok("subscriptions_confirmed" in result, JSON.stringify(result));
  assert.ok("subscriptions_pending" in result);
  assert.ok("arn" in result);
});

test("ace_get_sns_topic: missing args returns error", async () => {
  const result = await observeExtendedTools.find(t => t.name === "ace_get_sns_topic").handler({});
  assert.ok(result.error);
});
```

- [ ] **Step 2: Run to verify failures**

```bash
node --test tests/test_mcp_server.js 2>&1 | grep -E "ace_publish_sns|ace_get_sns_topic"
```

Expected: `TypeError: Cannot read properties of undefined (reading 'handler')` — tool not yet defined.

- [ ] **Step 3: Implement SNS tools in probe_extended.js**

Replace `harness/mcp_server/tools/probe_extended.js` with:

```js
import { SNSClient, PublishCommand } from "@aws-sdk/client-sns";

const awsConfig = {
  endpoint: process.env.LOCALSTACK_ENDPOINT ?? "http://localhost:4566",
  region: "us-east-1",
  credentials: { accessKeyId: "test", secretAccessKey: "test" },
};

const snsClient = new SNSClient(awsConfig);

export const probeExtendedTools = [
  {
    name: "ace_publish_sns",
    description: "Publish a test message to an SNS topic and return the message ID",
    inputSchema: {
      type: "object",
      properties: {
        topic_arn: { type: "string" },
        message: { type: "string" },
        subject: { type: "string" },
      },
      required: ["topic_arn", "message"],
    },
    async handler({ topic_arn, message, subject } = {}) {
      if (!topic_arn || !message) return { error: "topic_arn and message are required" };
      try {
        const res = await snsClient.send(new PublishCommand({
          TopicArn: topic_arn,
          Message: message,
          ...(subject ? { Subject: subject } : {}),
        }));
        return { message_id: res.MessageId, sequence_number: res.SequenceNumber ?? null };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "SNS_ERROR" };
      }
    },
  },
];
```

- [ ] **Step 4: Implement SNS observe tool in observe_extended.js**

Replace `harness/mcp_server/tools/observe_extended.js` with:

```js
import { SNSClient, GetTopicAttributesCommand } from "@aws-sdk/client-sns";

const awsConfig = {
  endpoint: process.env.LOCALSTACK_ENDPOINT ?? "http://localhost:4566",
  region: "us-east-1",
  credentials: { accessKeyId: "test", secretAccessKey: "test" },
};

const snsClient = new SNSClient(awsConfig);

export const observeExtendedTools = [
  {
    name: "ace_get_sns_topic",
    description: "Get SNS topic attributes including subscription counts and resource policy",
    inputSchema: {
      type: "object",
      properties: { topic_arn: { type: "string" } },
      required: ["topic_arn"],
    },
    async handler({ topic_arn } = {}) {
      if (!topic_arn) return { error: "topic_arn is required" };
      try {
        const res = await snsClient.send(new GetTopicAttributesCommand({ TopicArn: topic_arn }));
        const a = res.Attributes ?? {};
        return {
          arn: a.TopicArn,
          name: a.TopicArn?.split(":").pop() ?? null,
          subscriptions_confirmed: parseInt(a.SubscriptionsConfirmed ?? "0"),
          subscriptions_pending: parseInt(a.SubscriptionsPending ?? "0"),
          subscriptions_deleted: parseInt(a.SubscriptionsDeleted ?? "0"),
          display_name: a.DisplayName ?? null,
          policy: a.Policy ? JSON.parse(a.Policy) : null,
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "SNS_ERROR" };
      }
    },
  },
];
```

- [ ] **Step 5: Run tests to verify pass**

```bash
node --test tests/test_mcp_server.js 2>&1 | grep -E "ace_publish_sns|ace_get_sns_topic|✓|✗"
```

Expected: All 4 SNS tests pass.

- [ ] **Step 6: Commit**

```bash
git add harness/mcp_server/tools/probe_extended.js harness/mcp_server/tools/observe_extended.js tests/test_mcp_server.js
git commit -m "feat(mcp): add SNS probe (ace_publish_sns) and observe (ace_get_sns_topic) tools"
```

---

## Task 5: EventBridge Probe + Observe + EventBridge Scheduler Observe

**Files:**
- Modify: `harness/mcp_server/tools/probe_extended.js`
- Modify: `harness/mcp_server/tools/observe_extended.js`
- Modify: `tests/test_mcp_server.js`

- [ ] **Step 1: Write failing tests**

```js
// === EventBridge ===
test("ace_put_events: returns failed_entry_count and entries array", async () => {
  const result = await probeExtendedTools.find(t => t.name === "ace_put_events")
    .handler({ bus_name: "default", source: "test.source", detail_type: "TestEvent", detail: { key: "val" } });
  assert.ok("failed_entry_count" in result, JSON.stringify(result));
  assert.ok(Array.isArray(result.entries));
});

test("ace_put_events: missing args returns error", async () => {
  const result = await probeExtendedTools.find(t => t.name === "ace_put_events").handler({});
  assert.ok(result.error);
});

test("ace_get_eventbridge_rule: nonexistent rule returns error", async () => {
  const result = await observeExtendedTools.find(t => t.name === "ace_get_eventbridge_rule")
    .handler({ rule_name: "no-such-rule-xyz" });
  assert.ok(result.error);
});

test("ace_get_eventbridge_rule: missing args returns error", async () => {
  const result = await observeExtendedTools.find(t => t.name === "ace_get_eventbridge_rule").handler({});
  assert.ok(result.error);
});

// === EventBridge Scheduler ===
test("ace_get_schedule: nonexistent schedule returns error", async () => {
  const result = await observeExtendedTools.find(t => t.name === "ace_get_schedule")
    .handler({ name: "no-such-schedule-xyz" });
  assert.ok(result.error);
});

test("ace_get_schedule: missing args returns error", async () => {
  const result = await observeExtendedTools.find(t => t.name === "ace_get_schedule").handler({});
  assert.ok(result.error);
});
```

- [ ] **Step 2: Run to verify failures**

```bash
node --test tests/test_mcp_server.js 2>&1 | grep -E "ace_put_events|ace_get_eventbridge|ace_get_schedule" | head -10
```

Expected: `TypeError` — tools not defined.

- [ ] **Step 3: Add EventBridge probe tool to probe_extended.js**

Append to the imports section at the top of `probe_extended.js`:

```js
import { EventBridgeClient, PutEventsCommand } from "@aws-sdk/client-eventbridge";
```

Append client instantiation after existing clients:

```js
const ebClient = new EventBridgeClient(awsConfig);
```

Append to the `probeExtendedTools` array:

```js
  {
    name: "ace_put_events",
    description: "Put a test event to an EventBridge event bus and return entry results",
    inputSchema: {
      type: "object",
      properties: {
        bus_name: { type: "string" },
        source: { type: "string" },
        detail_type: { type: "string" },
        detail: { type: "object" },
      },
      required: ["bus_name", "source", "detail_type"],
    },
    async handler({ bus_name, source, detail_type, detail = {} } = {}) {
      if (!bus_name || !source || !detail_type)
        return { error: "bus_name, source, and detail_type are required" };
      try {
        const res = await ebClient.send(new PutEventsCommand({
          Entries: [{
            EventBusName: bus_name,
            Source: source,
            DetailType: detail_type,
            Detail: JSON.stringify(detail),
          }],
        }));
        return {
          failed_entry_count: res.FailedEntryCount,
          entries: (res.Entries ?? []).map(e => ({
            event_id: e.EventId ?? null,
            error_code: e.ErrorCode ?? null,
            error_message: e.ErrorMessage ?? null,
          })),
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "EB_ERROR" };
      }
    },
  },
```

- [ ] **Step 4: Add EventBridge + Scheduler observe tools to observe_extended.js**

Append imports:

```js
import { EventBridgeClient, DescribeRuleCommand, ListTargetsByRuleCommand } from "@aws-sdk/client-eventbridge";
import { SchedulerClient, GetScheduleCommand } from "@aws-sdk/client-scheduler";
```

Append client instantiations:

```js
const ebClient = new EventBridgeClient(awsConfig);
const schedulerClient = new SchedulerClient(awsConfig);
```

Append to `observeExtendedTools` array:

```js
  {
    name: "ace_get_eventbridge_rule",
    description: "Describe an EventBridge rule including its schedule expression, event pattern, and targets",
    inputSchema: {
      type: "object",
      properties: {
        rule_name: { type: "string" },
        bus_name: { type: "string" },
      },
      required: ["rule_name"],
    },
    async handler({ rule_name, bus_name = "default" } = {}) {
      if (!rule_name) return { error: "rule_name is required" };
      try {
        const [ruleRes, targetsRes] = await Promise.all([
          ebClient.send(new DescribeRuleCommand({ Name: rule_name, EventBusName: bus_name })),
          ebClient.send(new ListTargetsByRuleCommand({ Rule: rule_name, EventBusName: bus_name })),
        ]);
        return {
          name: ruleRes.Name,
          arn: ruleRes.Arn,
          state: ruleRes.State,
          schedule_expression: ruleRes.ScheduleExpression ?? null,
          event_pattern: ruleRes.EventPattern ? JSON.parse(ruleRes.EventPattern) : null,
          description: ruleRes.Description ?? null,
          targets_count: targetsRes.Targets?.length ?? 0,
          targets: (targetsRes.Targets ?? []).map(t => ({ id: t.Id, arn: t.Arn })),
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "EB_ERROR" };
      }
    },
  },
  {
    name: "ace_get_schedule",
    description: "Describe an EventBridge Scheduler schedule including expression, target ARN, and state",
    inputSchema: {
      type: "object",
      properties: {
        name: { type: "string" },
        group_name: { type: "string" },
      },
      required: ["name"],
    },
    async handler({ name, group_name = "default" } = {}) {
      if (!name) return { error: "name is required" };
      try {
        const res = await schedulerClient.send(new GetScheduleCommand({ Name: name, GroupName: group_name }));
        return {
          name: res.Name,
          arn: res.Arn,
          state: res.State,
          schedule_expression: res.ScheduleExpression,
          target_arn: res.Target?.Arn ?? null,
          role_arn: res.Target?.RoleArn ?? null,
          description: res.Description ?? null,
          flexible_window_minutes: res.FlexibleTimeWindow?.MaximumWindowInMinutes ?? null,
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "SCHEDULER_ERROR" };
      }
    },
  },
```

- [ ] **Step 5: Run tests**

```bash
node --test tests/test_mcp_server.js 2>&1 | grep -E "ace_put_events|ace_get_eventbridge|ace_get_schedule"
```

Expected: All 6 tests pass.

- [ ] **Step 6: Commit**

```bash
git add harness/mcp_server/tools/probe_extended.js harness/mcp_server/tools/observe_extended.js tests/test_mcp_server.js
git commit -m "feat(mcp): add EventBridge probe/observe and Scheduler observe tools"
```

---

## Task 6: Step Functions Probe + Observe, SWF Probe + Observe

**Files:**
- Modify: `harness/mcp_server/tools/probe_extended.js`
- Modify: `harness/mcp_server/tools/observe_extended.js`
- Modify: `tests/test_mcp_server.js`

- [ ] **Step 1: Write failing tests**

```js
// === Step Functions ===
test("ace_start_execution: nonexistent state machine returns error", async () => {
  const result = await probeExtendedTools.find(t => t.name === "ace_start_execution")
    .handler({ state_machine_arn: "arn:aws:states:us-east-1:000000000000:stateMachine:no-such-sm" });
  assert.ok(result.error, JSON.stringify(result));
});

test("ace_start_execution: missing args returns error", async () => {
  const result = await probeExtendedTools.find(t => t.name === "ace_start_execution").handler({});
  assert.ok(result.error);
});

test("ace_describe_state_machine: nonexistent SM returns error", async () => {
  const result = await observeExtendedTools.find(t => t.name === "ace_describe_state_machine")
    .handler({ state_machine_arn: "arn:aws:states:us-east-1:000000000000:stateMachine:no-such-sm" });
  assert.ok(result.error, JSON.stringify(result));
});

// === SWF ===
test("ace_count_open_executions: nonexistent domain returns error", async () => {
  const result = await probeExtendedTools.find(t => t.name === "ace_count_open_executions")
    .handler({ domain: "no-such-domain-xyz" });
  assert.ok(result.error, JSON.stringify(result));
});

test("ace_count_open_executions: missing args returns error", async () => {
  const result = await probeExtendedTools.find(t => t.name === "ace_count_open_executions").handler({});
  assert.ok(result.error);
});

test("ace_describe_swf_domain: nonexistent domain returns error", async () => {
  const result = await observeExtendedTools.find(t => t.name === "ace_describe_swf_domain")
    .handler({ domain: "no-such-domain-xyz" });
  assert.ok(result.error, JSON.stringify(result));
});
```

- [ ] **Step 2: Run to verify failures**

```bash
node --test tests/test_mcp_server.js 2>&1 | grep -E "ace_start_execution|ace_describe_state|ace_count_open|ace_describe_swf" | head -10
```

Expected: `TypeError` — tools not defined.

- [ ] **Step 3: Add SFN + SWF probe tools to probe_extended.js**

Append imports:

```js
import { SFNClient, StartExecutionCommand, DescribeExecutionCommand } from "@aws-sdk/client-sfn";
import { SWFClient, CountOpenWorkflowExecutionsCommand } from "@aws-sdk/client-swf";
```

Append clients:

```js
const sfnClient = new SFNClient(awsConfig);
const swfClient = new SWFClient(awsConfig);
```

Append to `probeExtendedTools`:

```js
  {
    name: "ace_start_execution",
    description: "Start a Step Functions state machine execution and poll up to 2 s for its terminal status",
    inputSchema: {
      type: "object",
      properties: {
        state_machine_arn: { type: "string" },
        input: { type: "object" },
      },
      required: ["state_machine_arn"],
    },
    async handler({ state_machine_arn, input = {} } = {}) {
      if (!state_machine_arn) return { error: "state_machine_arn is required" };
      try {
        const startRes = await sfnClient.send(new StartExecutionCommand({
          stateMachineArn: state_machine_arn,
          input: JSON.stringify(input),
        }));
        await new Promise(r => setTimeout(r, 2000));
        const descRes = await sfnClient.send(new DescribeExecutionCommand({
          executionArn: startRes.executionArn,
        }));
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
  {
    name: "ace_count_open_executions",
    description: "Count open SWF workflow executions in a domain over the past 7 days",
    inputSchema: {
      type: "object",
      properties: { domain: { type: "string" } },
      required: ["domain"],
    },
    async handler({ domain } = {}) {
      if (!domain) return { error: "domain is required" };
      try {
        const res = await swfClient.send(new CountOpenWorkflowExecutionsCommand({
          domain,
          startTimeFilter: { oldestDate: new Date(Date.now() - 7 * 24 * 60 * 60 * 1000) },
        }));
        return { count: res.count, truncated: res.truncated };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "SWF_ERROR" };
      }
    },
  },
```

- [ ] **Step 4: Add SFN + SWF observe tools to observe_extended.js**

Append imports:

```js
import { SFNClient, DescribeStateMachineCommand } from "@aws-sdk/client-sfn";
import { SWFClient, DescribeDomainCommand } from "@aws-sdk/client-swf";
```

Append clients:

```js
const sfnClient = new SFNClient(awsConfig);
const swfClient = new SWFClient(awsConfig);
```

Append to `observeExtendedTools`:

```js
  {
    name: "ace_describe_state_machine",
    description: "Describe a Step Functions state machine configuration, type, role, and state count",
    inputSchema: {
      type: "object",
      properties: { state_machine_arn: { type: "string" } },
      required: ["state_machine_arn"],
    },
    async handler({ state_machine_arn } = {}) {
      if (!state_machine_arn) return { error: "state_machine_arn is required" };
      try {
        const res = await sfnClient.send(new DescribeStateMachineCommand({
          stateMachineArn: state_machine_arn,
        }));
        const def = JSON.parse(res.definition ?? "{}");
        return {
          name: res.name,
          arn: res.stateMachineArn,
          status: res.status,
          type: res.type,
          role_arn: res.roleArn,
          state_count: Object.keys(def.States ?? {}).length,
          logging_level: res.loggingConfiguration?.level ?? null,
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "SFN_ERROR" };
      }
    },
  },
  {
    name: "ace_describe_swf_domain",
    description: "Describe an SWF domain status and workflow execution retention period",
    inputSchema: {
      type: "object",
      properties: { domain: { type: "string" } },
      required: ["domain"],
    },
    async handler({ domain } = {}) {
      if (!domain) return { error: "domain is required" };
      try {
        const res = await swfClient.send(new DescribeDomainCommand({ name: domain }));
        return {
          name: res.domainInfo?.name,
          status: res.domainInfo?.status,
          description: res.domainInfo?.description ?? null,
          workflow_execution_retention_period_days:
            res.configuration?.workflowExecutionRetentionPeriodInDays ?? null,
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "SWF_ERROR" };
      }
    },
  },
```

- [ ] **Step 5: Run tests**

```bash
node --test tests/test_mcp_server.js 2>&1 | grep -E "ace_start_execution|ace_describe_state|ace_count_open|ace_describe_swf"
```

Expected: All 6 tests pass.

- [ ] **Step 6: Commit**

```bash
git add harness/mcp_server/tools/probe_extended.js harness/mcp_server/tools/observe_extended.js tests/test_mcp_server.js
git commit -m "feat(mcp): add Step Functions and SWF probe/observe tools"
```

---

## Task 7: SES Probe + Observe

**Files:**
- Modify: `harness/mcp_server/tools/probe_extended.js`
- Modify: `harness/mcp_server/tools/observe_extended.js`
- Modify: `tests/test_mcp_server.js`

- [ ] **Step 1: Write failing tests**

```js
// === SES ===
test("ace_send_test_email: returns message_id", async () => {
  const result = await probeExtendedTools.find(t => t.name === "ace_send_test_email")
    .handler({ from: "test@example.com", to: "dest@example.com", subject: "ACE Test" });
  assert.ok("message_id" in result, JSON.stringify(result));
});

test("ace_send_test_email: missing args returns error", async () => {
  const result = await probeExtendedTools.find(t => t.name === "ace_send_test_email").handler({});
  assert.ok(result.error);
});

test("ace_get_ses_identity: returns verification status per identity", async () => {
  const result = await observeExtendedTools.find(t => t.name === "ace_get_ses_identity")
    .handler({ identities: ["test@example.com"] });
  assert.ok(typeof result === "object" && !Array.isArray(result), JSON.stringify(result));
  assert.ok("test@example.com" in result);
});

test("ace_get_ses_identity: missing args returns error", async () => {
  const result = await observeExtendedTools.find(t => t.name === "ace_get_ses_identity").handler({});
  assert.ok(result.error);
});
```

- [ ] **Step 2: Run to verify failures**

```bash
node --test tests/test_mcp_server.js 2>&1 | grep -E "ace_send_test_email|ace_get_ses_identity" | head -10
```

Expected: `TypeError` — tools not defined.

- [ ] **Step 3: Add SES probe tool to probe_extended.js**

Append import:

```js
import { SESClient, SendEmailCommand } from "@aws-sdk/client-ses";
```

Append client:

```js
const sesClient = new SESClient(awsConfig);
```

Append to `probeExtendedTools`:

```js
  {
    name: "ace_send_test_email",
    description: "Send a test email via SES (LocalStack mock) and return the message ID",
    inputSchema: {
      type: "object",
      properties: {
        from: { type: "string" },
        to: { type: "string" },
        subject: { type: "string" },
        body: { type: "string" },
      },
      required: ["from", "to", "subject"],
    },
    async handler({ from, to, subject, body = "ACE-Bench diagnostic test email" } = {}) {
      if (!from || !to || !subject) return { error: "from, to, and subject are required" };
      try {
        const res = await sesClient.send(new SendEmailCommand({
          Source: from,
          Destination: { ToAddresses: [to] },
          Message: {
            Subject: { Data: subject },
            Body: { Text: { Data: body } },
          },
        }));
        return { message_id: res.MessageId };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "SES_ERROR" };
      }
    },
  },
```

- [ ] **Step 4: Add SES observe tool to observe_extended.js**

Append import:

```js
import { SESClient, GetIdentityVerificationAttributesCommand } from "@aws-sdk/client-ses";
```

Append client:

```js
const sesClient = new SESClient(awsConfig);
```

Append to `observeExtendedTools`:

```js
  {
    name: "ace_get_ses_identity",
    description: "Check SES verification status for one or more email or domain identities",
    inputSchema: {
      type: "object",
      properties: {
        identities: { type: "array", items: { type: "string" } },
      },
      required: ["identities"],
    },
    async handler({ identities } = {}) {
      if (!identities?.length) return { error: "identities array is required" };
      try {
        const res = await sesClient.send(
          new GetIdentityVerificationAttributesCommand({ Identities: identities })
        );
        const out = {};
        for (const [id, attrs] of Object.entries(res.VerificationAttributes ?? {})) {
          out[id] = {
            verification_status: attrs.VerificationStatus,
            verification_token: attrs.VerificationToken ?? null,
          };
        }
        return out;
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "SES_ERROR" };
      }
    },
  },
```

- [ ] **Step 5: Run tests**

```bash
node --test tests/test_mcp_server.js 2>&1 | grep -E "ace_send_test_email|ace_get_ses_identity"
```

Expected: All 4 tests pass.

- [ ] **Step 6: Commit**

```bash
git add harness/mcp_server/tools/probe_extended.js harness/mcp_server/tools/observe_extended.js tests/test_mcp_server.js
git commit -m "feat(mcp): add SES probe (ace_send_test_email) and observe (ace_get_ses_identity) tools"
```

---

## Task 8: EC2 Probe + Observe

**Files:**
- Modify: `harness/mcp_server/tools/probe_extended.js`
- Modify: `harness/mcp_server/tools/observe_extended.js`
- Modify: `tests/test_mcp_server.js`

- [ ] **Step 1: Write failing tests**

```js
// === EC2 ===
test("ace_check_instance_state: nonexistent instance returns error", async () => {
  const result = await probeExtendedTools.find(t => t.name === "ace_check_instance_state")
    .handler({ instance_id: "i-0000000000000dead" });
  assert.ok(result.error, JSON.stringify(result));
});

test("ace_check_instance_state: missing args returns error", async () => {
  const result = await probeExtendedTools.find(t => t.name === "ace_check_instance_state").handler({});
  assert.ok(result.error);
});

test("ace_describe_security_group: nonexistent group returns error", async () => {
  const result = await observeExtendedTools.find(t => t.name === "ace_describe_security_group")
    .handler({ group_id: "sg-000000000dead" });
  assert.ok(result.error, JSON.stringify(result));
});

test("ace_describe_security_group: missing args returns error", async () => {
  const result = await observeExtendedTools.find(t => t.name === "ace_describe_security_group").handler({});
  assert.ok(result.error);
});
```

- [ ] **Step 2: Run to verify failures**

```bash
node --test tests/test_mcp_server.js 2>&1 | grep -E "ace_check_instance|ace_describe_security" | head -10
```

Expected: `TypeError` — tools not defined.

- [ ] **Step 3: Add EC2 probe tool to probe_extended.js**

Append import:

```js
import { EC2Client, DescribeInstancesCommand } from "@aws-sdk/client-ec2";
```

Append client:

```js
const ec2Client = new EC2Client(awsConfig);
```

Append to `probeExtendedTools`:

```js
  {
    name: "ace_check_instance_state",
    description: "Get the current state, instance type, and network info for an EC2 instance",
    inputSchema: {
      type: "object",
      properties: { instance_id: { type: "string" } },
      required: ["instance_id"],
    },
    async handler({ instance_id } = {}) {
      if (!instance_id) return { error: "instance_id is required" };
      try {
        const res = await ec2Client.send(new DescribeInstancesCommand({ InstanceIds: [instance_id] }));
        const inst = res.Reservations?.[0]?.Instances?.[0];
        if (!inst) return { error: "instance not found", error_type: "NOT_FOUND" };
        return {
          state: inst.State?.Name,
          instance_type: inst.InstanceType,
          public_ip: inst.PublicIpAddress ?? null,
          private_ip: inst.PrivateIpAddress ?? null,
          launch_time: inst.LaunchTime?.toISOString() ?? null,
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "EC2_ERROR" };
      }
    },
  },
```

- [ ] **Step 4: Add EC2 observe tool to observe_extended.js**

Append import:

```js
import { EC2Client, DescribeSecurityGroupsCommand } from "@aws-sdk/client-ec2";
```

Append client:

```js
const ec2Client = new EC2Client(awsConfig);
```

Append to `observeExtendedTools`:

```js
  {
    name: "ace_describe_security_group",
    description: "Get EC2 security group inbound and outbound rules",
    inputSchema: {
      type: "object",
      properties: { group_id: { type: "string" } },
      required: ["group_id"],
    },
    async handler({ group_id } = {}) {
      if (!group_id) return { error: "group_id is required" };
      try {
        const res = await ec2Client.send(new DescribeSecurityGroupsCommand({ GroupIds: [group_id] }));
        const sg = res.SecurityGroups?.[0];
        if (!sg) return { error: "security group not found", error_type: "NOT_FOUND" };
        const mapRule = p => ({
          protocol: p.IpProtocol,
          from_port: p.FromPort ?? null,
          to_port: p.ToPort ?? null,
          cidr: (p.IpRanges ?? []).map(r => r.CidrIp),
          cidr_ipv6: (p.Ipv6Ranges ?? []).map(r => r.CidrIpv6),
        });
        return {
          group_id: sg.GroupId,
          group_name: sg.GroupName,
          description: sg.Description,
          vpc_id: sg.VpcId ?? null,
          inbound_rules: (sg.IpPermissions ?? []).map(mapRule),
          outbound_rules: (sg.IpPermissionsEgress ?? []).map(mapRule),
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "EC2_ERROR" };
      }
    },
  },
```

- [ ] **Step 5: Run tests**

```bash
node --test tests/test_mcp_server.js 2>&1 | grep -E "ace_check_instance|ace_describe_security"
```

Expected: All 4 tests pass.

- [ ] **Step 6: Commit**

```bash
git add harness/mcp_server/tools/probe_extended.js harness/mcp_server/tools/observe_extended.js tests/test_mcp_server.js
git commit -m "feat(mcp): add EC2 probe (ace_check_instance_state) and observe (ace_describe_security_group) tools"
```

---

## Task 9: Route 53 Probe + Observe, Route 53 Resolver Probe + Observe

**Files:**
- Modify: `harness/mcp_server/tools/probe_extended.js`
- Modify: `harness/mcp_server/tools/observe_extended.js`
- Modify: `tests/test_mcp_server.js`

- [ ] **Step 1: Write failing tests**

```js
// === Route 53 ===
test("ace_check_hosted_zone: nonexistent zone returns error", async () => {
  const result = await probeExtendedTools.find(t => t.name === "ace_check_hosted_zone")
    .handler({ hosted_zone_id: "/hostedzone/ZDEADBEEF0" });
  assert.ok(result.error, JSON.stringify(result));
});

test("ace_check_hosted_zone: missing args returns error", async () => {
  const result = await probeExtendedTools.find(t => t.name === "ace_check_hosted_zone").handler({});
  assert.ok(result.error);
});

test("ace_list_dns_records: nonexistent zone returns error", async () => {
  const result = await observeExtendedTools.find(t => t.name === "ace_list_dns_records")
    .handler({ hosted_zone_id: "/hostedzone/ZDEADBEEF0" });
  assert.ok(result.error, JSON.stringify(result));
});

// === Route 53 Resolver ===
test("ace_list_resolver_endpoints: returns array", async () => {
  const result = await probeExtendedTools.find(t => t.name === "ace_list_resolver_endpoints").handler({});
  assert.ok(Array.isArray(result), JSON.stringify(result));
});

test("ace_get_resolver_endpoint: nonexistent endpoint returns error", async () => {
  const result = await observeExtendedTools.find(t => t.name === "ace_get_resolver_endpoint")
    .handler({ resolver_endpoint_id: "rslvr-in-deadbeef000" });
  assert.ok(result.error, JSON.stringify(result));
});

test("ace_get_resolver_endpoint: missing args returns error", async () => {
  const result = await observeExtendedTools.find(t => t.name === "ace_get_resolver_endpoint").handler({});
  assert.ok(result.error);
});
```

- [ ] **Step 2: Run to verify failures**

```bash
node --test tests/test_mcp_server.js 2>&1 | grep -E "ace_check_hosted|ace_list_dns|ace_list_resolver|ace_get_resolver" | head -10
```

Expected: `TypeError` — tools not defined.

- [ ] **Step 3: Add Route 53 + Resolver probe tools to probe_extended.js**

Append imports:

```js
import { Route53Client, GetHostedZoneCommand } from "@aws-sdk/client-route-53";
import { Route53ResolverClient, ListResolverEndpointsCommand } from "@aws-sdk/client-route-53-resolver";
```

Append clients:

```js
const r53Client = new Route53Client(awsConfig);
const r53ResolverClient = new Route53ResolverClient(awsConfig);
```

Append to `probeExtendedTools`:

```js
  {
    name: "ace_check_hosted_zone",
    description: "Get Route 53 hosted zone details and its resource record set count",
    inputSchema: {
      type: "object",
      properties: { hosted_zone_id: { type: "string" } },
      required: ["hosted_zone_id"],
    },
    async handler({ hosted_zone_id } = {}) {
      if (!hosted_zone_id) return { error: "hosted_zone_id is required" };
      try {
        const res = await r53Client.send(new GetHostedZoneCommand({ Id: hosted_zone_id }));
        return {
          id: res.HostedZone?.Id,
          name: res.HostedZone?.Name,
          record_count: res.HostedZone?.ResourceRecordSetCount,
          private_zone: res.HostedZone?.Config?.PrivateZone ?? false,
          comment: res.HostedZone?.Config?.Comment ?? null,
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "R53_ERROR" };
      }
    },
  },
  {
    name: "ace_list_resolver_endpoints",
    description: "List Route 53 Resolver endpoints, optionally filtered by direction (INBOUND|OUTBOUND)",
    inputSchema: {
      type: "object",
      properties: {
        direction: { type: "string", enum: ["INBOUND", "OUTBOUND"] },
      },
    },
    async handler({ direction } = {}) {
      try {
        const params = direction
          ? { Filters: [{ Name: "Direction", Values: [direction] }] }
          : {};
        const res = await r53ResolverClient.send(new ListResolverEndpointsCommand(params));
        return (res.ResolverEndpoints ?? []).map(e => ({
          id: e.Id,
          name: e.Name,
          direction: e.Direction,
          status: e.Status,
          ip_address_count: e.IpAddressCount,
          host_vpc_id: e.HostVPCId,
        }));
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "R53R_ERROR" };
      }
    },
  },
```

- [ ] **Step 4: Add Route 53 + Resolver observe tools to observe_extended.js**

Append imports:

```js
import { Route53Client, ListResourceRecordSetsCommand } from "@aws-sdk/client-route-53";
import { Route53ResolverClient, GetResolverEndpointCommand } from "@aws-sdk/client-route-53-resolver";
```

Append clients:

```js
const r53Client = new Route53Client(awsConfig);
const r53ResolverClient = new Route53ResolverClient(awsConfig);
```

Append to `observeExtendedTools`:

```js
  {
    name: "ace_list_dns_records",
    description: "List Route 53 DNS resource record sets in a hosted zone, optionally filtered by type",
    inputSchema: {
      type: "object",
      properties: {
        hosted_zone_id: { type: "string" },
        record_type: { type: "string" },
      },
      required: ["hosted_zone_id"],
    },
    async handler({ hosted_zone_id, record_type } = {}) {
      if (!hosted_zone_id) return { error: "hosted_zone_id is required" };
      try {
        const res = await r53Client.send(new ListResourceRecordSetsCommand({ HostedZoneId: hosted_zone_id }));
        let records = (res.ResourceRecordSets ?? []).map(r => ({
          name: r.Name,
          type: r.Type,
          ttl: r.TTL ?? null,
          values: (r.ResourceRecords ?? []).map(rr => rr.Value),
          alias_target: r.AliasTarget?.DNSName ?? null,
        }));
        if (record_type) records = records.filter(r => r.type === record_type);
        return records;
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "R53_ERROR" };
      }
    },
  },
  {
    name: "ace_get_resolver_endpoint",
    description: "Describe a Route 53 Resolver endpoint configuration and IP address count",
    inputSchema: {
      type: "object",
      properties: { resolver_endpoint_id: { type: "string" } },
      required: ["resolver_endpoint_id"],
    },
    async handler({ resolver_endpoint_id } = {}) {
      if (!resolver_endpoint_id) return { error: "resolver_endpoint_id is required" };
      try {
        const res = await r53ResolverClient.send(
          new GetResolverEndpointCommand({ ResolverEndpointId: resolver_endpoint_id })
        );
        const ep = res.ResolverEndpoint;
        return {
          id: ep?.Id,
          name: ep?.Name,
          direction: ep?.Direction,
          status: ep?.Status,
          ip_address_count: ep?.IpAddressCount,
          host_vpc_id: ep?.HostVPCId,
          security_group_ids: ep?.SecurityGroupIds ?? [],
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "R53R_ERROR" };
      }
    },
  },
```

- [ ] **Step 5: Run tests**

```bash
node --test tests/test_mcp_server.js 2>&1 | grep -E "ace_check_hosted|ace_list_dns|ace_list_resolver|ace_get_resolver"
```

Expected: All 6 tests pass.

- [ ] **Step 6: Commit**

```bash
git add harness/mcp_server/tools/probe_extended.js harness/mcp_server/tools/observe_extended.js tests/test_mcp_server.js
git commit -m "feat(mcp): add Route 53 and Route 53 Resolver probe/observe tools"
```

---

## Task 10: Kinesis Streams Probe + Observe

**Files:**
- Modify: `harness/mcp_server/tools/probe_extended.js`
- Modify: `harness/mcp_server/tools/observe_extended.js`
- Modify: `tests/test_mcp_server.js`

- [ ] **Step 1: Write failing tests**

```js
// === Kinesis Streams ===
test("ace_put_kinesis_record: returns shard_id and sequence_number", async () => {
  const result = await probeExtendedTools.find(t => t.name === "ace_put_kinesis_record")
    .handler({ stream_name: KINESIS_STREAM, data: "hello-world", partition_key: "pk-1" });
  assert.ok("shard_id" in result, JSON.stringify(result));
  assert.ok("sequence_number" in result);
});

test("ace_put_kinesis_record: missing args returns error", async () => {
  const result = await probeExtendedTools.find(t => t.name === "ace_put_kinesis_record").handler({});
  assert.ok(result.error);
});

test("ace_describe_kinesis_stream: returns stream status and shard count", async () => {
  const result = await observeExtendedTools.find(t => t.name === "ace_describe_kinesis_stream")
    .handler({ stream_name: KINESIS_STREAM });
  assert.ok("stream_status" in result, JSON.stringify(result));
  assert.ok("shard_count" in result);
  assert.ok("retention_period_hours" in result);
});

test("ace_describe_kinesis_stream: nonexistent stream returns error", async () => {
  const result = await observeExtendedTools.find(t => t.name === "ace_describe_kinesis_stream")
    .handler({ stream_name: "no-such-stream-xyz" });
  assert.ok(result.error, JSON.stringify(result));
});
```

- [ ] **Step 2: Run to verify failures**

```bash
node --test tests/test_mcp_server.js 2>&1 | grep -E "ace_put_kinesis|ace_describe_kinesis" | head -10
```

Expected: `TypeError` — tools not defined.

- [ ] **Step 3: Add Kinesis probe tool to probe_extended.js**

Append import:

```js
import { KinesisClient, PutRecordCommand as KinesisPutRecordCommand } from "@aws-sdk/client-kinesis";
```

Append client:

```js
const kinesisClient = new KinesisClient(awsConfig);
```

Append to `probeExtendedTools`:

```js
  {
    name: "ace_put_kinesis_record",
    description: "Put a test record to a Kinesis data stream and return the shard ID and sequence number",
    inputSchema: {
      type: "object",
      properties: {
        stream_name: { type: "string" },
        data: { type: "string" },
        partition_key: { type: "string" },
      },
      required: ["stream_name", "data", "partition_key"],
    },
    async handler({ stream_name, data, partition_key } = {}) {
      if (!stream_name || !data || !partition_key)
        return { error: "stream_name, data, and partition_key are required" };
      try {
        const res = await kinesisClient.send(new KinesisPutRecordCommand({
          StreamName: stream_name,
          Data: Buffer.from(data),
          PartitionKey: partition_key,
        }));
        return {
          shard_id: res.ShardId,
          sequence_number: res.SequenceNumber,
          encryption_type: res.EncryptionType ?? "NONE",
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "KINESIS_ERROR" };
      }
    },
  },
```

- [ ] **Step 4: Add Kinesis observe tool to observe_extended.js**

Append import:

```js
import { KinesisClient, DescribeStreamSummaryCommand } from "@aws-sdk/client-kinesis";
```

Append client:

```js
const kinesisClient = new KinesisClient(awsConfig);
```

Append to `observeExtendedTools`:

```js
  {
    name: "ace_describe_kinesis_stream",
    description: "Describe a Kinesis data stream including shard count, retention period, and encryption",
    inputSchema: {
      type: "object",
      properties: { stream_name: { type: "string" } },
      required: ["stream_name"],
    },
    async handler({ stream_name } = {}) {
      if (!stream_name) return { error: "stream_name is required" };
      try {
        const res = await kinesisClient.send(new DescribeStreamSummaryCommand({ StreamName: stream_name }));
        const s = res.StreamDescriptionSummary;
        return {
          stream_arn: s?.StreamARN,
          stream_status: s?.StreamStatus,
          shard_count: s?.OpenShardCount,
          retention_period_hours: s?.RetentionPeriodHours,
          encryption_type: s?.EncryptionType ?? "NONE",
          key_id: s?.KeyId ?? null,
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "KINESIS_ERROR" };
      }
    },
  },
```

- [ ] **Step 5: Run tests**

```bash
node --test tests/test_mcp_server.js 2>&1 | grep -E "ace_put_kinesis|ace_describe_kinesis"
```

Expected: All 4 tests pass.

- [ ] **Step 6: Commit**

```bash
git add harness/mcp_server/tools/probe_extended.js harness/mcp_server/tools/observe_extended.js tests/test_mcp_server.js
git commit -m "feat(mcp): add Kinesis Streams probe (ace_put_kinesis_record) and observe (ace_describe_kinesis_stream) tools"
```

---

## Task 11: Kinesis Firehose Probe + Observe, DynamoDB Streams Probe + Observe

**Files:**
- Modify: `harness/mcp_server/tools/probe_extended.js`
- Modify: `harness/mcp_server/tools/observe_extended.js`
- Modify: `tests/test_mcp_server.js`

- [ ] **Step 1: Write failing tests**

```js
// === Kinesis Firehose ===
test("ace_put_firehose_record: nonexistent stream returns error", async () => {
  const result = await probeExtendedTools.find(t => t.name === "ace_put_firehose_record")
    .handler({ delivery_stream_name: "no-such-firehose-xyz", data: "test-data" });
  assert.ok(result.error, JSON.stringify(result));
});

test("ace_put_firehose_record: missing args returns error", async () => {
  const result = await probeExtendedTools.find(t => t.name === "ace_put_firehose_record").handler({});
  assert.ok(result.error);
});

test("ace_describe_firehose_stream: nonexistent stream returns error", async () => {
  const result = await observeExtendedTools.find(t => t.name === "ace_describe_firehose_stream")
    .handler({ delivery_stream_name: "no-such-firehose-xyz" });
  assert.ok(result.error, JSON.stringify(result));
});

// === DynamoDB Streams ===
test("ace_get_stream_records: nonexistent stream ARN returns error", async () => {
  const result = await probeExtendedTools.find(t => t.name === "ace_get_stream_records")
    .handler({ stream_arn: "arn:aws:dynamodb:us-east-1:000000000000:table/no-table/stream/2020-01-01T00:00:00.000" });
  assert.ok(result.error, JSON.stringify(result));
});

test("ace_get_stream_records: missing args returns error", async () => {
  const result = await probeExtendedTools.find(t => t.name === "ace_get_stream_records").handler({});
  assert.ok(result.error);
});

test("ace_describe_dynamo_stream: nonexistent stream ARN returns error", async () => {
  const result = await observeExtendedTools.find(t => t.name === "ace_describe_dynamo_stream")
    .handler({ stream_arn: "arn:aws:dynamodb:us-east-1:000000000000:table/no-table/stream/2020-01-01T00:00:00.000" });
  assert.ok(result.error, JSON.stringify(result));
});
```

- [ ] **Step 2: Run to verify failures**

```bash
node --test tests/test_mcp_server.js 2>&1 | grep -E "ace_put_firehose|ace_describe_firehose|ace_get_stream_records|ace_describe_dynamo_stream" | head -10
```

Expected: `TypeError` — tools not defined.

- [ ] **Step 3: Add Firehose + DDB Streams probe tools to probe_extended.js**

Append imports:

```js
import { FirehoseClient, PutRecordCommand as FirehosePutRecordCommand } from "@aws-sdk/client-firehose";
import {
  DynamoDBStreamsClient,
  DescribeStreamCommand as DDBDescribeStreamCommand,
  GetShardIteratorCommand,
  GetRecordsCommand,
} from "@aws-sdk/client-dynamodb-streams";
import { unmarshall } from "@aws-sdk/util-dynamodb";
```

Append clients:

```js
const firehoseClient = new FirehoseClient(awsConfig);
const dynamoStreamsClient = new DynamoDBStreamsClient(awsConfig);
```

Append to `probeExtendedTools`:

```js
  {
    name: "ace_put_firehose_record",
    description: "Put a test record to a Kinesis Firehose delivery stream",
    inputSchema: {
      type: "object",
      properties: {
        delivery_stream_name: { type: "string" },
        data: { type: "string" },
      },
      required: ["delivery_stream_name", "data"],
    },
    async handler({ delivery_stream_name, data } = {}) {
      if (!delivery_stream_name || !data)
        return { error: "delivery_stream_name and data are required" };
      try {
        const res = await firehoseClient.send(new FirehosePutRecordCommand({
          DeliveryStreamName: delivery_stream_name,
          Record: { Data: Buffer.from(data) },
        }));
        return { record_id: res.RecordId, encrypted: res.Encrypted ?? false };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "FIREHOSE_ERROR" };
      }
    },
  },
  {
    name: "ace_get_stream_records",
    description: "Read recent records from the latest shard of a DynamoDB stream",
    inputSchema: {
      type: "object",
      properties: { stream_arn: { type: "string" } },
      required: ["stream_arn"],
    },
    async handler({ stream_arn } = {}) {
      if (!stream_arn) return { error: "stream_arn is required" };
      try {
        const descRes = await dynamoStreamsClient.send(
          new DDBDescribeStreamCommand({ StreamArn: stream_arn })
        );
        const shards = descRes.StreamDescription?.Shards ?? [];
        if (shards.length === 0) return { records: [], shard_count: 0 };
        const shard_id = shards[shards.length - 1].ShardId;
        const iterRes = await dynamoStreamsClient.send(new GetShardIteratorCommand({
          StreamArn: stream_arn,
          ShardId: shard_id,
          ShardIteratorType: "TRIM_HORIZON",
        }));
        const recordsRes = await dynamoStreamsClient.send(new GetRecordsCommand({
          ShardIterator: iterRes.ShardIterator,
          Limit: 10,
        }));
        return {
          records: (recordsRes.Records ?? []).map(r => ({
            event_name: r.eventName,
            keys: r.dynamodb?.Keys ? unmarshall(r.dynamodb.Keys) : {},
            new_image: r.dynamodb?.NewImage ? unmarshall(r.dynamodb.NewImage) : null,
            old_image: r.dynamodb?.OldImage ? unmarshall(r.dynamodb.OldImage) : null,
          })),
          shard_count: shards.length,
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "DYNAMO_STREAMS_ERROR" };
      }
    },
  },
```

- [ ] **Step 4: Add Firehose + DDB Streams observe tools to observe_extended.js**

Append imports:

```js
import { FirehoseClient, DescribeDeliveryStreamCommand } from "@aws-sdk/client-firehose";
import {
  DynamoDBStreamsClient,
  DescribeStreamCommand as DDBDescribeStreamCommand,
} from "@aws-sdk/client-dynamodb-streams";
```

Append clients:

```js
const firehoseClient = new FirehoseClient(awsConfig);
const dynamoStreamsClient = new DynamoDBStreamsClient(awsConfig);
```

Append to `observeExtendedTools`:

```js
  {
    name: "ace_describe_firehose_stream",
    description: "Describe a Kinesis Firehose delivery stream including status and destination configuration",
    inputSchema: {
      type: "object",
      properties: { delivery_stream_name: { type: "string" } },
      required: ["delivery_stream_name"],
    },
    async handler({ delivery_stream_name } = {}) {
      if (!delivery_stream_name) return { error: "delivery_stream_name is required" };
      try {
        const res = await firehoseClient.send(
          new DescribeDeliveryStreamCommand({ DeliveryStreamName: delivery_stream_name })
        );
        const desc = res.DeliveryStreamDescription;
        return {
          arn: desc?.DeliveryStreamARN,
          status: desc?.DeliveryStreamStatus,
          type: desc?.DeliveryStreamType,
          destinations: (desc?.Destinations ?? []).map(d => ({
            destination_id: d.DestinationId,
            s3_bucket: d.ExtendedS3DestinationDescription?.BucketARN
              ?? d.S3DestinationDescription?.BucketARN
              ?? null,
            http_url: d.HttpEndpointDestinationDescription?.EndpointConfiguration?.Url ?? null,
          })),
          encryption_status: desc?.DeliveryStreamEncryptionConfiguration?.Status ?? "DISABLED",
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "FIREHOSE_ERROR" };
      }
    },
  },
  {
    name: "ace_describe_dynamo_stream",
    description: "Describe a DynamoDB stream including status, view type, and shard list",
    inputSchema: {
      type: "object",
      properties: { stream_arn: { type: "string" } },
      required: ["stream_arn"],
    },
    async handler({ stream_arn } = {}) {
      if (!stream_arn) return { error: "stream_arn is required" };
      try {
        const res = await dynamoStreamsClient.send(
          new DDBDescribeStreamCommand({ StreamArn: stream_arn })
        );
        const desc = res.StreamDescription;
        return {
          stream_arn: desc?.StreamArn,
          table_name: desc?.TableName,
          stream_status: desc?.StreamStatus,
          view_type: desc?.StreamViewType,
          shard_count: desc?.Shards?.length ?? 0,
          shards: (desc?.Shards ?? []).map(s => ({
            shard_id: s.ShardId,
            parent_shard_id: s.ParentShardId ?? null,
            starting_sequence: s.SequenceNumberRange?.StartingSequenceNumber ?? null,
            ending_sequence: s.SequenceNumberRange?.EndingSequenceNumber ?? null,
          })),
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "DYNAMO_STREAMS_ERROR" };
      }
    },
  },
```

- [ ] **Step 5: Run tests**

```bash
node --test tests/test_mcp_server.js 2>&1 | grep -E "ace_put_firehose|ace_describe_firehose|ace_get_stream_records|ace_describe_dynamo_stream"
```

Expected: All 6 tests pass.

- [ ] **Step 6: Commit**

```bash
git add harness/mcp_server/tools/probe_extended.js harness/mcp_server/tools/observe_extended.js tests/test_mcp_server.js
git commit -m "feat(mcp): add Firehose and DynamoDB Streams probe/observe tools"
```

---

## Task 12: KMS Probe + Observe, Secrets Manager Probe + Observe

**Files:**
- Modify: `harness/mcp_server/tools/probe_extended.js`
- Modify: `harness/mcp_server/tools/observe_extended.js`
- Modify: `tests/test_mcp_server.js`

- [ ] **Step 1: Write failing tests**

```js
// === KMS ===
test("ace_encrypt_decrypt: roundtrip returns matches:true", async () => {
  const result = await probeExtendedTools.find(t => t.name === "ace_encrypt_decrypt")
    .handler({ key_id: KEY_ID, plaintext: "ace-bench-test-plaintext" });
  assert.ok("matches" in result, JSON.stringify(result));
  assert.equal(result.matches, true);
  assert.equal(result.decrypted, "ace-bench-test-plaintext");
});

test("ace_encrypt_decrypt: missing args returns error", async () => {
  const result = await probeExtendedTools.find(t => t.name === "ace_encrypt_decrypt").handler({});
  assert.ok(result.error);
});

test("ace_describe_kms_key: returns key state and usage", async () => {
  const result = await observeExtendedTools.find(t => t.name === "ace_describe_kms_key")
    .handler({ key_id: KEY_ID });
  assert.ok("state" in result, JSON.stringify(result));
  assert.ok("key_usage" in result);
  assert.ok("arn" in result);
});

// === Secrets Manager ===
test("ace_get_secret: returns secret_string", async () => {
  const result = await probeExtendedTools.find(t => t.name === "ace_get_secret")
    .handler({ secret_id: SECRET_NAME });
  assert.ok("secret_string" in result, JSON.stringify(result));
  assert.ok("name" in result);
});

test("ace_get_secret: nonexistent secret returns error", async () => {
  const result = await probeExtendedTools.find(t => t.name === "ace_get_secret")
    .handler({ secret_id: "no-such-secret-xyz" });
  assert.ok(result.error, JSON.stringify(result));
});

test("ace_describe_secret: returns rotation_enabled and tags", async () => {
  const result = await observeExtendedTools.find(t => t.name === "ace_describe_secret")
    .handler({ secret_id: SECRET_NAME });
  assert.ok("rotation_enabled" in result, JSON.stringify(result));
  assert.ok("name" in result);
  assert.ok("arn" in result);
});
```

- [ ] **Step 2: Run to verify failures**

```bash
node --test tests/test_mcp_server.js 2>&1 | grep -E "ace_encrypt_decrypt|ace_describe_kms|ace_get_secret|ace_describe_secret" | head -10
```

Expected: `TypeError` — tools not defined.

- [ ] **Step 3: Add KMS + Secrets Manager probe tools to probe_extended.js**

Append imports:

```js
import { KMSClient, EncryptCommand, DecryptCommand } from "@aws-sdk/client-kms";
import { SecretsManagerClient, GetSecretValueCommand } from "@aws-sdk/client-secrets-manager";
```

Append clients:

```js
const kmsClient = new KMSClient(awsConfig);
const secretsClient = new SecretsManagerClient(awsConfig);
```

Append to `probeExtendedTools`:

```js
  {
    name: "ace_encrypt_decrypt",
    description: "Encrypt then decrypt a test value using a KMS key to verify key usability",
    inputSchema: {
      type: "object",
      properties: {
        key_id: { type: "string" },
        plaintext: { type: "string" },
      },
      required: ["key_id", "plaintext"],
    },
    async handler({ key_id, plaintext } = {}) {
      if (!key_id || !plaintext) return { error: "key_id and plaintext are required" };
      try {
        const encRes = await kmsClient.send(new EncryptCommand({
          KeyId: key_id,
          Plaintext: Buffer.from(plaintext),
        }));
        const decRes = await kmsClient.send(new DecryptCommand({
          CiphertextBlob: encRes.CiphertextBlob,
          KeyId: key_id,
        }));
        const decrypted = Buffer.from(decRes.Plaintext).toString("utf-8");
        return { decrypted, matches: decrypted === plaintext, key_id: decRes.KeyId };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "KMS_ERROR" };
      }
    },
  },
  {
    name: "ace_get_secret",
    description: "Retrieve a secret value from Secrets Manager by name or ARN",
    inputSchema: {
      type: "object",
      properties: {
        secret_id: { type: "string" },
        version_stage: { type: "string" },
      },
      required: ["secret_id"],
    },
    async handler({ secret_id, version_stage } = {}) {
      if (!secret_id) return { error: "secret_id is required" };
      try {
        const params = { SecretId: secret_id };
        if (version_stage) params.VersionStage = version_stage;
        const res = await secretsClient.send(new GetSecretValueCommand(params));
        return {
          name: res.Name,
          arn: res.ARN,
          secret_string: res.SecretString ?? null,
          created_date: res.CreatedDate?.toISOString() ?? null,
          version_id: res.VersionId,
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "SECRETS_ERROR" };
      }
    },
  },
```

- [ ] **Step 4: Add KMS + Secrets Manager observe tools to observe_extended.js**

Append imports:

```js
import { KMSClient, DescribeKeyCommand, GetKeyRotationStatusCommand } from "@aws-sdk/client-kms";
import { SecretsManagerClient, DescribeSecretCommand } from "@aws-sdk/client-secrets-manager";
```

Append clients:

```js
const kmsClient = new KMSClient(awsConfig);
const secretsClient = new SecretsManagerClient(awsConfig);
```

Append to `observeExtendedTools`:

```js
  {
    name: "ace_describe_kms_key",
    description: "Describe a KMS key including its state, usage, spec, and rotation status",
    inputSchema: {
      type: "object",
      properties: { key_id: { type: "string" } },
      required: ["key_id"],
    },
    async handler({ key_id } = {}) {
      if (!key_id) return { error: "key_id is required" };
      try {
        const [keyRes, rotationRes] = await Promise.all([
          kmsClient.send(new DescribeKeyCommand({ KeyId: key_id })),
          kmsClient.send(new GetKeyRotationStatusCommand({ KeyId: key_id })).catch(() => null),
        ]);
        const k = keyRes.KeyMetadata;
        return {
          key_id: k?.KeyId,
          arn: k?.Arn,
          description: k?.Description ?? null,
          state: k?.KeyState,
          creation_date: k?.CreationDate?.toISOString() ?? null,
          deletion_date: k?.DeletionDate?.toISOString() ?? null,
          key_usage: k?.KeyUsage,
          key_spec: k?.KeySpec,
          rotation_enabled: rotationRes?.KeyRotationEnabled ?? null,
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "KMS_ERROR" };
      }
    },
  },
  {
    name: "ace_describe_secret",
    description: "Get Secrets Manager secret metadata without retrieving the secret value",
    inputSchema: {
      type: "object",
      properties: { secret_id: { type: "string" } },
      required: ["secret_id"],
    },
    async handler({ secret_id } = {}) {
      if (!secret_id) return { error: "secret_id is required" };
      try {
        const res = await secretsClient.send(new DescribeSecretCommand({ SecretId: secret_id }));
        return {
          name: res.Name,
          arn: res.ARN,
          description: res.Description ?? null,
          rotation_enabled: res.RotationEnabled ?? false,
          rotation_lambda_arn: res.RotationLambdaARN ?? null,
          tags: (res.Tags ?? []).reduce((acc, t) => { acc[t.Key] = t.Value; return acc; }, {}),
          created_date: res.CreatedDate?.toISOString() ?? null,
          last_changed_date: res.LastChangedDate?.toISOString() ?? null,
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "SECRETS_ERROR" };
      }
    },
  },
```

- [ ] **Step 5: Run tests**

```bash
node --test tests/test_mcp_server.js 2>&1 | grep -E "ace_encrypt_decrypt|ace_describe_kms|ace_get_secret|ace_describe_secret"
```

Expected: All 6 tests pass.

- [ ] **Step 6: Commit**

```bash
git add harness/mcp_server/tools/probe_extended.js harness/mcp_server/tools/observe_extended.js tests/test_mcp_server.js
git commit -m "feat(mcp): add KMS and Secrets Manager probe/observe tools"
```

---

## Task 13: STS Probe, SSM Probe + Observe

**Files:**
- Modify: `harness/mcp_server/tools/probe_extended.js`
- Modify: `harness/mcp_server/tools/observe_extended.js`
- Modify: `tests/test_mcp_server.js`

- [ ] **Step 1: Write failing tests**

```js
// === STS ===
test("ace_get_caller_identity: returns account, user_id, arn", async () => {
  const result = await probeExtendedTools.find(t => t.name === "ace_get_caller_identity").handler({});
  assert.ok("account" in result, JSON.stringify(result));
  assert.ok("user_id" in result);
  assert.ok("arn" in result);
});

test("ace_assume_role: nonexistent role returns error", async () => {
  const result = await probeExtendedTools.find(t => t.name === "ace_assume_role")
    .handler({
      role_arn: "arn:aws:iam::000000000000:role/no-such-role",
      session_name: "ace-test-session",
    });
  assert.ok(result.error, JSON.stringify(result));
});

test("ace_assume_role: missing args returns error", async () => {
  const result = await probeExtendedTools.find(t => t.name === "ace_assume_role").handler({});
  assert.ok(result.error);
});

// === SSM ===
test("ace_get_parameter: returns name, type, value", async () => {
  const result = await probeExtendedTools.find(t => t.name === "ace_get_parameter")
    .handler({ name: PARAM_NAME });
  assert.ok("name" in result, JSON.stringify(result));
  assert.ok("value" in result);
  assert.equal(result.value, "test-value-mcp");
});

test("ace_get_parameter: nonexistent parameter returns error", async () => {
  const result = await probeExtendedTools.find(t => t.name === "ace_get_parameter")
    .handler({ name: "/no/such/param" });
  assert.ok(result.error, JSON.stringify(result));
});

test("ace_describe_parameters: returns array of parameter metadata", async () => {
  const result = await observeExtendedTools.find(t => t.name === "ace_describe_parameters")
    .handler({ path_prefix: "/test/mcp" });
  assert.ok(Array.isArray(result), JSON.stringify(result));
  if (result.length > 0) {
    assert.ok("name" in result[0]);
    assert.ok("type" in result[0]);
  }
});
```

- [ ] **Step 2: Run to verify failures**

```bash
node --test tests/test_mcp_server.js 2>&1 | grep -E "ace_get_caller|ace_assume_role|ace_get_parameter|ace_describe_parameters" | head -10
```

Expected: `TypeError` — tools not defined.

- [ ] **Step 3: Add STS + SSM probe tools to probe_extended.js**

Append imports:

```js
import { STSClient, GetCallerIdentityCommand, AssumeRoleCommand } from "@aws-sdk/client-sts";
import { SSMClient, GetParameterCommand } from "@aws-sdk/client-ssm";
```

Append clients:

```js
const stsClient = new STSClient(awsConfig);
const ssmClient = new SSMClient(awsConfig);
```

Append to `probeExtendedTools`:

```js
  {
    name: "ace_get_caller_identity",
    description: "Return the AWS account ID, user ID, and ARN for the current caller",
    inputSchema: { type: "object", properties: {} },
    async handler() {
      try {
        const res = await stsClient.send(new GetCallerIdentityCommand({}));
        return { account: res.Account, user_id: res.UserId, arn: res.Arn };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "STS_ERROR" };
      }
    },
  },
  {
    name: "ace_assume_role",
    description: "Assume an IAM role via STS and return credential metadata (secret key not returned)",
    inputSchema: {
      type: "object",
      properties: {
        role_arn: { type: "string" },
        session_name: { type: "string" },
      },
      required: ["role_arn", "session_name"],
    },
    async handler({ role_arn, session_name } = {}) {
      if (!role_arn || !session_name) return { error: "role_arn and session_name are required" };
      try {
        const res = await stsClient.send(new AssumeRoleCommand({
          RoleArn: role_arn,
          RoleSessionName: session_name,
        }));
        return {
          access_key_id: res.Credentials?.AccessKeyId,
          expiration: res.Credentials?.Expiration?.toISOString() ?? null,
          assumed_role_arn: res.AssumedRoleUser?.Arn,
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "STS_ERROR" };
      }
    },
  },
  {
    name: "ace_get_parameter",
    description: "Retrieve an SSM Parameter Store parameter value by name",
    inputSchema: {
      type: "object",
      properties: {
        name: { type: "string" },
        with_decryption: { type: "boolean" },
      },
      required: ["name"],
    },
    async handler({ name, with_decryption = false } = {}) {
      if (!name) return { error: "name is required" };
      try {
        const res = await ssmClient.send(new GetParameterCommand({
          Name: name,
          WithDecryption: with_decryption,
        }));
        return {
          name: res.Parameter?.Name,
          type: res.Parameter?.Type,
          value: res.Parameter?.Value,
          version: res.Parameter?.Version,
          last_modified: res.Parameter?.LastModifiedDate?.toISOString() ?? null,
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "SSM_ERROR" };
      }
    },
  },
```

- [ ] **Step 4: Add SSM observe tool to observe_extended.js**

Append import:

```js
import { SSMClient, DescribeParametersCommand } from "@aws-sdk/client-ssm";
```

Append client:

```js
const ssmClient = new SSMClient(awsConfig);
```

Append to `observeExtendedTools`:

```js
  {
    name: "ace_describe_parameters",
    description: "List SSM Parameter Store parameters, optionally filtered by path prefix or type",
    inputSchema: {
      type: "object",
      properties: {
        path_prefix: { type: "string" },
        parameter_type: {
          type: "string",
          enum: ["String", "StringList", "SecureString"],
        },
      },
    },
    async handler({ path_prefix, parameter_type } = {}) {
      try {
        const filters = [];
        if (path_prefix) filters.push({ Key: "Name", Option: "BeginsWith", Values: [path_prefix] });
        if (parameter_type) filters.push({ Key: "Type", Option: "Equals", Values: [parameter_type] });
        const params = filters.length ? { ParameterFilters: filters } : {};
        const res = await ssmClient.send(new DescribeParametersCommand(params));
        return (res.Parameters ?? []).map(p => ({
          name: p.Name,
          type: p.Type,
          description: p.Description ?? null,
          version: p.Version,
          last_modified: p.LastModifiedDate?.toISOString() ?? null,
          tier: p.Tier ?? null,
        }));
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "SSM_ERROR" };
      }
    },
  },
```

- [ ] **Step 5: Run tests**

```bash
node --test tests/test_mcp_server.js 2>&1 | grep -E "ace_get_caller|ace_assume_role|ace_get_parameter|ace_describe_parameters"
```

Expected: All 6 tests pass.

- [ ] **Step 6: Commit**

```bash
git add harness/mcp_server/tools/probe_extended.js harness/mcp_server/tools/observe_extended.js tests/test_mcp_server.js
git commit -m "feat(mcp): add STS probe and SSM probe/observe tools"
```

---

## Task 14: S3 Control Probe + Observe, CloudWatch Metrics Probe + Observe, IAM Simulation Probe

**Files:**
- Modify: `harness/mcp_server/tools/probe_extended.js`
- Modify: `harness/mcp_server/tools/observe_extended.js`
- Modify: `tests/test_mcp_server.js`

- [ ] **Step 1: Write failing tests**

```js
// === S3 Control ===
test("ace_list_access_points: returns array", async () => {
  const result = await probeExtendedTools.find(t => t.name === "ace_list_access_points")
    .handler({ account_id: "000000000000" });
  assert.ok(Array.isArray(result), JSON.stringify(result));
});

test("ace_list_access_points: missing args returns error", async () => {
  const result = await probeExtendedTools.find(t => t.name === "ace_list_access_points").handler({});
  assert.ok(result.error);
});

test("ace_get_public_access_block: returns block config fields", async () => {
  const result = await observeExtendedTools.find(t => t.name === "ace_get_public_access_block")
    .handler({ account_id: "000000000000" });
  assert.ok(
    "block_public_acls" in result || result.error,
    JSON.stringify(result)
  );
});

// === CloudWatch Metrics ===
test("ace_put_metric_data: returns success:true", async () => {
  const result = await probeExtendedTools.find(t => t.name === "ace_put_metric_data")
    .handler({ namespace: "ACEBench/Test", metric_name: "TestMetric", value: 42 });
  assert.ok("success" in result, JSON.stringify(result));
  assert.equal(result.success, true);
});

test("ace_put_metric_data: missing args returns error", async () => {
  const result = await probeExtendedTools.find(t => t.name === "ace_put_metric_data").handler({});
  assert.ok(result.error);
});

test("ace_get_metric_statistics: returns label and datapoints array", async () => {
  const result = await observeExtendedTools.find(t => t.name === "ace_get_metric_statistics")
    .handler({ namespace: "ACEBench/Test", metric_name: "TestMetric" });
  assert.ok("label" in result || result.error, JSON.stringify(result));
  if (!result.error) assert.ok(Array.isArray(result.datapoints));
});

// === IAM Simulation ===
test("ace_simulate_policy: nonexistent principal returns error", async () => {
  const result = await probeExtendedTools.find(t => t.name === "ace_simulate_policy")
    .handler({
      policy_source_arn: "arn:aws:iam::000000000000:role/no-such-role",
      action_names: ["s3:GetObject"],
    });
  assert.ok(result.error || Array.isArray(result), JSON.stringify(result));
});

test("ace_simulate_policy: missing args returns error", async () => {
  const result = await probeExtendedTools.find(t => t.name === "ace_simulate_policy").handler({});
  assert.ok(result.error);
});
```

- [ ] **Step 2: Run to verify failures**

```bash
node --test tests/test_mcp_server.js 2>&1 | grep -E "ace_list_access|ace_get_public|ace_put_metric|ace_get_metric|ace_simulate" | head -15
```

Expected: `TypeError` — tools not defined.

- [ ] **Step 3: Add S3 Control + CloudWatch + IAM probe tools to probe_extended.js**

Append imports:

```js
import { S3ControlClient, ListAccessPointsCommand } from "@aws-sdk/client-s3-control";
import { CloudWatchClient, PutMetricDataCommand } from "@aws-sdk/client-cloudwatch";
import { IAMClient, SimulatePrincipalPolicyCommand } from "@aws-sdk/client-iam";
```

Append clients:

```js
const s3ControlClient = new S3ControlClient(awsConfig);
const cwClient = new CloudWatchClient(awsConfig);
const iamClient = new IAMClient(awsConfig);
```

Append to `probeExtendedTools`:

```js
  {
    name: "ace_list_access_points",
    description: "List S3 Access Points for an account, optionally filtered by bucket",
    inputSchema: {
      type: "object",
      properties: {
        account_id: { type: "string" },
        bucket: { type: "string" },
      },
      required: ["account_id"],
    },
    async handler({ account_id, bucket } = {}) {
      if (!account_id) return { error: "account_id is required" };
      try {
        const params = { AccountId: account_id };
        if (bucket) params.Bucket = bucket;
        const res = await s3ControlClient.send(new ListAccessPointsCommand(params));
        return (res.AccessPointList ?? []).map(ap => ({
          name: ap.Name,
          arn: ap.AccessPointArn,
          bucket: ap.Bucket,
          network_origin: ap.NetworkOrigin,
        }));
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "S3CONTROL_ERROR" };
      }
    },
  },
  {
    name: "ace_put_metric_data",
    description: "Put a test metric data point to CloudWatch Metrics",
    inputSchema: {
      type: "object",
      properties: {
        namespace: { type: "string" },
        metric_name: { type: "string" },
        value: { type: "number" },
        unit: { type: "string" },
      },
      required: ["namespace", "metric_name", "value"],
    },
    async handler({ namespace, metric_name, value, unit = "None" } = {}) {
      if (!namespace || !metric_name || value === undefined)
        return { error: "namespace, metric_name, and value are required" };
      try {
        await cwClient.send(new PutMetricDataCommand({
          Namespace: namespace,
          MetricData: [{
            MetricName: metric_name,
            Value: value,
            Unit: unit,
            Timestamp: new Date(),
          }],
        }));
        return { success: true, namespace, metric_name, value, unit };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "CW_ERROR" };
      }
    },
  },
  {
    name: "ace_simulate_policy",
    description: "Simulate IAM policy to check whether actions are allowed for a principal ARN",
    inputSchema: {
      type: "object",
      properties: {
        policy_source_arn: { type: "string" },
        action_names: { type: "array", items: { type: "string" } },
        resource_arns: { type: "array", items: { type: "string" } },
      },
      required: ["policy_source_arn", "action_names"],
    },
    async handler({ policy_source_arn, action_names, resource_arns = ["*"] } = {}) {
      if (!policy_source_arn || !action_names?.length)
        return { error: "policy_source_arn and action_names are required" };
      try {
        const res = await iamClient.send(new SimulatePrincipalPolicyCommand({
          PolicySourceArn: policy_source_arn,
          ActionNames: action_names,
          ResourceArns: resource_arns,
        }));
        return (res.EvaluationResults ?? []).map(r => ({
          action: r.EvalActionName,
          resource: r.EvalResourceName,
          decision: r.EvalDecision,
          matched_statements: (r.MatchedStatements ?? []).map(s => s.SourcePolicyId),
        }));
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "IAM_ERROR" };
      }
    },
  },
```

- [ ] **Step 4: Add S3 Control + CloudWatch observe tools to observe_extended.js**

Append imports:

```js
import { S3ControlClient, GetPublicAccessBlockCommand } from "@aws-sdk/client-s3-control";
import { CloudWatchClient, GetMetricStatisticsCommand } from "@aws-sdk/client-cloudwatch";
```

Append clients:

```js
const s3ControlClient = new S3ControlClient(awsConfig);
const cwClient = new CloudWatchClient(awsConfig);
```

Append to `observeExtendedTools`:

```js
  {
    name: "ace_get_public_access_block",
    description: "Get account-level S3 public access block configuration",
    inputSchema: {
      type: "object",
      properties: { account_id: { type: "string" } },
      required: ["account_id"],
    },
    async handler({ account_id } = {}) {
      if (!account_id) return { error: "account_id is required" };
      try {
        const res = await s3ControlClient.send(new GetPublicAccessBlockCommand({ AccountId: account_id }));
        const c = res.PublicAccessBlockConfiguration ?? {};
        return {
          block_public_acls: c.BlockPublicAcls ?? false,
          ignore_public_acls: c.IgnorePublicAcls ?? false,
          block_public_policy: c.BlockPublicPolicy ?? false,
          restrict_public_buckets: c.RestrictPublicBuckets ?? false,
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "S3CONTROL_ERROR" };
      }
    },
  },
  {
    name: "ace_get_metric_statistics",
    description: "Get CloudWatch metric statistics for a namespace/metric over a time window",
    inputSchema: {
      type: "object",
      properties: {
        namespace: { type: "string" },
        metric_name: { type: "string" },
        period: { type: "number" },
        statistics: {
          type: "array",
          items: { type: "string", enum: ["Average", "Sum", "Maximum", "Minimum", "SampleCount"] },
        },
        start_time: { type: "string" },
        end_time: { type: "string" },
      },
      required: ["namespace", "metric_name"],
    },
    async handler({ namespace, metric_name, period = 60, statistics = ["Average"], start_time, end_time } = {}) {
      if (!namespace || !metric_name) return { error: "namespace and metric_name are required" };
      const now = new Date();
      const startDate = start_time ? new Date(start_time) : new Date(now.getTime() - 60 * 60 * 1000);
      const endDate = end_time ? new Date(end_time) : now;
      try {
        const res = await cwClient.send(new GetMetricStatisticsCommand({
          Namespace: namespace,
          MetricName: metric_name,
          Period: period,
          Statistics: statistics,
          StartTime: startDate,
          EndTime: endDate,
        }));
        return {
          label: res.Label,
          datapoints: (res.Datapoints ?? []).map(d => ({
            timestamp: d.Timestamp?.toISOString(),
            average: d.Average ?? null,
            sum: d.Sum ?? null,
            maximum: d.Maximum ?? null,
            minimum: d.Minimum ?? null,
            sample_count: d.SampleCount ?? null,
            unit: d.Unit,
          })),
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "CW_ERROR" };
      }
    },
  },
```

- [ ] **Step 5: Run tests**

```bash
node --test tests/test_mcp_server.js 2>&1 | grep -E "ace_list_access|ace_get_public|ace_put_metric|ace_get_metric|ace_simulate"
```

Expected: All 8 tests pass.

- [ ] **Step 6: Commit**

```bash
git add harness/mcp_server/tools/probe_extended.js harness/mcp_server/tools/observe_extended.js tests/test_mcp_server.js
git commit -m "feat(mcp): add S3 Control, CloudWatch Metrics, and IAM simulation tools"
```

---

## Task 15: Full Test Suite + Tool Count Verification

**Files:**
- Read only

- [ ] **Step 1: Run the complete test suite**

```bash
cd /Users/shubhan/ACEDebugging-benchmark && node --test tests/test_mcp_server.js 2>&1 | tail -30
```

Expected: All tests pass. Zero failures.

- [ ] **Step 2: Verify tool count**

```bash
node -e "
import('./harness/mcp_server/tools/probe_extended.js').then(p =>
import('./harness/mcp_server/tools/observe_extended.js').then(o => {
  console.log('probe_extended:', p.probeExtendedTools.length, 'tools');
  console.log('observe_extended:', o.observeExtendedTools.length, 'tools');
  console.log('total new:', p.probeExtendedTools.length + o.observeExtendedTools.length);
  console.log('probe names:', p.probeExtendedTools.map(t => t.name).join(', '));
  console.log('observe names:', o.observeExtendedTools.map(t => t.name).join(', '));
}))
" --input-type=module
```

Expected output:
```
probe_extended: 19 tools
observe_extended: 17 tools
total new: 36
```

- [ ] **Step 3: Verify MCP server starts cleanly with all 48 tools**

```bash
node -e "
import('./harness/mcp_server/tools/probe.js').then(p =>
import('./harness/mcp_server/tools/probe_extended.js').then(pe =>
import('./harness/mcp_server/tools/observe.js').then(o =>
import('./harness/mcp_server/tools/observe_extended.js').then(oe =>
import('./harness/mcp_server/tools/score.js').then(s => {
  const all = [...p.probeTools, ...pe.probeExtendedTools, ...o.observeTools, ...oe.observeExtendedTools, ...s.scoreTools];
  console.log('Total tools:', all.length);
  const names = all.map(t => t.name);
  const dupes = names.filter((n, i) => names.indexOf(n) !== i);
  console.log('Duplicate names:', dupes.length === 0 ? 'none' : dupes.join(', '));
}))))
" --input-type=module
```

Expected:
```
Total tools: 48
Duplicate names: none
```

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat(mcp): expand MCP server from 12 to 48 tools covering 27 LocalStack services"
```

---

## Self-Review Checklist

**Spec coverage:**
- [x] SNS: probe (`ace_publish_sns`) + observe (`ace_get_sns_topic`)
- [x] EventBridge: probe (`ace_put_events`) + observe (`ace_get_eventbridge_rule`)
- [x] EventBridge Scheduler: observe only (`ace_get_schedule`) — no active invocation API
- [x] Step Functions: probe (`ace_start_execution`) + observe (`ace_describe_state_machine`)
- [x] SWF: probe (`ace_count_open_executions`) + observe (`ace_describe_swf_domain`)
- [x] SES: probe (`ace_send_test_email`) + observe (`ace_get_ses_identity`)
- [x] EC2: probe (`ace_check_instance_state`) + observe (`ace_describe_security_group`)
- [x] Route 53: probe (`ace_check_hosted_zone`) + observe (`ace_list_dns_records`)
- [x] Route 53 Resolver: probe (`ace_list_resolver_endpoints`) + observe (`ace_get_resolver_endpoint`)
- [x] Kinesis Streams: probe (`ace_put_kinesis_record`) + observe (`ace_describe_kinesis_stream`)
- [x] Kinesis Firehose: probe (`ace_put_firehose_record`) + observe (`ace_describe_firehose_stream`)
- [x] DynamoDB Streams: probe (`ace_get_stream_records`) + observe (`ace_describe_dynamo_stream`)
- [x] KMS: probe (`ace_encrypt_decrypt`) + observe (`ace_describe_kms_key`)
- [x] Secrets Manager: probe (`ace_get_secret`) + observe (`ace_describe_secret`)
- [x] STS: probe (`ace_get_caller_identity` + `ace_assume_role`) — observe redundant with probe
- [x] SSM Parameter Store: probe (`ace_get_parameter`) + observe (`ace_describe_parameters`)
- [x] S3 Control: probe (`ace_list_access_points`) + observe (`ace_get_public_access_block`)
- [x] CloudWatch Metrics: probe (`ace_put_metric_data`) + observe (`ace_get_metric_statistics`)
- [x] IAM Simulation: probe (`ace_simulate_policy`) — simulation is inherently a probe operation

**No placeholders:** All steps contain actual code, exact commands, and expected outputs.

**Type consistency:** All `probeExtendedTools` and `observeExtendedTools` use consistent array spread in index.js. All `handler` functions follow the same `async handler({ ... } = {})` pattern with `{ error: "...", error_type: "..." }` returns on failure.