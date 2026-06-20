# Streaming & Analytics Architecture (arch05) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the streaming & analytics breadth-track corpus architecture (arch05) — a streaming ingest pipeline on Kinesis → Firehose → OpenSearch + S3 — with five new streaming MCP diagnostic tools and four behavior-manifesting fault scenarios. Priority-1 services (Kinesis, Firehose, OpenSearch) are built unconditionally; priority-2 services (Glue, Athena, MSK, EMR) are spike-gated and dropped if LocalStack emulation is empty or posture-only.

**Architecture:** `API Gateway → Lambda (Producer) → Kinesis Data Stream → Kinesis Firehose Delivery Stream → OpenSearch Domain + S3 (skipped-docs backup)`. A separate search Lambda queries OpenSearch. This is the clean arch05 corpus — arch02 is NOT modified here; the migration plan (`corpus-migration`) adopts these tools in a separate track.

**Tech Stack:** CloudFormation (LocalStack Ultimate), Python 3.11 Lambda handlers, Node.js v22+ MCP server (`@aws-sdk/client-kinesis`, `@aws-sdk/client-firehose`, `@aws-sdk/client-opensearch` — already present in `package.json` for Kinesis/Firehose; `@aws-sdk/client-opensearch` to be added if not present), pytest + `node:test`.

## Global Constraints

- LocalStack runs with `ENFORCE_IAM=1 IAM_SOFT_MODE=0`; any fixture/scenario creating Lambdas must define a real assumable role.
- AWS creds: accessKeyId=`test`, secretAccessKey=`test`, region=`us-east-1`, endpoint `http://localhost:4566`. IAM fake account `000000000000`.
- Prefer real AWS APIs; never use LocalStack-proprietary introspection (App Inspector, IAM Policy Streams).
- `fault_manifest.json` and `known_good.yaml` are NEVER exposed to the model.
- **Fault design principle (mandatory):** every fault must produce an observable behavioral symptom Pass-1 functional verification detects; `scenario.md` states only the symptom, never the cause. No posture-only faults (wrong retention period with no delivery failure, wrong tag, etc.).
- MCP tool files live in `harness/mcp_server/tools/` and are spread into `harness/mcp_server/index.js`. Each tool is `{ name, description, inputSchema, async handler(args) }` and returns a plain object (never throws).
- Corpus dir name: `corpus/arch_05_streaming_ingest_pipeline/`. Scenario dirs: `scenarios/arch05_fault0N_<class>/`.
- Node MCP tests: `node --test tests/test_mcp_server.js`. Spike + corpus run against a live LocalStack (`localstack start -d`).
- **Pre-flight for Task 2:** `cd harness/mcp_server && npm install`.
- **Priority-2 decision rule (spike-gated):** a priority-2 tool (Glue, Athena, MSK, EMR) ships only if the Task 1 spike empirically confirms real execution fidelity (non-empty API responses after real activity). If the entire priority-2 block is dropped, Task 2 Step 7 is skipped wholesale and the plan notes the drop.

---

## Task 1: De-risking spike (the gate)

Exploratory, not TDD. Validates the streaming family's fault premises and tool-data fidelity on the current LocalStack build before any corpus or tooling fan-out. **Do not start Task 2 or Task 3 until this passes.** Findings are recorded in this plan as a `## Task 1 findings` section appended at the end.

**Files:**
- Create: `scratch/spike_streaming.mjs` (gitignored; `scratch/` is already in `.gitignore`)
- Create: `scratch/spike_streaming_stack.yaml` (minimal CFN: Kinesis stream + Firehose + OpenSearch + S3 bucket + IAM roles + optional producer Lambda)

**Interfaces:**
- Consumes: nothing (standalone spike).
- Produces: a capability×fidelity matrix and locked tool list + fault mechanisms (primary + fallback each), appended to this plan file. Tasks 2–4 read these decisions.

- [ ] **Step 1: Confirm LocalStack is up with IAM enforcement and service health**

Run the Section 2 preamble verbatim:
```bash
# Start LocalStack Ultimate with IAM enforcement (required for valid scoring).
LOCALSTACK_AUTH_TOKEN=ls-... ENFORCE_IAM=1 IAM_SOFT_MODE=0 localstack start -d
until localstack status services 2>/dev/null | grep -q running; do sleep 2; done
# Confirm the family's services are emulated on this build:
curl -s localhost:4566/_localstack/health | grep -oE '"kinesis"\s*:\s*"[a-z]+"'
curl -s localhost:4566/_localstack/health | grep -oE '"firehose"\s*:\s*"[a-z]+"'
curl -s localhost:4566/_localstack/health | grep -oE '"es"\s*:\s*"[a-z]+"'
curl -s localhost:4566/_localstack/health | grep -oE '"glue"\s*:\s*"[a-z]+"'
curl -s localhost:4566/_localstack/health | grep -oE '"athena"\s*:\s*"[a-z]+"'
# Record the LocalStack version for the findings block:
curl -s localhost:4566/_localstack/info | grep -oE '"version":\s*"[^"]+"'
```
Expected: `kinesis`, `firehose`, and `es` (OpenSearch/Elasticsearch) all `"running"`. Record the version string. If any priority-1 service is missing or `"error"`, the plan stops here with a documented "shelved" finding.

- [ ] **Step 2: Write the minimal spike stack**

Create `scratch/spike_streaming_stack.yaml` with these resources (minimal — no API Gateway, just a producer Lambda for direct `PutRecord` calls):
```yaml
AWSTemplateFormatVersion: '2010-09-09'
Description: Spike — Kinesis + Firehose + OpenSearch + S3
Resources:
  IngestStream:
    Type: AWS::Kinesis::Stream
    Properties:
      Name: spike-ingest-stream
      ShardCount: 1

  SkippedDocsBucket:
    Type: AWS::S3::Bucket
    Properties:
      BucketName: spike-skipped-docs

  SearchDomain:
    Type: AWS::Elasticsearch::Domain
    Properties:
      DomainName: spike-search
      ElasticsearchVersion: '7.10'
      ElasticsearchClusterConfig:
        InstanceType: m4.large.elasticsearch
        InstanceCount: 1

  FirehoseRole:
    Type: AWS::IAM::Role
    Properties:
      RoleName: spike-firehose-role
      AssumeRolePolicyDocument:
        Version: '2012-10-17'
        Statement:
          - Effect: Allow
            Principal: {Service: firehose.amazonaws.com}
            Action: sts:AssumeRole
      Policies:
        - PolicyName: firehose-policy
          PolicyDocument:
            Version: '2012-10-17'
            Statement:
              - Effect: Allow
                Action: [kinesis:DescribeStream, kinesis:GetRecords, kinesis:GetShardIterator, kinesis:ListShards]
                Resource: !GetAtt IngestStream.Arn
              - Effect: Allow
                Action: [s3:AbortMultipartUpload, s3:GetBucketLocation, s3:GetObject, s3:ListBucket, s3:ListBucketMultipartUploads, s3:PutObject]
                Resource: [!GetAtt SkippedDocsBucket.Arn, !Sub '${SkippedDocsBucket.Arn}/*']
              - Effect: Allow
                Action: [es:ESHttpPost, es:ESHttpPut, es:DescribeElasticsearchDomain]
                Resource: !Sub '${SearchDomain.Arn}/*'

  IngestDeliveryStream:
    Type: AWS::KinesisFirehose::DeliveryStream
    Properties:
      DeliveryStreamName: spike-ingest-delivery
      DeliveryStreamType: KinesisStreamAsSource
      KinesisStreamSourceConfiguration:
        KinesisStreamARN: !GetAtt IngestStream.Arn
        RoleARN: !GetAtt FirehoseRole.Arn
      ElasticsearchDestinationConfiguration:
        DomainARN: !GetAtt SearchDomain.Arn
        IndexName: docs
        TypeName: _doc
        RoleARN: !GetAtt FirehoseRole.Arn
        S3BackupMode: FailedDocumentsOnly
        S3Configuration:
          BucketARN: !GetAtt SkippedDocsBucket.Arn
          RoleARN: !GetAtt FirehoseRole.Arn

  ProducerRole:
    Type: AWS::IAM::Role
    Properties:
      RoleName: spike-producer-role
      AssumeRolePolicyDocument:
        Version: '2012-10-17'
        Statement:
          - Effect: Allow
            Principal: {Service: lambda.amazonaws.com}
            Action: sts:AssumeRole
      ManagedPolicyArns:
        - arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
      Policies:
        - PolicyName: producer-policy
          PolicyDocument:
            Version: '2012-10-17'
            Statement:
              - Effect: Allow
                Action: kinesis:PutRecord
                Resource: !GetAtt IngestStream.Arn

Outputs:
  StreamName: {Value: !Ref IngestStream}
  DeliveryStreamName: {Value: !Ref IngestDeliveryStream}
  DomainEndpoint: {Value: !GetAtt SearchDomain.DomainEndpoint}
  DomainArn: {Value: !GetAtt SearchDomain.Arn}
```

- [ ] **Step 3: Write `scratch/spike_streaming.mjs` — provisioning check**

Write the spike script with a `provision` command that deploys the CFN stack, waits for `CREATE_COMPLETE`, then calls:
- `DescribeStreamSummary` on the Kinesis stream — record `StreamStatus`, `OpenShardCount`, `RetentionPeriodHours`.
- `DescribeDeliveryStream` on the Firehose — record `DeliveryStreamStatus`, `DeliveryStreamType`, `Source` (Kinesis config), `Destinations[0]` (ElasticsearchDestinationDescription or equivalent).
- `DescribeElasticsearchDomain` (or `DescribeDomain` for OpenSearch-flavored SDK) on the domain — record `DomainStatus.Endpoint`, `DomainStatus.Processing`, `ElasticsearchClusterConfig`.

Run:
```bash
node scratch/spike_streaming.mjs provision
```
Expected: stack reaches `CREATE_COMPLETE`; all three API responses contain non-empty data. **Record which fields are populated** (the tools in Task 2 depend on them).

- [ ] **Step 4: Probe (a) tool-data fidelity**

