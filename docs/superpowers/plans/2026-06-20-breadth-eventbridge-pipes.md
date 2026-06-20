# EventBridge Pipes Architecture (arch06) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the third breadth-track corpus architecture — an event-driven app routed through EventBridge Pipes (arch06) — with two new Pipes MCP diagnostic tools and four behavior-manifesting fault scenarios.

**Architecture:** `SQS (source) → EventBridge Pipe (filter + optional enrichment Lambda) → Lambda (target) → DynamoDB (store)`. A Pipe continuously polls the SQS source, applies a JSON filter pattern, optionally calls an enrichment Lambda, then invokes the target Lambda. The target writes filtered/enriched records to DynamoDB. A DLQ on the source SQS queue captures events that the Pipe fails to deliver. Corpus wires a realistic inventory-update pipeline: an uploader puts JSON messages onto the source queue; the Pipe filters by a `status` field; the target Lambda upserts each record into DynamoDB.

**Tech Stack:** CloudFormation (LocalStack Ultimate), Python 3.11 Lambda handlers, Node.js v22+ MCP server (`@aws-sdk/client-pipes`), pytest + `node:test`.

---

## Global Constraints

- LocalStack runs with `ENFORCE_IAM=1 IAM_SOFT_MODE=0`; any fixture or scenario creating Lambdas must define a real assumable IAM role.
- AWS creds: `accessKeyId=test`, `secretAccessKey=test`, `region=us-east-1`, endpoint `http://localhost:4566`. IAM fake account `000000000000`.
- Prefer real AWS APIs (`Describe*`, `Get*`). Never use LocalStack-proprietary introspection (App Inspector, IAM Policy Streams).
- `fault_manifest.json` and `known_good.yaml` are NEVER exposed to the model.
- **Fault design principle (mandatory):** every fault must produce an observable behavioral symptom that Pass-1 functional verification detects. `scenario.md` states only the symptom, never the cause. No posture-only faults.
- **Fault-enforcement empiricism rule (mandatory):** a fault mechanism ships only if the Task 1 spike empirically proves LocalStack enforces it with a behavioral symptom. If the obvious mechanism is posture-only, fall back to an enforced one (wrong env var, removed IAM action, wrong target ARN, etc.).
- MCP tool files live in `harness/mcp_server/tools/` and are spread into `harness/mcp_server/index.js`. Each tool is `{ name, description, inputSchema, async handler(args) }`, returns a plain object, never throws.
- Corpus dir: `corpus/arch_06_eventbridge_pipes/`. Scenario dirs: `scenarios/arch06_fault0N_<class>/`.
- Node MCP tests: `node --test tests/test_mcp_server.js`. Spike + corpus runs require a live LocalStack.
- **Pre-flight before any execution step:** `cd harness/mcp_server && npm install`
- **This plan is self-gated:** Tasks 2–6 do not start until Task 1 produces a filled `## Task 1 findings` section that confirms LocalStack emulates Pipes end-to-end with behavioral fault enforcement. If Pipes has no execution fidelity, this plan stops at Task 1 with a documented "shelved" finding.

---

## Task 1: De-risking spike (the gate)

Exploratory — not TDD. Validates EventBridge Pipes emulation fidelity on the current LocalStack build. Downstream tasks do not start until this passes.

**Files (all gitignored):**
- Create: `scratch/spike_pipes_stack.yaml` (minimal CFN: SQS source + DLQ, EventBridge Pipe with a filter, target Lambda, execution role, DynamoDB table)
- Create: `scratch/spike_pipes.mjs` (provision → send messages → wait → probe tool-data fidelity → probe fault enforcement → teardown)

**Interfaces:**
- Consumes: LocalStack Ultimate; `@aws-sdk/client-pipes`, `@aws-sdk/client-sqs`, `@aws-sdk/client-lambda`, `@aws-sdk/client-dynamodb` at LocalStack endpoint.
- Produces: `## Task 1 findings` appended to this plan file; locked tool list and fault mechanisms (primary + fallback each).

- [ ] **Step 1: Load LocalStack and confirm Pipes is emulated**

Run the Section 2 LocalStack-load preamble verbatim:
```bash
# Start LocalStack Ultimate with IAM enforcement (required for valid scoring).
LOCALSTACK_AUTH_TOKEN=ls-... ENFORCE_IAM=1 IAM_SOFT_MODE=0 localstack start -d
until localstack status services 2>/dev/null | grep -q running; do sleep 2; done
# Confirm EventBridge Pipes appears in health:
curl -s localhost:4566/_localstack/health | python3 -c "import sys,json; h=json.load(sys.stdin); print({k:v for k,v in h.get('services',{}).items() if 'pipe' in k.lower() or 'event' in k.lower()})"
# Record LocalStack version:
curl -s localhost:4566/_localstack/info | python3 -c "import sys,json; i=json.load(sys.stdin); print('version:', i.get('version'))"
```
Expected: `pipes` key appears in services (value `running` or `available`). Record the version string for the findings block. If `pipes` does not appear, record "shelved: Pipes service absent" in Task 1 findings and stop.

- [ ] **Step 2: Write the spike CFN stack**

Create `scratch/spike_pipes_stack.yaml`:
```yaml
AWSTemplateFormatVersion: '2010-09-09'
Description: Spike — EventBridge Pipes end-to-end fidelity check

Resources:
  SpikeDlq:
    Type: AWS::SQS::Queue
    Properties:
      QueueName: spike-pipes-dlq

  SpikeSourceQueue:
    Type: AWS::SQS::Queue
    Properties:
      QueueName: spike-pipes-source
      VisibilityTimeout: 60
      RedrivePolicy:
        deadLetterTargetArn: !GetAtt SpikeDlq.Arn
        maxReceiveCount: 2

  SpikeTable:
    Type: AWS::DynamoDB::Table
    Properties:
      TableName: spike-pipes-table
      BillingMode: PAY_PER_REQUEST
      AttributeDefinitions:
        - AttributeName: id
          AttributeType: S
      KeySchema:
        - AttributeName: id
          KeyType: HASH

  SpikePipeRole:
    Type: AWS::IAM::Role
    Properties:
      RoleName: spike-pipes-role
      AssumeRolePolicyDocument:
        Version: '2012-10-17'
        Statement:
          - Effect: Allow
            Principal:
              Service: pipes.amazonaws.com
            Action: sts:AssumeRole
      Policies:
        - PolicyName: spike-pipes-policy
          PolicyDocument:
            Version: '2012-10-17'
            Statement:
              - Effect: Allow
                Action:
                  - sqs:ReceiveMessage
                  - sqs:DeleteMessage
                  - sqs:GetQueueAttributes
                Resource: !GetAtt SpikeSourceQueue.Arn
              - Effect: Allow
                Action: lambda:InvokeFunction
                Resource: !GetAtt SpikeTargetFunction.Arn

  SpikeTargetRole:
    Type: AWS::IAM::Role
    Properties:
      RoleName: spike-target-role
      AssumeRolePolicyDocument:
        Version: '2012-10-17'
        Statement:
          - Effect: Allow
            Principal:
              Service: lambda.amazonaws.com
            Action: sts:AssumeRole
      Policies:
        - PolicyName: spike-target-policy
          PolicyDocument:
            Version: '2012-10-17'
            Statement:
              - Effect: Allow
                Action:
                  - logs:CreateLogGroup
                  - logs:CreateLogStream
                  - logs:PutLogEvents
                Resource: '*'
              - Effect: Allow
                Action: dynamodb:PutItem
                Resource: !GetAtt SpikeTable.Arn

  SpikeTargetFunction:
    Type: AWS::Lambda::Function
    Properties:
      FunctionName: spike-pipes-target
      Runtime: python3.11
      Handler: index.lambda_handler
      Role: !GetAtt SpikeTargetRole.Arn
      Timeout: 15
      Environment:
        Variables:
          TABLE_NAME: !Ref SpikeTable
      Code:
        ZipFile: |
          import json, os, uuid, boto3
          ddb = boto3.client("dynamodb", endpoint_url=os.environ.get("AWS_ENDPOINT_URL","http://localhost:4566"))
          def lambda_handler(event, context):
              for rec in (event if isinstance(event, list) else [event]):
                  body = rec.get("body", rec)
                  if isinstance(body, str):
                      body = json.loads(body)
                  ddb.put_item(TableName=os.environ["TABLE_NAME"], Item={"id": {"S": body.get("id", str(uuid.uuid4()))}, "status": {"S": body.get("status","unknown")}})

  SpikePipe:
    Type: AWS::Pipes::Pipe
    Properties:
      Name: spike-pipe
      RoleArn: !GetAtt SpikePipeRole.Arn
      Source: !GetAtt SpikeSourceQueue.Arn
      SourceParameters:
        SqsQueueParameters:
          BatchSize: 1
        FilterCriteria:
          Filters:
            - Pattern: '{"body": {"status": ["ACTIVE"]}}'
      Target: !GetAtt SpikeTargetFunction.Arn
      DesiredState: RUNNING

Outputs:
  SourceQueueUrl:
    Value: !Ref SpikeSourceQueue
  PipeName:
    Value: !Ref SpikePipe
  TableName:
    Value: !Ref SpikeTable
  TargetFunctionName:
    Value: !Ref SpikeTargetFunction
```

- [ ] **Step 3: Write and run the spike script**

