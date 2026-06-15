**Update 2026-06-14:** This project now runs on the LocalStack **Ultimate** license, not the free/Hobby tier. The table below documents the old free-tier service set and is retained for historical reference. Depth tooling (CloudTrail `ace_lookup_events`) and real IAM enforcement now assume Ultimate; X-Ray trace tools are planned for a future phase.

---

 Localstack's free tier:  Category | Allowed service | Notes |
|----------|-----------------|--------|
| **Analytics** | Amazon ElasticSearch | |
| **Analytics** | Amazon Kinesis Streams | |
| **Analytics** | Amazon Kinesis | |
| **Analytics** | Amazon Kinesis Data Firehose | |
| **Analytics** | Amazon Redshift | |
| **Application integration** | Amazon Simple Workflow Service (SWF) | Benchmark shorthand **SW** where used. |
| **Application integration** | Amazon Simple Notification Service (SNS) | |
| **Application integration** | Amazon Simple Queue Service (SQS) | |
| **Application integration** | AWS Step Functions | |
| **Application integration** | Amazon EventBridge | |
| **Application integration** | Amazon EventBridge Schedule | |
| **Business applications** | Amazon Simple Email Service (SES) | |
| **Compute** | Amazon Elastic Compute Cloud (EC2) | |
| **Compute** | AWS Lambda | |
| **AWS Support API** | AWS Support API | |
| **Databases** | Amazon DynamoDB | |
| **Databases** | Amazon DynamoDB Streams | |
| **Management & governance** | AWS CloudFormation | |
| **Management & governance** | Amazon CloudWatch Metrics | |
| **Management & governance** | Amazon CloudWatch Logs | |
| **Management & governance** | AWS Resource Groups | |
| **Management & governance** | AWS Systems Manager Parameter Store | SSM APIs **for parameters** (Parameter Store). |
| **Management & governance** | AWS Config | |
| **Machine learning** | Amazon Transcribe | |
| **Networking & content delivery** | Amazon Route 53 | |
| **Networking & content delivery** | Amazon Route 53 Resolver | |
| **Networking & content delivery** | Amazon API Gateway REST API | **Via HTTP to the stack ApiEndpoint, not SDK** (LocalStack contract). REST API surfaces only unless PDF maps to this row. |
| **Security, identity & compliance** | AWS Key Management Service (KMS) | |
| **Security, identity & compliance** | AWS Secrets Manager | |
| **Security, identity & compliance** | AWS Security Token Service (STS) | |
| **Security, identity & compliance** | AWS Certificate Manager | |
| **Security, identity & compliance** | AWS Identity and Access Management (IAM) | |
| **Storage** | Amazon S3 | |
| **Storage** | Amazon S3 Control | |
