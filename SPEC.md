# ACE-Bench Harness — Implementation Spec

## Scope

This spec covers the implementation of the ACE-Bench evaluation
harness: the components that take a completed scenario corpus (Steps
1–2 already done, scenarios exist on disk) and run a model through
diagnosis, fix, and verification (Steps 3–6).

Step 7 scoring is out of scope for this build. Step 3 context
delivery, Step 4 MCP server, Step 5 deployment handler, and Step 6
verify loop are all in scope and require complete implementations.

---

## Pre-conditions (assumed complete before any phase starts)

The following exist on disk and are treated as read-only inputs:

```
corpus/
└── arch_01_[name]/
    ├── known_good.yaml
    ├── functional_test.py
    └── traffic_flow.md

scenarios/
└── arch01_fault01_[class]/
    ├── scenario.md
    ├── faulted.yaml
    ├── fault_manifest.json
    └── deployment/
        └── lambda/handler.py
```

`fault_manifest.json` fields the harness depends on:
- `fault_class` — used in Pass 4 (concurrency probe gate)
- `optimal_tool_calls` — used in efficiency scoring
- `optimal_files_changed` — used in efficiency scoring
- `optimal_lines_changed` — used in efficiency scoring
- `valid_fixes` — used in Pass 3 semantic classification
- `invalid_patches` — used in Pass 3 semantic classification
- `target_resource` — used in Pass 3 structural diff
- `target_property` — used in Pass 3 structural diff
- `original_value` — used in Pass 3 structural diff

---

## Project layout (harness owns everything under harness/)

```
ace-bench/
├── CLAUDE.md
├── harness/
│   ├── shared/
│   │   ├── localstack_client.py
│   │   ├── cfn_lint_runner.py
│   │   ├── file_differ.py
│   │   └── result_logger.py
│   ├── mcp_server/              # Phase B — 50 tools, 27 services
│   │   ├── index.js             # spreads all 5 tool arrays
│   │   ├── package.json
│   │   └── tools/
│   │       ├── probe.js          # 6 core probe tools
│   │       ├── probe_extended.js # 19 extended probe tools
│   │       ├── observe.js        # 6 core observe tools
│   │       ├── observe_extended.js # 17 extended observe tools
│   │       └── score.js          # 2 gated score tools
│   ├── runner/                  # Phase C
│   │   ├── scenario_runner.py
│   │   ├── context_builder.py
│   │   └── deployment_handler.py
│   ├── verify/                  # Phase D
│   │   ├── verify_loop.py
│   │   ├── pass1_functional.py
│   │   ├── pass2_regression.py
│   │   ├── pass3_classification.py
│   │   └── pass4_concurrency.py
│   ├── scoring/                 # Phase F
│   │   ├── agent.py
│   │   ├── scorer.py
│   │   ├── gate.py
│   │   └── dimensions/
│   │       ├── identification.py
│   │       ├── fix_correctness.py
│   │       ├── regression.py
│   │       ├── efficiency.py
│   │       └── quality.py
│   ├── agent/                   # Phase G — inline agent runner
│   │   ├── __init__.py
│   │   ├── tools.py
│   │   └── loop.py
│   └── run.py                   # Phase E — top-level entry point
└── results/
    └── [run_id]/
        ├── scenario_id.txt
        ├── tool_call_trace.json
        ├── file_change_log.json
        ├── faulted_baseline.json
        ├── verify_result.json
        └── score.json
```

---

## CLAUDE.md (place at project root before any phase)

```markdown
# ACE-Bench Harness

## What this is
Evaluation harness for the ACE-Bench debugging benchmark. Takes a
completed scenario corpus and runs a model through diagnosis, file
editing, redeployment, and verification. Supports any LLM provider
via LiteLLM (Anthropic, OpenAI, Gemini, Ollama, etc.).

## Runtime
- Python 3.11 — all harness code
- Node.js v22+ — MCP server only
- LocalStack free tier at http://localhost:4566
- Fake credentials: accessKeyId=test, secretAccessKey=test, region=us-east-1
- Fake account ID for IAM ARNs: 000000000000
- LocalStack must be running before any phase is executed

## Python dependencies
- boto3, requests, python-dotenv, cfn-lint, pytest, pytest-mock
- litellm — universal LLM adapter (Anthropic, OpenAI, Gemini, Ollama, etc.)
- mcp — MCP client for spawning the diagnostic server from Python
- anthropic — used by the scoring agent only

## Imports
All Python modules import shared utilities from harness/shared/:
  from harness.shared.localstack_client import cf_client, lambda_client, s3_client, sqs_client, iam_client, logs_client
  from harness.shared.cfn_lint_runner import run_lint
  from harness.shared.file_differ import diff_directories
  from harness.shared.result_logger import log_result, log_tool_call, log_file_change

Agent modules:
  from harness.agent.tools import mcp_to_openai_tool, filter_model_tools, dispatch_file_tool, FILE_TOOL_DEFINITIONS
  from harness.agent.loop import run_agent_loop

## Key invariants — never violate
- The model's first UPDATE_COMPLETE is final. Never allow a second deploy.
- fault_manifest.json is never exposed to the model under any circumstance.
- known_good.yaml is never exposed to the model under any circumstance.
- File edits by the model do not count toward tool_call_trace.
- Tool calls are logged individually with input, output, and timestamp.
- Score tools in the MCP server require HARNESS_API_KEY in the request.
  The key is set as an environment variable and never passed to the model.
- The inline agent (--model) spawns the MCP server internally; no external
  registration is needed. The agent's write_file tool is restricted to
  deployment/ and faulted.yaml. read_file blocks fault_manifest.json and
  known_good.yaml. submit_fix writes the signal file for the polling loop.

## Dependency order
Phase A (shared utilities) → Phase B (MCP server) → Phase C (runner
+ deployment handler) → Phase D (verify loop) → Phase E (entry point)
→ Phase F (scoring) → Phase G (inline agent runner)

## Result files written per run
results/[run_id]/scenario_id.txt       — which scenario was run
results/[run_id]/tool_call_trace.json  — every MCP diagnostic call
results/[run_id]/file_change_log.json  — every file edit with line counts
results/[run_id]/faulted_baseline.json — assertion results on faulted deploy
results/[run_id]/verify_result.json    — output of all verification passes
results/[run_id]/score.json            — final score with per-dimension breakdown
```

---

## Phase A — Shared Utilities

**Depends on:** nothing
**Blocks:** all other phases
**Session scope:** complete in one session

### Goal
Build the four shared modules that every other phase imports. These
have no business logic — they are infrastructure for the harness.
Get them right and tested before writing anything else.

### A1 — `harness/shared/localstack_client.py`

Expose pre-configured boto3 clients for every AWS service the harness
touches. All clients point to `http://localhost:4566` with fake
credentials. Import once, use everywhere.