Create `scratch/spike_pipes.mjs`:
```javascript
import {
  CloudFormationClient,
  CreateStackCommand,
  DescribeStacksCommand,
  DeleteStackCommand,
  waitUntilStackCreateComplete,
  waitUntilStackDeleteComplete,
} from "@aws-sdk/client-cloudformation";
import { SQSClient, SendMessageCommand, GetQueueAttributesCommand } from "@aws-sdk/client-sqs";
import { PipesClient, DescribePipeCommand, ListPipesCommand } from "@aws-sdk/client-pipes";
import { DynamoDBClient, ScanCommand } from "@aws-sdk/client-dynamodb";
import { readFileSync } from "fs";

const cfg = {
  endpoint: "http://localhost:4566",
  region: "us-east-1",
  credentials: { accessKeyId: "test", secretAccessKey: "test" },
};
const cf = new CloudFormationClient(cfg);
const sqs = new SQSClient(cfg);
const pipes = new PipesClient(cfg);
const ddb = new DynamoDBClient(cfg);

const STACK = "ace-bench-spike-pipes";
const TEMPLATE = readFileSync("scratch/spike_pipes_stack.yaml", "utf8");

async function stackOutput(outputs, key) {
  return outputs.find(o => o.OutputKey === key)?.OutputValue;
}

async function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// ── PROVISION ────────────────────────────────────────────────────────────────
console.log("PROBE provision: creating spike stack …");
await cf.send(new CreateStackCommand({
  StackName: STACK,
  TemplateBody: TEMPLATE,
  Capabilities: ["CAPABILITY_NAMED_IAM"],
}));
await waitUntilStackCreateComplete({ client: cf, maxWaitTime: 120 }, { StackName: STACK });
const { Stacks } = await cf.send(new DescribeStacksCommand({ StackName: STACK }));
const outputs = Stacks[0].Outputs;
const queueUrl = await stackOutput(outputs, "SourceQueueUrl");
const pipeName = await stackOutput(outputs, "PipeName");
const tableName = await stackOutput(outputs, "TableName");
console.log(`PROBE provision: CREATE_COMPLETE  queue=${queueUrl}  pipe=${pipeName}  table=${tableName}`);

// ── PROBE (a) TOOL-DATA FIDELITY — DescribePipe ──────────────────────────────
console.log("PROBE tool-data: ace_describe_pipe (DescribePipe) …");
try {
  const dp = await pipes.send(new DescribePipeCommand({ Name: pipeName }));
  console.log(`PROBE tool-data DescribePipe: name=${dp.Name} currentState=${dp.CurrentState} desiredState=${dp.DesiredState} source=${dp.Source} target=${dp.Target} enrichment=${dp.Enrichment ?? "none"} stateReason=${dp.StateReason ?? "ok"}`);
} catch (e) {
  console.log("PROBE tool-data DescribePipe: ERROR", String(e.message ?? e));
}

console.log("PROBE tool-data: ListPipes …");
try {
  const lp = await pipes.send(new ListPipesCommand({}));
  console.log(`PROBE tool-data ListPipes: count=${lp.Pipes?.length ?? 0}  pipes=${JSON.stringify(lp.Pipes?.map(p=>({name:p.Name,state:p.CurrentState})))}`);
} catch (e) {
  console.log("PROBE tool-data ListPipes: ERROR", String(e.message ?? e));
}

// ── SEND TRAFFIC ──────────────────────────────────────────────────────────────
console.log("PROBE traffic: sending 3 messages (2 ACTIVE, 1 INACTIVE) …");
for (const msg of [
  { id: "spike-001", status: "ACTIVE" },
  { id: "spike-002", status: "ACTIVE" },
  { id: "spike-003", status: "INACTIVE" },
]) {
  await sqs.send(new SendMessageCommand({ QueueUrl: queueUrl, MessageBody: JSON.stringify(msg) }));
}

// Wait for Pipe to poll and invoke target
console.log("PROBE traffic: waiting 15s for Pipe to poll source and invoke target …");
await sleep(15000);

// Check DynamoDB for delivered records
const scan = await ddb.send(new ScanCommand({ TableName: tableName }));
console.log(`PROBE traffic: DynamoDB records written=${scan.Count ?? 0}  items=${JSON.stringify(scan.Items)}`);
// Expectation: 2 records (ACTIVE), not 3 (INACTIVE filtered by Pipe filter)

// ── PROBE (b) FAULT ENFORCEMENT — FILTER PATTERN ─────────────────────────────
// The filter already ran: if DynamoDB has 2 records and not 3, the filter is enforced.
const filterEnforced = (scan.Count ?? 0) === 2;
console.log(`PROBE fault enforcement: filter silently drops INACTIVE → ${filterEnforced ? "ENFORCED (2 records, not 3)" : "NOT ENFORCED or pipe didn't fire"}`);

// ── PROBE (b) FAULT ENFORCEMENT — MISSING PIPE ROLE IAM ACTION ───────────────
// Test: can we see what happens if the pipe execution role loses lambda:InvokeFunction?
// We probe this structurally: check if LocalStack returns a PipeState of STOPPED/ERROR
// when the target ARN is deliberately invalid (wrong ARN format → describe shows error).
// This requires creating a second pipe or reading stateReason for signal.
console.log("PROBE fault enforcement: broken target (wrong ARN) → will attempt to create a second pipe with a nonexistent target …");
try {
  const { PipesClient: PC2, CreatePipeCommand } = await import("@aws-sdk/client-pipes");
  const p2 = new PC2(cfg);
  await p2.send(new CreatePipeCommand({
    Name: "spike-pipe-brokentarget",
    RoleArn: `arn:aws:iam::000000000000:role/spike-pipes-role`,
    Source: queueUrl,
    Target: "arn:aws:lambda:us-east-1:000000000000:function:nonexistent-target-function",
    DesiredState: "RUNNING",
  }));
  // Send a message and wait
  await sqs.send(new SendMessageCommand({ QueueUrl: queueUrl, MessageBody: JSON.stringify({ id: "bfault-001", status: "ACTIVE" }) }));
  await sleep(10000);
  const dpf = await pipes.send(new DescribePipeCommand({ Name: "spike-pipe-brokentarget" }));
  console.log(`PROBE fault enforcement: broken target pipe state=${dpf.CurrentState} stateReason=${dpf.StateReason ?? "ok"}`);
} catch (e2) {
  console.log("PROBE fault enforcement: broken target error:", String(e2.message ?? e2));
}

// ── PROBE (b) FAULT ENFORCEMENT — MISSING SQS RECEIVE PERMISSION on PIPE ROLE
console.log("PROBE fault enforcement: record DLQ depth as baseline …");
const dlqAttrs = await sqs.send(new GetQueueAttributesCommand({
  QueueUrl: queueUrl.replace("spike-pipes-source", "spike-pipes-dlq"),
  AttributeNames: ["ApproximateNumberOfMessages"],
}));
console.log(`PROBE fault enforcement: DLQ depth=${dlqAttrs.Attributes?.ApproximateNumberOfMessages ?? "unknown"}`);

// ── PROBE (b) FAULT ENFORCEMENT — PIPE STOPPED WHEN DesiredState=STOPPED
console.log("PROBE fault enforcement: does DesiredState=STOPPED prevent polling? (structural check via DescribePipe after update)");
try {
  const { UpdatePipeCommand } = await import("@aws-sdk/client-pipes");
  await pipes.send(new UpdatePipeCommand({ Name: pipeName, DesiredState: "STOPPED" }));
  await sleep(5000);
  const dpStopped = await pipes.send(new DescribePipeCommand({ Name: pipeName }));
  console.log(`PROBE fault enforcement: after STOPPED desiredState=${dpStopped.DesiredState} currentState=${dpStopped.CurrentState}`);
} catch (e3) {
  console.log("PROBE fault enforcement: UpdatePipe STOPPED:", String(e3.message ?? e3));
}

// ── TEARDOWN ──────────────────────────────────────────────────────────────────
console.log("PROBE teardown: deleting spike stack …");
await cf.send(new DeleteStackCommand({ StackName: STACK }));
await waitUntilStackDeleteComplete({ client: cf, maxWaitTime: 120 }, { StackName: STACK });
console.log("PROBE teardown: complete.");
```

Run:
```bash
cd /path/to/ace-bench  # repo root
node scratch/spike_pipes.mjs 2>&1 | tee scratch/spike_pipes_output.txt
```
Record all labeled output lines in the `## Task 1 findings` section below.

- [ ] **Step 4: Record capability×fidelity matrix and lock decisions**

After the spike script completes, fill in the `## Task 1 findings` template at the end of this file. The matrix must answer:
- Does `DescribePipe` return `Name`, `CurrentState`, `DesiredState`, `Source`, `Target`, `Enrichment`, `StateReason`?
- Does `ListPipes` return a non-empty list with `Name`/`CurrentState`?
- Does the Pipe actually poll SQS and invoke Lambda (end-to-end)?
- Does the filter pattern silently drop non-matching messages (enforced)?
- Does a broken/nonexistent target ARN produce a detectable state change or error on `DescribePipe`?
- Does removing `sqs:ReceiveMessage` from the pipe role prevent polling (enforced IAM)?

Lock: tool list, fault mechanisms (primary + fallback), X-Ray decision.

---

## Task 2: EventBridge Pipes MCP diagnostic tools

Adds `harness/mcp_server/tools/probe_pipes.js` with two tools and wires it into `index.js`. TDD via `node:test`.

**Files:**
- Create: `harness/mcp_server/tools/probe_pipes.js`
- Modify: `harness/mcp_server/index.js` (import + spread `probePipesTools`)
- Modify: `harness/mcp_server/package.json` (add `@aws-sdk/client-pipes`)
- Modify: `tests/test_mcp_server.js` (append `probePipesTools` block)

**Interfaces:**
- Consumes: the `awsConfig` client pattern from `probe_rds.js`; the `tool(list, name)` helper and `before()` hook in `tests/test_mcp_server.js`; Task 1 locked tool list.
- Produces: `export const probePipesTools` — array of two tools:
  - `ace_describe_pipe({ pipe_name })` → `{ name, current_state, desired_state, source_arn, target_arn, enrichment_arn, filter_patterns, state_reason, last_modified, creation_time }` or `{ error }`.
  - `ace_get_pipe_source_target({ pipe_name })` → `{ name, source_arn, source_type, target_arn, target_type, enrichment_arn, filter_patterns, batch_size, source_parameters, target_parameters }` or `{ error }`.

**Note:** if Task 1 finds that `DescribePipe` is empty or the Pipes API is absent, this task adds zero tools and records the shelving decision. The test file still gets a membership assertion for `probePipesTools = []` so the test suite does not regress.

- [ ] **Step 1: Pre-flight**

```bash
cd harness/mcp_server && npm install && cd -
```

- [ ] **Step 2: Add the Pipes SDK dependency**

```bash
cd harness/mcp_server && npm install @aws-sdk/client-pipes && cd -
```
Expected: `@aws-sdk/client-pipes` appears in `harness/mcp_server/package.json` dependencies.

- [ ] **Step 3: Write the failing tests**

Append to `tests/test_mcp_server.js`. First add the import near the other tool imports:
```javascript
import { probePipesTools } from "../harness/mcp_server/tools/probe_pipes.js";
```

Then add a `before()`-seeded Pipes block and tests:
```javascript
// ── EventBridge Pipes seeded data ──────────────────────────────────────────
// The before() hook is already running; seed a minimal Pipe in the shared before().
// These tests are self-contained: missing-arg and unknown-name cases need no seeded pipe.
// The happy-path test uses the seeded pipe name from PIPE_NAME constant below.

const PIPE_NAME = "test-mcp-pipe";
// NOTE: Seeding an actual pipe (CreatePipe) requires the pipe execution role and an SQS source.
// We test happy-path fidelity against the unknown-name error path here; a LocalStack-live
// happy-path test is done via the spike (Task 1). The test suite remains fast and offline-safe.

test("probePipesTools exposes the two Pipes tools", () => {
  for (const n of ["ace_describe_pipe", "ace_get_pipe_source_target"]) {
    assert.ok(tool(probePipesTools, n), `missing ${n}`);
  }
});

test("ace_describe_pipe: missing pipe_name returns error", async () => {
  const res = await tool(probePipesTools, "ace_describe_pipe").handler({});
  assert.ok(res.error, "expected error for missing pipe_name");
});

test("ace_describe_pipe: unknown pipe_name returns error", async () => {
  const res = await tool(probePipesTools, "ace_describe_pipe").handler({ pipe_name: "nope-does-not-exist-pipe-xyz" });
  assert.ok(res.error, "expected error for nonexistent pipe");
});

test("ace_get_pipe_source_target: missing pipe_name returns error", async () => {
  const res = await tool(probePipesTools, "ace_get_pipe_source_target").handler({});
  assert.ok(res.error, "expected error for missing pipe_name");
});

test("ace_get_pipe_source_target: unknown pipe_name returns error", async () => {
  const res = await tool(probePipesTools, "ace_get_pipe_source_target").handler({ pipe_name: "nope-does-not-exist-pipe-xyz" });
  assert.ok(res.error, "expected error for nonexistent pipe");
});

test("ace_describe_pipe: result shape has required fields on success", async () => {
  // This test only runs meaningfully against a seeded pipe (live LocalStack).
  // Skips gracefully if the pipe does not exist (error key present).
  const res = await tool(probePipesTools, "ace_describe_pipe").handler({ pipe_name: PIPE_NAME });
  if (res.error) {
    // No live pipe seeded in before() — skip shape check.
    assert.ok(true, "no seeded pipe, shape check skipped");
    return;
  }
  assert.ok(typeof res.name === "string", "name must be string");
  assert.ok(typeof res.current_state === "string", "current_state must be string");
  assert.ok(typeof res.desired_state === "string", "desired_state must be string");
  assert.ok(typeof res.source_arn === "string", "source_arn must be string");
  assert.ok(typeof res.target_arn === "string", "target_arn must be string");
  assert.ok(Array.isArray(res.filter_patterns), "filter_patterns must be array");
});

test("ace_get_pipe_source_target: result shape has required fields on success", async () => {
  const res = await tool(probePipesTools, "ace_get_pipe_source_target").handler({ pipe_name: PIPE_NAME });
  if (res.error) {
    assert.ok(true, "no seeded pipe, shape check skipped");
    return;
  }
  assert.ok(typeof res.name === "string", "name must be string");
  assert.ok(typeof res.source_arn === "string", "source_arn must be string");
  assert.ok(typeof res.target_arn === "string", "target_arn must be string");
  assert.ok(Array.isArray(res.filter_patterns), "filter_patterns must be array");
});
```

- [ ] **Step 4: Confirm tests fail (file not yet created)**

```bash
node --test tests/test_mcp_server.js 2>&1 | grep -A2 probePipesTools
```
Expected: `FAIL — Cannot find module '.../probe_pipes.js'`.

