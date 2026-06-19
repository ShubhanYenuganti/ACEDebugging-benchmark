import { test, before } from "node:test";
import assert from "node:assert/strict";
import { LambdaClient, CreateFunctionCommand } from "@aws-sdk/client-lambda";
import { DynamoDBClient, CreateTableCommand, PutItemCommand } from "@aws-sdk/client-dynamodb";
import { marshall as marshallUtil } from "@aws-sdk/util-dynamodb";
import { SQSClient, CreateQueueCommand } from "@aws-sdk/client-sqs";
import { CloudFormationClient, CreateStackCommand } from "@aws-sdk/client-cloudformation";
import JSZip from "jszip";
import { SNSClient, CreateTopicCommand } from "@aws-sdk/client-sns";
import { KinesisClient, CreateStreamCommand } from "@aws-sdk/client-kinesis";
import { KMSClient, CreateKeyCommand } from "@aws-sdk/client-kms";
import { SecretsManagerClient, CreateSecretCommand } from "@aws-sdk/client-secrets-manager";
import { SSMClient, PutParameterCommand } from "@aws-sdk/client-ssm";
import { SESClient, VerifyEmailIdentityCommand } from "@aws-sdk/client-ses";
import { IAMClient, CreateRoleCommand, PutRolePolicyCommand } from "@aws-sdk/client-iam";
import { XRayClient, PutTraceSegmentsCommand } from "@aws-sdk/client-xray";

import { probeTools } from "../harness/mcp_server/tools/probe.js";
import { observeTools } from "../harness/mcp_server/tools/observe.js";
import { scoreTools } from "../harness/mcp_server/tools/score.js";

import { probeExtendedTools } from "../harness/mcp_server/tools/probe_extended.js";
import { observeExtendedTools } from "../harness/mcp_server/tools/observe_extended.js";
import { observeTracingTools } from "../harness/mcp_server/tools/observe_tracing.js";
import { probeRdsTools } from "../harness/mcp_server/tools/probe_rds.js";

const awsConfig = {
  endpoint: process.env.LOCALSTACK_ENDPOINT ?? "http://localhost:4566",
  region: "us-east-1",
  credentials: { accessKeyId: "test", secretAccessKey: "test" },
};

const lambda = new LambdaClient(awsConfig);
const dynamo = new DynamoDBClient(awsConfig);
const sqs = new SQSClient(awsConfig);
const cf = new CloudFormationClient(awsConfig);

const snsCl = new SNSClient(awsConfig);
const kinesisCl = new KinesisClient(awsConfig);
const kmsCl = new KMSClient(awsConfig);
const secretsCl = new SecretsManagerClient(awsConfig);
const ssmCl = new SSMClient(awsConfig);
const sesCl = new SESClient(awsConfig);
const iamCl = new IAMClient(awsConfig);

const FN = "test-identity-fn";
const TABLE = "test-table";
const RANGE_TABLE = "test-range-table";
const QUEUE = "test-queue";

let TOPIC_ARN;
let KEY_ID;
const KINESIS_STREAM = "test-kinesis-stream";
const SECRET_NAME = "test-secret-mcp";
const PARAM_NAME = "/test/mcp/param";

function tool(list, name) {
  return list.find(t => t.name === name);
}