Services needed: CloudFormation, Lambda, S3, SQS, IAM,
CloudWatch Logs, API Gateway.

Each client should be a module-level singleton — instantiated once
at import time, not per call. Include a `health_check()` function
that calls `cf_client.list_stacks()` and raises `RuntimeError` if
LocalStack is not reachable. The entry point calls this before
starting any run.

### A2 — `harness/shared/cfn_lint_runner.py`

Wrap `cfn-lint` as a subprocess call. Accept a template path, return
a structured result dict:

```python
{
  "passed": bool,
  "fatal_errors": [{"rule": str, "message": str, "location": str}],
  "warnings": [{"rule": str, "message": str, "location": str}]
}
```

Only fatal errors (`E` rules) set `passed = False`. Warnings (`W`
rules) are recorded but do not fail the lint check. Parse cfn-lint's
JSON output format (`--format json` flag). If cfn-lint is not
installed, raise `EnvironmentError` with a clear message.

### A3 — `harness/shared/file_differ.py`

Compare two snapshots of a directory and return a structured diff.
Used to compute what the model changed between scenario start and
fix submission.

Two functions:

`snapshot(directory: str) -> dict` — walk the directory tree and
return `{relative_path: file_content_hash}` for every file. Used
to capture state before the model starts.

`diff_snapshots(before: dict, after: dict, directory: str) -> dict`
— compare two snapshots and return:

```python
{
  "files_added": [str],           # paths present in after, not before
  "files_modified": [str],        # paths present in both, hash changed
  "files_removed": [str],         # paths present in before, not after
  "total_files_changed": int,     # len(added) + len(modified)
  "per_file_line_changes": {
    relative_path: {
      "lines_added": int,
      "lines_modified": int,
      "lines_removed": int,
      "total_lines_changed": int  # added + modified
    }
  },
  "total_lines_changed": int      # sum of total_lines_changed across all files
}
```

Line-level diff uses Python's `difflib.unified_diff`. Count a line
as "added" if it appears only in the after version, "removed" if
only in before, "modified" is not counted separately at the line
level — treat every changed line as either added or removed. The
`total_lines_changed` for efficiency scoring is lines_added +
lines_removed across all changed files.

### A4 — `harness/shared/result_logger.py`

Write structured JSON to `results/[run_id]/`. Four functions:

`init_run(run_id: str, scenario_id: str)` — create the results
directory, write `scenario_id.txt`.

`log_tool_call(run_id: str, turn: int, tool: str, input: dict,
output: dict, timestamp: str)` — append one entry to
`tool_call_trace.json`. File is a JSON array; append to it without
rewriting the whole file on each call.

`log_file_change(run_id: str, diff: dict)` — write the full diff
dict from `file_differ.diff_snapshots` to `file_change_log.json`.
Called once at submission time.

`log_verify_result(run_id: str, result: dict)` — write the verify
loop output dict to `verify_result.json`. Called once after Step 6
completes.

### A — Verification

Write a `tests/test_shared.py` that:
- Confirms `health_check()` raises when LocalStack is unreachable
  (mock the boto3 call)
- Confirms cfn-lint runner returns `passed: True` on a minimal valid
  template and `passed: False` on a template with a known E-rule error
- Confirms `diff_snapshots` correctly counts added, modified, and
  removed files and lines across a hand-crafted before/after pair
- Confirms result logger writes valid JSON for each of the four
  functions without file corruption across concurrent calls

All four tests must pass before Phase B begins.

---

## Phase B — Diagnostic MCP Server

**Depends on:** Phase A (shared utilities must exist)
**Blocks:** Phase C (runner needs the MCP server registered)
**Session scope:** complete in one session

### Goal
Build `ace-bench-diagnostic-mcp`: a Node.js MCP server that exposes
probe tools, observe tools, and gated score tools to any MCP client.
The benchmarked model uses probe and observe tools freely. Score tools
are locked behind a harness API key the model never sees.

All tools make AWS SDK calls against LocalStack. No tool interprets
results or surfaces root causes — they return raw signals only.

### B1 — Server scaffold (`harness/mcp_server/`)

`package.json` dependencies:
- `@modelcontextprotocol/sdk` — MCP server protocol
- `@aws-sdk/client-cloudformation`, `@aws-sdk/client-lambda`, `@aws-sdk/client-dynamodb`,
  `@aws-sdk/client-dynamodb-streams`, `@aws-sdk/client-sqs`, `@aws-sdk/client-iam`,
  `@aws-sdk/client-cloudwatch-logs`, `@aws-sdk/client-cloudwatch`, `@aws-sdk/client-s3`,
  `@aws-sdk/client-s3-control`, `@aws-sdk/client-sns`, `@aws-sdk/client-eventbridge`,
  `@aws-sdk/client-scheduler`, `@aws-sdk/client-sfn`, `@aws-sdk/client-swf`,
  `@aws-sdk/client-ses`, `@aws-sdk/client-ec2`, `@aws-sdk/client-route-53`,
  `@aws-sdk/client-route53resolver`, `@aws-sdk/client-kinesis`, `@aws-sdk/client-firehose`,
  `@aws-sdk/client-kms`, `@aws-sdk/client-secrets-manager`, `@aws-sdk/client-sts`,
  `@aws-sdk/client-ssm`, `@aws-sdk/util-dynamodb`

All AWS clients share one config object:

```js
const awsConfig = {
  endpoint: process.env.LOCALSTACK_ENDPOINT ?? "http://localhost:4566",
  region: "us-east-1",
  credentials: { accessKeyId: "test", secretAccessKey: "test" }
}
```

`index.js` — imports tool definitions from `tools/probe.js`,
`tools/probe_extended.js`, `tools/observe.js`,
`tools/observe_extended.js`, `tools/score.js`, spreads all five
arrays into a single loop that registers each tool with the MCP
server, then starts stdio transport.

Every tool call must:
1. Validate required parameters before calling AWS — return a
   structured error if missing, not an uncaught exception
2. Catch all AWS SDK errors and return them as structured error
   responses, never crash the server process
3. Log the tool name and timestamp to stderr for the harness
   interceptor (Phase C reads this stream to build tool_call_trace)

### B2 — Probe tools (`tools/probe.js`)

Six tools. Each makes one or more AWS SDK calls and returns a raw
result. No interpretation, no error diagnosis in the response.

