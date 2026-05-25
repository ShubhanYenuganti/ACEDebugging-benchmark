# Track A2 — MCP Stack Extended Review: Additional Gaps and Functional Fixes

> Follow-up to `2026-05-15-track-a-mcp-observability.md`. That plan closed two gaps (`filter_criteria`, `ace_scan_table_range`). This document inventories remaining gaps relative to a real-AWS SRE workflow and catalogs functional issues in the currently-shipped 50 tools.

**Scope:** 50 MCP tools across `probe.js` (6), `probe_extended.js` (20), `observe.js` (6), `observe_extended.js` (17), `score.js` (2 stubs).

**Corpus characteristics (from `scenarios/*/faulted.yaml`):** 40 scenarios, 5 fault classes (connectivity, data_correctness, performance, reliability, security), 20 CFN resource types in use. DynamoDB present in 30 scenarios, S3 in 30, ApiGateway in 10, DLQ wiring (RedrivePolicy/DeadLetterConfig) in 20.

---

## Part 1 — Remaining Observability Gaps

Ranked by expected hit rate across the 40-scenario corpus. P0 = unblocks ≥10 scenarios, P1 = unblocks 4–9, P2 = unblocks 1–3 or unblocks rare-but-critical paths.

### P0 — Highest Leverage

#### Gap 1: SQS message-body peek (`ace_peek_queue_messages`)
**Current:** `ace_check_queue_depth` returns counts only. No way to read message bodies.

**Why it matters:** Data_correctness faults at the SQS layer (arch12 family, 8+ scenarios) hinge on the *shape* of the message — wrong field name (`qty` vs `quantity`), wrong type (string vs number), missing field. Without peek, agents reverse-engineer the shape from downstream `KeyError` log lines, which is slow and unreliable.

**Real AWS analog:** `ReceiveMessage` with `VisibilityTimeout=0` + immediate `ChangeMessageVisibility` to release. Standard SRE move.

**Proposed shape:**
```js
{
  name: "ace_peek_queue_messages",
  description: "Read up to N messages from an SQS queue without consuming them (visibility timeout 0). Returns message bodies and attributes.",
  inputSchema: {
    type: "object",
    properties: {
      queue_name: { type: "string" },
      max_messages: { type: "number" },     // default 5, max 10
    },
    required: ["queue_name"],
  },
  // handler: ReceiveMessage with VisibilityTimeout=0, WaitTimeSeconds=0, MessageAttributeNames=["All"]
}
```

#### Gap 2: S3 GetObject content (`ace_get_s3_object_content`)
**Current:** `ace_check_s3_object` does HeadObject — exists/size/last_modified only.

**Why it matters:** Any scenario with config files, CSV inputs, or fixture data in S3 (arch12, arch08, several arch02 scenarios) requires reading the file. Today agents have to invoke the Lambda and inspect logs to indirectly observe S3 contents.

**Real AWS analog:** `aws s3 cp s3://bucket/key -` — first thing any SRE runs.

**Proposed shape:**
```js
{
  name: "ace_get_s3_object_content",
  description: "Read contents of an S3 object as UTF-8 text. Capped at 256 KB; binary objects rejected.",
  inputSchema: {
    type: "object",
    properties: {
      bucket: { type: "string" },
      key: { type: "string" },
      max_bytes: { type: "number" },      // default 65_536, max 262_144
    },
    required: ["bucket", "key"],
  },
  // handler: GetObject; if ContentLength > max_bytes, return { truncated: true, partial: head(max_bytes) }
}
```

#### Gap 3: CloudWatch Logs filtering (`ace_filter_log_events`)
**Current:** `ace_get_log_tail` returns the last 20 lines from the most-recent log stream of a single Lambda. Cannot grep, cannot search older invocations, cannot search across streams.

**Why it matters:** When a fault is intermittent or already passed (e.g., reliability fault swallowed an error two invocations ago), the relevant log line isn't in the current stream's tail. Agents currently can't find it.

**Real AWS analog:** `aws logs filter-log-events --filter-pattern "ERROR"`.