Extend `scratch/spike_streaming.mjs` with a `fidelity` command. Put one record into the Kinesis stream (`PutRecord`), wait 5–10 s for Firehose to deliver, then:
1. Call `GetShardIterator` + `GetRecords` on the stream — confirm records appear.
2. Call `DescribeDeliveryStream` — confirm `DeliveryStreamStatus` is `ACTIVE`, inspect `DeliveryStartTimestamp` or equivalent delivery stats if present.
3. HTTP GET to `http://<DomainEndpoint>/docs/_count` — confirm the document count is ≥ 1 (or note if 0 meaning delivery did not reach OpenSearch).
4. Call `ListShards` — confirm shard list is non-empty.

Record per-tool fidelity: ✅ non-empty real data / ⚠️ present but stale/empty / ❌ error or no data.

Run:
```bash
node scratch/spike_streaming.mjs fidelity
```

- [ ] **Step 5: Probe (b) fault enforcement — four candidate mechanisms**

Extend `scratch/spike_streaming.mjs` with an `enforce` command. Test each candidate fault mechanism **empirically** — does LocalStack enforce it, producing a real Pass-1-detectable behavioral symptom?

**Probe 1 — Wrong delivery destination (wrong stream name on Firehose source):**
Modify the Firehose to point at a non-existent Kinesis stream name. Put a record to the real stream. Confirm the record is NOT indexed in OpenSearch (doc count stays 0). Record: enforced ✅ or posture-only ❌.

**Probe 2 — Missing `kinesis:PutRecord` IAM on producer:**
Create a role WITHOUT `kinesis:PutRecord`, call `PutRecord` as that role. Record whether `AccessDeniedException` is returned (LocalStack IAM enforcement active). This is the primary IAM fault probe.

**Probe 3 — OpenSearch index mapping mismatch (strict mapping):**
PUT a strict mapping to `docs/_mapping` that rejects an integer field as a string. Then send a document with the wrong type. Confirm Firehose reports a delivery failure (S3 backup) or the doc count stays 0. Record: enforced ✅ or not enforced ❌.

**Probe 4 — Wrong stream name env var on producer Lambda:**
Set the Lambda's `STREAM_NAME` env to a non-existent stream. Invoke the Lambda, confirm `ResourceNotFoundException` in the Lambda log or response. Record: enforced ✅ (Lambda returns 500 / AccessDenied to caller).

Run:
```bash
node scratch/spike_streaming.mjs enforce
```

- [ ] **Step 6: Priority-2 spike (Glue / Athena / MSK / EMR) — health check only**

Run quick health checks for priority-2 services:
```bash
curl -s localhost:4566/_localstack/health | grep -oE '"(glue|athena|kafka|emr)"\s*:\s*"[a-z]+"'
```
If any service is `"running"`, write a minimal job/query/cluster via the SDK and confirm it executes (non-error, non-empty response). If the service is `"error"` or absent, mark it ❌ immediately. Record one line per service: **running + real-execution confirmed ✅ / running but empty/stub ⚠️ / absent or errored ❌**. Priority-2 tools are included in Task 2 only if ✅; otherwise the entire priority-2 block in Task 2 Step 7 is dropped and noted here.

- [ ] **Step 7: Record findings + lock decisions**

Append a `## Task 1 findings` section to THIS plan file (after the last heading) with:
1. LocalStack version string (from Step 1).
2. **Capability × fidelity matrix** — one row per candidate tool/service (format below).
3. **Locked tool list** — the exact set of tools to build in Task 2.
4. **Locked fault mechanisms** — one row per fault (primary + fallback), referencing enforcement probes.
5. **Priority-2 decision** — keep or drop.

Use this exact matrix format:

| Service / Tool Backing | Fidelity Probe | Result | Decision |
|---|---|---|---|
| Kinesis `DescribeStreamSummary` | StreamStatus + OpenShardCount populated | _fill_ | _fill_ |
| Kinesis `ListShards` | non-empty shard list | _fill_ | _fill_ |
| Firehose `DescribeDeliveryStream` | DeliveryStreamStatus ACTIVE, Source config populated | _fill_ | _fill_ |
| OpenSearch `DescribeElasticsearchDomain` | Endpoint + Processing populated | _fill_ | _fill_ |
| OpenSearch HTTP doc count | ≥1 doc after PutRecord+wait | _fill_ | _fill_ |
| Glue (priority-2) | job run non-empty | _fill_ | _fill_ |
| Athena (priority-2) | query execution non-empty | _fill_ | _fill_ |
| MSK (priority-2) | cluster describe non-empty | _fill_ | _fill_ |
| EMR (priority-2) | step run non-empty | _fill_ | _fill_ |

Fault mechanism lock format:

| Fault | Class | Primary mechanism | Primary enforced? | Fallback mechanism |
|---|---|---|---|---|
| fault01 | delivery | wrong Firehose delivery target (wrong stream on source) | _fill_ | wrong stream name env var on producer Lambda |
| fault02 | iam | missing `kinesis:PutRecord` on producer role | _fill_ | missing `firehose:PutRecord` on Firehose source role |
| fault03 | mapping | OpenSearch strict mapping rejects delivery | _fill_ | wrong index name env var on Firehose destination |
| fault04 | config | wrong stream name env var on producer Lambda | _fill_ | wrong Firehose delivery stream name env var |

Commit this plan-file update:
```bash
git add docs/superpowers/plans/2026-06-20-breadth-streaming-analytics.md
git commit -m "docs(plan): record arch05 streaming spike findings and locked fault mechanisms"
```

- [ ] **Step 8: Tear down the spike stack**

```bash
node scratch/spike_streaming.mjs teardown
```
(DeleteStack + wait for `DELETE_COMPLETE`.) No commit — scratch is gitignored.

---

## Task 2: Streaming MCP diagnostic tools

Adds `harness/mcp_server/tools/probe_streaming.js` with priority-1 tools (and optionally priority-2 if spike confirmed ✅), wires it into `index.js`, and adds tests in `tests/test_mcp_server.js` via TDD. Use the Task 1 locked tool list.

**Files:**
- Create: `harness/mcp_server/tools/probe_streaming.js`
- Modify: `harness/mcp_server/index.js` (import + spread `probeStreamingTools`)
- Modify: `harness/mcp_server/package.json` (add `@aws-sdk/client-opensearch` if absent; Kinesis/Firehose already present)
- Modify: `tests/test_mcp_server.js` (append streaming tool tests)

**Interfaces:**
- Consumes: Task 1 locked tool list; the `awsConfig` pattern from `probe_rds.js`; the `tool(list, name)` helper and `before()` hook in `tests/test_mcp_server.js`.
- Produces: `export const probeStreamingTools` — an array of 5 priority-1 tools (priority-2 tools added in Step 7 only if Task 1 confirmed ✅):
  - `ace_describe_kinesis_stream({ stream_name })` → `{ name, status, shard_count, retention_period_hours, stream_arn, stream_creation_timestamp }` or `{ error }`.
  - `ace_list_kinesis_shards({ stream_name })` → `{ shards: [{ shard_id, starting_hash_key, ending_hash_key, parent_shard_id }], shard_count }` or `{ error }`.
  - `ace_describe_firehose_delivery_stream({ delivery_stream_name })` → `{ name, status, delivery_stream_type, source_kinesis_stream_arn, destination_type, destination_index, destination_domain_arn, s3_backup_bucket, s3_backup_mode, has_errors, error_output_prefix }` or `{ error }`.
  - `ace_describe_opensearch_domain({ domain_name })` → `{ domain_name, domain_id, arn, endpoint, processing, elasticsearch_version, instance_type, instance_count, dedicated_master_enabled }` or `{ error }`.
  - `ace_count_opensearch_docs({ domain_endpoint, index_name })` → `{ index, count, status }` or `{ error }`.

- [ ] **Step 1: Install the OpenSearch SDK if not already present**

