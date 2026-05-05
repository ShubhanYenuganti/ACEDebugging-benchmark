import { SNSClient, GetTopicAttributesCommand } from "@aws-sdk/client-sns";
import { EventBridgeClient, DescribeRuleCommand, ListTargetsByRuleCommand } from "@aws-sdk/client-eventbridge";
import { SchedulerClient, GetScheduleCommand } from "@aws-sdk/client-scheduler";
import { SFNClient, DescribeStateMachineCommand } from "@aws-sdk/client-sfn";
import { SWFClient, DescribeDomainCommand } from "@aws-sdk/client-swf";
import { SESClient, GetIdentityVerificationAttributesCommand } from "@aws-sdk/client-ses";
import { EC2Client, DescribeSecurityGroupsCommand } from "@aws-sdk/client-ec2";
import { Route53Client, ListResourceRecordSetsCommand } from "@aws-sdk/client-route-53";
import { Route53ResolverClient, GetResolverEndpointCommand } from "@aws-sdk/client-route53resolver";
import { KinesisClient, DescribeStreamSummaryCommand } from "@aws-sdk/client-kinesis";
import { FirehoseClient, DescribeDeliveryStreamCommand } from "@aws-sdk/client-firehose";
import {
  DynamoDBStreamsClient,
  DescribeStreamCommand as DDBDescribeStreamCommand,
} from "@aws-sdk/client-dynamodb-streams";
import { KMSClient, DescribeKeyCommand, GetKeyRotationStatusCommand } from "@aws-sdk/client-kms";
import { SecretsManagerClient, DescribeSecretCommand } from "@aws-sdk/client-secrets-manager";
import { SSMClient, DescribeParametersCommand } from "@aws-sdk/client-ssm";

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
const sesClient = new SESClient(awsConfig);
const ec2Client = new EC2Client(awsConfig);
const r53Client = new Route53Client(awsConfig);
const r53ResolverClient = new Route53ResolverClient(awsConfig);
const kinesisClient = new KinesisClient(awsConfig);
const firehoseClient = new FirehoseClient(awsConfig);
const dynamoStreamsClient = new DynamoDBStreamsClient(awsConfig);
const kmsClient = new KMSClient(awsConfig);
const secretsClient = new SecretsManagerClient(awsConfig);
const ssmClient = new SSMClient(awsConfig);

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
  {
    name: "ace_get_ses_identity",
    description: "Check SES verification status for one or more email or domain identities",
    inputSchema: {
      type: "object",
      properties: {
        identities: { type: "array", items: { type: "string" } },
      },
      required: ["identities"],
    },
    async handler({ identities } = {}) {
      if (!identities?.length) return { error: "identities array is required" };
      try {
        const res = await sesClient.send(
          new GetIdentityVerificationAttributesCommand({ Identities: identities })
        );
        const out = {};
        for (const [id, attrs] of Object.entries(res.VerificationAttributes ?? {})) {
          out[id] = {
            verification_status: attrs.VerificationStatus,
            verification_token: attrs.VerificationToken ?? null,
          };
        }
        return out;
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "SES_ERROR" };
      }
    },
  },
  {
    name: "ace_describe_security_group",
    description: "Get EC2 security group inbound and outbound rules",
    inputSchema: {
      type: "object",
      properties: { group_id: { type: "string" } },
      required: ["group_id"],
    },
    async handler({ group_id } = {}) {
      if (!group_id) return { error: "group_id is required" };
      try {
        const res = await ec2Client.send(new DescribeSecurityGroupsCommand({ GroupIds: [group_id] }));
        const sg = res.SecurityGroups?.[0];
        if (!sg) return { error: "security group not found", error_type: "NOT_FOUND" };
        const mapRule = p => ({
          protocol: p.IpProtocol,
          from_port: p.FromPort ?? null,
          to_port: p.ToPort ?? null,
          cidr: (p.IpRanges ?? []).map(r => r.CidrIp),
          cidr_ipv6: (p.Ipv6Ranges ?? []).map(r => r.CidrIpv6),
        });
        return {
          group_id: sg.GroupId,
          group_name: sg.GroupName,
          description: sg.Description,
          vpc_id: sg.VpcId ?? null,
          inbound_rules: (sg.IpPermissions ?? []).map(mapRule),
          outbound_rules: (sg.IpPermissionsEgress ?? []).map(mapRule),
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "EC2_ERROR" };
      }
    },
  },
  {
    name: "ace_list_dns_records",
    description: "List Route 53 DNS resource record sets in a hosted zone, optionally filtered by type",
    inputSchema: {
      type: "object",
      properties: {
        hosted_zone_id: { type: "string" },
        record_type: { type: "string" },
      },
      required: ["hosted_zone_id"],
    },
    async handler({ hosted_zone_id, record_type } = {}) {
      if (!hosted_zone_id) return { error: "hosted_zone_id is required" };
      try {
        const res = await r53Client.send(new ListResourceRecordSetsCommand({ HostedZoneId: hosted_zone_id }));
        let records = (res.ResourceRecordSets ?? []).map(r => ({
          name: r.Name,
          type: r.Type,
          ttl: r.TTL ?? null,
          values: (r.ResourceRecords ?? []).map(rr => rr.Value),
          alias_target: r.AliasTarget?.DNSName ?? null,
        }));
        if (record_type) records = records.filter(r => r.type === record_type);
        return records;
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "R53_ERROR" };
      }
    },
  },
  {
    name: "ace_get_resolver_endpoint",
    description: "Describe a Route 53 Resolver endpoint configuration and IP address count",
    inputSchema: {
      type: "object",
      properties: { resolver_endpoint_id: { type: "string" } },
      required: ["resolver_endpoint_id"],
    },
    async handler({ resolver_endpoint_id } = {}) {
      if (!resolver_endpoint_id) return { error: "resolver_endpoint_id is required" };
      try {
        const res = await r53ResolverClient.send(
          new GetResolverEndpointCommand({ ResolverEndpointId: resolver_endpoint_id })
        );
        const ep = res.ResolverEndpoint;
        return {
          id: ep?.Id,
          name: ep?.Name,
          direction: ep?.Direction,
          status: ep?.Status,
          ip_address_count: ep?.IpAddressCount,
          host_vpc_id: ep?.HostVPCId,
          security_group_ids: ep?.SecurityGroupIds ?? [],
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "R53R_ERROR" };
      }
    },
  },
  {
    name: "ace_describe_kinesis_stream",
    description: "Describe a Kinesis data stream including shard count, retention period, and encryption",
    inputSchema: {
      type: "object",
      properties: { stream_name: { type: "string" } },
      required: ["stream_name"],
    },
    async handler({ stream_name } = {}) {
      if (!stream_name) return { error: "stream_name is required" };
      try {
        const res = await kinesisClient.send(new DescribeStreamSummaryCommand({ StreamName: stream_name }));
        const s = res.StreamDescriptionSummary;
        return {
          stream_arn: s?.StreamARN,
          stream_status: s?.StreamStatus,
          shard_count: s?.OpenShardCount,
          retention_period_hours: s?.RetentionPeriodHours,
          encryption_type: s?.EncryptionType ?? "NONE",
          key_id: s?.KeyId ?? null,
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "KINESIS_ERROR" };
      }
    },
  },
  {
    name: "ace_describe_firehose_stream",
    description: "Describe a Kinesis Firehose delivery stream including status and destination configuration",
    inputSchema: {
      type: "object",
      properties: { delivery_stream_name: { type: "string" } },
      required: ["delivery_stream_name"],
    },
    async handler({ delivery_stream_name } = {}) {
      if (!delivery_stream_name) return { error: "delivery_stream_name is required" };
      try {
        const res = await firehoseClient.send(
          new DescribeDeliveryStreamCommand({ DeliveryStreamName: delivery_stream_name })
        );
        const desc = res.DeliveryStreamDescription;
        return {
          arn: desc?.DeliveryStreamARN,
          status: desc?.DeliveryStreamStatus,
          type: desc?.DeliveryStreamType,
          destinations: (desc?.Destinations ?? []).map(d => ({
            destination_id: d.DestinationId,
            s3_bucket: d.ExtendedS3DestinationDescription?.BucketARN
              ?? d.S3DestinationDescription?.BucketARN
              ?? null,
            http_url: d.HttpEndpointDestinationDescription?.EndpointConfiguration?.Url ?? null,
          })),
          encryption_status: desc?.DeliveryStreamEncryptionConfiguration?.Status ?? "DISABLED",
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "FIREHOSE_ERROR" };
      }
    },
  },
  {
    name: "ace_describe_dynamo_stream",
    description: "Describe a DynamoDB stream including status, view type, and shard list",
    inputSchema: {
      type: "object",
      properties: { stream_arn: { type: "string" } },
      required: ["stream_arn"],
    },
    async handler({ stream_arn } = {}) {
      if (!stream_arn) return { error: "stream_arn is required" };
      try {
        const res = await dynamoStreamsClient.send(
          new DDBDescribeStreamCommand({ StreamArn: stream_arn })
        );
        const desc = res.StreamDescription;
        return {
          stream_arn: desc?.StreamArn,
          table_name: desc?.TableName,
          stream_status: desc?.StreamStatus,
          view_type: desc?.StreamViewType,
          shard_count: desc?.Shards?.length ?? 0,
          shards: (desc?.Shards ?? []).map(s => ({
            shard_id: s.ShardId,
            parent_shard_id: s.ParentShardId ?? null,
            starting_sequence: s.SequenceNumberRange?.StartingSequenceNumber ?? null,
            ending_sequence: s.SequenceNumberRange?.EndingSequenceNumber ?? null,
          })),
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "DYNAMO_STREAMS_ERROR" };
      }
    },
  },
  {
    name: "ace_describe_kms_key",
    description: "Describe a KMS key including its state, usage, spec, and rotation status",
    inputSchema: {
      type: "object",
      properties: { key_id: { type: "string" } },
      required: ["key_id"],
    },
    async handler({ key_id } = {}) {
      if (!key_id) return { error: "key_id is required" };
      try {
        const [keyRes, rotationRes] = await Promise.all([
          kmsClient.send(new DescribeKeyCommand({ KeyId: key_id })),
          kmsClient.send(new GetKeyRotationStatusCommand({ KeyId: key_id })).catch(() => null),
        ]);
        const k = keyRes.KeyMetadata;
        return {
          key_id: k?.KeyId,
          arn: k?.Arn,
          description: k?.Description ?? null,
          state: k?.KeyState,
          creation_date: k?.CreationDate?.toISOString() ?? null,
          deletion_date: k?.DeletionDate?.toISOString() ?? null,
          key_usage: k?.KeyUsage,
          key_spec: k?.KeySpec,
          rotation_enabled: rotationRes?.KeyRotationEnabled ?? null,
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "KMS_ERROR" };
      }
    },
  },
  {
    name: "ace_describe_secret",
    description: "Get Secrets Manager secret metadata without retrieving the secret value",
    inputSchema: {
      type: "object",
      properties: { secret_id: { type: "string" } },
      required: ["secret_id"],
    },
    async handler({ secret_id } = {}) {
      if (!secret_id) return { error: "secret_id is required" };
      try {
        const res = await secretsClient.send(new DescribeSecretCommand({ SecretId: secret_id }));
        return {
          name: res.Name,
          arn: res.ARN,
          description: res.Description ?? null,
          rotation_enabled: res.RotationEnabled ?? false,
          rotation_lambda_arn: res.RotationLambdaARN ?? null,
          tags: (res.Tags ?? []).reduce((acc, t) => { acc[t.Key] = t.Value; return acc; }, {}),
          created_date: res.CreatedDate?.toISOString() ?? null,
          last_changed_date: res.LastChangedDate?.toISOString() ?? null,
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "SECRETS_ERROR" };
      }
    },
  },
  {
    name: "ace_describe_parameters",
    description: "List SSM Parameter Store parameters, optionally filtered by path prefix or type",
    inputSchema: {
      type: "object",
      properties: {
        path_prefix: { type: "string" },
        parameter_type: {
          type: "string",
          enum: ["String", "StringList", "SecureString"],
        },
      },
    },
    async handler({ path_prefix, parameter_type } = {}) {
      try {
        const filters = [];
        if (path_prefix) filters.push({ Key: "Name", Option: "BeginsWith", Values: [path_prefix] });
        if (parameter_type) filters.push({ Key: "Type", Option: "Equals", Values: [parameter_type] });
        const params = filters.length ? { ParameterFilters: filters } : {};
        const res = await ssmClient.send(new DescribeParametersCommand(params));
        return (res.Parameters ?? []).map(p => ({
          name: p.Name,
          type: p.Type,
          description: p.Description ?? null,
          version: p.Version,
          last_modified: p.LastModifiedDate?.toISOString() ?? null,
          tier: p.Tier ?? null,
        }));
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "SSM_ERROR" };
      }
    },
  },
];