**Proposed shape:**
```js
{
  name: "ace_filter_log_events",
  description: "Search a Lambda's CloudWatch logs across all streams using a CloudWatch filter pattern.",
  inputSchema: {
    type: "object",
    properties: {
      function_name: { type: "string" },
      filter_pattern: { type: "string" },  // e.g., "ERROR" or "?KeyError ?ValidationError"
      start_minutes_ago: { type: "number" },  // default 30, max 1440
      limit: { type: "number" },           // default 30, max 100
    },
    required: ["function_name", "filter_pattern"],
  },
  // handler: FilterLogEventsCommand on /aws/lambda/<function_name>
}
```

### P1 — High Leverage

#### Gap 4: DynamoDB Scan with filter (`ace_scan_table`)
**Current:** `ace_scan_table_range` requires `KeyConditionExpression` — useless when the agent doesn't know the partition key.

**Why it matters:** Data_correctness faults that corrupt the key itself (swapped fields, wrong cast) leave items at *unknown* partition keys. Agents need full-table scan with a filter to find anomalies.

**Proposed shape:**
```js
{
  name: "ace_scan_table",
  description: "Full-table Scan with optional filter expression. Read-only. Returns up to 25 items.",
  inputSchema: {
    type: "object",
    properties: {
      table_name: { type: "string" },
      filter_expression: { type: "string" },         // optional, e.g., "attribute_exists(quantity)"
      expression_values: { type: "object" },
      expression_names: { type: "object" },
      limit: { type: "number" },                     // clamped to 1..25
    },
    required: ["table_name"],
  },
}
```

#### Gap 5: CFN stack events (`ace_get_stack_events`)
**Current:** No way to see deployment history. `ace_list_resources` shows current state but not the failed transitions.

**Why it matters:** When `submit_fix` triggers a redeploy that fails, the agent only sees a generic failure. Stack events expose the exact resource and reason (e.g., "InvalidParameterValueException: zip file too large", "policy did not validate"). Today agents have to reason backward from CFN error strings.

**Proposed shape:**
```js
{
  name: "ace_get_stack_events",
  description: "Get recent CloudFormation stack events for the ace-bench-stack, ordered newest first.",
  inputSchema: {
    type: "object",
    properties: {
      limit: { type: "number" },    // default 20, max 50
      status_filter: { type: "string", enum: ["FAILED", "ALL"] },
    },
  },
  // handler: DescribeStackEventsCommand, filter to FAILED logical statuses if requested
}
```

#### Gap 6: Lambda invocation metrics (`ace_get_lambda_metrics`)
**Current:** Reliability faults need invocation/error/throttle counts. `ace_get_metric_statistics` *can* fetch these but the agent must already know the AWS/Lambda namespace, dimension structure, etc. — significant friction.

**Why it matters:** A purpose-built tool that returns invocations, errors, throttles, duration percentiles for a Lambda over a window collapses 4–5 generic metric calls into one. Critical for reliability fault diagnosis.

**Proposed shape:**
```js
{
  name: "ace_get_lambda_metrics",
  description: "Get Lambda invocation, error, throttle, and DLQ failure counts over a window.",
  inputSchema: {
    type: "object",
    properties: {
      function_name: { type: "string" },
      window_minutes: { type: "number" },   // default 15, max 60
    },
    required: ["function_name"],
  },
  // handler: GetMetricStatisticsCommand × {Invocations, Errors, Throttles, DeadLetterErrors, Duration}
}
```

### P2 — Targeted Leverage

#### Gap 7: DLQ depth correlator (`ace_check_dlq_health`)
**Current:** Agent must (1) describe queue to find RedrivePolicy, (2) parse DLQ ARN, (3) call `ace_check_queue_depth` on DLQ. Three round trips.

**Proposed:** Single tool that takes the *main* queue name and returns `{main_depth, dlq_arn, dlq_depth, max_receive_count}`.

#### Gap 8: API Gateway integration details (`ace_describe_apigw_integration`)
**Current:** `ace_describe_resource` on a Method returns generic CFN summary; agents read the template to see VTL/proxy/integration backend ARN.

