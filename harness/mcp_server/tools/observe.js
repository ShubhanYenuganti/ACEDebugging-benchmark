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
  GetEventSourceMappingCommand,
} from "@aws-sdk/client-lambda";
import { DynamoDBClient, DescribeTableCommand } from "@aws-sdk/client-dynamodb";
import { SQSClient, GetQueueUrlCommand, GetQueueAttributesCommand } from "@aws-sdk/client-sqs";
import { SNSClient, GetTopicAttributesCommand } from "@aws-sdk/client-sns";
import { S3Client, GetBucketLocationCommand } from "@aws-sdk/client-s3";
import { KinesisClient, DescribeStreamSummaryCommand } from "@aws-sdk/client-kinesis";
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
const dynamoClient = new DynamoDBClient(awsConfig);
const sqsClient = new SQSClient(awsConfig);
const snsClient = new SNSClient(awsConfig);
const s3Client = new S3Client(awsConfig);
const kinesisClient = new KinesisClient(awsConfig);

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