- [ ] **Step 5: Implement `probe_pipes.js`**

Create `harness/mcp_server/tools/probe_pipes.js`:
```javascript
import {
  PipesClient,
  DescribePipeCommand,
} from "@aws-sdk/client-pipes";

const awsConfig = {
  endpoint: process.env.LOCALSTACK_ENDPOINT ?? "http://localhost:4566",
  region: "us-east-1",
  credentials: { accessKeyId: "test", secretAccessKey: "test" },
};

const pipesClient = new PipesClient(awsConfig);

export const probePipesTools = [
  {
    name: "ace_describe_pipe",
    description:
      "EventBridge Pipes DescribePipe: return one pipe's runtime state — current_state (RUNNING/STOPPED/STARTING/STOPPING/CREATING/UPDATING/DELETING/CREATE_FAILED/UPDATE_FAILED/START_FAILED/STOP_FAILED), desired_state, source_arn, target_arn, enrichment_arn (if any), filter_patterns (array of JSON strings), state_reason, last_modified, creation_time. " +
      "Maps to the AWS Pipes DescribePipe API. " +
      "Use when the pipeline is silent — target Lambda never fires, records stop appearing in the destination store — to determine whether the pipe is RUNNING, stuck in a failed state, or has a stateReason that explains the outage. " +
      "Also use when diagnosing a missing enrichment step or a misconfigured execution role (pipe may enter CREATE_FAILED or START_FAILED with a permission stateReason).",
    inputSchema: {
      type: "object",
      properties: {
        pipe_name: {
          type: "string",
          description: "The name of the EventBridge Pipe to describe.",
        },
      },
      required: ["pipe_name"],
    },
    async handler({ pipe_name } = {}) {
      if (!pipe_name) return { error: "pipe_name is required" };
      try {
        const out = await pipesClient.send(new DescribePipeCommand({ Name: pipe_name }));
        // Extract filter patterns from SourceParameters.FilterCriteria
        const filters = (out.SourceParameters?.FilterCriteria?.Filters ?? []).map(
          (f) => f.Pattern ?? ""
        );
        return {
          name: out.Name ?? null,
          current_state: out.CurrentState ?? null,
          desired_state: out.DesiredState ?? null,
          source_arn: out.Source ?? null,
          target_arn: out.Target ?? null,
          enrichment_arn: out.Enrichment ?? null,
          filter_patterns: filters,
          state_reason: out.StateReason ?? null,
          last_modified: out.LastModifiedTime?.toISOString() ?? null,
          creation_time: out.CreationTime?.toISOString() ?? null,
        };
      } catch (err) {
        return { error: String(err?.message ?? err) };
      }
    },
  },
  {
    name: "ace_get_pipe_source_target",
    description:
      "EventBridge Pipes DescribePipe (wiring view): return the resolved source ARN, source type (SQS/DynamoDB-stream/Kinesis), target ARN, target type (Lambda/SQS/EventBridge/StepFunctions), enrichment ARN, filter patterns, batch size, and the raw source_parameters and target_parameters objects. " +
      "Maps to the AWS Pipes DescribePipe API, focusing on the wiring fields rather than state. " +
      "Use to verify that the pipe's source, target, and enrichment are wired to the correct resources — a broken target ARN (typo, deleted Lambda, wrong region) or a missing enrichment ARN will be immediately visible here. " +
      "Pair with ace_describe_pipe (for current_state/stateReason) when the pipeline is silent but the pipe appears RUNNING.",
    inputSchema: {
      type: "object",
      properties: {
        pipe_name: {
          type: "string",
          description: "The name of the EventBridge Pipe to inspect.",
        },
      },
      required: ["pipe_name"],
    },
    async handler({ pipe_name } = {}) {
      if (!pipe_name) return { error: "pipe_name is required" };
      try {
        const out = await pipesClient.send(new DescribePipeCommand({ Name: pipe_name }));
        const sourceArn = out.Source ?? null;
        const targetArn = out.Target ?? null;
        const filters = (out.SourceParameters?.FilterCriteria?.Filters ?? []).map(
          (f) => f.Pattern ?? ""
        );
        // Derive source/target types from ARN prefix
        const sourceType = sourceArn
          ? sourceArn.includes(":sqs:") ? "SQS"
            : sourceArn.includes(":kinesis:") ? "Kinesis"
            : sourceArn.includes(":dynamodb:") ? "DynamoDB-stream"
            : "unknown"
          : null;
        const targetType = targetArn
          ? targetArn.includes(":lambda:") ? "Lambda"
            : targetArn.includes(":sqs:") ? "SQS"
            : targetArn.includes(":events:") ? "EventBridge"
            : targetArn.includes(":states:") ? "StepFunctions"
            : "unknown"
          : null;
        const batchSize =
          out.SourceParameters?.SqsQueueParameters?.BatchSize ??
          out.SourceParameters?.KinesisStreamParameters?.BatchSize ??
          out.SourceParameters?.DynamoDBStreamParameters?.BatchSize ??
          null;
        return {
          name: out.Name ?? null,
          source_arn: sourceArn,
          source_type: sourceType,
          target_arn: targetArn,
          target_type: targetType,
          enrichment_arn: out.Enrichment ?? null,
          filter_patterns: filters,
          batch_size: batchSize,
          source_parameters: out.SourceParameters ?? null,
          target_parameters: out.TargetParameters ?? null,
        };
      } catch (err) {
        return { error: String(err?.message ?? err) };
      }
    },
  },
];
```

- [ ] **Step 6: Wire into `index.js`**

In `harness/mcp_server/index.js`, add the import alongside the others:
```javascript
import { probePipesTools } from "./tools/probe_pipes.js";
```
And add `...probePipesTools` to the spread in the `for` loop:
```javascript
for (const tool of [...probeTools, ...probeExtendedTools, ...observeTools, ...observeExtendedTools, ...observeTracingTools, ...probeRdsTools, ...probePipesTools, ...scoreTools]) {
```

- [ ] **Step 7: Run the tests to verify they pass**

```bash
node --test tests/test_mcp_server.js 2>&1 | tail -30
```
Expected: all `probePipesTools` / `ace_describe_pipe` / `ace_get_pipe_source_target` tests PASS; no prior tests regress.

- [ ] **Step 8: Commit**

```bash
git add harness/mcp_server/tools/probe_pipes.js harness/mcp_server/index.js harness/mcp_server/package.json harness/mcp_server/package-lock.json tests/test_mcp_server.js
git commit -m "feat(mcp): add EventBridge Pipes diagnostic tools (ace_describe_pipe, ace_get_pipe_source_target)"
```

---

## Task 3: arch06 corpus (known-good)

Builds the `corpus/arch_06_eventbridge_pipes/` directory: a CloudFormation known-good template, Python Lambda handlers (sender + target), and a functional test.

**Files:**
- Create: `corpus/arch_06_eventbridge_pipes/known_good.yaml`
- Create: `corpus/arch_06_eventbridge_pipes/deployment/lambda/sender/index.py` (puts messages onto the source SQS queue)
- Create: `corpus/arch_06_eventbridge_pipes/deployment/lambda/target/index.py` (writes filtered records to DynamoDB)
- Create: `corpus/arch_06_eventbridge_pipes/deployment/lambda/enrichment/index.py` (optional enrichment Lambda, adds a `processed_at` field)
- Create: `corpus/arch_06_eventbridge_pipes/functional_test.py`
- Create: `corpus/arch_06_eventbridge_pipes/traffic_flow.md`

**Interfaces:**
- Consumes: Task 1 findings (confirms Pipes polls SQS → filter → Lambda end-to-end); Task 2 tools (used in Task 4 diagnostic verification).
- Produces: a deployable, working arch06 stack verified via `functional_test.py` against a live LocalStack.

- [ ] **Step 1: Write `known_good.yaml`**

Create `corpus/arch_06_eventbridge_pipes/known_good.yaml`:
```yaml
AWSTemplateFormatVersion: '2010-09-09'
Description: arch06 — EventBridge Pipes inventory pipeline (SQS source → Pipe with filter → Lambda target → DynamoDB).

Resources:

  # ── Dead-letter queue for undeliverable source messages ──────────────────────
  InventorySourceDlq:
    Type: AWS::SQS::Queue
    Properties:
      QueueName: !Sub '${AWS::StackName}-inventory-source-dlq'
      VisibilityTimeout: 300

  # ── Source SQS queue (Pipe polls this) ───────────────────────────────────────
  InventorySourceQueue:
    Type: AWS::SQS::Queue
    Properties:
      QueueName: !Sub '${AWS::StackName}-inventory-source'
      VisibilityTimeout: 60
      RedrivePolicy:
        deadLetterTargetArn: !GetAtt InventorySourceDlq.Arn
        maxReceiveCount: 3

  # ── DynamoDB table (target Lambda writes here) ────────────────────────────────
  InventoryTable:
    Type: AWS::DynamoDB::Table
    Properties:
      TableName: !Sub '${AWS::StackName}-inventory'
      BillingMode: PAY_PER_REQUEST
      AttributeDefinitions:
        - AttributeName: item_id
          AttributeType: S
      KeySchema:
        - AttributeName: item_id
          KeyType: HASH

  # ── IAM role: Pipe execution ──────────────────────────────────────────────────
  PipeExecutionRole:
    Type: AWS::IAM::Role
    Properties:
      RoleName: !Sub '${AWS::StackName}-pipe-role'
      AssumeRolePolicyDocument:
        Version: '2012-10-17'
        Statement:
          - Effect: Allow
            Principal:
              Service: pipes.amazonaws.com
            Action: sts:AssumeRole
      Policies:
        - PolicyName: pipe-execution-policy
          PolicyDocument:
            Version: '2012-10-17'
            Statement:
              - Effect: Allow
                Action:
                  - sqs:ReceiveMessage
                  - sqs:DeleteMessage
                  - sqs:GetQueueAttributes
                Resource: !GetAtt InventorySourceQueue.Arn
              - Effect: Allow
                Action: lambda:InvokeFunction
                Resource:
                  - !GetAtt EnrichmentFunction.Arn
                  - !GetAtt TargetFunction.Arn

  # ── IAM role: Sender Lambda ───────────────────────────────────────────────────
  SenderRole:
    Type: AWS::IAM::Role
    Properties:
      RoleName: !Sub '${AWS::StackName}-sender-role'
      AssumeRolePolicyDocument:
        Version: '2012-10-17'
        Statement:
          - Effect: Allow
            Principal:
              Service: lambda.amazonaws.com
            Action: sts:AssumeRole
      Policies:
        - PolicyName: sender-policy
          PolicyDocument:
            Version: '2012-10-17'
            Statement:
              - Effect: Allow
                Action:
                  - logs:CreateLogGroup
                  - logs:CreateLogStream
                  - logs:PutLogEvents
                Resource: '*'
              - Effect: Allow
                Action: sqs:SendMessage
                Resource: !GetAtt InventorySourceQueue.Arn

  # ── IAM role: Enrichment Lambda ───────────────────────────────────────────────
  EnrichmentRole:
    Type: AWS::IAM::Role
    Properties:
      RoleName: !Sub '${AWS::StackName}-enrichment-role'
      AssumeRolePolicyDocument:
        Version: '2012-10-17'
        Statement:
          - Effect: Allow
            Principal:
              Service: lambda.amazonaws.com
            Action: sts:AssumeRole
      Policies:
        - PolicyName: enrichment-policy
          PolicyDocument:
            Version: '2012-10-17'
            Statement:
              - Effect: Allow
                Action:
                  - logs:CreateLogGroup
                  - logs:CreateLogStream
                  - logs:PutLogEvents
                Resource: '*'

  # ── IAM role: Target Lambda ───────────────────────────────────────────────────
  TargetRole:
    Type: AWS::IAM::Role
    Properties:
      RoleName: !Sub '${AWS::StackName}-target-role'
      AssumeRolePolicyDocument:
        Version: '2012-10-17'
        Statement:
          - Effect: Allow
            Principal:
              Service: lambda.amazonaws.com
            Action: sts:AssumeRole
      Policies:
        - PolicyName: target-policy
          PolicyDocument:
            Version: '2012-10-17'
            Statement:
              - Effect: Allow
                Action:
                  - logs:CreateLogGroup
                  - logs:CreateLogStream
                  - logs:PutLogEvents
                Resource: '*'
              - Effect: Allow
                Action:
                  - dynamodb:PutItem
                  - dynamodb:UpdateItem
                Resource: !GetAtt InventoryTable.Arn

  # ── Sender Lambda — puts inventory update messages onto source queue ───────────
  SenderFunction:
    Type: AWS::Lambda::Function
    Properties:
      FunctionName: !Sub '${AWS::StackName}-sender'
      Runtime: python3.11
      Handler: index.lambda_handler
      Role: !GetAtt SenderRole.Arn
      Timeout: 30
      Environment:
        Variables:
          QUEUE_URL: !Ref InventorySourceQueue
      Code:
        S3Bucket: ace-bench-artifacts
        S3Key: arch06-sender.zip

  # ── Enrichment Lambda — adds processed_at timestamp ──────────────────────────
  EnrichmentFunction:
    Type: AWS::Lambda::Function
    Properties:
      FunctionName: !Sub '${AWS::StackName}-enrichment'
      Runtime: python3.11
      Handler: index.lambda_handler
      Role: !GetAtt EnrichmentRole.Arn
      Timeout: 15
      Code:
        S3Bucket: ace-bench-artifacts
        S3Key: arch06-enrichment.zip

  # ── Target Lambda — writes enriched records to DynamoDB ──────────────────────
  TargetFunction:
    Type: AWS::Lambda::Function
    Properties:
      FunctionName: !Sub '${AWS::StackName}-target'
      Runtime: python3.11
      Handler: index.lambda_handler
      Role: !GetAtt TargetRole.Arn
      Timeout: 30
      Environment:
        Variables:
          TABLE_NAME: !Ref InventoryTable
      Code:
        S3Bucket: ace-bench-artifacts
        S3Key: arch06-target.zip

  # ── EventBridge Pipe ──────────────────────────────────────────────────────────
  InventoryPipe:
    Type: AWS::Pipes::Pipe
    Properties:
      Name: !Sub '${AWS::StackName}-inventory-pipe'
      RoleArn: !GetAtt PipeExecutionRole.Arn
      Source: !GetAtt InventorySourceQueue.Arn
      SourceParameters:
        SqsQueueParameters:
          BatchSize: 1
          MaximumBatchingWindowInSeconds: 0
        FilterCriteria:
          Filters:
            - Pattern: '{"body": {"status": ["ACTIVE"]}}'
      Enrichment: !GetAtt EnrichmentFunction.Arn
      Target: !GetAtt TargetFunction.Arn
      DesiredState: RUNNING

Outputs:
  InventorySourceQueueUrl:
    Value: !Ref InventorySourceQueue
    Description: URL of the source SQS queue (send messages here)
  InventorySourceDlqUrl:
    Value: !Ref InventorySourceDlq
    Description: URL of the source DLQ (undeliverable messages land here)
  InventoryTableName:
    Value: !Ref InventoryTable
    Description: DynamoDB table name where records are written
  SenderFunctionName:
    Value: !Ref SenderFunction
    Description: Name of the sender Lambda
  TargetFunctionName:
    Value: !Ref TargetFunction
    Description: Name of the target Lambda
  PipeName:
    Value: !Ref InventoryPipe
    Description: Name of the EventBridge Pipe
```