**Proposed:** GetIntegration + GetMethod for a method, returning request mapping template, response status mappings, backend ARN, timeout.

#### Gap 9: Lambda concurrency state (`ace_get_lambda_concurrency`)
**Current:** No direct tool. `ace_describe_resource` returns Configuration but doesn't surface `ReservedConcurrentExecutions` or provisioned concurrency.

**Proposed:** GetFunctionConcurrency + GetProvisionedConcurrencyConfig + account-level limit.

#### Gap 10: Code-hash drift check (`ace_get_function_code_state`)
**Current:** Agents can't verify their deployed fix actually contains new code (vs cached/stale).

**Proposed:** Returns `{code_sha256, last_modified, code_size, runtime, handler}` so the agent can confirm post-submission that the new code is live.

---

## Part 2 — Functional Issues in Existing Tools

Bugs and brittleness in shipped code. Each entry: severity, file:line, behavior, fix.

### Critical (causes wrong answers or silent failures)

#### F1. `ace_describe_resource` returns empty `properties: {}` for non-Lambda resources
- **File:** `observe.js:60`
- **Behavior:** The handler only branches on `AWS::Lambda::Function` to fetch real properties. For Tables, Queues, Topics, Buckets, IAM Roles, etc., it returns `properties: {}` with no useful detail.
- **Impact:** Agents calling `ace_describe_resource` on a DynamoDB table get nothing — must know to use other tools. This is the most natural exploratory tool and it lies.
- **Fix:** Branch by ResourceType and call the appropriate Describe API per resource:

| ResourceType | Describe API |
|---|---|
| `AWS::DynamoDB::Table` | DescribeTableCommand |
| `AWS::SQS::Queue` | GetQueueAttributesCommand |
| `AWS::SNS::Topic` | GetTopicAttributesCommand |
| `AWS::S3::Bucket` | GetBucketLocation + GetBucketPolicy (optional) |
| `AWS::IAM::Role` | reuse existing `ace_get_iam_role` body |
| `AWS::Lambda::EventSourceMapping` | GetEventSourceMappingCommand |
| `AWS::Kinesis::Stream` | DescribeStreamSummaryCommand |
| fallback | leave `properties: {}` with a `note: "use type-specific tool"` field |

#### F2. `ace_simulate_policy` defaults `resource_arns` to `["*"]`
- **File:** `probe_extended.js:551`
- **Behavior:** When the agent omits `resource_arns`, simulation runs against `*`. Policies that explicitly grant on `arn:aws:dynamodb:...:table/X` return `implicitDeny` against `*`, which the agent reads as a misconfigured policy.
- **Impact:** False negatives on IAM diagnosis. Agent concludes "permission missing" when policy is actually correct.
- **Fix:** Make `resource_arns` required *or* derive a sensible default by reading the policy first. Recommend: make it required and return an error if missing.

#### F3. `ace_get_iam_role` does not expand managed (attached) policy documents
- **File:** `observe.js:108–114`
- **Behavior:** Inline policies are expanded to JSON documents; attached policies return only `{name, arn}`.
- **Impact:** Security faults where the broken permission lives in an attached managed policy are invisible. Agent sees the role has *some* attached policy but can't read it.
- **Fix:** For each `AttachedPolicy`, call `GetPolicy` + `GetPolicyVersion` (default version) and include the document. Add a 5-policy cap to avoid response blowup.

#### F4. `ace_get_stream_records` hardcodes `TRIM_HORIZON` and picks last shard arbitrarily
- **File:** `probe_extended.js:329–339`
- **Behavior:** Iterator type is `TRIM_HORIZON` (oldest records) on `shards[shards.length - 1]`. After many records, replays the same prefix every call; if there are multiple shards, all but one are invisible.
- **Impact:** False-negative "no records" when records exist on other shards; wasted iterator budget on stale records.
- **Fix:** Iterate all shards (capped at 4); accept an optional `iterator_type` parameter (`LATEST` | `TRIM_HORIZON`, default `LATEST`); cap returned records at 20 total.

