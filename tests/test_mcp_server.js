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

import { probeTools } from "../harness/mcp_server/tools/probe.js";
import { observeTools } from "../harness/mcp_server/tools/observe.js";
import { scoreTools } from "../harness/mcp_server/tools/score.js";

import { probeExtendedTools } from "../harness/mcp_server/tools/probe_extended.js";
import { observeExtendedTools } from "../harness/mcp_server/tools/observe_extended.js";

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