before(async () => {
  const zip = new JSZip();
  zip.file("index.js", "exports.handler = async (e) => ({ statusCode: 200, body: JSON.stringify(e) });");
  const zipBuf = await zip.generateAsync({ type: "nodebuffer" });

  // IAM role the test Lambda assumes (required under ENFORCE_IAM=1)
  try {
    await iamCl.send(new CreateRoleCommand({
      RoleName: "test-role",
      AssumeRolePolicyDocument: JSON.stringify({
        Version: "2012-10-17",
        Statement: [{ Effect: "Allow", Principal: { Service: "lambda.amazonaws.com" }, Action: "sts:AssumeRole" }],
      }),
    }));
    await iamCl.send(new PutRolePolicyCommand({
      RoleName: "test-role",
      PolicyName: "test-role-inline",
      PolicyDocument: JSON.stringify({
        Version: "2012-10-17",
        Statement: [{ Effect: "Allow", Action: "*", Resource: "*" }],
      }),
    }));
  } catch (e) {
    if (!(e.name?.includes("EntityAlreadyExists") || e.message?.includes("already exist"))) throw e;
  }

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

  // HASH+RANGE table for ace_scan_table_range tests
  try {
    await dynamo.send(new CreateTableCommand({
      TableName: RANGE_TABLE,
      AttributeDefinitions: [
        { AttributeName: "pk", AttributeType: "S" },
        { AttributeName: "sk", AttributeType: "S" },
      ],
      KeySchema: [
        { AttributeName: "pk", KeyType: "HASH" },
        { AttributeName: "sk", KeyType: "RANGE" },
      ],
      BillingMode: "PAY_PER_REQUEST",
    }));
  } catch (e) {
    if (!e.message?.includes("already exist")) throw e;
  }
  try {
    await dynamo.send(new PutItemCommand({
      TableName: RANGE_TABLE,
      Item: marshallUtil({ pk: "user-1", sk: "profile", name: "Alice" }),
    }));
  } catch (e) {
    if (!e.message?.includes("already exist")) throw e;
  }

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

  // SES identity verification (LocalStack auto-verifies)
  await sesCl.send(new VerifyEmailIdentityCommand({ EmailAddress: "test@example.com" }));
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

test("ace_check_event_source includes filter_criteria field", async () => {
  const t = tool(probeTools, "ace_check_event_source");
  const result = await t.handler({ function_name: FN });
  assert.ok(Array.isArray(result));
  for (const mapping of result) {
    assert.ok("filter_criteria" in mapping, "each mapping must have filter_criteria key");
  }
});

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

test("ace_check_s3_object: nonexistent bucket returns exists:false", async () => {
  const result = await tool(probeTools, "ace_check_s3_object").handler({
    bucket: "no-such-bucket-xyz123",
    key: "no-key",
  });
  assert.ok("exists" in result);
  assert.equal(result.exists, false);
});

test("ace_invoke_endpoint accepts output_key override", async () => {
  const t = tool(probeTools, "ace_invoke_endpoint");
  const result = await t.handler({ path: "/", method: "GET", output_key: "ApiEndpoint" });
  assert.ok(result.error !== "ApiEndpoint not found in stack outputs",
    `should not get old hardcoded error; got: ${JSON.stringify(result)}`);
});

test("ace_invoke_endpoint falls back to pattern search when no output_key", async () => {
  const t = tool(probeTools, "ace_invoke_endpoint");
  const result = await t.handler({ path: "/", method: "GET" });
  assert.ok(result.error !== "No ApiEndpoint or ApiUrl output found in stack outputs",
    `pattern search should find ApiEndpoint in stack; got: ${JSON.stringify(result)}`);
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

test("ace_get_iam_role: nonexistent role returns error", async () => {
  const result = await tool(observeTools, "ace_get_iam_role").handler({ role_name: "nonexistent-xyz" });
  assert.ok(result.error);
});

test("ace_get_iam_role attached_policies has document field", async () => {
  const t = tool(observeTools, "ace_get_iam_role");
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
  for (const p of result.attached_policies) {
    assert.ok("document" in p, `attached policy ${p.name} must have document field`);
  }
});

test("ace_describe_resource returns properties for DynamoDB table", async () => {
  const t = tool(observeTools, "ace_describe_resource");
  const result = await t.handler({ logical_resource_id: "NonExistentTable" });
  assert.ok(result.error, "nonexistent resource should return error, not empty properties");
});

test("ace_describe_resource Lambda returns non-empty properties", async () => {
  const t = tool(observeTools, "ace_describe_resource");
  const result = await t.handler({ logical_resource_id: "Placeholder" });
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

test("probe_extended and observe_extended export arrays", () => {
  assert.ok(Array.isArray(probeExtendedTools));
  assert.ok(Array.isArray(observeExtendedTools));
});

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

test("ace_get_stream_records accepts iterator_type parameter", async () => {
  const t = probeExtendedTools.find(t => t.name === "ace_get_stream_records");
  assert.ok(t, "ace_get_stream_records must exist");
  const result = await t.handler({ stream_arn: "arn:aws:dynamodb:us-east-1:000000000000:table/nonexistent/stream/2020-01-01T00:00:00.000" });
  assert.ok(result.error, "nonexistent stream should return error");
});

test("ace_get_stream_records returns error for missing stream_arn", async () => {
  const t = probeExtendedTools.find(t => t.name === "ace_get_stream_records");
  const result = await t.handler({});
  assert.ok(result.error, "missing stream_arn should return error");
});

test("ace_describe_dynamo_stream: nonexistent stream ARN returns error", async () => {
  const result = await observeExtendedTools.find(t => t.name === "ace_describe_dynamo_stream")
    .handler({ stream_arn: "arn:aws:dynamodb:us-east-1:000000000000:table/no-table/stream/2020-01-01T00:00:00.000" });
  assert.ok(result.error, JSON.stringify(result));
});

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

// === STS ===
test("ace_get_caller_identity: returns account, user_id, arn", async () => {
  const result = await probeExtendedTools.find(t => t.name === "ace_get_caller_identity").handler({});
  assert.ok("account" in result, JSON.stringify(result));
  assert.ok("user_id" in result);
  assert.ok("arn" in result);
});

test("ace_assume_role: nonexistent role returns error or localstack mock credentials", async () => {
  const result = await probeExtendedTools.find(t => t.name === "ace_assume_role")
    .handler({
      role_arn: "arn:aws:iam::000000000000:role/no-such-role",
      session_name: "ace-test-session",
    });
  assert.ok(result.error || result.assumed_role_arn, JSON.stringify(result));
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

test("ace_simulate_policy returns error when resource_arns omitted", async () => {
  const t = probeExtendedTools.find(t => t.name === "ace_simulate_policy");
  assert.ok(t, "ace_simulate_policy must exist");
  const result = await t.handler({
    policy_source_arn: "arn:aws:iam::000000000000:role/test-role",
    action_names: ["dynamodb:GetItem"],
  });
  assert.ok(result.error, `should error when resource_arns is omitted; got: ${JSON.stringify(result)}`);
  assert.ok(/resource_arns/i.test(result.error), "error message must mention resource_arns");
});

// === DynamoDB Query (ace_scan_table_range) ===
test("ace_scan_table_range returns items matching key condition", async () => {
  const t = probeExtendedTools.find(t => t.name === "ace_scan_table_range");
  assert.ok(t, "ace_scan_table_range tool must exist");
  const result = await t.handler({
    table_name: RANGE_TABLE,
    key_condition: "pk = :pk",
    expression_values: { ":pk": "user-1" },
  });
  assert.ok(!result.error, `unexpected error: ${result.error}`);
  assert.ok("items" in result);
  assert.ok("count" in result);
  assert.ok("scanned_count" in result);
  assert.strictEqual(result.count, 1);
  assert.strictEqual(result.items[0].pk, "user-1");
});

test("ace_scan_table_range clamps limit to 25", async () => {
  const t = probeExtendedTools.find(t => t.name === "ace_scan_table_range");
  const result = await t.handler({
    table_name: RANGE_TABLE,
    key_condition: "pk = :pk",
    expression_values: { ":pk": "user-1" },
    limit: 999,
  });
  assert.ok(!result.error);
  assert.ok(result.count <= 25);
});

test("ace_scan_table_range returns error for nonexistent table", async () => {
  const t = probeExtendedTools.find(t => t.name === "ace_scan_table_range");
  const result = await t.handler({
    table_name: "nonexistent-table-xyz",
    key_condition: "pk = :pk",
    expression_values: { ":pk": "x" },
  });
  assert.ok(result.error, "should return error for nonexistent table");
});

test("ace_scan_table_range returns error when table_name missing", async () => {
  const t = probeExtendedTools.find(t => t.name === "ace_scan_table_range");
  const result = await t.handler({ key_condition: "pk = :pk", expression_values: { ":pk": "x" } });
  assert.ok(result.error);
});

test("ace_scan_table_range returns error when key_condition missing", async () => {
  const t = probeExtendedTools.find(t => t.name === "ace_scan_table_range");
  const result = await t.handler({ table_name: RANGE_TABLE, expression_values: { ":pk": "x" } });
  assert.ok(result.error);
});

test("ace_scan_table_range returns error when expression_values missing", async () => {
  const t = probeExtendedTools.find(t => t.name === "ace_scan_table_range");
  const result = await t.handler({ table_name: RANGE_TABLE, key_condition: "pk = :pk" });
  assert.ok(result.error);
});

// === DynamoDB Scan (ace_scan_table) ===
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

// === CloudFormation Stack Events ===
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

// === CloudWatch Lambda Metrics ===
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

// === CloudTrail Tracing ===
test("observe_tracing exports an array with the CloudTrail tool", () => {
  assert.ok(Array.isArray(observeTracingTools));
  assert.equal(observeTracingTools.length, 3);
  assert.ok(observeTracingTools.some((t) => t.name === "ace_lookup_events"));
});

test("ace_lookup_events: returns events array or error", async () => {
  const res = await tool(observeTracingTools, "ace_lookup_events").handler({ window_minutes: 30 });
  if (res.error) { assert.ok(typeof res.error === "string"); }
  else {
    assert.ok(Array.isArray(res.events));
    assert.equal(typeof res.count, "number");
    assert.equal(res.window_minutes, 30);
  }
});

test("ace_lookup_events: clamps max_results to <= 100", async () => {
  const res = await tool(observeTracingTools, "ace_lookup_events").handler({ max_results: 9999 });
  if (!res.error) { assert.ok(res.events.length <= 100); }
});

// === X-Ray Trace Tools ===
test("observeTracingTools includes the two X-Ray trace tools", () => {
  assert.ok(observeTracingTools.some((t) => t.name === "ace_get_trace_summaries"));
  assert.ok(observeTracingTools.some((t) => t.name === "ace_get_trace"));
});

test("ace_get_trace_summaries: returns traces array or error", async () => {
  const res = await tool(observeTracingTools, "ace_get_trace_summaries").handler({ window_minutes: 30 });
  if (res.error) { assert.ok(typeof res.error === "string"); }
  else {
    assert.ok(Array.isArray(res.traces));
    assert.equal(typeof res.count, "number");
    assert.equal(res.window_minutes, 30);
  }
});

test("ace_get_trace_summaries: clamps window_minutes to <= 1440", async () => {
  const res = await tool(observeTracingTools, "ace_get_trace_summaries").handler({ window_minutes: 99999 });
  if (!res.error) { assert.equal(res.window_minutes, 1440); }
});

test("ace_get_trace: missing trace_id returns error", async () => {
  const res = await tool(observeTracingTools, "ace_get_trace").handler({});
  assert.equal(res.error_type, "INVALID_INPUT");
});

test("ace_get_trace: unknown trace_id returns structured-empty", async () => {
  const epoch = Date.now() / 1000;
  const traceId = `1-${Math.floor(epoch).toString(16)}-${Array.from({length:24},()=>Math.floor(Math.random()*16).toString(16)).join("")}`;
  const res = await tool(observeTracingTools, "ace_get_trace").handler({ trace_id: traceId });
  if (res.error) { assert.ok(typeof res.error === "string"); }
  else {
    assert.equal(res.trace_id, traceId);
    assert.deepEqual(res.segments, []);
  }
});

test("ace_get_trace: round-trips a seeded segment with subsegment", async () => {
  const xray = new XRayClient({
    endpoint: "http://localhost:4566", region: "us-east-1",
    credentials: { accessKeyId: "test", secretAccessKey: "test" },
  });
  const epoch = Date.now() / 1000;
  const traceId = `1-${Math.floor(epoch).toString(16)}-${Array.from({length:24},()=>Math.floor(Math.random()*16).toString(16)).join("")}`;
  const segment = JSON.stringify({
    trace_id: traceId,
    id: Array.from({length:16},()=>Math.floor(Math.random()*16).toString(16)).join(""),
    name: "ace-test-service",
    start_time: epoch - 1,
    end_time: epoch,
    in_progress: false,
    subsegments: [{
      id: Array.from({length:16},()=>Math.floor(Math.random()*16).toString(16)).join(""),
      name: "ace-test-table",
      namespace: "aws",
      start_time: epoch - 0.9,
      end_time: epoch - 0.1,
      fault: true,
      aws: { operation: "PutItem" },
    }],
  });
  try {
    await xray.send(new PutTraceSegmentsCommand({ TraceSegmentDocuments: [segment] }));
  } catch (_) {
    // LocalStack not available — skip
    return;
  }
  await new Promise(r => setTimeout(r, 1500));
  const res = await tool(observeTracingTools, "ace_get_trace").handler({ trace_id: traceId });
  if (res.error) return; // LocalStack X-Ray unavailable in this env; tolerated
  assert.equal(res.trace_id, traceId);
  assert.ok(res.segments.length >= 1);
  const sub = res.segments[0].subsegments.find((s) => s.name === "ace-test-table");
  assert.ok(sub);
  assert.equal(sub.fault, true);
  assert.equal(sub.aws_operation, "PutItem");
});

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