Check `harness/mcp_server/package.json` for `@aws-sdk/client-opensearch`. If absent, run:
```bash
cd harness/mcp_server && npm install @aws-sdk/client-opensearch && cd -
```
(`@aws-sdk/client-kinesis` and `@aws-sdk/client-firehose` are already present in `package.json` — confirmed from the repo's current state. Do not re-install them unless `npm install` fails.)

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_mcp_server.js`. First add the import near the other tool imports:
```javascript
import { probeStreamingTools } from "../harness/mcp_server/tools/probe_streaming.js";
```

Then append the test block (all five priority-1 tools):
```javascript
// ── Streaming tools (arch05) ─────────────────────────────────────────────────
test("probeStreamingTools exposes all five priority-1 streaming tools", () => {
  for (const n of [
    "ace_describe_kinesis_stream",
    "ace_list_kinesis_shards",
    "ace_describe_firehose_delivery_stream",
    "ace_describe_opensearch_domain",
    "ace_count_opensearch_docs",
  ]) {
    assert.ok(tool(probeStreamingTools, n), `missing ${n}`);
  }
});

test("ace_describe_kinesis_stream: missing stream_name returns error", async () => {
  const res = await tool(probeStreamingTools, "ace_describe_kinesis_stream").handler({});
  assert.ok(res.error, "expected error for missing stream_name");
});

test("ace_describe_kinesis_stream: unknown stream returns error", async () => {
  const res = await tool(probeStreamingTools, "ace_describe_kinesis_stream").handler({
    stream_name: "does-not-exist-spike-xyz",
  });
  assert.ok(res.error, "expected error for unknown stream");
});

test("ace_list_kinesis_shards: missing stream_name returns error", async () => {
  const res = await tool(probeStreamingTools, "ace_list_kinesis_shards").handler({});
  assert.ok(res.error, "expected error for missing stream_name");
});

test("ace_list_kinesis_shards: unknown stream returns error", async () => {
  const res = await tool(probeStreamingTools, "ace_list_kinesis_shards").handler({
    stream_name: "does-not-exist-spike-xyz",
  });
  assert.ok(res.error, "expected error for unknown stream");
});

test("ace_describe_firehose_delivery_stream: missing name returns error", async () => {
  const res = await tool(probeStreamingTools, "ace_describe_firehose_delivery_stream").handler({});
  assert.ok(res.error, "expected error for missing delivery_stream_name");
});

test("ace_describe_firehose_delivery_stream: unknown stream returns error", async () => {
  const res = await tool(probeStreamingTools, "ace_describe_firehose_delivery_stream").handler({
    delivery_stream_name: "does-not-exist-spike-xyz",
  });
  assert.ok(res.error, "expected error for unknown stream");
});

test("ace_describe_opensearch_domain: missing domain_name returns error", async () => {
  const res = await tool(probeStreamingTools, "ace_describe_opensearch_domain").handler({});
  assert.ok(res.error, "expected error for missing domain_name");
});

test("ace_describe_opensearch_domain: unknown domain returns error", async () => {
  const res = await tool(probeStreamingTools, "ace_describe_opensearch_domain").handler({
    domain_name: "does-not-exist-spike-xyz",
  });
  assert.ok(res.error, "expected error for unknown domain");
});

test("ace_count_opensearch_docs: missing domain_endpoint returns error", async () => {
  const res = await tool(probeStreamingTools, "ace_count_opensearch_docs").handler({
    index_name: "docs",
  });
  assert.ok(res.error, "expected error for missing domain_endpoint");
});

test("ace_count_opensearch_docs: unreachable endpoint returns error", async () => {
  const res = await tool(probeStreamingTools, "ace_count_opensearch_docs").handler({
    domain_endpoint: "127.0.0.1:1",
    index_name: "docs",
  });
  assert.ok(res.error, "expected error for unreachable endpoint");
});
```

- [ ] **Step 3: Run the tests to verify they fail (red)**

Run:
```bash
cd /home/shubhan/projects/ACEDebugging-benchmark && node --test tests/test_mcp_server.js 2>&1 | grep -A2 "probeStreamingTools\|ace_describe_kinesis\|ace_list_kinesis\|ace_describe_firehose\|ace_describe_opensearch\|ace_count_opensearch"
```
Expected: FAIL — `Cannot find module '.../probe_streaming.js'`.

- [ ] **Step 4: Implement `probe_streaming.js` — priority-1 tools**

Create `harness/mcp_server/tools/probe_streaming.js`:
```javascript
import {
  KinesisClient,
  DescribeStreamSummaryCommand,
  ListShardsCommand,
} from "@aws-sdk/client-kinesis";
import {
  FirehoseClient,
  DescribeDeliveryStreamCommand,
} from "@aws-sdk/client-firehose";
import {
  OpenSearchClient,
  DescribeDomainCommand,
} from "@aws-sdk/client-opensearch";

const awsConfig = {
  endpoint: process.env.LOCALSTACK_ENDPOINT ?? "http://localhost:4566",
  region: "us-east-1",
  credentials: { accessKeyId: "test", secretAccessKey: "test" },
};

const kinesisClient = new KinesisClient(awsConfig);
const firehoseClient = new FirehoseClient(awsConfig);
const opensearchClient = new OpenSearchClient(awsConfig);

export const probeStreamingTools = [
  {
    name: "ace_describe_kinesis_stream",
    description:
      "Kinesis DescribeStreamSummary: return a stream's configuration — StreamStatus (ACTIVE/CREATING/DELETING), OpenShardCount, RetentionPeriodHours, StreamARN, StreamCreationTimestamp. Use to diagnose stream misconfiguration, wrong stream name, and producer-side delivery failures where PutRecord returns ResourceNotFoundException.",
    inputSchema: {
      type: "object",
      properties: {
        stream_name: { type: "string", description: "Kinesis stream name" },
      },
      required: ["stream_name"],
    },
    async handler({ stream_name } = {}) {
      if (!stream_name) return { error: "stream_name is required" };
      try {
        const out = await kinesisClient.send(
          new DescribeStreamSummaryCommand({ StreamName: stream_name })
        );
        const s = out.StreamDescriptionSummary;
        if (!s) return { error: `stream not found: ${stream_name}` };
        return {
          name: s.StreamName ?? null,
          status: s.StreamStatus ?? null,
          shard_count: s.OpenShardCount ?? null,
          retention_period_hours: s.RetentionPeriodHours ?? null,
          stream_arn: s.StreamARN ?? null,
          stream_creation_timestamp: s.StreamCreationTimestamp
            ? s.StreamCreationTimestamp.toISOString()
            : null,
        };
      } catch (err) {
        return { error: String(err?.message ?? err) };
      }
    },
  },
  {
    name: "ace_list_kinesis_shards",
    description:
      "Kinesis ListShards: list all shards in a stream — shard_id, starting_hash_key, ending_hash_key, parent_shard_id. Use when diagnosing resharding issues, confirming shard count matches expectations, or identifying a wrong-stream fault where shard_count is 0 or the stream is missing entirely.",
    inputSchema: {
      type: "object",
      properties: {
        stream_name: { type: "string", description: "Kinesis stream name" },
      },
      required: ["stream_name"],
    },
    async handler({ stream_name } = {}) {
      if (!stream_name) return { error: "stream_name is required" };
      try {
        const shards = [];
        let nextToken;
        do {
          const out = await kinesisClient.send(
            new ListShardsCommand({
              StreamName: nextToken ? undefined : stream_name,
              NextToken: nextToken,
            })
          );
          for (const sh of out.Shards ?? []) {
            shards.push({
              shard_id: sh.ShardId ?? null,
              starting_hash_key: sh.HashKeyRange?.StartingHashKey ?? null,
              ending_hash_key: sh.HashKeyRange?.EndingHashKey ?? null,
              parent_shard_id: sh.ParentShardId ?? null,
            });
          }
          nextToken = out.NextToken;
        } while (nextToken && shards.length < 1000);
        return { shards, shard_count: shards.length };
      } catch (err) {
        return { error: String(err?.message ?? err) };
      }
    },
  },
  {
    name: "ace_describe_firehose_delivery_stream",
    description:
      "Firehose DescribeDeliveryStream: return a delivery stream's configuration and status — DeliveryStreamStatus (ACTIVE/CREATING/DELETING), DeliveryStreamType, source Kinesis stream ARN, destination type (OpenSearch/Elasticsearch), destination index name, destination domain ARN, S3 backup bucket, S3 backup mode (FailedDocumentsOnly/AllDocuments), and whether the stream currently has delivery errors. Use to diagnose records-not-reaching-OpenSearch faults: wrong source stream, wrong destination index, failed delivery errors, or missing destination config.",
    inputSchema: {
      type: "object",
      properties: {
        delivery_stream_name: {
          type: "string",
          description: "Firehose delivery stream name",
        },
      },
      required: ["delivery_stream_name"],
    },
    async handler({ delivery_stream_name } = {}) {
      if (!delivery_stream_name)
        return { error: "delivery_stream_name is required" };
      try {
        const out = await firehoseClient.send(
          new DescribeDeliveryStreamCommand({
            DeliveryStreamName: delivery_stream_name,
          })
        );
        const d = out.DeliveryStreamDescription;
        if (!d) return { error: `delivery stream not found: ${delivery_stream_name}` };
        // Source (Kinesis stream)
        const srcArn =
          d.Source?.KinesisStreamSourceDescription?.KinesisStreamARN ?? null;
        // Destination — OpenSearch or Elasticsearch
        const dest = (d.Destinations ?? [])[0];
        const esDest =
          dest?.ElasticsearchDestinationDescription ??
          dest?.AmazonopensearchserviceDestinationDescription ??
          null;
        const s3 =
          esDest?.S3DestinationDescription ??
          dest?.S3DestinationDescription ??
          null;
        return {
          name: d.DeliveryStreamName ?? null,
          status: d.DeliveryStreamStatus ?? null,
          delivery_stream_type: d.DeliveryStreamType ?? null,
          source_kinesis_stream_arn: srcArn,
          destination_type: dest
            ? Object.keys(dest).filter((k) => k !== "DestinationId")[0] ?? null
            : null,
          destination_index: esDest?.IndexName ?? null,
          destination_domain_arn: esDest?.DomainARN ?? null,
          s3_backup_bucket:
            s3?.BucketARN?.split(":::")[1] ?? null,
          s3_backup_mode: esDest?.S3BackupMode ?? null,
          has_errors: !!(d.DeliveryStreamStatus === "ACTIVE" && esDest?.ProcessingConfiguration?.Processors?.length),
          failure_description: d.FailureDescription
            ? { type: d.FailureDescription.Type, details: d.FailureDescription.Details }
            : null,
        };
      } catch (err) {
        return { error: String(err?.message ?? err) };
      }
    },
  },
  {
    name: "ace_describe_opensearch_domain",
    description:
      "OpenSearch/Elasticsearch DescribeDomain (DescribeElasticsearchDomain): return a domain's status — domain_name, domain_id, ARN, endpoint URL, processing flag (true = update in progress), elasticsearch_version, instance_type, instance_count, dedicated_master_enabled. Use to confirm the domain is reachable, identify a wrong domain name, verify the domain endpoint the Firehose destination or Lambda is configured against, and diagnose delivery-failures caused by domain unavailability.",
    inputSchema: {
      type: "object",
      properties: {
        domain_name: {
          type: "string",
          description: "OpenSearch/Elasticsearch domain name",
        },
      },
      required: ["domain_name"],
    },
    async handler({ domain_name } = {}) {
      if (!domain_name) return { error: "domain_name is required" };
      try {
        const out = await opensearchClient.send(
          new DescribeDomainCommand({ DomainName: domain_name })
        );
        const s = out.DomainStatus;
        if (!s) return { error: `domain not found: ${domain_name}` };
        return {
          domain_name: s.DomainName ?? null,
          domain_id: s.DomainId ?? null,
          arn: s.ARN ?? null,
          endpoint: s.Endpoint ?? null,
          processing: s.Processing ?? null,
          elasticsearch_version: s.ElasticsearchVersion ?? null,
          instance_type:
            s.ElasticsearchClusterConfig?.InstanceType ?? null,
          instance_count:
            s.ElasticsearchClusterConfig?.InstanceCount ?? null,
          dedicated_master_enabled:
            s.ElasticsearchClusterConfig?.DedicatedMasterEnabled ?? null,
        };
      } catch (err) {
        return { error: String(err?.message ?? err) };
      }
    },
  },
  {
    name: "ace_count_opensearch_docs",
    description:
      "HTTP GET to /<index>/_count on the OpenSearch/Elasticsearch domain endpoint — returns the document count for an index. Use to verify end-to-end delivery: after records are put to Kinesis and Firehose has had time to deliver, a count of 0 with a non-empty stream signals a delivery fault (wrong index, wrong domain, IAM block on Firehose → OpenSearch, or mapping rejection).",
    inputSchema: {
      type: "object",
      properties: {
        domain_endpoint: {
          type: "string",
          description:
            "OpenSearch domain endpoint host (e.g. 'localhost.localstack.cloud:4571' or the DomainEndpoint output). Do NOT include http://.",
        },
        index_name: {
          type: "string",
          description: "Index name to count documents in (e.g. 'docs' or 'movies')",
        },
      },
      required: ["domain_endpoint", "index_name"],
    },
    async handler({ domain_endpoint, index_name } = {}) {
      if (!domain_endpoint) return { error: "domain_endpoint is required" };
      if (!index_name) return { error: "index_name is required" };
      try {
        const url = `http://${domain_endpoint}/${index_name}/_count`;
        const resp = await fetch(url, { signal: AbortSignal.timeout(5000) });
        const body = await resp.json();
        if (!resp.ok) {
          return {
            error: `HTTP ${resp.status}: ${JSON.stringify(body)}`,
            index: index_name,
          };
        }
        return {
          index: index_name,
          count: body.count ?? null,
          status: resp.status,
        };
      } catch (err) {
        return { error: String(err?.message ?? err) };
      }
    },
  },
];
```

- [ ] **Step 5: Wire `probe_streaming.js` into `index.js`**

In `harness/mcp_server/index.js`, add the import after the `probeRdsTools` import:
```javascript
import { probeStreamingTools } from "./tools/probe_streaming.js";
```
Update the `for` loop spread to include `...probeStreamingTools`:
```javascript
for (const tool of [...probeTools, ...probeExtendedTools, ...observeTools, ...observeExtendedTools, ...observeTracingTools, ...probeRdsTools, ...probeStreamingTools, ...scoreTools]) {
```

- [ ] **Step 6: Run the tests to verify they pass (green)**

Run:
```bash
cd /home/shubhan/projects/ACEDebugging-benchmark && node --test tests/test_mcp_server.js 2>&1 | tail -30
```
Expected: all `probeStreamingTools` / `ace_describe_kinesis_stream` / `ace_list_kinesis_shards` / `ace_describe_firehose_delivery_stream` / `ace_describe_opensearch_domain` / `ace_count_opensearch_docs` tests PASS; no prior tests regress.

- [ ] **Step 7: Priority-2 tools (Glue / Athena / MSK / EMR) — conditional on Task 1 findings**

**This step is executed only if Task 1 marked ≥1 priority-2 service as ✅ real-execution confirmed.** If all are ⚠️ or ❌, skip this step, note the drop in the plan, and proceed to Step 8.

If any service was confirmed ✅ in Task 1:
- Add the relevant SDK imports to `probe_streaming.js` (e.g. `@aws-sdk/client-glue`, `@aws-sdk/client-athena`).
- Run `cd harness/mcp_server && npm install <pkg>` for each new SDK.
- Implement tool handlers following the same `{ name, description, inputSchema, async handler(args) }` pattern. Each description must name the real AWS API, the fields returned, and the symptom/fault-class that triggers it.
- Add `node:test` cases (membership, missing-arg error, unknown-resource error).
- Add the new tools to the `probeStreamingTools` export array.
- Wire them into the `for` loop spread in `index.js`.
- Re-run `node --test tests/test_mcp_server.js 2>&1 | tail -30` and confirm all pass.

Candidate priority-2 tools (only include what Task 1 confirmed ✅):
- `ace_describe_glue_job_run({ job_name, run_id })` → `{ job_name, run_id, state, started_on, completed_on, error_message, execution_time_seconds }` — Glue `GetJobRun`. Use to diagnose ETL job failures in analytics pipelines.
- `ace_get_athena_query_execution({ query_execution_id })` → `{ state, state_change_reason, database, query, output_location, data_scanned_bytes }` — Athena `GetQueryExecution`. Use to diagnose query failures or missing output.
- `ace_describe_msk_cluster({ cluster_arn })` → `{ cluster_name, state, kafka_version, number_of_broker_nodes, zookeeper_connect_string }` — MSK `DescribeCluster`. Use to diagnose consumer lag or broker unavailability.

- [ ] **Step 8: Commit**

```bash
git add harness/mcp_server/tools/probe_streaming.js harness/mcp_server/index.js harness/mcp_server/package.json harness/mcp_server/package-lock.json tests/test_mcp_server.js
git commit -m "feat(mcp): add streaming diagnostic tools (kinesis, firehose, opensearch)"
```

---

## Task 3: arch05 corpus (known-good)

Builds the working corpus architecture and proves it deploys clean and passes functional tests under IAM enforcement.

**Files:**
- Create: `corpus/arch_05_streaming_ingest_pipeline/known_good.yaml`
- Create: `corpus/arch_05_streaming_ingest_pipeline/functional_test.py`
- Create: `corpus/arch_05_streaming_ingest_pipeline/traffic_flow.md`
- Create: `corpus/arch_05_streaming_ingest_pipeline/deployment/lambda/producer/index.py`
- Create: `corpus/arch_05_streaming_ingest_pipeline/deployment/lambda/search/index.py`

**Interfaces:**
- Consumes: Task 1 findings (locked fault mechanisms inform which env vars and IAM policies are "the correct value"); arch01/arch02's functional-test conventions (`emit_pass`/`emit_fail`/`finalize` from `harness.shared.functional_test_helpers`, exit 0).
- Produces: a deployable `known_good.yaml` whose stack name is `ace-bench-stack`, exporting outputs `ProducerFunctionUrl`, `SearchFunctionUrl`, `StreamName`, `DeliveryStreamName`, `DomainName`, `DomainEndpoint`, `ProducerRoleArn`, `FirehoseRoleArn` (Task 4 faults and the functional test read these).

- [ ] **Step 1: Decide the domain (ingest + search documents)**

Domain: a minimal "events" ingest pipeline. Documents are JSON objects `{ id, title, timestamp }`. The producer Lambda accepts `POST /` with a JSON body containing `title`; it generates a UUID `id`, puts it to Kinesis. Firehose delivers batches to OpenSearch index `events`. The search Lambda accepts `GET /?q=<term>` and queries OpenSearch for matching titles.

- [ ] **Step 2: Write the producer Lambda handler**

Create `corpus/arch_05_streaming_ingest_pipeline/deployment/lambda/producer/index.py`:
```python
import json
import os
import uuid
import boto3


STREAM_NAME = os.environ["STREAM_NAME"]
ENDPOINT = os.environ.get("AWS_ENDPOINT_URL", "http://localhost:4566")

kinesis = boto3.client("kinesis", endpoint_url=ENDPOINT)


def handler(event, context):
    body = json.loads(event.get("body") or "{}")
    title = body.get("title", "untitled")
    doc_id = str(uuid.uuid4())
    record = json.dumps({"id": doc_id, "title": title, "timestamp": context.aws_request_id})
    kinesis.put_record(
        StreamName=STREAM_NAME,
        Data=record.encode("utf-8"),
        PartitionKey=doc_id,
    )
    return {
        "statusCode": 202,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"id": doc_id, "status": "accepted"}),
    }