#### F5. `ace_invoke_endpoint` hardcodes stack output key `ApiEndpoint`
- **File:** `probe.js:50`
- **Behavior:** Looks up `outputs["ApiEndpoint"]`. If the scenario CFN exposes the API URL under a different output name (e.g., `ApiUrl`, `RestApiEndpoint`), tool returns `"ApiEndpoint not found in stack outputs"`.
- **Impact:** Connectivity scenarios silently fail to probe even when API GW is healthy.
- **Fix:** Accept optional `output_key`; fall back to searching for any output whose key matches `/Api(Endpoint|Url)/i`; return the matched key in the response.

### High (correctness-affecting in some scenarios)

#### F6. `ace_start_execution` blocks exactly 2 seconds then describes
- **File:** `probe_extended.js:113–122`
- **Behavior:** Hardcoded `await new Promise(r => setTimeout(r, 2000))` then DescribeExecution. Long executions return `status: "RUNNING"` with empty `output`.
- **Fix:** Accept `poll_timeout_ms` (default 5000, max 15000); poll in loop with 500ms increments until status ∈ {SUCCEEDED, FAILED, TIMED_OUT, ABORTED}.

#### F7. `ace_get_log_tail` shape inconsistency on missing stream
- **File:** `observe.js:151–155`
- **Behavior:** Returns `[]` (array) when no log stream exists, but `[{...}, ...]` (array of objects) otherwise. Other tools return error objects. This is a minor API surface inconsistency.
- **Note:** Actually `[]` *is* internally consistent with the populated case (both are arrays). Lower severity — flag for documentation, not a bug.

#### F8. `ace_get_log_tail` reads only the most-recent stream
- **File:** `observe.js:131–137`
- **Behavior:** `limit: 1` on DescribeLogStreams. A Lambda with concurrent invocations writes to multiple streams; the latest-event-time stream may not be the one containing the error.
- **Fix:** Accept `stream_count` (default 3, max 10), fetch events from each, merge by timestamp.

#### F9. `ace_encrypt_decrypt` tests the *harness* credentials, not the Lambda role
- **File:** `probe_extended.js:418–434`
- **Behavior:** Uses LocalStack `test/test` creds, which can encrypt/decrypt anything in LocalStack. So a KMS-permission fault in the Lambda role goes undetected via this tool.
- **Fix:** Document explicitly that this tool verifies *key state*, not *role permission*. Add `note: "verifies key usability, not principal access — use ace_simulate_policy for permission checks"` to the description. Or: accept an optional `principal_arn` and use `ace_simulate_policy` semantics under the hood.

#### F10. `ace_check_event_source` only by function name, not by source ARN
- **File:** `probe.js:174`
- **Behavior:** `ListEventSourceMappings(FunctionName=...)`. To find the consumer of a queue, agent must list all Lambdas and check each.
- **Fix:** Accept either `function_name` or `event_source_arn`; pass through to ListEventSourceMappings (both are valid filters in the AWS API).

### Medium (papercuts and confusing UX)

#### F11. `ace_invoke_endpoint` declares `payload: { type: "object" }`
- **File:** `probe.js:36`
- **Behavior:** Zod validation rejects string bodies (form-encoded, plain text). API GW scenarios with non-JSON content types can't be probed.
- **Fix:** Schema accepts `payload` as object *or* string. Stringify only if object.

#### F12. `buildZodShape` flattens nested object/array schemas
- **File:** `index.js:32–35`
- **Behavior:** `case "object": return z.record(z.any())` — discards nested `properties` and `required`. Array items declared as objects with shape lose their shape entirely.
- **Impact:** Tools that declare structured nested args (e.g., `expression_values` as object) accept anything — Zod doesn't catch shape errors. Only matters when the LLM passes a weird shape; today that's permissive but noisy.
- **Fix:** Recurse into nested object/array schemas. The current code recurses into `array.items` but always uses `z.any()` if items is itself an object — fix to recurse fully.