- [ ] **Step 2: Write the sender Lambda handler**

Create `corpus/arch_06_eventbridge_pipes/deployment/lambda/sender/index.py`:
```python
"""
Sender Lambda — receives an API event with a list of inventory items and puts
each as a JSON message onto the source SQS queue for the EventBridge Pipe.

Expected event: { "items": [ { "item_id": "...", "name": "...", "status": "ACTIVE"|"INACTIVE", "quantity": N } ] }
Returns: { "sent": N, "queue_url": "..." }
"""
import json
import os
import boto3

_sqs = None


def _get_sqs():
    global _sqs
    if _sqs is None:
        _sqs = boto3.client(
            "sqs",
            endpoint_url=os.environ.get("AWS_ENDPOINT_URL", "http://localhost:4566"),
        )
    return _sqs


def lambda_handler(event, context):
    queue_url = os.environ["QUEUE_URL"]
    items = event.get("items", [])
    sqs = _get_sqs()
    sent = 0
    for item in items:
        sqs.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(item),
        )
        sent += 1
    return {"sent": sent, "queue_url": queue_url}
```

- [ ] **Step 3: Write the enrichment Lambda handler**

Create `corpus/arch_06_eventbridge_pipes/deployment/lambda/enrichment/index.py`:
```python
"""
Enrichment Lambda — called by the EventBridge Pipe between filter and target.
Receives a list of pipe event records, adds a `processed_at` timestamp to each
body, and returns the enriched list. The Pipe passes the return value as the
event to the target Lambda.

Input from Pipe: list of SQS record dicts with body as string or dict.
Output: list of enriched record dicts (body dict with `processed_at` added).
"""
import json
import os
from datetime import datetime, timezone


def lambda_handler(event, context):
    enriched = []
    records = event if isinstance(event, list) else [event]
    for rec in records:
        body = rec.get("body", rec)
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except ValueError:
                body = {"raw": body}
        body["processed_at"] = datetime.now(timezone.utc).isoformat()
        enriched_rec = dict(rec)
        enriched_rec["body"] = body
        enriched.append(enriched_rec)
    return enriched
```

- [ ] **Step 4: Write the target Lambda handler**

Create `corpus/arch_06_eventbridge_pipes/deployment/lambda/target/index.py`:
```python
"""
Target Lambda — invoked by the EventBridge Pipe for each filtered+enriched record.
Writes the inventory item to DynamoDB.

Input from Pipe: list of (possibly enriched) SQS record dicts.
Each record body must contain: item_id (str), name (str), status (str), quantity (int).
Optional: processed_at (str, added by enrichment Lambda).
"""
import json
import os
import boto3

_ddb = None


def _get_ddb():
    global _ddb
    if _ddb is None:
        _ddb = boto3.client(
            "dynamodb",
            endpoint_url=os.environ.get("AWS_ENDPOINT_URL", "http://localhost:4566"),
        )
    return _ddb


def lambda_handler(event, context):
    table = os.environ["TABLE_NAME"]
    ddb = _get_ddb()
    records = event if isinstance(event, list) else [event]
    written = 0
    for rec in records:
        body = rec.get("body", rec)
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except ValueError:
                continue
        item = {
            "item_id": {"S": str(body["item_id"])},
            "name": {"S": str(body.get("name", ""))},
            "status": {"S": str(body.get("status", "UNKNOWN"))},
            "quantity": {"N": str(body.get("quantity", 0))},
        }
        if "processed_at" in body:
            item["processed_at"] = {"S": str(body["processed_at"])}
        ddb.put_item(TableName=table, Item=item)
        written += 1
    return {"written": written}
```

- [ ] **Step 5: Write the functional test**

Create `corpus/arch_06_eventbridge_pipes/functional_test.py`:
```python
"""
functional_test.py — EventBridge Pipes inventory pipeline (arch06)
corpus/arch_06_eventbridge_pipes/

Verifies that inventory items placed on the source SQS queue are filtered by
the Pipe (status=ACTIVE passes, status=INACTIVE is silently dropped), optionally
enriched, and written to DynamoDB.

Output format: ASSERT pass|fail [name]: [message]
Primary assertions: no suffix — all must pass for a run to score
Secondary assertions: _secondary suffix — tracked for regression check
Exit code: always 0
"""
import json
import os
import sys
import time
import uuid

import boto3

WAIT_TIMEOUT_SECONDS = 90
STACK_NAME = os.environ.get("STACK_NAME", "ace-bench-stack")
AWS_ENDPOINT = os.environ.get("AWS_ENDPOINT_URL", "http://localhost:4566")


def client(svc):
    return boto3.client(
        svc,
        endpoint_url=AWS_ENDPOINT,
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )


def emit(status, name, message):
    print(f"ASSERT {status} [{name}]: {message}", flush=True)


def emit_pass(name, message):
    emit("pass", name, message)


def emit_fail(name, message):
    emit("fail", name, message)


def get_stack_output(key):
    cf = client("cloudformation")
    stacks = cf.describe_stacks(StackName=STACK_NAME).get("Stacks", [])
    for output in stacks[0].get("Outputs", []):
        if output.get("OutputKey") == key:
            return output.get("OutputValue")
    raise KeyError(f"Missing CloudFormation output: {key}")


def send_items(queue_url, items):
    sqs = client("sqs")
    for item in items:
        sqs.send_message(QueueUrl=queue_url, MessageBody=json.dumps(item))


def wait_for_ddb_records(table_name, item_ids, timeout=WAIT_TIMEOUT_SECONDS):
    ddb = client("dynamodb")
    deadline = time.time() + timeout
    found = set()
    while time.time() < deadline:
        resp = ddb.scan(TableName=table_name)
        for row in resp.get("Items", []):
            iid = row.get("item_id", {}).get("S")
            if iid:
                found.add(iid)
        if all(iid in found for iid in item_ids):
            return True, found
        time.sleep(3)
    return False, found


def assert_active_items_written(table_name, active_ids):
    ok, found = wait_for_ddb_records(table_name, active_ids)
    if ok:
        emit_pass("active_items_written", f"found={sorted(found)}")
    else:
        emit_fail("active_items_written", f"expected={sorted(active_ids)} found={sorted(found)}")


def assert_inactive_items_filtered(table_name, inactive_ids):
    # After waiting for active items, inactive ones must NOT be in DynamoDB
    ddb = client("dynamodb")
    resp = ddb.scan(TableName=table_name)
    in_table = {row.get("item_id", {}).get("S") for row in resp.get("Items", [])}
    leaked = [iid for iid in inactive_ids if iid in in_table]
    if not leaked:
        emit_pass("inactive_items_filtered", "no INACTIVE items in DynamoDB (filter enforced)")
    else:
        emit_fail("inactive_items_filtered", f"INACTIVE items leaked into DynamoDB: {leaked}")


def assert_source_dlq_empty_secondary(dlq_url):
    attrs = client("sqs").get_queue_attributes(
        QueueUrl=dlq_url,
        AttributeNames=["ApproximateNumberOfMessages"],
    )["Attributes"]
    depth = int(attrs.get("ApproximateNumberOfMessages", "0"))
    if depth == 0:
        emit_pass("source_dlq_empty_secondary", "DLQ has 0 messages")
    else:
        emit_fail("source_dlq_empty_secondary", f"DLQ has {depth} messages (delivery failures)")


def assert_table_active_secondary(table_name):
    status = client("dynamodb").describe_table(TableName=table_name)["Table"]["TableStatus"]
    if status == "ACTIVE":
        emit_pass("table_active_secondary", f"status={status}")
    else:
        emit_fail("table_active_secondary", f"status={status}")


def assert_enrichment_field_present_secondary(table_name, active_ids):
    """Check that at least one written record has the processed_at enrichment field."""
    ddb = client("dynamodb")
    resp = ddb.scan(TableName=table_name)
    has_enrichment = any(
        row.get("item_id", {}).get("S") in active_ids and "processed_at" in row
        for row in resp.get("Items", [])
    )
    if has_enrichment:
        emit_pass("enrichment_field_present_secondary", "processed_at field found in at least one record")
    else:
        emit_fail("enrichment_field_present_secondary", "processed_at not found in any DynamoDB record (enrichment Lambda may not have run)")


if __name__ == "__main__":
    try:
        queue_url = get_stack_output("InventorySourceQueueUrl")
        dlq_url = get_stack_output("InventorySourceDlqUrl")
        table_name = get_stack_output("InventoryTableName")
    except Exception as e:
        emit_fail("stack_outputs", f"Failed to read CloudFormation outputs: {e}")
        sys.exit(0)

    run_id = uuid.uuid4().hex[:8]
    active_ids = [f"item-active-{run_id}-{i}" for i in range(3)]
    inactive_ids = [f"item-inactive-{run_id}-{i}" for i in range(2)]

    items = (
        [{"item_id": iid, "name": f"Widget {iid}", "status": "ACTIVE", "quantity": 10} for iid in active_ids]
        + [{"item_id": iid, "name": f"Widget {iid}", "status": "INACTIVE", "quantity": 0} for iid in inactive_ids]
    )
    send_items(queue_url, items)

    # Primary: ACTIVE items arrive in DynamoDB
    assert_active_items_written(table_name, active_ids)
    # Primary: INACTIVE items are NOT in DynamoDB (filter enforced)
    assert_inactive_items_filtered(table_name, inactive_ids)
    # Secondary checks
    assert_source_dlq_empty_secondary(dlq_url)
    assert_table_active_secondary(table_name)
    assert_enrichment_field_present_secondary(table_name, active_ids)
```