```

- [ ] **Step 3: Write the search Lambda handler**

Create `corpus/arch_05_streaming_ingest_pipeline/deployment/lambda/search/index.py`:
```python
import json
import os
import urllib.request


DOMAIN_ENDPOINT = os.environ["OPENSEARCH_ENDPOINT"]
INDEX = os.environ.get("OPENSEARCH_INDEX", "events")


def handler(event, context):
    q = (event.get("queryStringParameters") or {}).get("q", "")
    query = json.dumps({
        "query": {"match": {"title": q}} if q else {"match_all": {}}
    }).encode("utf-8")
    url = f"http://{DOMAIN_ENDPOINT}/{INDEX}/_search"
    req = urllib.request.Request(url, data=query, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            hits_raw = json.loads(r.read())
        hits = [h["_source"] for h in hits_raw.get("hits", {}).get("hits", [])]
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"results": hits, "total": len(hits)}),
        }
    except Exception as exc:
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": str(exc)}),
        }
```

- [ ] **Step 4: Write `known_good.yaml`**

Create `corpus/arch_05_streaming_ingest_pipeline/known_good.yaml` with these resources (correct, fault-free):

- `IngestStream` (`AWS::Kinesis::Stream`) — `ShardCount: 1`.
- `SkippedDocsBucket` (`AWS::S3::Bucket`) — for Firehose failed-document backup.
- `EventsDomain` (`AWS::Elasticsearch::Domain`) — `ElasticsearchVersion: '7.10'`, `InstanceType: m4.large.elasticsearch`, `InstanceCount: 1`.
- `ProducerRole` (`AWS::IAM::Role`) — assumable by `lambda.amazonaws.com`; `AWSLambdaBasicExecutionRole` managed policy; inline policy: `kinesis:PutRecord` on `IngestStream.Arn` (CORRECT; fault02 removes this, or fault04 points it at the wrong stream).
- `SearchRole` (`AWS::IAM::Role`) — assumable by `lambda.amazonaws.com`; `AWSLambdaBasicExecutionRole`; inline policy: `es:ESHttpPost` on `EventsDomain.Arn/*`.
- `FirehoseRole` (`AWS::IAM::Role`) — assumable by `firehose.amazonaws.com`; inline policy: Kinesis read (`DescribeStream`, `GetRecords`, `GetShardIterator`, `ListShards`) on `IngestStream.Arn` + S3 write on `SkippedDocsBucket` + `es:ESHttpPost`/`es:ESHttpPut`/`es:DescribeElasticsearchDomain` on `EventsDomain.Arn/*` (CORRECT; fault01 uses wrong source stream or wrong destination).
- `IngestDeliveryStream` (`AWS::KinesisFirehose::DeliveryStream`) — `DeliveryStreamType: KinesisStreamAsSource`, `KinesisStreamSourceConfiguration`: `KinesisStreamARN: !GetAtt IngestStream.Arn` (CORRECT; fault01 may point at wrong ARN), `RoleARN: !GetAtt FirehoseRole.Arn`; `ElasticsearchDestinationConfiguration`: `DomainARN: !GetAtt EventsDomain.Arn`, `IndexName: events` (CORRECT; fault03 changes this), `TypeName: _doc`, `S3BackupMode: FailedDocumentsOnly`, `S3Configuration`: bucket + `FirehoseRole.Arn`.
- `ProducerFunction` (`AWS::Lambda::Function`) — Python 3.11, `Role: !GetAtt ProducerRole.Arn`; env: `STREAM_NAME: !Ref IngestStream` (CORRECT; fault04 sets this to wrong name), `AWS_ENDPOINT_URL: http://localhost:4566`; `FunctionUrl` (`AWS::Lambda::Url`, `AuthType: NONE`).
- `SearchFunction` (`AWS::Lambda::Function`) — Python 3.11, `Role: !GetAtt SearchRole.Arn`; env: `OPENSEARCH_ENDPOINT: !GetAtt EventsDomain.DomainEndpoint`, `OPENSEARCH_INDEX: events`; `FunctionUrl` (`AWS::Lambda::Url`, `AuthType: NONE`).
- `Outputs`: `ProducerFunctionUrl`, `SearchFunctionUrl`, `StreamName` (`!Ref IngestStream`), `DeliveryStreamName` (`!Ref IngestDeliveryStream`), `DomainName` (`!Ref EventsDomain`), `DomainEndpoint` (`!GetAtt EventsDomain.DomainEndpoint`), `ProducerRoleArn` (`!GetAtt ProducerRole.Arn`), `FirehoseRoleArn` (`!GetAtt FirehoseRole.Arn`).

- [ ] **Step 5: Deploy the known-good stack**

Run:
```bash
python -c "
import boto3, sys
cf = boto3.client('cloudformation', endpoint_url='http://localhost:4566', region_name='us-east-1',
                  aws_access_key_id='test', aws_secret_access_key='test')
body = open('corpus/arch_05_streaming_ingest_pipeline/known_good.yaml').read()
cf.create_stack(StackName='ace-bench-stack', TemplateBody=body, Capabilities=['CAPABILITY_NAMED_IAM', 'CAPABILITY_AUTO_EXPAND'])
w = cf.get_waiter('stack_create_complete')
w.wait(StackName='ace-bench-stack')
print('CREATE_COMPLETE')
"
```
Expected: `CREATE_COMPLETE`. If it fails, inspect with:
```bash
aws --endpoint-url=http://localhost:4566 cloudformation describe-stack-events --stack-name ace-bench-stack --query 'StackEvents[?ResourceStatus==`CREATE_FAILED`].[LogicalResourceId,ResourceStatusReason]' --output table
```
Fix the template and retry.

- [ ] **Step 6: Write `functional_test.py`**

Create `corpus/arch_05_streaming_ingest_pipeline/functional_test.py` with complete code (no placeholders):
```python
import json
import sys
import time
import uuid
from urllib import request, error
import boto3

from harness.shared.functional_test_helpers import emit_fail, emit_pass, finalize

ENDPOINT = "http://localhost:4566"
REGION = "us-east-1"
CREDS = {"aws_access_key_id": "test", "aws_secret_access_key": "test"}
STACK = "ace-bench-stack"
WAIT_TIMEOUT = 60  # seconds for Firehose delivery


def client(service):
    return boto3.client(service, endpoint_url=ENDPOINT, region_name=REGION, **CREDS)


def output(key):
    st = client("cloudformation").describe_stacks(StackName=STACK)["Stacks"][0]
    return next(o["OutputValue"] for o in st["Outputs"] if o["OutputKey"] == key)


def http_post(url, body_dict):
    data = json.dumps(body_dict).encode()
    req = request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except error.HTTPError as e:
        return e.code, {}
    except Exception as exc:
        return -1, {"error": str(exc)}


def http_get(url):
    try:
        with request.urlopen(url, timeout=30) as r:
            return r.status, json.loads(r.read())
    except error.HTTPError as e:
        return e.code, {}
    except Exception as exc:
        return -1, {"error": str(exc)}


def main():
    producer_url = output("ProducerFunctionUrl").rstrip("/")
    search_url = output("SearchFunctionUrl").rstrip("/")
    domain_endpoint = output("DomainEndpoint")
    unique_title = f"test-doc-{uuid.uuid4().hex[:8]}"

    # Primary assertion 1: producer accepts a document
    status, body = http_post(producer_url, {"title": unique_title})
    if status == 202 and "id" in body:
        emit_pass("producer_accepted", f"POST returned 202 with id={body['id']}")
        doc_id = body["id"]
    else:
        emit_fail("producer_accepted", f"status={status} body={body}")
        doc_id = None

    # Primary assertion 2: document appears in OpenSearch (Firehose delivery)
    if doc_id:
        deadline = time.time() + WAIT_TIMEOUT
        found = False
        last_count = None
        while time.time() < deadline:
            count_url = f"http://{domain_endpoint}/events/_count"
            cs, cb = http_get(count_url)
            last_count = cb.get("count", 0) if cs == 200 else 0
            if last_count >= 1:
                found = True
                break
            time.sleep(5)
        if found:
            emit_pass("document_indexed", f"document appeared in OpenSearch (count={last_count})")
        else:
            emit_fail("document_indexed", f"count={last_count} after {WAIT_TIMEOUT}s")

    # Primary assertion 3: search Lambda returns the ingested title
    if doc_id:
        deadline = time.time() + 30
        found_in_search = False
        while time.time() < deadline:
            ss, sb = http_get(f"{search_url}?q={unique_title}")
            if ss == 200 and any(r.get("title") == unique_title for r in sb.get("results", [])):
                found_in_search = True
                break
            time.sleep(5)
        if found_in_search:
            emit_pass("search_returns_document", "search function returned the ingested document")
        else:
            emit_fail("search_returns_document", f"not found in search after 30s")

    # Secondary: stream is ACTIVE
    stream_name = output("StreamName")
    k = client("kinesis")
    desc = k.describe_stream_summary(StreamName=stream_name)["StreamDescriptionSummary"]
    if desc["StreamStatus"] == "ACTIVE":
        emit_pass("stream_active_secondary", f"status={desc['StreamStatus']}")
    else:
        emit_fail("stream_active_secondary", f"status={desc['StreamStatus']}")

    # Secondary: Firehose delivery stream is ACTIVE
    ds_name = output("DeliveryStreamName")
    f = client("firehose")
    ds = f.describe_delivery_stream(DeliveryStreamName=ds_name)["DeliveryStreamDescription"]
    if ds["DeliveryStreamStatus"] == "ACTIVE":
        emit_pass("firehose_active_secondary", f"status={ds['DeliveryStreamStatus']}")
    else:
        emit_fail("firehose_active_secondary", f"status={ds['DeliveryStreamStatus']}")

    finalize()


if __name__ == "__main__":
    main()
    sys.exit(0)
```

- [ ] **Step 7: Run the functional test against the deployed known-good**

Run:
```bash
python corpus/arch_05_streaming_ingest_pipeline/functional_test.py
```
Expected: `ASSERT pass producer_accepted`, `ASSERT pass document_indexed`, `ASSERT pass search_returns_document`. Secondary checks (`stream_active_secondary`, `firehose_active_secondary`) should also pass. All primary assertions must pass before proceeding. If `document_indexed` fails (count stays 0), investigate Firehose delivery timing — increase `WAIT_TIMEOUT` or check `DescribeDeliveryStream` error outputs.

- [ ] **Step 8: Write `traffic_flow.md`**

Create `corpus/arch_05_streaming_ingest_pipeline/traffic_flow.md` with one short paragraph per hop:

```
# Traffic Flow — arch05 Streaming Ingest Pipeline

**Ingest path:** Client sends POST with JSON {title} to the ProducerFunction URL (Lambda Function URL, AuthType NONE). The producer Lambda reads STREAM_NAME from its environment, serializes the document as {id, title, timestamp}, and calls kinesis:PutRecord to place the record onto the IngestStream shard. Kinesis acknowledges with a SequenceNumber.

**Firehose delivery path:** IngestDeliveryStream polls IngestStream (KinesisStreamAsSource) using FirehoseRole credentials — consuming kinesis:GetRecords and kinesis:GetShardIterator. Firehose batches records and calls es:ESHttpPut to deliver them to the EventsDomain OpenSearch cluster under index "events". Failed documents are backed up to SkippedDocsBucket via S3 (FirehoseRole has s3:PutObject). Delivery typically completes within 60 seconds for small batches.

**Search path:** Client sends GET ?q=<term> to the SearchFunction URL. The search Lambda reads OPENSEARCH_ENDPOINT and OPENSEARCH_INDEX from its environment, constructs a match query, and issues an HTTP POST to /<index>/_search on the OpenSearch domain. Results are returned as a JSON array of _source documents.

**IAM boundaries:** ProducerRole holds kinesis:PutRecord on IngestStream — removing this causes AccessDeniedException on every POST. FirehoseRole holds Kinesis read permissions (required to source records) and es:ESHttpPut (required to deliver to OpenSearch) — removing either breaks the delivery chain with no client-visible error until the doc-count check shows 0.
```

- [ ] **Step 9: Tear down + commit**

```bash
python -c "
import boto3
cf = boto3.client('cloudformation', endpoint_url='http://localhost:4566', region_name='us-east-1',
                  aws_access_key_id='test', aws_secret_access_key='test')
cf.delete_stack(StackName='ace-bench-stack')
cf.get_waiter('stack_delete_complete').wait(StackName='ace-bench-stack')
print('deleted')
"
git add corpus/arch_05_streaming_ingest_pipeline
git commit -m "feat(corpus): add arch05 streaming ingest pipeline corpus (known-good)"
```

---

## Task 4: Four fault scenarios

Each scenario = a copy of the corpus deployment with one injected fault, a symptom-only `scenario.md`, a `fault_manifest.json` (never exposed), and a verified reproduction. Use the Task 1 locked mechanisms.

**Files (per scenario `scenarios/arch05_fault0N_<class>/`):**
- Create: `faulted.yaml` (corpus `known_good.yaml` with ONE injected fault)
- Create: `scenario.md` (symptom only)
- Create: `fault_manifest.json` (never exposed)
- Create: `deployment/lambda/producer/index.py` and `deployment/lambda/search/index.py` (copies of corpus handlers)

**Interfaces:**
- Consumes: corpus `known_good.yaml` + handlers (Task 3); streaming MCP tools (Task 2); Task 1 findings (locked mechanisms).
- Produces: four scenario dirs each reproducing its fault and diagnosable via the intended path; each `fault_manifest.json` carries `optimal_tool_calls`/`optimal_files_changed`/`optimal_lines_changed` measured in Step 8.

- [ ] **Step 1: Scaffold all four scenario dirs from the corpus**

```bash
CORP=corpus/arch_05_streaming_ingest_pipeline
for s in arch05_fault01_delivery arch05_fault02_iam arch05_fault03_mapping arch05_fault04_config; do
  mkdir -p scenarios/$s
  cp $CORP/known_good.yaml scenarios/$s/faulted.yaml
  cp -r $CORP/deployment scenarios/$s/deployment
done
```

- [ ] **Step 2: Inject fault01 (delivery — wrong Firehose source)**

In `scenarios/arch05_fault01_delivery/faulted.yaml`, apply the Task 1-locked mechanism:
- **Primary (wrong source stream, if confirmed enforced):** In `IngestDeliveryStream`, change `KinesisStreamSourceConfiguration.KinesisStreamARN` to a hardcoded wrong ARN (e.g. `arn:aws:kinesis:us-east-1:000000000000:stream/does-not-exist`). The Firehose can no longer source records from the real stream; records put to `IngestStream` are never delivered to OpenSearch.
- **Fallback (if primary not enforced):** Change `ProducerFunction` env `STREAM_NAME` to `wrong-stream-name`. `kinesis:PutRecord` will return `ResourceNotFoundException`; the producer Lambda fails on every request. *(Record the exact `target_resource`/`target_property`/`original_value`/`injected_value` for the manifest.)*

- [ ] **Step 3: Inject fault02 (IAM — missing producer kinesis:PutRecord)**

In `scenarios/arch05_fault02_iam/faulted.yaml`, remove the `kinesis:PutRecord` action from `ProducerRole`'s inline policy (leave the role and policy block present but with an empty or unrelated action list, e.g. only `kinesis:DescribeStream`). The producer Lambda receives `AccessDeniedException` on every `PutRecord` call.
- **Fallback (if IAM not enforced on PutRecord):** Remove `es:ESHttpPost`/`es:ESHttpPut` from `FirehoseRole` inline policy instead. Firehose can still source records but cannot deliver to OpenSearch; symptom is records-lost (count stays 0).
Record the locked mechanism from Task 1.

- [ ] **Step 4: Inject fault03 (mapping — wrong index name on Firehose destination)**

In `scenarios/arch05_fault03_mapping/faulted.yaml`, in `IngestDeliveryStream.ElasticsearchDestinationConfiguration`, change `IndexName` from `events` to `wrong-index` (an index that does not exist and has no mapping configured). The search Lambda queries `events` index (env var unchanged), which stays empty; Firehose delivers to `wrong-index` instead.
- **Primary verification:** after ingest, `ace_count_opensearch_docs` on `events` returns 0; on `wrong-index` returns >0. The mismatch is the fault.
- **Fallback (if Firehose destination IndexName change is not enforced):** Change `SearchFunction` env `OPENSEARCH_INDEX` to `wrong-index` while keeping Firehose delivering to `events`. The search Lambda queries an empty index and returns no results even after successful ingest.
Record the locked mechanism from Task 1.

- [ ] **Step 5: Inject fault04 (config — wrong stream name env var on producer)**

In `scenarios/arch05_fault04_config/faulted.yaml`, in `ProducerFunction.Properties.Environment.Variables`, change `STREAM_NAME` from `!Ref IngestStream` to the literal string `wrong-stream-name`. The producer Lambda calls `kinesis:PutRecord` against a non-existent stream and receives `ResourceNotFoundException`; every POST to the producer returns 500.
- This fault is distinct from fault02 (which is an IAM denial) and fault01 (which is a Firehose-side misconfiguration); here the Lambda itself cannot find the stream due to a wrong env var, and the Firehose delivery chain is unaffected (it still polls IngestStream, which is empty).
- **Fallback:** if `ResourceNotFoundException` is not propagated (not expected, but noted): change `STREAM_NAME` to an empty string. PutRecord will fail with a validation error.

- [ ] **Step 6: Write symptom-only `scenario.md` for each**

For each scenario, create `scenario.md` with these sections: `## System overview` (one sentence describing the ingest-pipeline architecture: API → producer Lambda → Kinesis → Firehose → OpenSearch + S3, plus a search Lambda), `## What you have access to` (the deployed `faulted.yaml`, the deployment files, and the MCP diagnostic tools; the stack deployed successfully to `CREATE_COMPLETE`), `## Reported symptom` (behavioral symptom ONLY — no cause named), `## What correct behavior looks like`. Use these symptom descriptions:

**fault01 (delivery):**
> Reported symptom: POST requests to the producer return 202 (accepted), but documents never appear in OpenSearch search results. The search function returns empty results even after waiting several minutes. The Kinesis stream appears active and records are being produced.

**fault02 (iam):**
> Reported symptom: POST requests to the producer return HTTP 500. CloudWatch logs for the producer Lambda show an error on every invocation. Search results are consistently empty.

**fault03 (mapping):**
> Reported symptom: POST requests to the producer return 202 (accepted). The producer appears healthy. However, the search Lambda consistently returns zero results for any query, even for documents that were recently ingested.

**fault04 (config):**
> Reported symptom: POST requests to the producer return HTTP 500. The stack deployed cleanly and all resources appear configured. The search function is reachable but returns no results.

**Never name the resource or property at fault in `scenario.md`.**

- [ ] **Step 7: Write `fault_manifest.json` for each**

Follow the arch01 schema exactly. Full set of fields:
```json
{
  "fault_id": "arch05_fault01",
  "fault_class": "delivery",
  "architecture": "arch05",
  "scenario_id": "arch05_fault01_delivery",
  "target_resource": "IngestDeliveryStream",
  "target_property": "KinesisStreamSourceConfiguration.KinesisStreamARN",
  "injected_value": "arn:aws:kinesis:us-east-1:000000000000:stream/does-not-exist",
  "original_value": "!GetAtt IngestStream.Arn",
  "valid_fixes": [
    "restore KinesisStreamSourceConfiguration.KinesisStreamARN to the real IngestStream ARN"
  ],
  "invalid_patches": [
    "open Firehose to all Kinesis streams via IAM *",
    "disable delivery buffering"
  ],
  "optimal_tool_calls": null,
  "optimal_files_changed": 1,
  "optimal_lines_changed": 1,
  "optimal_diagnostic_path": [
    "ace_describe_firehose_delivery_stream",
    "ace_describe_kinesis_stream",
    "ace_count_opensearch_docs"
  ],
  "deployment_check": "CREATE_COMPLETE",
  "observability_check": "functional_test primary assertion document_indexed FAILS; stream_active_secondary PASSES",
  "observable_symptom": "POST returns 202 but documents never appear in OpenSearch; stream is active but Firehose sources the wrong stream",
  "root_cause": "IngestDeliveryStream KinesisStreamSourceConfiguration.KinesisStreamARN points at a non-existent stream; Firehose has no records to deliver",
  "corpus_path": "corpus/arch_05_streaming_ingest_pipeline",
  "functional_test_path": "corpus/arch_05_streaming_ingest_pipeline/functional_test.py",
  "known_good_path": "corpus/arch_05_streaming_ingest_pipeline/known_good.yaml",
  "concurrency_probe_n": 5
}
```
Replicate for fault02 (`target_resource: ProducerRole`, `target_property: inline policy Actions`, `injected_value: kinesis:PutRecord removed`), fault03 (`target_resource: IngestDeliveryStream`, `target_property: ElasticsearchDestinationConfiguration.IndexName`, `injected_value: "wrong-index"`), fault04 (`target_resource: ProducerFunction`, `target_property: Environment.Variables.STREAM_NAME`, `injected_value: "wrong-stream-name"`).

Set `optimal_tool_calls` to `null` in all four; it is filled in Step 8 after empirical measurement.

- [ ] **Step 8: Verify each scenario reproduces + is diagnosable**

For each scenario: deploy `faulted.yaml` as `ace-bench-stack`, confirm `CREATE_COMPLETE`, run `functional_test.py`, confirm the primary assertion FAILS (the symptom reproduces). Then walk the intended diagnostic path with the actual MCP tools:
```bash
# Example: fault01 after deploy — confirm Firehose source is wrong and stream is fine:
node -e "
import('./harness/mcp_server/tools/probe_streaming.js').then(async m => {
  const t = n => m.probeStreamingTools.find(x => x.name === n);
  const ds = await t('ace_describe_firehose_delivery_stream').handler({ delivery_stream_name: '<DeliveryStreamName output>' });
  console.log('firehose source ARN:', ds.source_kinesis_stream_arn);
  const ks = await t('ace_describe_kinesis_stream').handler({ stream_name: '<StreamName output>' });
  console.log('real stream ARN:', ks.stream_arn);
  const cnt = await t('ace_count_opensearch_docs').handler({ domain_endpoint: '<DomainEndpoint output>', index_name: 'events' });
  console.log('doc count:', cnt.count);
});
"
```
Confirm the tool output reveals the fault. Tear down between scenarios. If a scenario does NOT reproduce, switch to its Task 1 fallback mechanism and re-verify. Record the count of MCP calls on the confirmed diagnostic path as `optimal_tool_calls`.

- [ ] **Step 9: Baseline `optimal_*` and finalize manifests**

For each scenario, set `optimal_tool_calls` to the count of MCP calls walked in Step 8 (the minimal sufficient path). `optimal_files_changed` = 1, `optimal_lines_changed` = 1 for each (all faults are single-property changes). Write the final values into each `fault_manifest.json`.

- [ ] **Step 10: Commit**

```bash
git add scenarios/arch05_fault01_delivery scenarios/arch05_fault02_iam scenarios/arch05_fault03_mapping scenarios/arch05_fault04_config
git commit -m "feat(scenarios): add four arch05 streaming fault scenarios with manifests"
```

---

## Task 5: Discoverability QA gate

A scenario is only a fair benchmark item if the fault is discoverable from what the model sees: the symptom-only `scenario.md`, the faulted deployment, and the tool list. This task runs the four checks from framework spec Section 4 against all four arch05 scenarios. **All four scenarios must pass all four checks before Task 6.**

**Files:**
- Read: `harness/agent/tools.py` (check `mcp_to_openai_tool`, `filter_model_tools`)
- Read: each `fault_manifest.json` for `optimal_diagnostic_path`

**Interfaces:**
- Consumes: deployed faulted scenarios (Task 4); `probeStreamingTools` in `index.js` (Task 2).
- Produces: per-scenario pass/fail record in this plan's `## Task 5 findings` section (appended after running).

### Check 1 — Agent-exposure plumbing

- [ ] **Step 1: Verify streaming tools flow through `mcp_to_openai_tool` and `filter_model_tools`**

Run:
```bash
cd /home/shubhan/projects/ACEDebugging-benchmark
python -c "
from harness.agent.tools import mcp_to_openai_tool, filter_model_tools, FILE_TOOL_DEFINITIONS
import asyncio, json
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    params = StdioServerParameters(command='node', args=['harness/mcp_server/index.js'])
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            streaming = [n for n in names if 'kinesis' in n or 'firehose' in n or 'opensearch' in n]
            score_tools_present = [n for n in names if n in ('ace_verify_fix', 'ace_score_run')]
            print('streaming tools visible:', streaming)
            print('score tools (must be absent):', score_tools_present)

asyncio.run(main())
"
```
Expected: all five streaming tools appear in the list; `ace_verify_fix` and `ace_score_run` are absent (filtered by `filter_model_tools`). If any streaming tool is missing, re-check the `index.js` spread (Task 2 Step 5). If score tools appear, re-check `filter_model_tools` in `harness/agent/tools.py`.

### Check 2 — Diagnostic-path reachability

- [ ] **Step 2: Walk each manifest's `optimal_diagnostic_path` against the deployed faulted stack**

For each of the four scenarios, deploy `faulted.yaml`, then run each tool on `optimal_diagnostic_path` with real inputs from the stack outputs and confirm the output contains the signal that pinpoints the fault:

```bash
# Example structure (repeat for each fault):
aws --endpoint-url=http://localhost:4566 cloudformation create-stack \
  --stack-name ace-bench-stack \
  --template-body file://scenarios/arch05_fault01_delivery/faulted.yaml \
  --capabilities CAPABILITY_NAMED_IAM
aws --endpoint-url=http://localhost:4566 cloudformation wait stack-create-complete \
  --stack-name ace-bench-stack

# Then invoke tools from the path (adjust tool names and inputs per manifest):
node -e "
import('./harness/mcp_server/tools/probe_streaming.js').then(async m => {
  const t = n => m.probeStreamingTools.find(x => x.name === n);
  // fault01 path: ace_describe_firehose_delivery_stream -> ace_describe_kinesis_stream -> ace_count_opensearch_docs
  const r1 = await t('ace_describe_firehose_delivery_stream').handler({ delivery_stream_name: 'ace-bench-stack-ingest-delivery' });
  console.log('FIREHOSE SOURCE ARN:', r1.source_kinesis_stream_arn, '— expected: wrong ARN');
  const r2 = await t('ace_describe_kinesis_stream').handler({ stream_name: 'ace-bench-stack-ingest-stream' });
  console.log('REAL STREAM ARN:', r2.stream_arn, '— should differ from firehose source ARN');
  const r3 = await t('ace_count_opensearch_docs').handler({ domain_endpoint: '<DomainEndpoint>', index_name: 'events' });
  console.log('DOC COUNT:', r3.count, '— expected: 0');
});
"
# Confirm the tool outputs collectively reveal the fault. Tear down after each.
aws --endpoint-url=http://localhost:4566 cloudformation delete-stack --stack-name ace-bench-stack
aws --endpoint-url=http://localhost:4566 cloudformation wait stack-delete-complete --stack-name ace-bench-stack
```
Record per-scenario: PASS (signal found) or FAIL (tool did not reveal the fault → fix the manifest's `optimal_diagnostic_path` or the tool's output fields).

### Check 3a — Static rubric pre-gate

- [ ] **Step 3: Verify all streaming tool descriptions satisfy the three rubric criteria**

For each of the five streaming tools, manually verify the `description` field in `probe_streaming.js` states:
- **(a) The real AWS API it maps to** (e.g. "Kinesis DescribeStreamSummary", "Firehose DescribeDeliveryStream", "OpenSearch/Elasticsearch DescribeDomain", "HTTP GET /<index>/_count").
- **(b) The concrete fields/signals it returns** (named explicitly: `StreamStatus`, `OpenShardCount`, `DeliveryStreamStatus`, `source_kinesis_stream_arn`, `destination_index`, etc.).
- **(c) When to reach for it** — at least one symptom or fault-class named.

Run this automated check:
```bash
node -e "
import('./harness/mcp_server/tools/probe_streaming.js').then(m => {
  const pass = [], fail = [];
  for (const t of m.probeStreamingTools) {
    const d = t.description;
    const hasApi = /DescribeStream|DescribeDeliveryStream|DescribeDomain|_count|ListShards|PutRecord/i.test(d);
    const hasFields = /status|ARN|endpoint|count|shard|index|error/i.test(d);
    const hasWhen = /use to|diagnose|symptom|fault|when/i.test(d);
    (hasApi && hasFields && hasWhen ? pass : fail).push(t.name + (hasApi?'':' [missing API]') + (hasFields?'':' [missing fields]') + (hasWhen?'':' [missing when-to-use]'));
  }
  console.log('PASS:', pass);
  console.log('FAIL:', fail);
});
"
```
Expected: all five tools in PASS list. If any fail, rewrite the offending tool's `description` in `probe_streaming.js` and re-run. Do not proceed to 3b until all pass.

### Check 3b — LLM-judge blind triggering (N=5)

- [ ] **Step 4: Run the LLM-judge blind-selection test for each scenario**

The judge model must be **cheaper and distinct from the primary eval target** (use `claude-haiku-3-5` or equivalent, not the model this benchmark evaluates). For each scenario, run 5 independent trials. Each trial gives the judge only the `scenario.md` text and the full tool list (names + descriptions + input schemas from `mcp_to_openai_tool`), asks which tools it would call first (in order), and records the picks.

Write a one-off script `scratch/discoverability_judge.py` (gitignored):
```python
"""Run N=5 blind-triggering trials per scenario. Usage: python scratch/discoverability_judge.py"""
import json, os, sys
import anthropic

client = anthropic.Anthropic()  # uses ANTHROPIC_API_KEY

# Load tool list from MCP server (run `node harness/mcp_server/index.js` in a subprocess or load directly)
# For simplicity, load the streaming tools' schema from the probe_streaming.js module via node:
import subprocess, json as _json

tool_defs_raw = subprocess.check_output([
    "node", "-e",
    """
import('./harness/mcp_server/tools/probe_streaming.js').then(m => {
  console.log(JSON.stringify(m.probeStreamingTools.map(t => ({
    name: t.name, description: t.description, inputSchema: t.inputSchema
  }))));
});
"""
])
tool_defs = _json.loads(tool_defs_raw)

SCENARIOS = [
    ("arch05_fault01_delivery", ["ace_describe_firehose_delivery_stream", "ace_describe_kinesis_stream", "ace_count_opensearch_docs"]),
    ("arch05_fault02_iam",      ["ace_describe_kinesis_stream", "ace_list_kinesis_shards"]),
    ("arch05_fault03_mapping",  ["ace_count_opensearch_docs", "ace_describe_firehose_delivery_stream"]),
    ("arch05_fault04_config",   ["ace_describe_kinesis_stream", "ace_describe_firehose_delivery_stream"]),
]

N = 5
SLACK = 1  # K = len(optimal_path) + SLACK

for scenario_id, optimal_path in SCENARIOS:
    scenario_md = open(f"scenarios/{scenario_id}/scenario.md").read()
    tool_list_text = "\n".join(
        f"- {t['name']}: {t['description']}" for t in tool_defs
    )
    prompt = (
        f"You are debugging a cloud infrastructure issue.\n\n"
        f"## Symptom\n{scenario_md}\n\n"
        f"## Available diagnostic tools\n{tool_list_text}\n\n"
        f"Which tools would you call FIRST (in order) to diagnose this symptom? "
        f"List up to {len(optimal_path) + SLACK} tool names, one per line, most important first."
    )
    hits = 0
    for trial in range(1, N + 1):
        resp = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        picks = [line.strip().lstrip("-•* ") for line in resp.content[0].text.strip().splitlines() if line.strip()]
        K = len(optimal_path) + SLACK
        picks_k = picks[:K]
        all_present = all(t in picks_k for t in optimal_path)
        if all_present:
            hits += 1
        print(f"  trial {trial}: picks={picks_k} -> {'PASS' if all_present else 'FAIL'}")
    result = "PASS" if hits >= 3 else "FAIL"
    print(f"{scenario_id}: {hits}/{N} trials -> {result}\n")
```

Run: `python scratch/discoverability_judge.py`

**Pass bar:** ≥3/5 trials where every tool on `optimal_diagnostic_path` appears in the judge's first-K picks (K = len(optimal_path) + 1 slack).

**Remediation:** if a scenario fails (< 3/5), apply the remediation ladder in order:
1. Rewrite the offending tool descriptions to be more specific about the symptom class (re-run Check 3a first).
2. Shorten `optimal_diagnostic_path` to only the tools that are unambiguously the right first call.
3. Make the symptom in `scenario.md` more specific (without naming the cause) — e.g. add "the producer returns HTTP 500 with an AccessDeniedException in logs" vs. just "returns 500".
4. If ≥3/5 is still not achievable, reconsider the fault design: the fault may be too ambiguous to be a fair benchmark item; replace it with an alternative from the candidate list.

Re-run after each remediation until ≥3/5 is achieved for all four scenarios.

### Check 4 — Trace + scoring dry run

- [ ] **Step 5: Verify optimal_tool_calls and scoring pass**

For one scenario (fault01 recommended), run the full agent loop in dry-run mode with the streaming tools, confirm the tool calls are logged in `tool_call_trace.json`, and confirm `ace_verify_fix` (when manually wired) would accept the minimal fix:
```bash
# Deploy fault01 faulted stack
aws --endpoint-url=http://localhost:4566 cloudformation create-stack \
  --stack-name ace-bench-stack \
  --template-body file://scenarios/arch05_fault01_delivery/faulted.yaml \
  --capabilities CAPABILITY_NAMED_IAM
aws --endpoint-url=http://localhost:4566 cloudformation wait stack-create-complete \
  --stack-name ace-bench-stack

# Run the harness (dry-run — HARNESS_API_KEY gates ace_verify_fix, so just confirm tool_call_trace.json is written):
python harness/run.py scenarios/arch05_fault01_delivery/ \
  --model anthropic/claude-haiku-4-5 \
  --api-key $ANTHROPIC_API_KEY 2>&1 | tail -20

# Confirm tool_call_trace.json contains streaming tool calls:
python -c "
import json, glob
traces = sorted(glob.glob('results/*/tool_call_trace.json'))
if traces:
    trace = json.load(open(traces[-1]))
    names = [c['tool'] for c in trace]
    print('Tool calls recorded:', names)
    streaming = [n for n in names if 'kinesis' in n or 'firehose' in n or 'opensearch' in n]
    print('Streaming tools used:', streaming)