#### F13. Error responses are unstructured strings inside `{ error: ... }`
- **Behavior:** Every tool returns `{ error: err.message, error_type: err.name }`. The agent sees a string. There's no machine-readable indicator that the tool *succeeded* but returned an error (e.g., `NotFound`) vs the SDK crashed.
- **Impact:** Agent retries spurious tool calls or misinterprets transient errors as fault signals.
- **Fix:** Add a `result_status` field to every tool: `"ok" | "tool_error" | "aws_error" | "not_found"`. Standardize across all 50 tools. (Lower priority — substantial refactor.)

#### F14. Hardcoded stack name `"ace-bench-stack"` not exposed to agents
- **Files:** `observe.js:53`, `observe.js:75`, `observe.js:191`, `probe.js:21`
- **Behavior:** Tools that need stack context assume `ace-bench-stack`. Agents that don't read CLAUDE.md or docs may not know this and try to specify a different stack via some imagined API.
- **Fix:** Add `stack_name` (optional, default `"ace-bench-stack"`) to the tools that take a stack context, even if today it always defaults. Documents the contract; future-proofs concurrent scenarios.

#### F15. Region pinned to `us-east-1` everywhere
- **Files:** All five tool files, plus `awsConfig` in index.js.
- **Behavior:** `region: "us-east-1"` hardcoded. Scenarios that intentionally test cross-region (none in current corpus, but listed as a future direction) cannot be modeled.
- **Fix:** Already flagged in audit. Read `process.env.AWS_REGION` with fallback. Defer behind a CLAUDE.md note that current corpus is single-region.

---

## Part 3 — Implementation Recommendation

### Phase 1 (this week): P0 gaps + critical fixes
- Add `ace_peek_queue_messages`, `ace_get_s3_object_content`, `ace_filter_log_events` (Gaps 1–3).
- Fix F1 (`ace_describe_resource` resource-type dispatch).
- Fix F2 (`ace_simulate_policy` require `resource_arns`).
- Fix F4 (`ace_get_stream_records` LATEST + all-shards).
- Fix F5 (`ace_invoke_endpoint` flexible output key).

### Phase 2 (next): P1 gaps + remaining high
- Add `ace_scan_table`, `ace_get_stack_events`, `ace_get_lambda_metrics` (Gaps 4–6).
- Fix F3 (managed policy expansion), F6 (SFN polling), F8 (multi-stream logs), F10 (event source by ARN).

### Phase 3 (later): P2 + papercuts
- Gaps 7–10 (DLQ correlator, ApiGW integration, concurrency, code drift).
- F9, F11, F12, F13, F14 polish.

### Out of scope (for this track)
- F15 region unpinning — defer until corpus needs it.
- Score-tool implementation (`score.js` stubs) — orthogonal concern.

---

## Self-Review

**Coverage:**
- Reviewed all 50 currently-shipped tools across 5 files.
- Mapped 10 additional observability gaps against AWS-SRE workflow + 40-scenario corpus characteristics.
- Identified 15 functional issues with file:line references and concrete fixes.

**Gap-source verification:**
- Gaps 1–3 (P0) align with the May 17 audit (`docs/eval_runner_sandbox_audit.md`) §5 categories: no message-body peek, no S3 GetObject, no log filtering.
- Gaps 4–6 (P1) extend audit P1 (DynamoDB Scan, diff tool → stack events, metrics correlation).
- Gaps 7–10 (P2) are new since the May 14 analysis; surfaced by reviewing corpus resource-type distribution (RedrivePolicy in 20/40, ApiGW in 10/40).

**Functional-issue verification:**
- F1, F2, F4, F5 verified by reading the handler source directly (file:line cited).
- F3 cross-checked against IAM client method list in `observe.js` imports — `GetPolicy`/`GetPolicyVersion` not imported, confirming the gap.
- F9 reasoning: the awsConfig credentials are `test/test`, not the Lambda role — so any KMS call from MCP runs as the harness, not as the workload principal.

**Not covered (intentional):**
- Did not propose tools for write/mutate operations beyond existing `ace_publish_sns` / `ace_put_events` / `ace_put_*_record` family — that's a separate design conversation about whether the sandbox should let agents *modify* state during diagnosis.
- Did not address the dead-code paths (`submitted.yaml`, `diff_text`, `invalid_patches`) — those are harness-side, not MCP-side.