```
ace_invoke_endpoint(path, method, payload)
  Implementation: HTTP fetch to the API Gateway invoke URL.
  The base URL comes from CloudFormation stack outputs
  (key: ApiEndpoint or similar — look it up dynamically).
  Returns: {status_code, latency_ms, body, error_type | null}
  error_type: populate only if the HTTP status indicates an error.

ace_invoke_lambda(function_name, payload)
  Implementation: LambdaClient.send(new InvokeCommand(...))
  InvocationType: "RequestResponse"
  Returns: {status_code, response_body, error_type | null,
            duration_ms, billed_duration_ms}
  error_type: populate from FunctionError field if present.
  response_body: base64-decode the Payload field.

ace_check_queue_depth(queue_name)
  Implementation: SQSClient GetQueueAttributes
  Attributes: ApproximateNumberOfMessages,
              ApproximateNumberOfMessagesNotVisible,
              ApproximateAgeOfOldestMessage
  Returns: {messages_available, messages_in_flight,
            oldest_message_age_seconds}

ace_read_table_item(table_name, key)
  Implementation: DynamoDBClient GetItem
  key is a plain object {pk_name: pk_value} — tool must convert to
  DynamoDB attribute value format internally.
  Returns: {item | null, consumed_read_capacity}
  item: unmarshalled back to plain JS object before returning.

ace_check_event_source(function_name)
  Implementation: LambdaClient ListEventSourceMappings
  Filter by FunctionName.
  Returns: [{source_arn, source_type, enabled, batch_size, state}]
  Empty list if no mappings exist — not an error.

ace_check_s3_object(bucket, key)
  Implementation: S3Client HeadObject
  Returns: {exists, size_bytes, last_modified | null}
  If object does not exist (404), return {exists: false} — not an error.
```

### B3 — Observe tools (`tools/observe.js`)

Six tools. Return infrastructure configuration as-is. No diff
against known-good, no highlighting of anomalies.

```
ace_describe_resource(logical_resource_id)
  Implementation: CloudFormationClient DescribeStackResource
  Then call the appropriate service API to get full resource config
  (e.g. if resource is Lambda::Function, call GetFunction).
  Returns: {resource_type, physical_id, properties, status}
  properties: the full resource configuration from the service API.

ace_list_resources(resource_type | null)
  Implementation: CloudFormationClient ListStackResources
  If resource_type provided, filter results to that type.
  Returns: [{logical_id, physical_id, resource_type, status}]

ace_get_iam_role(role_name)
  Implementation: IAMClient GetRole + ListRolePolicies +
  ListAttachedRolePolicies + GetRolePolicy for each inline policy.
  Returns: {assume_role_policy, attached_policies, inline_policies}
  inline_policies: [{name, document}] with document fully parsed.

ace_get_log_tail(function_name, line_count)
  Implementation: CloudWatchLogsClient DescribeLogGroups to find
  the log group, then GetLogEvents on the most recent log stream.
  Returns: [{timestamp, request_id, level, message}]
  Parse Lambda log line format to extract request_id and level.
  Return at most line_count entries, most recent first.

ace_get_stack_outputs()
  Implementation: CloudFormationClient DescribeStacks
  Returns: {output_key: output_value} for all stack outputs.
  Flat dict — not the raw Outputs array.

ace_get_environment_variables(function_name)
  Implementation: LambdaClient GetFunctionConfiguration
  Returns: {key: value} from Environment.Variables.
  Empty dict if no environment variables set.
```

### B2b — Extended probe tools (`tools/probe_extended.js`)

Nineteen additional probe tools covering services beyond the core set.
Each follows the same pattern as B2: structured input, raw AWS SDK
response, `{error, error_type}` on failure. All share a single
`awsConfig` identical to B1.

```
ace_publish_sns(topic_arn, message, subject?)             → {message_id, sequence_number}
ace_put_events(bus_name, source, detail_type, detail?)    → {failed_entry_count, entries[]}
ace_start_execution(state_machine_arn, input?)            → {execution_arn, status, output, error, cause}
ace_count_open_executions(domain)                         → {count, truncated}
ace_send_test_email(from, to, subject, body?)             → {message_id}
ace_check_instance_state(instance_id)                     → {state, instance_type, public_ip, private_ip}
ace_check_hosted_zone(hosted_zone_id)                     → {id, name, record_count, private_zone}
ace_list_resolver_endpoints(direction?)                   → [{id, name, direction, status}]
ace_put_kinesis_record(stream_name, data, partition_key)  → {shard_id, sequence_number}
ace_put_firehose_record(delivery_stream_name, data)       → {record_id, encrypted}
ace_get_stream_records(stream_arn)                        → {records[], shard_count}
ace_encrypt_decrypt(key_id, plaintext)                    → {decrypted, matches, key_id}
ace_get_secret(secret_id, version_stage?)                 → {name, arn, secret_string}
ace_get_caller_identity()                                 → {account, user_id, arn}
ace_assume_role(role_arn, session_name)                   → {access_key_id, expiration, assumed_role_arn}
ace_get_parameter(name, with_decryption?)                 → {name, type, value, version}
ace_list_access_points(account_id, bucket?)               → [{name, arn, bucket, network_origin}]
ace_put_metric_data(namespace, metric_name, value, unit?) → {success, namespace, metric_name, value}
ace_simulate_policy(policy_source_arn, action_names, resource_arns?) → [{action, resource, decision}]
```

### B3b — Extended observe tools (`tools/observe_extended.js`)

Seventeen additional observe tools. Same pattern as B3.

```
ace_get_sns_topic(topic_arn)                 → {arn, subscriptions_confirmed, subscriptions_pending, policy}
ace_get_eventbridge_rule(rule_name, bus?)    → {name, arn, state, schedule_expression, event_pattern, targets[]}
ace_get_schedule(name, group_name?)          → {name, arn, state, schedule_expression, target_arn}
ace_describe_state_machine(state_machine_arn) → {name, arn, status, type, role_arn, state_count}
ace_describe_swf_domain(domain)             → {name, status, retention_period_days}
ace_get_ses_identity(identities[])          → {identity: {verification_status, verification_token}}
ace_describe_security_group(group_id)       → {group_id, group_name, vpc_id, inbound_rules[], outbound_rules[]}
ace_list_dns_records(hosted_zone_id, type?) → [{name, type, ttl, values[], alias_target}]
ace_get_resolver_endpoint(resolver_endpoint_id) → {id, name, direction, status, ip_address_count}
ace_describe_kinesis_stream(stream_name)    → {stream_arn, stream_status, shard_count, retention_period_hours}
ace_describe_firehose_stream(delivery_stream_name) → {arn, status, type, destinations[], encryption_status}
ace_describe_dynamo_stream(stream_arn)      → {stream_arn, table_name, stream_status, view_type, shards[]}
ace_describe_kms_key(key_id)               → {key_id, arn, state, key_usage, key_spec, rotation_enabled}
ace_describe_secret(secret_id)             → {name, arn, rotation_enabled, rotation_lambda_arn, tags}
ace_describe_parameters(path_prefix?, parameter_type?) → [{name, type, version, tier}]
ace_get_public_access_block(account_id)    → {block_public_acls, ignore_public_acls, ...}
ace_get_metric_statistics(namespace, metric_name, period?, statistics?, ...) → {label, datapoints[]}
```

### B4 — Score tools (`tools/score.js`)

Two tools, gated by `HARNESS_API_KEY`. Any call that does not
include `harness_api_key` matching the environment variable returns:

```json
{"error": "unauthorized", "message": "score tools require harness_api_key"}
```

These tools are called by the harness verify loop (Phase D), never
by the model. Their implementation is defined in Phase D — for now,
stub them to return `{"status": "not_implemented"}` so the server
starts cleanly.

```
ace_verify_fix(run_id, harness_api_key)
  Stub for now. Phase D implements the logic.

ace_score_run(run_id, harness_api_key)
  Stub for now. Phase D implements the logic.
```

### B5 — MCP server usage

The inline agent runner (Phase G) spawns the MCP server automatically
as a stdio subprocess — no manual registration is needed. The harness
passes `HARNESS_API_KEY` and `LOCALSTACK_ENDPOINT` as environment
variables to the subprocess.

Generate a `HARNESS_API_KEY` and store it in `.env` at the project root:

```bash
echo "HARNESS_API_KEY=$(openssl rand -hex 32)" >> .env
```

The harness Python code reads this via `python-dotenv`. Never commit `.env`.

### B — Verification

Write `tests/test_mcp_server.js` using Node's built-in test runner
(`node --test`). LocalStack must be running before tests start.

**Fixtures created in `before()` hook:**
- Lambda function (identity stub), DynamoDB table, SQS queue,
  CloudFormation stack (core fixtures)
- SNS topic, Kinesis stream, KMS key, Secrets Manager secret,
  SSM parameter (extended fixtures)

**Test coverage (76 tests):**
- Core probe tools (6): response shape + missing-arg error for each
- Core observe tools (6): response shape + error conditions
- Score tools (2): unauthorized without key, unauthorized with wrong key
- Extended probe tools (19): success path + missing-arg/not-found error
- Extended observe tools (17): success path + missing-arg/not-found error
- Smoke test: `probeExtendedTools` and `observeExtendedTools` are arrays

All tools must return structured responses, never throw uncaught
exceptions, for all test cases including error conditions (resource
not found, wrong types, empty results).

---

## Phase C — Scenario Runner and Deployment Handler

**Depends on:** Phase A, Phase B (MCP server must be registered)
**Blocks:** Phase D (verify loop needs the runner to have completed a run)
**Session scope:** complete in one session

### Goal
Build the components that present a scenario to the model, monitor
what it does, intercept its redeployment call, run packaging
pre-flight, and hand off to the verify loop. This phase owns the
boundary between the model's free-form activity and the harness's
controlled evaluation.

### C1 — Context builder (`harness/runner/context_builder.py`)

Assembles everything the model receives at the start of a run.
The model is given exactly what Step 3 specifies and nothing else.

`build_context(scenario_dir: str) -> dict` — reads:
- `scenario.md` — the business brief
- `faulted.yaml` — the CloudFormation template with fault injected
- `deployment/` — the full supporting file tree
- Stack outputs from LocalStack (the scenario must already be deployed
  before the runner starts — see C3)

Returns a context dict:
```python
{
  "scenario_brief": str,          # contents of scenario.md
  "template_path": str,           # absolute path to faulted.yaml
  "deployment_dir": str,          # absolute path to deployment/
  "stack_outputs": dict,          # from ace_get_stack_outputs()
  "instruction": str              # fixed instruction text (see below)
}
```

Fixed instruction text (verbatim, do not vary between scenarios):
```
A deployed instance of this system is running in your local
environment. The deployment directory and CloudFormation template
are available to you directly. Diagnostic tools are available via
MCP. Diagnose the reported symptom, edit whatever files need
changing, and redeploy using localstack-deployer when ready.
Your first successful redeployment is your scored submission.
```

`build_context` must raise `ValueError` if `fault_manifest.json`
is readable from `scenario_dir` — it must not be present in the
path the model can access. If it exists where the model can reach
it, stop and raise before handing context to the model.

### C2 — Tool call interceptor (`harness/runner/scenario_runner.py`)

The harness needs to observe every MCP tool call the model makes
without the model being aware of the observation. This is implemented
by tailing the MCP server's stderr stream (which logs tool name and
timestamp per B3) and writing each entry to `tool_call_trace.json`
via `result_logger.log_tool_call`.

`ScenarioRunner` class:

`__init__(self, scenario_dir, run_id)` — validates pre-conditions,
initialises the MCP server stderr tail, takes a directory snapshot
via `file_differ.snapshot(deployment_dir)` and stores it as
`self.start_snapshot`. This is the baseline for computing what the
model changed.

`start(self)` — deploys `faulted.yaml` to LocalStack via
`localstack-deployer` (create-stack if no stack exists, or
teardown-and-recreate for a clean state). Waits for
CREATE_COMPLETE. Raises if deployment fails — a faulted template
that won't deploy is a broken scenario, not a model failure.

`intercept_tool_call(self, tool_name, input, output)` — called
by the stderr tail on each tool call event. Increments
`self.tool_call_count`, writes to trace log via result_logger.

`on_model_redeploy(self)` — called when the harness detects the
model has triggered `localstack-deployer`. This is the submission
boundary. Calls `deployment_handler.handle_submission()` and
returns its result. After this returns, the model may not make
further edits or deployments.

How to detect the model's redeployment trigger: monitor the
`localstack-deployer` MCP tool invocations on the stderr stream.
When a `localstack-deployer update-stack` call is detected, treat
it as the submission event.

`self.submitted` flag — set to True the first time
`on_model_redeploy` is called. If a second redeployment is
attempted while `submitted` is True, the deployment handler
returns an error response to the model without allowing the
deployment to proceed.

### C3 — Deployment handler (`harness/runner/deployment_handler.py`)

Handles everything between the model triggering redeployment and
the verify loop starting.

`handle_submission(scenario_dir, run_id, start_snapshot) -> dict`

Step 1 — Take end snapshot:
```python
end_snapshot = file_differ.snapshot(deployment_dir)
diff = file_differ.diff_snapshots(start_snapshot, end_snapshot, deployment_dir)
result_logger.log_file_change(run_id, diff)
```

Step 2 — Run cfn-lint on the (possibly modified) template:
```python
lint_result = cfn_lint_runner.run_lint(template_path)
if not lint_result["passed"]:
    return {"outcome": "lint_fail", "errors": lint_result["fatal_errors"]}
```

Step 3 — Packaging pre-flight:
Walk `diff["files_modified"] + diff["files_added"]` for any files
under `deployment/lambda/`. For each changed Lambda handler:
  a. Zip the handler file into `deployment/lambda/[function_name].zip`
  b. Upload the zip to the LocalStack S3 artifact bucket
     (`ace-bench-artifacts`) using `s3_client`
  c. Update the `S3Key` reference in the template YAML in memory
     (do not write back to disk — pass the patched template body
     as a string to the CloudFormation update call)

If no Lambda files changed, skip packaging. Template body is read
from disk as-is.

