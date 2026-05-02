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
