import { test, before } from "node:test";
import assert from "node:assert/strict";
import { LambdaClient, CreateFunctionCommand } from "@aws-sdk/client-lambda";
import { DynamoDBClient, CreateTableCommand } from "@aws-sdk/client-dynamodb";
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