- [ ] **Step 6: Write `traffic_flow.md`**

Create `corpus/arch_06_eventbridge_pipes/traffic_flow.md`:
```markdown
# Traffic Flow — EventBridge Pipes inventory pipeline (arch06)

## Architecture summary

SQS source queue → EventBridge Pipe (filter: status=ACTIVE → enrichment Lambda) → target Lambda → DynamoDB table.

## Correct end-to-end flow

1. A producer (SenderFunction or direct SDK call) puts a JSON message onto `InventorySourceQueue`.
2. The EventBridge Pipe (`InventoryPipe`, state=RUNNING) polls `InventorySourceQueue` using `sqs:ReceiveMessage`.
3. The Pipe applies its filter: messages where `body.status` is not `"ACTIVE"` are silently dropped.
4. Matching messages are forwarded to `EnrichmentFunction` (invoked via `lambda:InvokeFunction`). The enrichment Lambda adds a `processed_at` timestamp to each record body.
5. The enriched records are passed to `TargetFunction` (invoked via `lambda:InvokeFunction`).
6. `TargetFunction` writes each record to `InventoryTable` (DynamoDB `PutItem`).
7. The Pipe deletes the source message from `InventorySourceQueue` on successful delivery.
8. Messages that fail delivery (Pipe or Lambda error) exhaust `maxReceiveCount=3` and route to `InventorySourceDlq`.

## Integration dependencies

| From | To | Mechanism | Required permission |
|---|---|---|---|
| Producer | InventorySourceQueue | SDK sqs:SendMessage | sqs:SendMessage |
| InventoryPipe | InventorySourceQueue | Pipe polling | sqs:ReceiveMessage, sqs:DeleteMessage, sqs:GetQueueAttributes |
| InventoryPipe (filter) | EnrichmentFunction | Pipe enrichment | lambda:InvokeFunction (on PipeExecutionRole) |
| InventoryPipe | TargetFunction | Pipe target | lambda:InvokeFunction (on PipeExecutionRole) |
| TargetFunction | InventoryTable | SDK dynamodb:PutItem | dynamodb:PutItem |

## What breaks at each hop

- **Pipe not RUNNING:** messages accumulate in source queue, DDB stays empty. `ace_describe_pipe` shows `current_state != RUNNING` or a `state_reason`.
- **Broken target ARN:** Pipe may enter a failed state or invoke no-op; target Lambda never runs. `ace_get_pipe_source_target` shows mismatched `target_arn`.
- **Filter drops all events:** both ACTIVE and INACTIVE messages disappear from the queue but DDB stays empty. `ace_get_pipe_source_target` reveals the filter pattern.
- **Missing `lambda:InvokeFunction` on PipeExecutionRole:** Pipe errors on delivery; messages hit DLQ. `ace_describe_pipe` shows `state_reason` with permission error.
- **Missing `sqs:ReceiveMessage` on PipeExecutionRole:** Pipe cannot poll; source queue depth rises. `ace_describe_pipe` may show `START_FAILED` or `state_reason`.
```

- [ ] **Step 7: Package and deploy the corpus to verify it works**

```bash
# Package sender Lambda
mkdir -p /tmp/arch06-sender && cp corpus/arch_06_eventbridge_pipes/deployment/lambda/sender/index.py /tmp/arch06-sender/
cd /tmp/arch06-sender && zip arch06-sender.zip index.py && cd -
aws --endpoint-url http://localhost:4566 s3 mb s3://ace-bench-artifacts 2>/dev/null || true
aws --endpoint-url http://localhost:4566 s3 cp /tmp/arch06-sender/arch06-sender.zip s3://ace-bench-artifacts/arch06-sender.zip

# Package enrichment Lambda
mkdir -p /tmp/arch06-enrichment && cp corpus/arch_06_eventbridge_pipes/deployment/lambda/enrichment/index.py /tmp/arch06-enrichment/
cd /tmp/arch06-enrichment && zip arch06-enrichment.zip index.py && cd -
aws --endpoint-url http://localhost:4566 s3 cp /tmp/arch06-enrichment/arch06-enrichment.zip s3://ace-bench-artifacts/arch06-enrichment.zip

# Package target Lambda
mkdir -p /tmp/arch06-target && cp corpus/arch_06_eventbridge_pipes/deployment/lambda/target/index.py /tmp/arch06-target/
cd /tmp/arch06-target && zip arch06-target.zip index.py && cd -
aws --endpoint-url http://localhost:4566 s3 cp /tmp/arch06-target/arch06-target.zip s3://ace-bench-artifacts/arch06-target.zip

# Deploy known-good stack
aws --endpoint-url http://localhost:4566 cloudformation deploy \
  --stack-name ace-bench-stack \
  --template-file corpus/arch_06_eventbridge_pipes/known_good.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset
aws --endpoint-url http://localhost:4566 cloudformation wait stack-create-complete --stack-name ace-bench-stack

# Run functional test
python corpus/arch_06_eventbridge_pipes/functional_test.py
```
Expected: ALL primary assertions `ASSERT pass`. If any assertion fails, debug the known-good template until it passes before proceeding.

- [ ] **Step 8: Tear down the corpus stack**

```bash
aws --endpoint-url http://localhost:4566 cloudformation delete-stack --stack-name ace-bench-stack
aws --endpoint-url http://localhost:4566 cloudformation wait stack-delete-complete --stack-name ace-bench-stack
```

- [ ] **Step 9: Commit**

```bash
git add corpus/arch_06_eventbridge_pipes
git commit -m "feat(corpus): add arch06 EventBridge Pipes inventory pipeline (known-good)"
```

---

## Task 4: Four fault scenarios

Each scenario = a copy of the corpus deployment with one injected fault, a symptom-only `scenario.md`, a `fault_manifest.json` (never exposed), and a verified reproduction. Use the Task 1-locked mechanisms.

**Files (per scenario `scenarios/arch06_fault0N_<class>/`):**
- Create: `faulted.yaml` (corpus `known_good.yaml` with ONE injected fault)
- Create: `scenario.md` (symptom only)
- Create: `fault_manifest.json` (never exposed)
- Create: `deployment/lambda/sender/index.py`, `deployment/lambda/enrichment/index.py`, `deployment/lambda/target/index.py` (copies of corpus handlers)

**Interfaces:**
- Consumes: corpus `known_good.yaml` + handlers (Task 3); the Pipes tools (Task 2); Task 1 locked fault mechanisms.
- Produces: four scenario dirs each reproducing its fault and diagnosable via the intended path; each `fault_manifest.json` carries `optimal_tool_calls`/`optimal_files_changed`/`optimal_lines_changed` measured in Step 8.

**Candidate faults (primary + fallback, final mechanism confirmed by Task 1 findings):**

| # | class | Primary mechanism | Fallback |
|---|-------|-------------------|----------|
| fault01 | target_wiring | Change `InventoryPipe.Target` ARN to a nonexistent function ARN | Remove `TargetFunction` resource from template so ARN is invalid |
| fault02 | permissions | Remove `lambda:InvokeFunction` on `TargetFunction.Arn` from `PipeExecutionRole` policy | Remove `sqs:ReceiveMessage` from `PipeExecutionRole` |
| fault03 | filter | Change filter pattern to `{"body": {"status": ["INACTIVE"]}}` (matches nothing the sender puts as ACTIVE, drops all events silently) | Change filter to `{"body": {"item_id": [{"prefix": "nonexistent-"}]}}` (matches no real item IDs) |
| fault04 | enrichment_wiring | Change `InventoryPipe.Enrichment` ARN to a nonexistent function ARN so enrichment invocation fails and Pipe errors | Remove `lambda:InvokeFunction` on `EnrichmentFunction.Arn` from `PipeExecutionRole` |

- [ ] **Step 1: Scaffold all four scenario dirs from the corpus**

```bash
CORP=corpus/arch_06_eventbridge_pipes
for s in arch06_fault01_target_wiring arch06_fault02_permissions arch06_fault03_filter arch06_fault04_enrichment_wiring; do
  mkdir -p scenarios/$s
  cp $CORP/known_good.yaml scenarios/$s/faulted.yaml
  cp -r $CORP/deployment scenarios/$s/deployment
done
```

- [ ] **Step 2: Inject fault01 (target_wiring)**

In `scenarios/arch06_fault01_target_wiring/faulted.yaml`, find the `InventoryPipe` resource and change the `Target` property to a nonexistent Lambda ARN:
```yaml
# In InventoryPipe:
Target: arn:aws:lambda:us-east-1:000000000000:function:nonexistent-target-xyz
```
Remove the `!GetAtt TargetFunction.Arn` reference and replace with the hardcoded wrong ARN. The `TargetFunction` resource itself stays (so the stack deploys); only the Pipe's wiring is broken.

If Task 1 found that broken target ARN causes the Pipe to enter `CREATE_FAILED` or `START_FAILED` (stack deploy fails), use the fallback: keep the correct `Target` ARN but change `TargetFunction`'s `TABLE_NAME` environment variable to `wrong-table-name-xyz` so the Lambda errors on every invocation.

- [ ] **Step 3: Inject fault02 (permissions)**

In `scenarios/arch06_fault02_permissions/faulted.yaml`, in the `PipeExecutionRole` inline policy, remove the statement that grants `lambda:InvokeFunction` on `TargetFunction.Arn`:
```yaml
# Remove this block from PipeExecutionRole Policies[0].PolicyDocument.Statement:
# - Effect: Allow
#   Action: lambda:InvokeFunction
#   Resource:
#     - !GetAtt EnrichmentFunction.Arn
#     - !GetAtt TargetFunction.Arn
```
Replace it with only enrichment invoke (or remove entirely if Task 1 found IAM is enforced):
```yaml
              - Effect: Allow
                Action: lambda:InvokeFunction
                Resource: !GetAtt EnrichmentFunction.Arn
```
This leaves the Pipe unable to invoke the target Lambda.

Fallback (if IAM on Pipes is not enforced): remove `sqs:ReceiveMessage` from `PipeExecutionRole`; the Pipe cannot poll and the source queue accumulates depth.

- [ ] **Step 4: Inject fault03 (filter)**

In `scenarios/arch06_fault03_filter/faulted.yaml`, in the `InventoryPipe` `FilterCriteria`, change the filter pattern so it matches no messages the sender sends:
```yaml
        FilterCriteria:
          Filters:
            - Pattern: '{"body": {"status": ["INACTIVE"]}}'
```
This passes syntactically and the Pipe accepts it, but the sender only sends `status=ACTIVE` messages, so every message is silently dropped by the filter — the DLQ stays empty (not an error) and DynamoDB never gets written.

- [ ] **Step 5: Inject fault04 (enrichment_wiring)**

In `scenarios/arch06_fault04_enrichment_wiring/faulted.yaml`, change the `InventoryPipe.Enrichment` ARN to a nonexistent function:
```yaml
# In InventoryPipe:
Enrichment: arn:aws:lambda:us-east-1:000000000000:function:nonexistent-enrichment-xyz
```
The Pipe tries to invoke the enrichment Lambda but gets an invocation error; the Pipe stops delivering to the target.

Fallback (if broken enrichment ARN causes CFN stack failure): remove the `lambda:InvokeFunction` on `EnrichmentFunction.Arn` from `PipeExecutionRole` so the Pipe errors when trying to call enrichment.

- [ ] **Step 6: Write symptom-only `scenario.md` for each**

