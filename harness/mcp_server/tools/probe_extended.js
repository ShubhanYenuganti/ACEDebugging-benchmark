import { SNSClient, PublishCommand } from "@aws-sdk/client-sns";
import { EventBridgeClient, PutEventsCommand } from "@aws-sdk/client-eventbridge";
import { SFNClient, StartExecutionCommand, DescribeExecutionCommand } from "@aws-sdk/client-sfn";
import { SWFClient, CountOpenWorkflowExecutionsCommand } from "@aws-sdk/client-swf";
import { SESClient, SendEmailCommand } from "@aws-sdk/client-ses";
import { EC2Client, DescribeInstancesCommand } from "@aws-sdk/client-ec2";
import { Route53Client, GetHostedZoneCommand } from "@aws-sdk/client-route-53";
import { Route53ResolverClient, ListResolverEndpointsCommand } from "@aws-sdk/client-route53resolver";
import { KinesisClient, PutRecordCommand as KinesisPutRecordCommand } from "@aws-sdk/client-kinesis";

const awsConfig = {
  endpoint: process.env.LOCALSTACK_ENDPOINT ?? "http://localhost:4566",
  region: "us-east-1",
  credentials: { accessKeyId: "test", secretAccessKey: "test" },
};

const snsClient = new SNSClient(awsConfig);
const ebClient = new EventBridgeClient(awsConfig);
const sfnClient = new SFNClient(awsConfig);
const swfClient = new SWFClient(awsConfig);
const sesClient = new SESClient(awsConfig);
const ec2Client = new EC2Client(awsConfig);
const r53Client = new Route53Client(awsConfig);
const r53ResolverClient = new Route53ResolverClient(awsConfig);
const kinesisClient = new KinesisClient(awsConfig);

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
  {
    name: "ace_send_test_email",
    description: "Send a test email via SES (LocalStack mock) and return the message ID",
    inputSchema: {
      type: "object",
      properties: {
        from: { type: "string" },
        to: { type: "string" },
        subject: { type: "string" },
        body: { type: "string" },
      },
      required: ["from", "to", "subject"],
    },
    async handler({ from, to, subject, body = "ACE-Bench diagnostic test email" } = {}) {
      if (!from || !to || !subject) return { error: "from, to, and subject are required" };
      try {
        const res = await sesClient.send(new SendEmailCommand({
          Source: from,
          Destination: { ToAddresses: [to] },
          Message: {
            Subject: { Data: subject },
            Body: { Text: { Data: body } },
          },
        }));
        return { message_id: res.MessageId };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "SES_ERROR" };
      }
    },
  },
  {
    name: "ace_check_instance_state",
    description: "Get the current state, instance type, and network info for an EC2 instance",
    inputSchema: {
      type: "object",
      properties: { instance_id: { type: "string" } },
      required: ["instance_id"],
    },
    async handler({ instance_id } = {}) {
      if (!instance_id) return { error: "instance_id is required" };
      try {
        const res = await ec2Client.send(new DescribeInstancesCommand({ InstanceIds: [instance_id] }));
        const inst = res.Reservations?.[0]?.Instances?.[0];
        if (!inst) return { error: "instance not found", error_type: "NOT_FOUND" };
        return {
          state: inst.State?.Name,
          instance_type: inst.InstanceType,
          public_ip: inst.PublicIpAddress ?? null,
          private_ip: inst.PrivateIpAddress ?? null,
          launch_time: inst.LaunchTime?.toISOString() ?? null,
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "EC2_ERROR" };
      }
    },
  },
  {
    name: "ace_check_hosted_zone",
    description: "Get Route 53 hosted zone details and its resource record set count",
    inputSchema: {
      type: "object",
      properties: { hosted_zone_id: { type: "string" } },
      required: ["hosted_zone_id"],
    },
    async handler({ hosted_zone_id } = {}) {
      if (!hosted_zone_id) return { error: "hosted_zone_id is required" };
      try {
        const res = await r53Client.send(new GetHostedZoneCommand({ Id: hosted_zone_id }));
        return {
          id: res.HostedZone?.Id,
          name: res.HostedZone?.Name,
          record_count: res.HostedZone?.ResourceRecordSetCount,
          private_zone: res.HostedZone?.Config?.PrivateZone ?? false,
          comment: res.HostedZone?.Config?.Comment ?? null,
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "R53_ERROR" };
      }
    },
  },
  {
    name: "ace_list_resolver_endpoints",
    description: "List Route 53 Resolver endpoints, optionally filtered by direction (INBOUND|OUTBOUND)",
    inputSchema: {
      type: "object",
      properties: {
        direction: { type: "string", enum: ["INBOUND", "OUTBOUND"] },
      },
    },
    async handler({ direction } = {}) {
      try {
        const params = direction
          ? { Filters: [{ Name: "Direction", Values: [direction] }] }
          : {};
        const res = await r53ResolverClient.send(new ListResolverEndpointsCommand(params));
        return (res.ResolverEndpoints ?? []).map(e => ({
          id: e.Id,
          name: e.Name,
          direction: e.Direction,
          status: e.Status,
          ip_address_count: e.IpAddressCount,
          host_vpc_id: e.HostVPCId,
        }));
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "R53R_ERROR" };
      }
    },
  },
  {
    name: "ace_put_kinesis_record",
    description: "Put a test record to a Kinesis data stream and return the shard ID and sequence number",
    inputSchema: {
      type: "object",
      properties: {
        stream_name: { type: "string" },
        data: { type: "string" },
        partition_key: { type: "string" },
      },
      required: ["stream_name", "data", "partition_key"],
    },
    async handler({ stream_name, data, partition_key } = {}) {
      if (!stream_name || !data || !partition_key)
        return { error: "stream_name, data, and partition_key are required" };
      try {
        const res = await kinesisClient.send(new KinesisPutRecordCommand({
          StreamName: stream_name,
          Data: Buffer.from(data),
          PartitionKey: partition_key,
        }));
        return {
          shard_id: res.ShardId,
          sequence_number: res.SequenceNumber,
          encryption_type: res.EncryptionType ?? "NONE",
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "KINESIS_ERROR" };
      }
    },
  },
];
