import { SNSClient, PublishCommand } from "@aws-sdk/client-sns";
import { EventBridgeClient, PutEventsCommand } from "@aws-sdk/client-eventbridge";
import { SFNClient, StartExecutionCommand, DescribeExecutionCommand } from "@aws-sdk/client-sfn";
import { SWFClient, CountOpenWorkflowExecutionsCommand } from "@aws-sdk/client-swf";
import { SESClient, SendEmailCommand } from "@aws-sdk/client-ses";
import { EC2Client, DescribeInstancesCommand } from "@aws-sdk/client-ec2";
import { Route53Client, GetHostedZoneCommand } from "@aws-sdk/client-route-53";
import { Route53ResolverClient, ListResolverEndpointsCommand } from "@aws-sdk/client-route53resolver";
import { KinesisClient, PutRecordCommand as KinesisPutRecordCommand } from "@aws-sdk/client-kinesis";
import { FirehoseClient, PutRecordCommand as FirehosePutRecordCommand } from "@aws-sdk/client-firehose";
import {
  DynamoDBStreamsClient,
  DescribeStreamCommand as DDBDescribeStreamCommand,
  GetShardIteratorCommand,
  GetRecordsCommand,
} from "@aws-sdk/client-dynamodb-streams";
import { unmarshall } from "@aws-sdk/util-dynamodb";
import { KMSClient, EncryptCommand, DecryptCommand } from "@aws-sdk/client-kms";
import { SecretsManagerClient, GetSecretValueCommand } from "@aws-sdk/client-secrets-manager";
import { STSClient, GetCallerIdentityCommand, AssumeRoleCommand } from "@aws-sdk/client-sts";
import { SSMClient, GetParameterCommand } from "@aws-sdk/client-ssm";

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
const firehoseClient = new FirehoseClient(awsConfig);
const dynamoStreamsClient = new DynamoDBStreamsClient(awsConfig);
const kmsClient = new KMSClient(awsConfig);
const secretsClient = new SecretsManagerClient(awsConfig);
const stsClient = new STSClient(awsConfig);
const ssmClient = new SSMClient(awsConfig);

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
  {
    name: "ace_put_firehose_record",
    description: "Put a test record to a Kinesis Firehose delivery stream",
    inputSchema: {
      type: "object",
      properties: {
        delivery_stream_name: { type: "string" },
        data: { type: "string" },
      },
      required: ["delivery_stream_name", "data"],
    },
    async handler({ delivery_stream_name, data } = {}) {
      if (!delivery_stream_name || !data)
        return { error: "delivery_stream_name and data are required" };
      try {
        const res = await firehoseClient.send(new FirehosePutRecordCommand({
          DeliveryStreamName: delivery_stream_name,
          Record: { Data: Buffer.from(data) },
        }));
        return { record_id: res.RecordId, encrypted: res.Encrypted ?? false };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "FIREHOSE_ERROR" };
      }
    },
  },
  {
    name: "ace_get_stream_records",
    description: "Read recent records from the latest shard of a DynamoDB stream",
    inputSchema: {
      type: "object",
      properties: { stream_arn: { type: "string" } },
      required: ["stream_arn"],
    },
    async handler({ stream_arn } = {}) {
      if (!stream_arn) return { error: "stream_arn is required" };
      try {
        const descRes = await dynamoStreamsClient.send(
          new DDBDescribeStreamCommand({ StreamArn: stream_arn })
        );
        const shards = descRes.StreamDescription?.Shards ?? [];
        if (shards.length === 0) return { records: [], shard_count: 0 };
        const shard_id = shards[shards.length - 1].ShardId;
        const iterRes = await dynamoStreamsClient.send(new GetShardIteratorCommand({
          StreamArn: stream_arn,
          ShardId: shard_id,
          ShardIteratorType: "TRIM_HORIZON",
        }));
        const recordsRes = await dynamoStreamsClient.send(new GetRecordsCommand({
          ShardIterator: iterRes.ShardIterator,
          Limit: 10,
        }));
        return {
          records: (recordsRes.Records ?? []).map(r => ({
            event_name: r.eventName,
            keys: r.dynamodb?.Keys ? unmarshall(r.dynamodb.Keys) : {},
            new_image: r.dynamodb?.NewImage ? unmarshall(r.dynamodb.NewImage) : null,
            old_image: r.dynamodb?.OldImage ? unmarshall(r.dynamodb.OldImage) : null,
          })),
          shard_count: shards.length,
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "DYNAMO_STREAMS_ERROR" };
      }
    },
  },
  {
    name: "ace_encrypt_decrypt",
    description: "Encrypt then decrypt a test value using a KMS key to verify key usability",
    inputSchema: {
      type: "object",
      properties: {
        key_id: { type: "string" },
        plaintext: { type: "string" },
      },
      required: ["key_id", "plaintext"],
    },
    async handler({ key_id, plaintext } = {}) {
      if (!key_id || !plaintext) return { error: "key_id and plaintext are required" };
      try {
        const encRes = await kmsClient.send(new EncryptCommand({
          KeyId: key_id,
          Plaintext: Buffer.from(plaintext),
        }));
        const decRes = await kmsClient.send(new DecryptCommand({
          CiphertextBlob: encRes.CiphertextBlob,
          KeyId: key_id,
        }));
        const decrypted = Buffer.from(decRes.Plaintext).toString("utf-8");
        return { decrypted, matches: decrypted === plaintext, key_id: decRes.KeyId };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "KMS_ERROR" };
      }
    },
  },
  {
    name: "ace_get_secret",
    description: "Retrieve a secret value from Secrets Manager by name or ARN",
    inputSchema: {
      type: "object",
      properties: {
        secret_id: { type: "string" },
        version_stage: { type: "string" },
      },
      required: ["secret_id"],
    },
    async handler({ secret_id, version_stage } = {}) {
      if (!secret_id) return { error: "secret_id is required" };
      try {
        const params = { SecretId: secret_id };
        if (version_stage) params.VersionStage = version_stage;
        const res = await secretsClient.send(new GetSecretValueCommand(params));
        return {
          name: res.Name,
          arn: res.ARN,
          secret_string: res.SecretString ?? null,
          created_date: res.CreatedDate?.toISOString() ?? null,
          version_id: res.VersionId,
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "SECRETS_ERROR" };
      }
    },
  },
  {
    name: "ace_get_caller_identity",
    description: "Return the AWS account ID, user ID, and ARN for the current caller",
    inputSchema: { type: "object", properties: {} },
    async handler() {
      try {
        const res = await stsClient.send(new GetCallerIdentityCommand({}));
        return { account: res.Account, user_id: res.UserId, arn: res.Arn };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "STS_ERROR" };
      }
    },
  },
  {
    name: "ace_assume_role",
    description: "Assume an IAM role via STS and return credential metadata (secret key not returned)",
    inputSchema: {
      type: "object",
      properties: {
        role_arn: { type: "string" },
        session_name: { type: "string" },
      },
      required: ["role_arn", "session_name"],
    },
    async handler({ role_arn, session_name } = {}) {
      if (!role_arn || !session_name) return { error: "role_arn and session_name are required" };
      try {
        const res = await stsClient.send(new AssumeRoleCommand({
          RoleArn: role_arn,
          RoleSessionName: session_name,
        }));
        return {
          access_key_id: res.Credentials?.AccessKeyId,
          expiration: res.Credentials?.Expiration?.toISOString() ?? null,
          assumed_role_arn: res.AssumedRoleUser?.Arn,
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "STS_ERROR" };
      }
    },
  },
  {
    name: "ace_get_parameter",
    description: "Retrieve an SSM Parameter Store parameter value by name",
    inputSchema: {
      type: "object",
      properties: {
        name: { type: "string" },
        with_decryption: { type: "boolean" },
      },
      required: ["name"],
    },
    async handler({ name, with_decryption = false } = {}) {
      if (!name) return { error: "name is required" };
      try {
        const res = await ssmClient.send(new GetParameterCommand({
          Name: name,
          WithDecryption: with_decryption,
        }));
        return {
          name: res.Parameter?.Name,
          type: res.Parameter?.Type,
          value: res.Parameter?.Value,
          version: res.Parameter?.Version,
          last_modified: res.Parameter?.LastModifiedDate?.toISOString() ?? null,
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "SSM_ERROR" };
      }
    },
  },
];