Step 4 — CloudFormation update-stack:
```python
cf_client.update_stack(
    StackName="ace-bench-stack",
    TemplateBody=template_body,  # possibly packaging-patched
    Capabilities=["CAPABILITY_IAM", "CAPABILITY_NAMED_IAM"]
)
```
Poll for UPDATE_COMPLETE or UPDATE_ROLLBACK_COMPLETE.
If rollback: return `{"outcome": "deploy_fail", "events": [...]}`
where events are the stack events with ResourceStatusReason populated.

Step 5 — On UPDATE_COMPLETE:
Return `{"outcome": "deploy_success"}` and signal the verify loop
to begin.

### C — Verification

Write `tests/test_runner.py`:

- Mock `localstack-deployer` and `localstack_client` to avoid
  needing a live LocalStack for unit tests
- Test that `build_context` raises `ValueError` when
  `fault_manifest.json` is present in the scenario directory
  at the model-accessible path
- Test that the `submitted` flag prevents a second redeployment
- Test that `handle_submission` returns `lint_fail` when cfn-lint
  returns fatal errors
- Test the packaging pre-flight: given a diff that includes a
  modified Lambda handler, confirm a zip is created and the S3
  upload is called with the correct bucket and key
- Test that a deploy failure (mocked UPDATE_ROLLBACK_COMPLETE)
  returns `deploy_fail` with events populated

---

## Phase D — Verify Loop

**Depends on:** Phase A, Phase C (needs a completed run with
`verify_result.json` path ready to write)
**Blocks:** Phase E (entry point calls verify loop)
**Session scope:** complete in one session — all four passes and the
orchestrating verify_loop.py together

### Goal
After the model's fix is deployed, run four sequential verification
passes and write a structured result. This phase is fully automated —
no human in the loop. The result feeds scoring (Step 7, deferred)
but is written completely regardless.

### D1 — Verify loop orchestrator (`harness/verify/verify_loop.py`)

`run_verify_loop(scenario_dir, run_id) -> dict`

Calls the four passes in order. Each pass receives the scenario_dir
and returns a structured dict. Aggregates results and writes to
`results/[run_id]/verify_result.json` via `result_logger`.

Orchestration logic:
- Pass 1 always runs
- Pass 2 always runs (even if Pass 1 fails — regression check
  needs to know what was previously passing)
- Pass 3 always runs
- Pass 4 runs only if `fault_manifest["fault_class"]` is
  `"performance"` or `"reliability"`
- If UPDATE_COMPLETE was not reached (lint_fail or deploy_fail),
  write a verify_result with all passes skipped and
  `outcome: "did_not_deploy"`

Final verify_result structure:
```python
{
  "outcome": "did_not_deploy" | "completed",
  "pass1_functional": {...},    # from D2
  "pass2_regression": {...},    # from D3
  "pass3_classification": {...}, # from D4
  "pass4_concurrency": {...} | None,  # from D5, None if skipped
}
```

### D2 — Pass 1: Functional correctness (`harness/verify/pass1_functional.py`)

`run_pass1(scenario_dir) -> dict`

Run `functional_test.py` from the corpus directory for this
architecture as a subprocess. Capture stdout and stderr. Parse
the test output to extract per-assertion results.

`functional_test.py` is written to produce one result line per
assertion in the format:
```
ASSERT [pass|fail] [assertion_name]: [optional message]
```

Parse these lines to produce:
```python
{
  "assertions": {
    "assertion_name": {"result": "pass" | "fail", "message": str}
  },
  "primary_assertions_passed": bool,
  "all_assertions_passed": bool,
  "failed_assertion_names": [str]
}
```

Primary assertions are those whose names do not contain `_secondary`
suffix (a convention established during corpus authoring in Step 1).
`primary_assertions_passed` is True only if all non-secondary
assertions pass.

Store `failed_assertion_names` for use by Pass 2.

### D3 — Pass 2: Regression check (`harness/verify/pass2_regression.py`)

`run_pass2(scenario_dir, run_id, pass1_result) -> dict`

Determines which assertions newly failed that were previously passing
on the faulted deployment.

The harness captures a baseline assertion result at scenario start
(when the faulted.yaml is deployed in C3). This baseline is stored
in `results/[run_id]/faulted_baseline.json` by the runner. It has
the same structure as the pass1 assertions dict.

Load `faulted_baseline.json` and compare against `pass1_result`:

```python
regressions = []
for assertion_name, baseline in faulted_baseline["assertions"].items():
    if baseline["result"] == "pass":  # was passing on faulted
        current = pass1_result["assertions"].get(assertion_name)
        if current and current["result"] == "fail":
            regressions.append({
                "assertion": assertion_name,
                "severity": "critical" if "_secondary" not in assertion_name
                             else "non_critical"
            })
```

Returns:
```python
{
  "regression_count": int,
  "regressions": [{assertion, severity}],
  "critical_regression_count": int,
  "non_critical_regression_count": int
}
```

Note: the runner must write `faulted_baseline.json` during C3
`start()`, immediately after `faulted.yaml` is deployed and before
the model is given context. Run `functional_test.py` at that point
and write the result to `results/[run_id]/faulted_baseline.json`.
Add this to Phase C implementation.

### D4 — Pass 3: Fix classification (`harness/verify/pass3_classification.py`)

`run_pass3(scenario_dir, run_id) -> dict`

Two signals determine the fix classification.

**Signal 1 — Structural diff:**
Load `fault_manifest.json`. Compare the model's submitted template
against `faulted.yaml` at the path `target_resource.target_property`.

Use PyYAML to parse both templates. Navigate to the resource
identified by `target_resource`, then to the property identified
by `target_property` (supports dot-notation and array indexing).

Check if the value at that path in the submitted template matches
`original_value` from the manifest:
```python
structural_match = (submitted_value == manifest["original_value"])
```

**Signal 2 — Semantic classification:**
Load `valid_fixes` and `invalid_patches` from `fault_manifest.json`.

Represent the submitted template changes as a plain-text diff
(the `file_change_log.json` already contains this). Feed the diff
and the `valid_fixes`/`invalid_patches` lists to a classifier.

For the prototype, the classifier is rule-based: check if any
string from `invalid_patches` appears as a substring in the diff
text. If yes, it is a `workaround` or worse. If no invalid patches
appear and `structural_match` is True, classify as `root_cause`.

Classification logic:
```python
if structural_match and no_invalid_patches_in_diff:
    classification = "root_cause"
elif pass1_result["primary_assertions_passed"] and not structural_match:
    classification = "workaround"
elif pass1_result["primary_assertions_passed"] is False:
    # check if any assertions improved
    classification = "partial" if any_improvement else "none"
else:
    classification = "none"
```

Returns:
```python
{
  "structural_match": bool,
  "invalid_patch_detected": bool,
  "classification": "root_cause" | "workaround" | "partial" | "none",
  "root_cause_addressed": bool  # True only for root_cause
}
```