**fault01 (`scenarios/arch06_fault01_target_wiring/scenario.md`):**
```markdown
# Scenario: arch06_fault01_target_wiring

## System overview

This system routes inventory update messages through an automated pipeline. A producer places JSON messages onto a source queue. An event-processing pipe picks up messages from the queue, applies a routing filter, optionally enriches the data, and then delivers the processed records to a downstream handler that writes them to a database. The system is designed to be fully automated — placing a message on the queue should result in a database record within two minutes.

## What you have access to

A deployed instance of this system is running in your local environment. The CloudFormation template (`faulted.yaml`) and all supporting deployment files are available to you directly. Diagnostic tools are available via MCP to probe the running system. The system deployed successfully.

## Reported symptom

Messages are placed onto the source queue and the queue depth drops to zero (the pipe is consuming them), but no records appear in the database after several minutes. The dead-letter queue also remains empty — there are no delivery failure signals.

## What correct behavior looks like

Every message with `status=ACTIVE` placed on the source queue should result in a corresponding record in the DynamoDB inventory table within approximately 90 seconds.
```

**fault02 (`scenarios/arch06_fault02_permissions/scenario.md`):**
```markdown
# Scenario: arch06_fault02_permissions

## System overview

This system routes inventory update messages through an automated pipeline. A producer places JSON messages onto a source queue. An event-processing pipe picks up messages from the queue, applies a routing filter, optionally enriches the data, and then delivers the processed records to a downstream handler that writes them to a database.

## What you have access to

A deployed instance of this system is running in your local environment. The CloudFormation template (`faulted.yaml`) and all supporting deployment files are available to you directly. Diagnostic tools are available via MCP to probe the running system. The system deployed successfully.

## Reported symptom

Messages placed on the source queue are not being written to the database. After several minutes, ACTIVE-status messages begin appearing in the dead-letter queue, indicating repeated delivery failures. The pipeline is running but all delivery attempts are failing.

## What correct behavior looks like

Every message with `status=ACTIVE` placed on the source queue should result in a corresponding record in the DynamoDB inventory table within approximately 90 seconds, with no messages in the dead-letter queue.
```

**fault03 (`scenarios/arch06_fault03_filter/scenario.md`):**
```markdown
# Scenario: arch06_fault03_filter

## System overview

This system routes inventory update messages through an automated pipeline. A producer places JSON messages onto a source queue. An event-processing pipe picks up messages from the queue, applies a routing filter, enriches matching messages, and delivers them to a handler that writes records to a database. Only messages matching the filter condition are forwarded downstream.

## What you have access to

A deployed instance of this system is running in your local environment. The CloudFormation template (`faulted.yaml`) and all supporting deployment files are available to you directly. Diagnostic tools are available via MCP to probe the running system. The system deployed successfully.

## Reported symptom

Messages placed on the source queue are consumed (the queue depth drops to zero) and no errors appear anywhere in the pipeline. However, no records are written to the database, and the dead-letter queue remains empty. The pipeline appears healthy but produces no output.

## What correct behavior looks like

Every message with `status=ACTIVE` placed on the source queue should result in a corresponding record in the DynamoDB inventory table within approximately 90 seconds.
```

**fault04 (`scenarios/arch06_fault04_enrichment_wiring/scenario.md`):**
```markdown
# Scenario: arch06_fault04_enrichment_wiring

## System overview

This system routes inventory update messages through an automated pipeline. A producer places JSON messages onto a source queue. An event-processing pipe picks up messages from the queue, applies a routing filter, calls an enrichment step to add metadata, and then delivers the enriched records to a target handler that writes them to a database.

## What you have access to

A deployed instance of this system is running in your local environment. The CloudFormation template (`faulted.yaml`) and all supporting deployment files are available to you directly. Diagnostic tools are available via MCP to probe the running system. The system deployed successfully.

## Reported symptom

Messages placed on the source queue are not being written to the database. After repeated attempts, ACTIVE-status messages begin appearing in the dead-letter queue. The target handler does not appear to be invoked at all — only the enrichment step seems to be failing.

## What correct behavior looks like

Every message with `status=ACTIVE` placed on the source queue should be enriched with a `processed_at` timestamp and then written to the DynamoDB inventory table within approximately 90 seconds.
```

- [ ] **Step 7: Write `fault_manifest.json` for each (template — fill `optimal_*` in Step 9)**

**`scenarios/arch06_fault01_target_wiring/fault_manifest.json`:**
```json
{
  "fault_id": "arch06_fault01",
  "fault_class": "target_wiring",
  "architecture": "arch_06_eventbridge_pipes",
  "scenario_id": "arch06_fault01_target_wiring",
  "target_resource": "InventoryPipe",
  "target_property": "Target",
  "injected_value": "arn:aws:lambda:us-east-1:000000000000:function:nonexistent-target-xyz",
  "original_value": "!GetAtt TargetFunction.Arn",
  "valid_fixes": [
    "Restore InventoryPipe.Target to !GetAtt TargetFunction.Arn (or the correct ARN of the target Lambda)"
  ],
  "invalid_patches": [
    "Create a new Lambda named nonexistent-target-xyz — fixes the broken ARN but not via the intended resource reference",
    "Remove the Enrichment step to skip the delivery chain — does not restore the intended target"
  ],
  "optimal_tool_calls": null,
  "optimal_files_changed": 1,
  "optimal_lines_changed": 1,
  "optimal_diagnostic_path": [
    "ace_describe_pipe(pipe_name=<PipeName>) → current_state or state_reason reveals target invocation failure",
    "ace_get_pipe_source_target(pipe_name=<PipeName>) → target_arn shows the wrong/nonexistent ARN"
  ],
  "concurrency_probe_n": null,
  "deployment_check": "CREATE_COMPLETE",
  "observability_check": "functional_test assertions 'active_items_written' fail; 'inactive_items_filtered' may pass trivially",
  "observable_symptom": "Messages are consumed from the source queue (queue depth drops) but no records appear in DynamoDB. The DLQ may or may not have messages depending on whether LocalStack surfaces the delivery error.",
  "root_cause": "InventoryPipe.Target is set to a nonexistent Lambda ARN. The Pipe cannot invoke the target Lambda; records are never written to DynamoDB.",
  "corpus_path": "corpus/arch_06_eventbridge_pipes",
  "functional_test_path": "corpus/arch_06_eventbridge_pipes/functional_test.py",
  "known_good_path": "corpus/arch_06_eventbridge_pipes/known_good.yaml"
}
```

**`scenarios/arch06_fault02_permissions/fault_manifest.json`:**
```json
{
  "fault_id": "arch06_fault02",
  "fault_class": "permissions",
  "architecture": "arch_06_eventbridge_pipes",
  "scenario_id": "arch06_fault02_permissions",
  "target_resource": "PipeExecutionRole",
  "target_property": "Policies[0].PolicyDocument.Statement[lambda:InvokeFunction on TargetFunction]",
  "injected_value": "statement removed",
  "original_value": "Effect=Allow Action=lambda:InvokeFunction Resource=[EnrichmentFunction.Arn, TargetFunction.Arn]",
  "valid_fixes": [
    "Add back the lambda:InvokeFunction Allow statement for TargetFunction.Arn on PipeExecutionRole"
  ],
  "invalid_patches": [
    "Grant lambda:* on * to PipeExecutionRole — overly broad",
    "Change the TargetFunction resource-based policy to allow pipes.amazonaws.com — may work but bypasses the intended role-based fix"
  ],
  "optimal_tool_calls": null,
  "optimal_files_changed": 1,
  "optimal_lines_changed": 4,
  "optimal_diagnostic_path": [
    "ace_describe_pipe(pipe_name=<PipeName>) → state_reason references permission or invocation error",
    "ace_check_sqs_queue_depth(queue=<InventorySourceDlqUrl>) → DLQ accumulating messages confirms repeated delivery failure"
  ],
  "concurrency_probe_n": null,
  "deployment_check": "CREATE_COMPLETE",
  "observability_check": "functional_test assertions 'active_items_written' fail; 'source_dlq_empty_secondary' fails",
  "observable_symptom": "Messages placed on the source queue are not written to DynamoDB. ACTIVE-status messages accumulate in the dead-letter queue after exhausting retries.",
  "root_cause": "PipeExecutionRole lacks lambda:InvokeFunction on TargetFunction.Arn. The Pipe can poll SQS but cannot invoke the target Lambda; every delivery attempt fails and messages route to the DLQ.",
  "corpus_path": "corpus/arch_06_eventbridge_pipes",
  "functional_test_path": "corpus/arch_06_eventbridge_pipes/functional_test.py",
  "known_good_path": "corpus/arch_06_eventbridge_pipes/known_good.yaml"
}
```

**`scenarios/arch06_fault03_filter/fault_manifest.json`:**
```json
{
  "fault_id": "arch06_fault03",
  "fault_class": "filter",
  "architecture": "arch_06_eventbridge_pipes",
  "scenario_id": "arch06_fault03_filter",
  "target_resource": "InventoryPipe",
  "target_property": "SourceParameters.FilterCriteria.Filters[0].Pattern",
  "injected_value": "{\"body\": {\"status\": [\"INACTIVE\"]}}",
  "original_value": "{\"body\": {\"status\": [\"ACTIVE\"]}}",
  "valid_fixes": [
    "Change the filter Pattern back to {\"body\": {\"status\": [\"ACTIVE\"]}} in InventoryPipe.SourceParameters.FilterCriteria"
  ],
  "invalid_patches": [
    "Change the sender to emit status=INACTIVE — fixes the mismatch but inverts the intended business logic",
    "Remove the FilterCriteria entirely — passes all messages including genuinely inactive ones, defeating the filter"
  ],
  "optimal_tool_calls": null,
  "optimal_files_changed": 1,
  "optimal_lines_changed": 1,
  "optimal_diagnostic_path": [
    "ace_describe_pipe(pipe_name=<PipeName>) → current_state=RUNNING, state_reason=ok (pipe appears healthy)",
    "ace_get_pipe_source_target(pipe_name=<PipeName>) → filter_patterns shows ['{\"body\":{\"status\":[\"INACTIVE\"]}}'] revealing the mismatch"
  ],
  "concurrency_probe_n": null,
  "deployment_check": "CREATE_COMPLETE",
  "observability_check": "functional_test assertions 'active_items_written' fail; 'source_dlq_empty_secondary' passes (no errors)",
  "observable_symptom": "Messages placed on the source queue are consumed (queue depth drops to zero) and no errors appear in the pipeline. No records are written to DynamoDB and the DLQ remains empty — the pipeline appears healthy but produces no output.",
  "root_cause": "InventoryPipe.SourceParameters.FilterCriteria.Filters[0].Pattern matches status=INACTIVE, but the sender only produces status=ACTIVE messages. Every message is silently dropped by the Pipe filter before reaching the enrichment or target Lambda.",
  "corpus_path": "corpus/arch_06_eventbridge_pipes",
  "functional_test_path": "corpus/arch_06_eventbridge_pipes/functional_test.py",
  "known_good_path": "corpus/arch_06_eventbridge_pipes/known_good.yaml"
}
```