"
aws --endpoint-url=http://localhost:4566 cloudformation delete-stack --stack-name ace-bench-stack
aws --endpoint-url=http://localhost:4566 cloudformation wait stack-delete-complete --stack-name ace-bench-stack
```
Expected: at least one streaming tool appears in the trace. If no streaming tools are called (model calls only file/score tools), the scenario's `scenario.md` or the tool descriptions need refinement (return to Check 3b remediation).

- [ ] **Step 6: Record Task 5 findings and commit**

Append a `## Task 5 findings` section to this plan file with:
- Per-scenario: Check 1 (plumbing PASS/FAIL), Check 2 (reachability PASS/FAIL), Check 3a (rubric PASS/FAIL per tool), Check 3b (N=5 judge results, hit count, PASS/FAIL), Check 4 (trace PASS/FAIL).
- Any remediations applied.

```bash
git add docs/superpowers/plans/2026-06-20-breadth-streaming-analytics.md
git commit -m "docs(plan): record arch05 discoverability QA gate findings (Task 5)"
```

---

## Task 6: Documentation

Bring tool counts and architecture inventory in sync across the guides.

**Files:**
- Modify: `CLAUDE.md` (tool counts; Project Layout corpus/scenarios entries; `tools/` listing)
- Modify: `README.md` (Phase B tool inventory; repository layout)
- Modify: `RUN.md` (tool inventory; model-access count)