### D5 — Pass 4: Concurrency probe (`harness/verify/pass4_concurrency.py`)

`run_pass4(scenario_dir) -> dict`

Only runs for `fault_class` in `["performance", "reliability"]`.
Sends N concurrent requests to the API Gateway endpoint and checks
for throttling or timeout responses.

N is read from `fault_manifest.json` field `concurrency_probe_n`
(add this field to the manifest spec). Default: 10 if not present.

Use Python's `concurrent.futures.ThreadPoolExecutor` to send all N
requests simultaneously via `requests.post` to the stack's API
endpoint (from stack outputs).

Classify each response:
- 200 → success
- 429 → throttled
- 504 → timeout
- other → error

Returns:
```python
{
  "requests_sent": int,
  "success_count": int,
  "throttled_count": int,
  "timeout_count": int,
  "error_count": int,
  "passed": bool  # True if throttled_count == 0 and timeout_count == 0
}
```

If `passed` is False and Pass 1 passed, override Pass 3
classification to `"partial"` in the orchestrator. A fix that
passes single-invocation tests but fails under concurrency is
not a root cause fix.

### D — Verification

Write `tests/test_verify.py`:

- Pass 1: mock `functional_test.py` subprocess output with known
  pass/fail lines, assert the assertions dict is parsed correctly,
  assert `primary_assertions_passed` and `all_assertions_passed`
  behave correctly with secondary assertions in the mix
- Pass 2: construct a hand-crafted `faulted_baseline.json` and
  `pass1_result` where specific assertions flipped from pass to
  fail, assert `regressions` contains exactly those assertions
  with correct severity
- Pass 3: construct a hand-crafted fault_manifest and two YAML
  templates (one matching original_value, one not), assert
  `root_cause` and `workaround` classifications are assigned
  correctly; also test that an invalid_patch string in the diff
  forces `workaround` even when structural_match is True
- Pass 4: mock `requests.post` to return mixed 200/429/504
  responses, assert counts and `passed` flag are correct
- Orchestrator: test that Pass 4 is skipped for non-performance/
  reliability fault classes, and that a Pass 4 failure overrides
  Pass 3 classification to `partial`

---

## Phase E — Entry Point

**Depends on:** Phases A, B, C, D
**Blocks:** nothing
**Session scope:** complete in one session

### Goal
Build `harness/run.py` — the single command that ties the harness
together. Takes a scenario directory as its argument and runs a
complete evaluation loop from context delivery through verify result.

### E1 — `harness/run.py`

```
Usage: python harness/run.py <scenario_dir> [--run-id <id>]
       [--model PROVIDER/MODEL] [--api-key KEY] [--base-url URL]
```

If `--run-id` is not provided, generate one: `uuid4()[:8]`.

Sequence:

1. Load environment from `.env` (HARNESS_API_KEY)
2. `localstack_client.health_check()` — fail fast if LocalStack
   is not running
3. Validate `scenario_dir` contains required files:
   `scenario.md`, `faulted.yaml`, `fault_manifest.json`,
   `deployment/`
4. Validate `fault_manifest.json` is not readable from any path
   the model can access (enforce this by checking the scenario_dir
   structure)
5. `result_logger.init_run(run_id, scenario_id)`
6. `runner = ScenarioRunner(scenario_dir, run_id)`
7. `runner.start()` — deploy faulted.yaml, capture baseline
   assertions, write `faulted_baseline.json`
8. `context = context_builder.build_context(scenario_dir)`
9. Print the context to stdout.
10. If `--model` is set, start the inline agent runner in a
    daemon thread (Phase G). The agent spawns the MCP server,
    drives the model, and writes the signal file on `submit_fix`.
11. Wait for the signal file to appear. Block until
    `runner.submitted` is True or a timeout (default: 30 minutes)
    is reached.
12. If timeout or agent crash: write `verify_result.json` with
    `outcome: "timed_out"` or `"agent_error"` and exit.
13. `verify_result = verify_loop.run_verify_loop(scenario_dir, run_id)`
14. `score_result = scorer.score_run(run_id, base_dir)`
15. Print a human-readable summary of the verify and score results.
16. Exit 0 if `verify_result["outcome"] == "completed"`, exit 1
    otherwise.

### E2 — Human-readable summary format

After the verify loop completes, print to stdout:

```
═══════════════════════════════════════
ACE-Bench Run: [run_id]
Scenario: [scenario_id]
═══════════════════════════════════════

Deployment:       [PASS | FAIL]
Functional test:  [PASS | PARTIAL | FAIL]
Regressions:      [none | N critical, M non-critical]
Classification:   [root_cause | workaround | partial | none]
Concurrency:      [PASS | FAIL | SKIPPED]

Tool calls made:  [N]
Files changed:    [N]
Lines changed:    [N]

Full results:     results/[run_id]/
═══════════════════════════════════════
```

### E — Verification

End-to-end integration test using one real scenario from the corpus:

```bash
# Start LocalStack
localstack start -d

# Run one scenario end-to-end with a stub model
# The stub model makes a known set of tool calls and then
# applies the known correct fix, triggering redeployment
python harness/run.py scenarios/arch01_fault01_security/ --run-id e2e-test

# Assert exit code 0
# Assert results/e2e-test/verify_result.json exists and is valid JSON
# Assert classification is root_cause
# Assert regression_count is 0
```

The stub model is a script that calls three specific MCP diagnostic
tools in sequence, applies the correct fix from the fault manifest,
and calls `localstack-deployer update-stack`. It does not involve
a real LLM — it exists only to verify the harness runs end-to-end
without errors.

---

## Phase F — Step 7 Scoring

**Depends on:** Phase A (shared utilities), Phases C/D outputs on disk
**Blocks:** nothing
**Session scope:** complete in one session

### Goal

An autonomous scoring agent powered by Claude Sonnet that evaluates
every completed run in `results/`. The agent reads the artifacts
produced by Phases A–E, reasons across five scoring dimensions, and
writes a structured `score.json` per run. Called by `harness/run.py`
as the final step of the pipeline.

Three dimensions require judgment (identification, quality, efficiency
rationale); two are purely deterministic (fix correctness, regression
penalty). This separation is explicit per module.

### Deliverables

```
harness/
└── scoring/
    ├── agent.py              # F1 — Claude Sonnet client
    ├── scorer.py             # F2 — orchestrator
    ├── dimensions/
    │   ├── identification.py # F3 — agent-evaluated
    │   ├── fix_correctness.py # F4 — deterministic
    │   ├── regression.py     # F5 — deterministic
    │   ├── efficiency.py     # F6 — formula + agent rationale
    │   └── quality.py        # F7 — agent-evaluated + gate
    └── gate.py               # F8 — thin re-export of check_gate

results/[run_id]/
└── score.json
```

