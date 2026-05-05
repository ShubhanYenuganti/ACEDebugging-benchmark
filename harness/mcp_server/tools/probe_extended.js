import { SNSClient, PublishCommand } from "@aws-sdk/client-sns";
import { EventBridgeClient, PutEventsCommand } from "@aws-sdk/client-eventbridge";

const awsConfig = {
  endpoint: process.env.LOCALSTACK_ENDPOINT ?? "http://localhost:4566",
  region: "us-east-1",
  credentials: { accessKeyId: "test", secretAccessKey: "test" },
};

const snsClient = new SNSClient(awsConfig);
const ebClient = new EventBridgeClient(awsConfig);

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
];