**Interfaces:**
- Consumes: the final tool list from Task 2 (5 priority-1 streaming tools, plus any priority-2 tools added in Task 2 Step 7) and the arch05 corpus/scenarios from Tasks 3–4.
- Produces: consistent counts and a documented arch05.

- [ ] **Step 1: Count the actual total tool count**

Run:
```bash
node -e "
Promise.all([
  import('./harness/mcp_server/tools/probe.js'),
  import('./harness/mcp_server/tools/probe_extended.js'),
  import('./harness/mcp_server/tools/observe.js'),
  import('./harness/mcp_server/tools/observe_extended.js'),
  import('./harness/mcp_server/tools/observe_tracing.js'),
  import('./harness/mcp_server/tools/probe_rds.js'),
  import('./harness/mcp_server/tools/probe_streaming.js'),
  import('./harness/mcp_server/tools/score.js'),
]).then(mods => {
  const arrays = mods.map(m => Object.values(m).find(Array.isArray) ?? []);
  const total = arrays.reduce((a, b) => a + b.length, 0);
  const diagnostic = total - (arrays[7]?.length ?? 0);
  console.log('diagnostic tools:', diagnostic, '| score tools:', arrays[7]?.length ?? 0, '| total:', total);
});
"
```
Record the printed counts — these are the source of truth for all documentation updates.

