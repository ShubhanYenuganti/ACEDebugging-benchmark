import { SNSClient, PublishCommand } from "@aws-sdk/client-sns";
import { EventBridgeClient, PutEventsCommand } from "@aws-sdk/client-eventbridge";
import { SFNClient, StartExecutionCommand, DescribeExecutionCommand } from "@aws-sdk/client-sfn";
import { SWFClient, CountOpenWorkflowExecutionsCommand } from "@aws-sdk/client-swf";

const awsConfig = {
  endpoint: process.env.LOCALSTACK_ENDPOINT ?? "http://localhost:4566",
  region: "us-east-1",
  credentials: { accessKeyId: "test", secretAccessKey: "test" },
};

const snsClient = new SNSClient(awsConfig);
const ebClient = new EventBridgeClient(awsConfig);
const sfnClient = new SFNClient(awsConfig);
const swfClient = new SWFClient(awsConfig);

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
];