`harness/run.py` is updated (F9) to call `scorer.score_run()` after
the verify loop and extend the terminal summary with scoring output.

### F1 — `harness/scoring/agent.py`

```python
import anthropic

client = anthropic.Anthropic()
SCORING_MODEL = "claude-sonnet-4-5"

def call_scoring_agent(system_prompt: str, user_prompt: str) -> str:
    message = client.messages.create(
        model=SCORING_MODEL,
        max_tokens=1024,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}]
    )
    return message.content[0].text.strip()
```

System prompt used for all calls:
```
You are an autonomous infrastructure debugging benchmark scorer.
You evaluate AI model runs against a known-good AWS architecture.
You will be given the fault injected, the model's tool-call trace,
file changes, and verify loop result.
Return ONLY valid JSON matching the schema in each prompt.
No explanation outside the JSON. No markdown fences.
Every numeric score must be a float between 0.0 and 1.0.
Every rationale field must be 1–2 sentences explaining the score.
```

### F2 — `harness/scoring/scorer.py`

`score_run(run_id: str, scenario_dir: str) -> dict`

Execution order:
1. Load `verify_result.json`, `tool_call_trace.json`, `file_change_log.json`,
   `fault_manifest.json`, `faulted.yaml`, `known_good.yaml`. Missing file →
   write `score.json` with `final_score: 0.0, zero_reason: "missing_artifacts"`.
2. If `verify_result["outcome"] != "completed"` → write zero score with
   `zero_reason: verify_result["outcome"]`. Return — no agent calls.
3. `gate.check_quality_gate(verify_result)` → if False, write
   `final_score: 0.0, quality_threshold_met: false, zero_reason: "quality_gate_failed"`.
4. Score all five dimensions:
   ```python
   d1 = identification.score(tool_trace, manifest, verify_result)
   d2 = fix_correctness.score(verify_result)
   d3 = regression.compute(verify_result)
   d4 = efficiency.score(tool_trace, file_log, manifest)
   d5 = quality.score(verify_result, manifest, file_log)
   ```
5. Composite formula:
   ```python
   weighted  = (d1["score"] * 0.20) + (d2["score"] * 0.25) \
             + (d4["score"] * 0.15) + (d5["score"] * 0.40)
   composite = max(0.0, round(weighted - d3["penalty"], 4))
   ```
6. Interpret composite: ≥0.90 root-cause + clean; ≥0.75 minor concern;
   ≥0.50 workaround or regressions; ≥0.25 partial; else failed.
7. Write `results/[run_id]/score.json`.

`score.json` schema:
```json
{
  "run_id": "...", "scenario_id": "...", "scored_by": "claude-sonnet-4-5",
  "quality_threshold_met": true, "zero_reason": null,
  "dimensions": {
    "identification":     {"score": 0.0, "rationale": "..."},
    "fix_correctness":    {"score": 0.0, "rationale": "..."},
    "regression_penalty": {"penalty": 0.0, "rationale": "..."},
    "efficiency": {
      "score": 0.0, "rationale": "...",
      "tool_calls":    {"actual": 0, "optimal": 0, "ratio": 0.0, "score": 0.0},
      "files_changed": {"actual": 0, "optimal": 0, "ratio": 0.0, "score": 0.0},
      "lines_changed": {"actual": 0, "optimal": 0, "ratio": 0.0, "score": 0.0}
    },
    "quality": {"score": 0.0, "classification": "...", "rationale": "..."}
  },
  "weighted": 0.0, "composite": 0.0, "final_score": 0.0, "interpretation": "..."
}
```

### F3 — `harness/scoring/dimensions/identification.py`

**Dimension 1 — Issue Identification (weight: 0.20) — Claude Sonnet**

`score(tool_trace, manifest, verify_result) -> dict`

Prompt gives agent the tool-call trace, injected fault definition, and
fix classification. Rubric: 1.0 if correct resource+property surfaced
before fix; 0.5 if resource correct but property wrong or late; 0.3 if
traces show no clear identification (possible guess); 0.1 if wrong
resource; 0.0 if no traceable diagnosis.

Returns: `{"score": float, "rationale": str}`

### F4 — `harness/scoring/dimensions/fix_correctness.py`

**Dimension 2 — Fix Correctness (weight: 0.25) — deterministic**

`score(verify_result) -> dict`

```python
p1 = verify_result["pass1_functional"]
if p1["all_assertions_passed"]:           s = 1.0
elif p1["primary_assertions_passed"]:     s = 0.6
elif len(p1["failed_assertion_names"]) < len(p1["assertions"]): s = 0.3
else:                                     s = 0.0
```

Returns: `{"score": float, "rationale": str}`

### F5 — `harness/scoring/dimensions/regression.py`

**Dimension 3 — Regression Penalty (subtracted from composite) — deterministic**

`compute(verify_result) -> dict`

```python
p2 = verify_result["pass2_regression"]
critical, non_critical = p2["critical_regression_count"], p2["non_critical_regression_count"]
if critical > 1 or (critical >= 1 and non_critical >= 1): penalty = 0.28
elif critical == 1:                                        penalty = 0.18
elif non_critical == 1:                                    penalty = 0.08
else:                                                      penalty = 0.00
```

Returns: `{"penalty": float, "rationale": str}`

### F6 — `harness/scoring/dimensions/efficiency.py`

**Dimension 4 — Efficiency (weight: 0.15) — formula + agent rationale**

`score(tool_trace, file_log, manifest) -> dict`

Sub-score formula for all three signals:
```python
def threshold_score(actual, optimal):
    if optimal == 0: return 1.0 if actual == 0 else 0.0
    ratio = actual / optimal
    if ratio <= 1.5:   return 1.0
    elif ratio <= 2.5: return 1.0 - 0.4 * (ratio - 1.5)
    elif ratio <= 4.0: return 0.6 - 0.4 * (ratio - 2.5)
    else:              return 0.0
```

Combined: `(tc_score * 0.50) + (fc_score * 0.25) + (lc_score * 0.25)`

After computing sub-scores, calls `call_scoring_agent` once to generate
the `rationale` field explaining what the scores reveal about diagnostic
efficiency. Agent receives actuals, optimals, ratios, and tool name list.

Returns: full dict with score, rationale, and per-signal breakdown.

### F7 — `harness/scoring/dimensions/quality.py`

**Dimension 5 — Fix Quality (weight: 0.40, dominant) — Claude Sonnet**

Contains both the quality gate check and the quality score.

`check_gate(verify_result) -> bool`
```python
classification_ok = p3["classification"] in ("root_cause", "workaround")
assertions_ok     = p1["primary_assertions_passed"]
no_regressions    = p2["regression_count"] == 0
return classification_ok and assertions_ok and no_regressions
```

`score(verify_result, manifest, file_log) -> dict`