- [ ] **Step 2: Update `CLAUDE.md`**

In `CLAUDE.md`, update the MCP server description from "61 diagnostic + 2 score tools across 28 LocalStack services" to the actual count (61 + 5 = 66 diagnostic + 2 score, or the Task 2 Step 7 adjusted total if priority-2 tools were added). Add `harness/mcp_server/tools/probe_streaming.js` (5 streaming tools: Kinesis stream/shards, Firehose delivery stream, OpenSearch domain, OpenSearch doc count) to the `tools/` listing. Add `corpus/arch_05_streaming_ingest_pipeline/` to the Project Layout corpus entries and the four `scenarios/arch05_fault0N_*` entries to the scenarios list.

- [ ] **Step 3: Update `README.md` and `RUN.md`**

Bump the diagnostic tool count and model-access count in both files by the number of tools added in Task 2. Add the five streaming tools (name, AWS API, description) to the tool tables. Add arch05 to any architecture/corpus inventory section.

- [ ] **Step 4: Verify counts are consistent**

Run:
```bash
grep -rEn "[0-9]+ diagnostic" CLAUDE.md README.md RUN.md | head
node -e "
Promise.all([
  import('./harness/mcp_server/tools/probe.js'),
  import('./harness/mcp_server/tools/probe_extended.js'),
  import('./harness/mcp_server/tools/observe.js'),
  import('./harness/mcp_server/tools/observe_extended.js'),
  import('./harness/mcp_server/tools/observe_tracing.js'),
  import('./harness/mcp_server/tools/probe_rds.js'),
  import('./harness/mcp_server/tools/probe_streaming.js'),
  import('./harness/mcp_server/tools/score.js'),
]).then(mods => {
  const total = mods.map(m => Object.values(m).find(Array.isArray)?.length ?? 0).reduce((a,b)=>a+b,0);
  console.log('total tools from index:', total);
});
"
```
Expected: the printed total equals the count cited in the updated docs. If there is a discrepancy, fix the docs until they agree.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md README.md RUN.md
git commit -m "docs: document arch05 streaming pipeline architecture and streaming MCP tools"
```

---

## Task 1 findings

> To be filled in by the executor after running the spike (Task 1 Steps 1–7). Record the LocalStack version, the capability×fidelity matrix, the locked tool list, the locked fault mechanisms (primary + fallback), and the priority-2 decision. Commit this section update with: `git commit -m "docs(plan): record arch05 streaming spike findings and locked fault mechanisms"`.

### LocalStack version

_fill in: e.g. `2026.5.4:abc1234`, edition=pro_

### Capability × fidelity matrix

| Service / Tool Backing | Fidelity Probe | Result | Decision |
|---|---|---|---|
| Kinesis `DescribeStreamSummary` | StreamStatus + OpenShardCount populated | _fill_ | _fill_ |
| Kinesis `ListShards` | non-empty shard list | _fill_ | _fill_ |
| Firehose `DescribeDeliveryStream` | DeliveryStreamStatus ACTIVE, Source config populated | _fill_ | _fill_ |
| OpenSearch `DescribeElasticsearchDomain` | Endpoint + Processing populated | _fill_ | _fill_ |
| OpenSearch HTTP doc count | ≥1 doc after PutRecord+Firehose wait | _fill_ | _fill_ |
| Glue (priority-2) | job run non-empty response | _fill_ | _fill_ |
| Athena (priority-2) | query execution non-empty response | _fill_ | _fill_ |
| MSK (priority-2) | cluster describe non-empty response | _fill_ | _fill_ |
| EMR (priority-2) | step run non-empty response | _fill_ | _fill_ |

### Locked tool list

_fill in: list the tools that Task 2 will build, based on ✅ rows above. At minimum: ace_describe_kinesis_stream, ace_list_kinesis_shards, ace_describe_firehose_delivery_stream, ace_describe_opensearch_domain, ace_count_opensearch_docs (all priority-1; drop any whose backing row is ❌). Any priority-2 tools added here if their row is ✅._

### Locked fault mechanisms

| Fault | Class | **Locked mechanism** | Primary enforced? | Confirmed symptom |
|---|---|---|---|---|
| fault01 | delivery | _fill: primary or fallback_ | _fill_ | _fill_ |
| fault02 | iam | _fill: primary or fallback_ | _fill_ | _fill_ |
| fault03 | mapping | _fill: primary or fallback_ | _fill_ | _fill_ |
| fault04 | config | _fill: primary or fallback_ | _fill_ | _fill_ |

### Priority-2 decision

_fill in: e.g. "All priority-2 services (Glue, Athena, MSK, EMR) are ❌ on this LocalStack build — Task 2 Step 7 dropped. No priority-2 tools ship." OR "Glue ✅ — ace_describe_glue_job_run added in Task 2 Step 7."_

---

## Self-Review Notes (author)

- **Spec coverage:** Architecture → Task 3; fault set (4 classes, behavior-manifesting, confirmed primary+fallback) → Task 4; new MCP tools (5 priority-1 + spike-gated priority-2) → Task 2; de-risking spike incl. all four fault mechanisms + priority-2 health check → Task 1; discoverability QA gate (all 4 checks, N=5/≥3/5 judge, static rubric, remediation ladder) → Task 5; docs → Task 6. All spec sections map to a task.
- **Priority-2 is cleanly separable:** Task 2 Step 7 is the only priority-2 code step; dropping it requires no changes to any other step. Task 1 Step 6 makes the decision explicit before any code is written.
- **`@aws-sdk/client-kinesis` and `@aws-sdk/client-firehose` are already in `package.json`** — confirmed from the repo. Only `@aws-sdk/client-opensearch` is potentially new (Task 2 Step 1 checks).
- **arch02 is NOT modified here.** The streaming tools built in Task 2 are what the corpus-migration plan's arch02 track will adopt — that plan runs in a separate branch/task.
- **Realism gate held:** `ace_count_opensearch_docs` uses a direct HTTP call to the OpenSearch `_count` API (a real AWS API endpoint, not LocalStack introspection). All other tools use standard AWS SDK `Describe*` / `List*` calls.
- **Fallbacks for every spike-risk fault** (fault01/02/03/04) are inlined in Task 4 so a worker never blocks.