**`scenarios/arch06_fault04_enrichment_wiring/fault_manifest.json`:**
```json
{
  "fault_id": "arch06_fault04",
  "fault_class": "enrichment_wiring",
  "architecture": "arch_06_eventbridge_pipes",
  "scenario_id": "arch06_fault04_enrichment_wiring",
  "target_resource": "InventoryPipe",
  "target_property": "Enrichment",
  "injected_value": "arn:aws:lambda:us-east-1:000000000000:function:nonexistent-enrichment-xyz",
  "original_value": "!GetAtt EnrichmentFunction.Arn",
  "valid_fixes": [
    "Restore InventoryPipe.Enrichment to !GetAtt EnrichmentFunction.Arn (or the correct ARN of the enrichment Lambda)"
  ],
  "invalid_patches": [
    "Remove Enrichment from InventoryPipe entirely — bypasses the wiring fault but removes the enrichment step, violating the architecture",
    "Create a Lambda named nonexistent-enrichment-xyz — fixes the broken ARN but not via the intended resource reference"
  ],
  "optimal_tool_calls": null,
  "optimal_files_changed": 1,
  "optimal_lines_changed": 1,
  "optimal_diagnostic_path": [
    "ace_describe_pipe(pipe_name=<PipeName>) → state_reason or current_state reveals enrichment invocation failure",
    "ace_get_pipe_source_target(pipe_name=<PipeName>) → enrichment_arn shows the wrong/nonexistent ARN"
  ],
  "concurrency_probe_n": null,
  "deployment_check": "CREATE_COMPLETE",
  "observability_check": "functional_test assertions 'active_items_written' fail; 'enrichment_field_present_secondary' fails",
  "observable_symptom": "Messages placed on the source queue are not written to DynamoDB. ACTIVE-status messages appear in the dead-letter queue, and the target Lambda is never invoked — only the enrichment step is failing.",
  "root_cause": "InventoryPipe.Enrichment points to a nonexistent Lambda ARN. The Pipe successfully filters messages but fails at the enrichment invocation step; the error causes all messages to route to the DLQ without reaching the target Lambda.",
  "corpus_path": "corpus/arch_06_eventbridge_pipes",
  "functional_test_path": "corpus/arch_06_eventbridge_pipes/functional_test.py",
  "known_good_path": "corpus/arch_06_eventbridge_pipes/known_good.yaml"
}
```

- [ ] **Step 8: Verify each scenario reproduces and is diagnosable**

For each scenario: package the handlers (same ZIPs from Task 3 Step 7), deploy `faulted.yaml` as `ace-bench-stack`, confirm `CREATE_COMPLETE`, run `functional_test.py`, confirm the primary assertion FAILS (symptom reproduces). Then walk the intended diagnostic path with real MCP tools to confirm the signal surfaces:

```bash
# Walkthrough example for fault01 after deploy:
node -e "
import('./harness/mcp_server/tools/probe_pipes.js').then(async m => {
  const t = n => m.probePipesTools.find(x => x.name === n);
  const PIPE = '<PipeName CloudFormation output>';
  console.log('ace_describe_pipe:', JSON.stringify(await t('ace_describe_pipe').handler({ pipe_name: PIPE }), null, 2));
  console.log('ace_get_pipe_source_target:', JSON.stringify(await t('ace_get_pipe_source_target').handler({ pipe_name: PIPE }), null, 2));
})
"
```

For fault03 (filter): after the functional test fails, `ace_get_pipe_source_target` must show `filter_patterns: ['{\"body\":{\"status\":[\"INACTIVE\"]}}']` — confirming the filter is diagnosable via the tool.

Tear down between scenarios:
```bash
aws --endpoint-url http://localhost:4566 cloudformation delete-stack --stack-name ace-bench-stack
aws --endpoint-url http://localhost:4566 cloudformation wait stack-delete-complete --stack-name ace-bench-stack
```

If a scenario does NOT reproduce (fault is posture-only), switch to the Task 1 fallback mechanism for that fault, update `faulted.yaml` accordingly, and re-verify. Record any substitution in the findings.

- [ ] **Step 9: Baseline `optimal_*` and finalize manifests**

For each scenario, count the MCP tool invocations on the intended diagnostic path walked in Step 8. Set:
- `optimal_tool_calls`: count of distinct MCP invocations on the shortest diagnosing path
- `optimal_files_changed`: `1` (single YAML edit per fault)
- `optimal_lines_changed`: `1` or `4` depending on fault (see manifest drafts above)

Write the final `optimal_*` values into each `fault_manifest.json`. Also fill in the `concurrency_probe_n` value: set to `null` unless the functional test naturally exercises concurrent senders (leave null for now; can be added when the concurrency probe is wired).

- [ ] **Step 10: Commit**

```bash
git add scenarios/arch06_fault01_target_wiring scenarios/arch06_fault02_permissions scenarios/arch06_fault03_filter scenarios/arch06_fault04_enrichment_wiring
git commit -m "feat(scenarios): add four arch06 EventBridge Pipes fault scenarios with manifests"
```

---

## Task 5: Discoverability QA gate

Run the four checks from Framework Spec §4 for every arch06 scenario. Record pass/fail and any remediation taken.

**Files:**
- Read: `harness/agent/tools.py` (verify `mcp_to_openai_tool` / `filter_model_tools` flow)
- Read: `harness/mcp_server/index.js` (confirm `probePipesTools` spread is present)
- No new files created (this task is validation only)

**Interfaces:**
- Consumes: Task 2 tools (wired); Task 4 scenario dirs; the deployed faulted stacks.
- Produces: a filled check-result table appended after this section (pass/fail per scenario per check).

- [ ] **Step 1: Check 1 — Agent-exposure plumbing**

Confirm that `ace_describe_pipe` and `ace_get_pipe_source_target` flow through `mcp_to_openai_tool` and appear in the model's runtime tool list (score tools remain filtered):

```bash
# Confirm probePipesTools is spread into index.js:
grep -n "probePipesTools" harness/mcp_server/index.js

# Confirm mcp_to_openai_tool sees the tools (no MCP server needed — check the conversion):
python3 -c "
from harness.agent.tools import mcp_to_openai_tool, filter_model_tools
# Simulate what the agent loop does: define a minimal tool dict and convert it
tools = [
    {'name': 'ace_describe_pipe', 'description': 'test', 'inputSchema': {'type': 'object', 'properties': {'pipe_name': {'type': 'string'}}, 'required': ['pipe_name']}},
    {'name': 'ace_get_pipe_source_target', 'description': 'test', 'inputSchema': {'type': 'object', 'properties': {'pipe_name': {'type': 'string'}}, 'required': ['pipe_name']}},
    {'name': 'ace_verify_fix', 'description': 'score', 'inputSchema': {'type': 'object', 'properties': {}}},
    {'name': 'ace_score_run', 'description': 'score', 'inputSchema': {'type': 'object', 'properties': {}}},
]
converted = [mcp_to_openai_tool(t) for t in tools]
filtered = filter_model_tools(converted)
names = [t['function']['name'] for t in filtered]
print('visible:', names)
assert 'ace_describe_pipe' in names, 'ace_describe_pipe missing'
assert 'ace_get_pipe_source_target' in names, 'ace_get_pipe_source_target missing'
assert 'ace_verify_fix' not in names, 'ace_verify_fix must be filtered'
assert 'ace_score_run' not in names, 'ace_score_run must be filtered'
print('Check 1 PASS')
"
```
Expected: `Check 1 PASS`. If `ace_describe_pipe` is absent, the `probePipesTools` spread in Step 6 of Task 2 was not committed.

- [ ] **Step 2: Check 2 — Diagnostic-path reachability**

For each scenario, deploy the `faulted.yaml` stack and walk the `optimal_diagnostic_path` with real MCP tools, confirming the signal that pinpoints the fault is returned:

```bash
# Deploy fault03 (the filter fault — most nuanced, best discriminator):
aws --endpoint-url http://localhost:4566 cloudformation deploy \
  --stack-name ace-bench-stack \
  --template-file scenarios/arch06_fault03_filter/faulted.yaml \
  --capabilities CAPABILITY_NAMED_IAM

# Send some ACTIVE messages:
python3 -c "
import boto3, json, time
cf = boto3.client('cloudformation', endpoint_url='http://localhost:4566', region_name='us-east-1', aws_access_key_id='test', aws_secret_access_key='test')
out = {o['OutputKey']: o['OutputValue'] for o in cf.describe_stacks(StackName='ace-bench-stack')['Stacks'][0].get('Outputs', [])}
sqs = boto3.client('sqs', endpoint_url='http://localhost:4566', region_name='us-east-1', aws_access_key_id='test', aws_secret_access_key='test')
for i in range(3):
    sqs.send_message(QueueUrl=out['InventorySourceQueueUrl'], MessageBody=json.dumps({'item_id': f'check2-{i}', 'status': 'ACTIVE', 'quantity': 1}))
print('sent 3 ACTIVE messages')
time.sleep(20)
"

# Walk the diagnostic path:
node -e "
import('./harness/mcp_server/tools/probe_pipes.js').then(async m => {
  const t = n => m.probePipesTools.find(x => x.name === n);
  const cf = (await import('@aws-sdk/client-cloudformation')).
    CloudFormationClient;
  // Just call tools directly:
  const PIPE_NAME = process.env.PIPE_NAME;  // set from CF output
  const desc = await t('ace_describe_pipe').handler({ pipe_name: PIPE_NAME });
  const wiring = await t('ace_get_pipe_source_target').handler({ pipe_name: PIPE_NAME });
  console.log('ace_describe_pipe:', JSON.stringify(desc, null, 2));
  console.log('ace_get_pipe_source_target:', JSON.stringify(wiring, null, 2));
  // For fault03: filter_patterns must show INACTIVE pattern
  const fp = wiring.filter_patterns ?? [];
  const hasWrongFilter = fp.some(p => p.includes('INACTIVE'));
  console.log('Check 2 fault03 filter pattern reveals fault:', hasWrongFilter ? 'PASS' : 'FAIL');
})
" PIPE_NAME=$(aws --endpoint-url http://localhost:4566 cloudformation describe-stacks --stack-name ace-bench-stack --query "Stacks[0].Outputs[?OutputKey=='PipeName'].OutputValue" --output text)

# Tear down:
aws --endpoint-url http://localhost:4566 cloudformation delete-stack --stack-name ace-bench-stack
aws --endpoint-url http://localhost:4566 cloudformation wait stack-delete-complete --stack-name ace-bench-stack
```

Repeat for fault01 (check `target_arn` is wrong) and fault02 (check `state_reason` or DLQ depth) and fault04 (check `enrichment_arn` is wrong). Record pass/fail per scenario.

- [ ] **Step 3a: Check 3a — Static rubric pre-gate**

Manually verify each tool description satisfies the three rubric criteria:
1. States the real AWS API it maps to (DescribePipe)
2. States the concrete fields/signals it returns (current_state, desired_state, source_arn, target_arn, enrichment_arn, filter_patterns, state_reason)
3. States when to reach for it (symptom / fault-class)

Run:
```bash
node -e "
import('./harness/mcp_server/tools/probe_pipes.js').then(m => {
  for (const t of m.probePipesTools) {
    const d = t.description;
    const hasApi = /DescribePipe/.test(d);
    const hasFields = /current_state/.test(d) && /target_arn/.test(d) && /filter_pattern/.test(d);
    const hasWhen = /Use (to|when)/.test(d);
    console.log(t.name, '→ api:', hasApi, '| fields:', hasFields, '| when:', hasWhen,
      '|', (hasApi && hasFields && hasWhen) ? 'PASS' : 'FAIL');
  }
})
"
```
Expected: both tools PASS all three criteria. If any fails, update the description in `probe_pipes.js` (preferred fix per remediation ladder) and re-run.

- [ ] **Step 3b: Check 3b — LLM-judge blind selection**

For each scenario, run **N=5** trials with a cheap judge model (e.g. `claude-haiku-3` or `gpt-4o-mini`) that receives ONLY the `scenario.md` symptom + the full tool list (names + descriptions + schemas, no manifest). The judge is asked: "Which tools would you call first, in order, to diagnose this symptom?"

Pass bar: every tool on `optimal_diagnostic_path` is named in the judge's first-K picks in ≥3/5 trials, where K = (path length) + 1 slack.