Prompt gives agent fault definition, valid_fixes, invalid_patches,
files changed, line-level changes, and verify loop signals. Rubric:
1.00 root cause + clean; 0.85 root cause + minor concern; 0.60
over-permissive fix; 0.35 workaround; 0.15 partial; 0.00 none.

Returns: `{"score": float, "classification": str, "rationale": str}`

### F8 — `harness/scoring/gate.py`

```python
from harness.scoring.dimensions.quality import check_gate
```

Thin re-export so scorer imports the gate from one place.

### F9 — Update `harness/run.py`

After the verify loop call, add:
```python
from harness.scoring.scorer import score_run
# ...
print("[scorer] Running Step 7 scoring agent (Claude Sonnet)...")
score = score_run(run_id, scenario_dir)
```

Extend terminal summary with scoring block:
```
── Scoring (Claude Sonnet) ─────────────
Quality gate:     [PASS | FAIL → score zeroed]
Identification:   [0.00]  [rationale]
Fix correctness:  [0.00]  [rationale]
Regression:      −[0.00]  [rationale]
Efficiency:       [0.00]  [rationale]
Quality:          [0.00]  [rationale]
────────────────────────────────────────
Final score:      [0.0000]
Interpretation:   [interpretation string]
────────────────────────────────────────
```

### F — Verification

Write `tests/test_scoring.py`:

- **F1:** mock `anthropic.Anthropic().messages.create`; assert text content
  returned; assert raises clearly on API failure.
- **F3:** mock `call_scoring_agent`; assert prompt differs between a trace
  that includes `ace_get_iam_role` and one that does not; assert score/rationale
  extracted correctly.
- **F4:** four hand-crafted `verify_result` dicts covering 1.0, 0.6, 0.3, 0.0
  outcomes; no agent call.
- **F5:** verify_results with 0, 1 non-critical, 1 critical, multiple
  regressions; assert penalties 0.00, 0.08, 0.18, 0.28.
- **F6:** test `threshold_score` at ratios 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0;
  test combined score with known inputs; mock rationale call.
- **F7 gate:** each gate failure mode returns False; clean root_cause returns True.
- **F7 score:** mock agent for each of the six quality classifications; assert
  score extracted and classification populated.
- **F2 integration:** mock all dimension modules; run `score_run` with
  hand-crafted inputs; assert `score.json` written with correct structure and
  composite formula applied; assert early-exit paths (did_not_deploy, gate
  failure) write zero-score JSON and make no agent calls.

---

## Phase G — Inline Agent Runner

**Depends on:** Phase A (shared utilities), Phase B (MCP server), Phase E (entry point), Phase F (scoring)
**Blocks:** nothing
**Session scope:** complete in one session

### Goal

Enable the harness to drive any LLM through a scenario end-to-end without requiring
Claude Code or an external model script. The `--model`, `--api-key`, and `--base-url`
flags on `run.py` activate an inline agent that spawns the MCP server as a subprocess,
discovers tools at runtime, and runs the model through the scenario using LiteLLM as
the universal LLM adapter.

### Architecture

A new `harness/agent/` package contains all agent logic. LiteLLM normalizes every
provider's API to the OpenAI format and converts OpenAI-format tool definitions into
each provider's native format. Tools are defined once in OpenAI function-calling format:
the MCP probe/observe tools (converted from MCP schema at runtime), Python-native file
tools (`read_file`, `write_file`, `list_directory` scoped to `deployment/` and
`faulted.yaml`), and a `submit_fix` tool that writes the redeployment signal file.

### G1 — `harness/agent/tools.py`

Owns all tool definitions and dispatch:

- `mcp_to_openai_tool(mcp_tool) -> dict` — converts MCP tool object to OpenAI format
- `filter_model_tools(tools) -> list[dict]` — removes `ace_verify_fix` and `ace_score_run`
- `FILE_TOOL_DEFINITIONS` — OpenAI-format defs for `read_file`, `write_file`, `list_directory`, `submit_fix`
- `dispatch_file_tool(name, inputs, scenario_dir) -> str` — synchronous dispatcher with:
  - Path traversal prevention via `resolve()` + `relative_to()`
  - `fault_manifest.json` and `known_good.yaml` read-blocked
  - `write_file` restricted to `deployment/` prefix and exact `faulted.yaml`
  - `submit_fix` writes `{"trigger": "update-stack"}` to the signal file

### G2 — `harness/agent/loop.py`

Async LiteLLM agent loop:

- `_start_mcp_session(harness_api_key)` — spawns Node.js MCP server via `mcp.client.stdio`
- `_build_system(context)` — constructs system prompt with template path, deployment dir, stack outputs
- `run_agent_loop(model, api_key, base_url, context, scenario_dir, run_id, harness_api_key, max_turns=50) -> bool`:
  - Discovers MCP tools, filters score tools, appends file tool definitions
  - Loops calling `litellm.completion()` up to `max_turns`
  - Dispatches file tools locally, MCP tools via session
  - Logs MCP tool calls (not file tools) via `result_logger.log_tool_call`
  - Exits on `stop`/`end_turn` finish reason, `submit_fix` call, or `max_turns`
  - Returns `True` if `submit_fix` was called

### G3 — `harness/run.py` modifications

Three new CLI arguments: `--model`, `--api-key`, `--base-url`. When `--model` is
provided, the harness validates `HARNESS_API_KEY` is set, then starts `run_agent_loop`
in a daemon thread after printing context. The agent writes the signal file via
`submit_fix`, the existing polling loop detects it, and the deployment/verify/scoring
pipeline proceeds unchanged. Agent thread crashes are caught and fail fast with a
clear error instead of silently timing out.

### G — Verification

Tests in `tests/test_agent_loop.py` (20 tests):

- MCP→OpenAI tool conversion shape
- Score tool filtering (`ace_verify_fix`, `ace_score_run` blocked)
- `read_file`: content return, `fault_manifest.json` blocked, `known_good.yaml` blocked, path traversal blocked
- `write_file`: `deployment/` allowed, `faulted.yaml` allowed, outside blocked, path traversal blocked
- `list_directory`: correct entries
- `submit_fix`: signal file written with correct JSON content
- Unknown tool dispatch returns error
- `FILE_TOOL_DEFINITIONS` OpenAI format validation
- Loop exits on `stop`, `end_turn`, `submit_fix`
- Loop respects `max_turns`
- MCP tool calls logged, file tool calls not logged

---

## Dependency Summary

```
Phase A (shared utilities)
    │
    ├──► Phase B (MCP server)
    │         │
    │    [B verified]
    │         │
    └──► Phase C (runner + deployment handler)
              │
         [C verified]
              │
         Phase D (verify loop)
              │
         [D verified]
              │
         Phase E (entry point + e2e test)
              │
         [E verified]
              │
         Phase F (scoring agent)
              │
         [F verified]
              │
         Phase G (inline agent runner)
```

No phase should begin until all phases it depends on have passed
their verification tests. A phase is not complete until its tests
pass — working code without tests does not count.