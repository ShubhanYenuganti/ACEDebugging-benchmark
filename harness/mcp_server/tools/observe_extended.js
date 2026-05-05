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