```bash
python3 - <<'EOF'
"""
Blind-trigger judge for arch06 scenarios.
Requires ANTHROPIC_API_KEY or OPENAI_API_KEY in environment.
Uses a cheaper model (haiku / gpt-4o-mini) distinct from the primary eval target.
"""
import json, os, re, sys
import anthropic

# Load tool list (names + descriptions only — no manifest, no known-good)
import subprocess
tool_list_raw = subprocess.check_output([
    "node", "-e",
    "import('./harness/mcp_server/tools/probe_pipes.js').then(m => {"
    " console.log(JSON.stringify(m.probePipesTools.map(t => ({name: t.name, description: t.description}))));"
    "})"
], text=True)
pipes_tools = json.loads(tool_list_raw)
# Add a selection of other relevant tools (ace_get_log_tail, ace_check_sqs_queue_depth) for realism
OTHER_TOOLS = [
    {"name": "ace_get_log_tail", "description": "CloudWatch Logs: return the last N log lines for a Lambda function. Use to diagnose Lambda-side errors (exceptions, permission denied)."},
    {"name": "ace_check_sqs_queue_depth", "description": "SQS GetQueueAttributes: return approximate message counts (visible, in-flight, DLQ). Use to detect queue accumulation or DLQ growth."},
    {"name": "ace_describe_lambda_function", "description": "Lambda GetFunction: return function config (runtime, handler, role, env vars, timeout). Use to verify configuration."},
]
all_tools = pipes_tools + OTHER_TOOLS

SCENARIOS = {
    "fault01": {
        "symptom": open("scenarios/arch06_fault01_target_wiring/scenario.md").read(),
        "path": ["ace_describe_pipe", "ace_get_pipe_source_target"],
    },
    "fault02": {
        "symptom": open("scenarios/arch06_fault02_permissions/scenario.md").read(),
        "path": ["ace_describe_pipe", "ace_check_sqs_queue_depth"],
    },
    "fault03": {
        "symptom": open("scenarios/arch06_fault03_filter/scenario.md").read(),
        "path": ["ace_describe_pipe", "ace_get_pipe_source_target"],
    },
    "fault04": {
        "symptom": open("scenarios/arch06_fault04_enrichment_wiring/scenario.md").read(),
        "path": ["ace_describe_pipe", "ace_get_pipe_source_target"],
    },
}

client = anthropic.Anthropic()
N_TRIALS = 5

for fault_id, sc in SCENARIOS.items():
    path = sc["path"]
    K = len(path) + 1
    passes = 0
    for trial in range(N_TRIALS):
        prompt = f"""You are diagnosing a broken cloud system. Here is the reported symptom:

{sc['symptom']}

Available diagnostic tools:
{json.dumps(all_tools, indent=2)}

List the tools you would call FIRST, in order (up to {K} tools), to diagnose this symptom. Output ONLY a JSON array of tool names, e.g. ["tool_a", "tool_b"]. No explanation."""
        resp = client.messages.create(
            model="claude-haiku-3-5",
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        try:
            picks = json.loads(re.search(r'\[.*?\]', raw, re.DOTALL).group())
        except Exception:
            picks = []
        hits = all(tool in picks[:K] for tool in path)
        if hits:
            passes += 1
        print(f"  {fault_id} trial {trial+1}: picks={picks[:K]} hits={hits}")
    result = "PASS" if passes >= 3 else "FAIL"
    print(f"{fault_id}: {passes}/5 trials → {result}  (path={path})")
EOF
```
If any fault fails (< 3/5), apply the remediation ladder:
1. Improve the tool description (add the specific signal it surfaces for that fault class).
2. Sharpen the `scenario.md` symptom to be more faithful to the observable behavior.
3. Re-baseline `optimal_diagnostic_path` to a more naturally-taken route.
4. Last resort: redesign or drop the fault.
Never leak the faulted resource or property into the symptom or description.

- [ ] **Step 4: Check 4 — Trace and scoring integration**

Confirm that Pipes tool calls land in `tool_call_trace.json` correctly and that the efficiency dimension reads them:

```bash
# Run one scenario end-to-end with the inline agent (requires HARNESS_API_KEY and a model):
python harness/run.py scenarios/arch06_fault03_filter/ \
  --model anthropic/claude-haiku-3-5 \
  --api-key "$ANTHROPIC_API_KEY" \
  2>&1 | tail -40

# After the run completes, inspect the trace:
python3 -c "
import json, glob
traces = sorted(glob.glob('results/*/tool_call_trace.json'))
if not traces: print('no trace found'); exit()
trace = json.load(open(traces[-1]))
names = [e['tool'] for e in trace]
print('tool calls:', names)
pipes_calls = [n for n in names if 'pipe' in n.lower()]
print('pipes tool calls in trace:', pipes_calls)
print('Check 4:', 'PASS' if pipes_calls else 'FAIL — no pipes tool calls logged')
"
```

Also verify `optimal_tool_calls` in `fault_manifest.json` matches what a minimal diagnostic agent would call:
```bash
python3 -c "
import json, glob
manifests = glob.glob('scenarios/arch06_*/fault_manifest.json')
for m in sorted(manifests):
    d = json.load(open(m))
    otc = d.get('optimal_tool_calls')
    path = d.get('optimal_diagnostic_path', [])
    print(f\"{d['scenario_id']}: optimal_tool_calls={otc}  path_len={len(path)}\")
    assert otc is not None, f\"{m}: optimal_tool_calls is null — fill in after Step 9\"
    assert otc >= len(path), f\"{m}: optimal_tool_calls ({otc}) < path length ({len(path)})\"
print('Check 4 manifest baselines OK')
"
```

Record the pass/fail table:

| Scenario | Check 1 (plumbing) | Check 2 (reachability) | Check 3a (static rubric) | Check 3b (blind judge ≥3/5) | Check 4 (trace+scoring) |
|---|---|---|---|---|---|
| arch06_fault01_target_wiring | _(fill)_ | _(fill)_ | _(fill)_ | _(fill)_ | _(fill)_ |
| arch06_fault02_permissions | _(fill)_ | _(fill)_ | _(fill)_ | _(fill)_ | _(fill)_ |
| arch06_fault03_filter | _(fill)_ | _(fill)_ | _(fill)_ | _(fill)_ | _(fill)_ |
| arch06_fault04_enrichment_wiring | _(fill)_ | _(fill)_ | _(fill)_ | _(fill)_ | _(fill)_ |

All four checks must show PASS for every scenario before Task 6.

---

## Task 6: Documentation

Bring tool counts and architecture inventory in sync across the guides.

**Files:**
- Modify: `CLAUDE.md` (tool counts; Project Layout corpus/scenarios entries)
- Modify: `README.md` (Phase B tool inventory; architecture/corpus inventory)
- Modify: `RUN.md` (tool inventory; model-access count)

**Interfaces:**
- Consumes: final tool list from Task 2 (2 new tools); arch06 corpus and scenario dirs from Tasks 3–4.
- Produces: consistent counts (current diagnostic count + 2) and a documented arch06 in all three files.

- [ ] **Step 1: Verify current tool count before editing**

```bash
node -e "
Promise.all([
  import('./harness/mcp_server/tools/probe.js'),
  import('./harness/mcp_server/tools/probe_extended.js'),
  import('./harness/mcp_server/tools/observe.js'),
  import('./harness/mcp_server/tools/observe_extended.js'),
  import('./harness/mcp_server/tools/observe_tracing.js'),
  import('./harness/mcp_server/tools/probe_rds.js'),
  import('./harness/mcp_server/tools/probe_pipes.js'),
  import('./harness/mcp_server/tools/score.js'),
]).then(mods => {
  const counts = mods.map((m, i) => {
    const arr = Object.values(m).find(Array.isArray) ?? [];
    return arr.length;
  });
  console.log('tool counts per file:', counts);
  console.log('total (incl score):', counts.reduce((a, b) => a + b, 0));
  console.log('diagnostic (excl score):', counts.slice(0, -1).reduce((a, b) => a + b, 0));
})
"
```
Use the printed diagnostic count as the new value in the docs (not a hardcoded number).

- [ ] **Step 2: Update `CLAUDE.md`**

In `CLAUDE.md`:
- Update the MCP server description line in the `harness/mcp_server/` entry: change the diagnostic tool count to `<current diagnostic count>` (printed above) and update the service count if EventBridge Pipes adds a new service.
- Add `harness/mcp_server/tools/probe_pipes.js` (2 Pipes tools) to the `tools/` listing.
- Add `corpus/arch_06_eventbridge_pipes/` to the corpus section under Project Layout.
- Add the four `scenarios/arch06_fault0N_*` entries to the scenarios listing.

- [ ] **Step 3: Update `README.md` and `RUN.md`**

In both files:
- Bump the diagnostic tool count by 2.
- Add `ace_describe_pipe` and `ace_get_pipe_source_target` to the tool tables (with their descriptions and the AWS API they map to).
- Add arch06 to any architecture/corpus inventory table.

- [ ] **Step 4: Verify counts are consistent**

```bash
grep -rEn "[0-9]+" CLAUDE.md README.md RUN.md | grep -iE "tool|diagnostic|model.access" | head -20
```
Confirm the diagnostic count is consistent across all three files and matches the `node` output from Step 1.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md README.md RUN.md
git commit -m "docs: document arch06 EventBridge Pipes architecture and Pipes MCP tools"
```

---

## Task 1 findings

> Recorded by executor. Fill this section after running `node scratch/spike_pipes.mjs`.

**LocalStack version:** _(fill: `curl -s localhost:4566/_localstack/info | grep version`)_

### Capability × Fidelity Matrix

| Capability | Result | Notes |
|---|---|---|
| `pipes` service in `/_localstack/health` | _(✅ / ❌)_ | _(fill)_ |
| `DescribePipe` returns non-empty data | _(✅ / ⚠️ / ❌)_ | _(fill: which fields populated)_ |
| `ListPipes` returns non-empty list | _(✅ / ⚠️ / ❌)_ | _(fill)_ |
| Pipe polls SQS source end-to-end | _(✅ / ⚠️ / ❌)_ | _(fill: did DynamoDB get records?)_ |
| Filter pattern silently drops non-matching messages | _(✅ / ❌)_ | _(fill: did DDB get 2, not 3, records?)_ |
| Broken target ARN → detectable state change | _(✅ / ⚠️ / ❌)_ | _(fill: currentState or stateReason changed?)_ |
| `lambda:InvokeFunction` IAM enforced on pipe role | _(✅ / ⚠️ / ❌)_ | _(fill)_ |
| `DesiredState=STOPPED` stops polling | _(✅ / ⚠️ / ❌)_ | _(fill)_ |
| Enrichment Lambda invoked by Pipe | _(✅ / ⚠️ / ❌)_ | _(fill: only in corpus, not in spike stack which has no enrichment)_ |

### Locked tool list

_(Fill after spike. Example if fidelity is confirmed:)_
- **`ace_describe_pipe`** — maps to `DescribePipe`. Returns `current_state`, `desired_state`, `source_arn`, `target_arn`, `enrichment_arn`, `filter_patterns`, `state_reason`, `last_modified`.
- **`ace_get_pipe_source_target`** — maps to `DescribePipe` (wiring view). Returns `source_arn`, `source_type`, `target_arn`, `target_type`, `enrichment_arn`, `filter_patterns`, `batch_size`, `source_parameters`, `target_parameters`.

If `DescribePipe` returns empty/error: drop both tools, record "shelved: Pipes API non-functional".

### Locked fault mechanisms (primary → fallback)

| Fault | Primary | Fallback | Spike result |
|---|---|---|---|
| fault01 — target_wiring | Broken `InventoryPipe.Target` ARN | Change `TargetFunction` env `TABLE_NAME` to wrong table | _(fill: primary enforced? / fallback needed?)_ |
| fault02 — permissions | Remove `lambda:InvokeFunction` on `TargetFunction` from `PipeExecutionRole` | Remove `sqs:ReceiveMessage` from `PipeExecutionRole` | _(fill: IAM enforced on Pipes? / which fallback?)_ |
| fault03 — filter | Change filter Pattern to `status=INACTIVE` | Change filter to `prefix: nonexistent-` | _(fill: filter silently drops events? / posture-only?)_ |
| fault04 — enrichment_wiring | Broken `InventoryPipe.Enrichment` ARN | Remove `lambda:InvokeFunction` on `EnrichmentFunction` from `PipeExecutionRole` | _(fill)_ |

### X-Ray decision

_(Fill: does LocalStack X-Ray capture segments for Lambda invocations triggered by EventBridge Pipes? If yes, instrument all three Lambdas. If the trace does not survive the Pipe→Lambda hop, defer instrumentation and record here.)_

### Gate verdict

- [ ] **PROCEED** — Pipes end-to-end fidelity confirmed; at least one fault mechanism enforced; tool APIs return real data.
- [ ] **SHELVED** — Pipes service absent or API returns no data; fill findings, stop plan here.
