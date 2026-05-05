import { SNSClient, GetTopicAttributesCommand } from "@aws-sdk/client-sns";
import { EventBridgeClient, DescribeRuleCommand, ListTargetsByRuleCommand } from "@aws-sdk/client-eventbridge";
import { SchedulerClient, GetScheduleCommand } from "@aws-sdk/client-scheduler";
import { SFNClient, DescribeStateMachineCommand } from "@aws-sdk/client-sfn";
import { SWFClient, DescribeDomainCommand } from "@aws-sdk/client-swf";

const awsConfig = {
  endpoint: process.env.LOCALSTACK_ENDPOINT ?? "http://localhost:4566",
  region: "us-east-1",
  credentials: { accessKeyId: "test", secretAccessKey: "test" },
};

const snsClient = new SNSClient(awsConfig);
const ebClient = new EventBridgeClient(awsConfig);
const schedulerClient = new SchedulerClient(awsConfig);
const sfnClient = new SFNClient(awsConfig);
const swfClient = new SWFClient(awsConfig);

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
  {
    name: "ace_get_eventbridge_rule",
    description: "Describe an EventBridge rule including its schedule expression, event pattern, and targets",
    inputSchema: {
      type: "object",
      properties: {
        rule_name: { type: "string" },
        bus_name: { type: "string" },
      },
      required: ["rule_name"],
    },
    async handler({ rule_name, bus_name = "default" } = {}) {
      if (!rule_name) return { error: "rule_name is required" };
      try {
        const [ruleRes, targetsRes] = await Promise.all([
          ebClient.send(new DescribeRuleCommand({ Name: rule_name, EventBusName: bus_name })),
          ebClient.send(new ListTargetsByRuleCommand({ Rule: rule_name, EventBusName: bus_name })),
        ]);
        return {
          name: ruleRes.Name,
          arn: ruleRes.Arn,
          state: ruleRes.State,
          schedule_expression: ruleRes.ScheduleExpression ?? null,
          event_pattern: ruleRes.EventPattern ? JSON.parse(ruleRes.EventPattern) : null,
          description: ruleRes.Description ?? null,
          targets_count: targetsRes.Targets?.length ?? 0,
          targets: (targetsRes.Targets ?? []).map(t => ({ id: t.Id, arn: t.Arn })),
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "EB_ERROR" };
      }
    },
  },
  {
    name: "ace_get_schedule",
    description: "Describe an EventBridge Scheduler schedule including expression, target ARN, and state",
    inputSchema: {
      type: "object",
      properties: {
        name: { type: "string" },
        group_name: { type: "string" },
      },
      required: ["name"],
    },
    async handler({ name, group_name = "default" } = {}) {
      if (!name) return { error: "name is required" };
      try {
        const res = await schedulerClient.send(new GetScheduleCommand({ Name: name, GroupName: group_name }));
        return {
          name: res.Name,
          arn: res.Arn,
          state: res.State,
          schedule_expression: res.ScheduleExpression,
          target_arn: res.Target?.Arn ?? null,
          role_arn: res.Target?.RoleArn ?? null,
          description: res.Description ?? null,
          flexible_window_minutes: res.FlexibleTimeWindow?.MaximumWindowInMinutes ?? null,
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "SCHEDULER_ERROR" };
      }
    },
  },
  {
    name: "ace_describe_state_machine",
    description: "Describe a Step Functions state machine configuration, type, role, and state count",
    inputSchema: {
      type: "object",
      properties: { state_machine_arn: { type: "string" } },
      required: ["state_machine_arn"],
    },
    async handler({ state_machine_arn } = {}) {
      if (!state_machine_arn) return { error: "state_machine_arn is required" };
      try {
        const res = await sfnClient.send(new DescribeStateMachineCommand({
          stateMachineArn: state_machine_arn,
        }));
        const def = JSON.parse(res.definition ?? "{}");
        return {
          name: res.name,
          arn: res.stateMachineArn,
          status: res.status,
          type: res.type,
          role_arn: res.roleArn,
          state_count: Object.keys(def.States ?? {}).length,
          logging_level: res.loggingConfiguration?.level ?? null,
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "SFN_ERROR" };
      }
    },
  },
  {
    name: "ace_describe_swf_domain",
    description: "Describe an SWF domain status and workflow execution retention period",
    inputSchema: {
      type: "object",
      properties: { domain: { type: "string" } },
      required: ["domain"],
    },
    async handler({ domain } = {}) {
      if (!domain) return { error: "domain is required" };
      try {
        const res = await swfClient.send(new DescribeDomainCommand({ name: domain }));
        return {
          name: res.domainInfo?.name,
          status: res.domainInfo?.status,
          description: res.domainInfo?.description ?? null,
          workflow_execution_retention_period_days:
            res.configuration?.workflowExecutionRetentionPeriodInDays ?? null,
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "SWF_ERROR" };
      }
    },
  },
];
