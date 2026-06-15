# ACE Ultimate Hop-Fidelity Survey Protocol

**Date:** 2026-06-15
**Status:** Protocol for ACE Ultimate survey planning
**Branch:** `ace/ultimate-hop-survey-protocol`

---

## Purpose

Define the canonical protocol for surveying hop-level observability fidelity in ACE-Bench Ultimate before splitting downstream family-specific planner work.

The survey answers, per architecture and per hop family:

1. Which runtime signals are available from LocalStack Ultimate using real AWS APIs?
2. Which signals require explicit handler instrumentation?
3. Which hop types can support reliable benchmark diagnostics and scoring?
4. Which MCP tools are already sufficient, and which are missing or deferred?

This protocol is intentionally a planning artifact. It seeds planner tasks only; it does not start Codex implementation, reviewer execution, or live probe work by itself.

## Non-goals

- Do not implement X-Ray or CloudTrail MCP tooling here.
- Do not modify scenario handlers here.
- Do not change scoring baselines here.
- Do not merge this protocol directly to `main` before the survey task consumes the shared branch.
- Do not use LocalStack-proprietary introspection as primary evidence. App Inspector and IAM Policy Streams remain out of scope because the benchmark should transfer to real AWS.

---

## Approved decisions encoded by this protocol

1. Validate all four current architectures: `arch01`, `arch02`, `arch08`, and `arch12`.
2. Use two-pass validation:
   - Pass A: active tracing / runtime-only behavior.
   - Pass B: explicit handler-emitted traces.
3. Pass B uses hybrid instrumentation:
   - temporary worktree changes,
   - production-shaped minimal handler pattern,
   - prove on one architecture first, then apply minimally across the others.
4. Use a per-hop scoped gate, not an all-or-nothing architecture gate.
5. Include broad live probes across all Ultimate-enabled extra hop families, not only the current architectures.
6. Use a hybrid planner split:
   - master protocol planner,
   - family-specific planners,
   - synthesis task later.
7. The initial board seeds only the master protocol task.
8. The master creates planner tasks only, not Codex/reviewer chains.
9. This protocol is committed in `docs/superpowers/specs/`.
10. This protocol commit lives on a dedicated branch only.
11. LocalStack requirements are tiered:
    - planner/protocol tasks: no live LocalStack required,
    - Codex execution tasks: live checks when available; exact blocker if unavailable,
    - reviewer tasks: live LocalStack required for ACCEPTED.
12. Strict resource namespace and cleanup are required:
    `ace-hop-<family>-<task-id>-<resource>`.
13. Each family probe emits both a Markdown report and a JSON matrix.
14. The master protocol includes a synthesis task template.
15. Family planner tasks created by the master are `todo` only.

---

## Source context

Primary source specs:

- `docs/superpowers/specs/2026-06-14-next-phase-features-roadmap.md`
- `docs/superpowers/specs/2026-06-14-ultimate-tier-depth-mcp-tooling-design.md`
- `docs/superpowers/plans/2026-06-14-ultimate-tier-depth-mcp-tooling.md`

Known baseline from the 2026-06-14 spike:

- CloudTrail `LookupEvents` works for management/API activity.
- CloudTrail did not capture IAM-denied calls in the observed LocalStack build.
- X-Ray `PutTraceSegments` round-trips through `GetTraceSummaries` and `BatchGetTraces`.
- Lambda active tracing alone did not auto-emit traces.
- X-Ray `GetServiceGraph` returned no services even after manual segment emission.
- IAM enforcement must be on: `ENFORCE_IAM=1`, `IAM_SOFT_MODE=0`.

---

## Architectures in scope

| Architecture | Flow shape | Hop families to classify |
|---|---|---|
| `arch01` | API Gateway REST -> Lambda -> DynamoDB, plus Lambda -> SQS async | HTTP sync, Lambda, DynamoDB, SQS async |
| `arch02` | Lambda Function URL -> Lambda -> Kinesis -> Firehose -> Elasticsearch + S3 | Function URL, Kinesis, Firehose, Elasticsearch/OpenSearch HTTP, S3 |
| `arch08` | SNS FIFO -> Lambda -> DynamoDB + S3 | SNS FIFO async fan-out, Lambda, DynamoDB, S3 |
| `arch12` | S3 event -> SQS -> Lambda -> DynamoDB | S3 event notification, SQS async, Lambda, DynamoDB |

Family planners may split this table further if a hop family needs a separate survey card to keep evidence small and verifiable.

---

## Two-pass validation protocol

### Pass A — runtime-only behavior

Goal: identify what LocalStack Ultimate emits without application-code instrumentation beyond deployment-level tracing settings.

For each in-scope architecture:

1. Deploy the known-good architecture with required IAM roles and `ENFORCE_IAM=1`, `IAM_SOFT_MODE=0`.
2. Enable active tracing in deployment templates where applicable without editing handler logic.
3. Run the architecture's `functional_test.py` or equivalent real traffic flow.
4. Probe real AWS APIs only:
   - X-Ray `GetTraceSummaries`
   - X-Ray `BatchGetTraces` for any trace IDs found
   - X-Ray `GetServiceGraph`
   - CloudTrail `LookupEvents`
   - existing CloudWatch Logs tools
   - existing IAM simulation/identity tools where relevant
5. Record per-hop observations in the matrix.

Expected based on prior spike: X-Ray may be empty unless handlers emit segments, while CloudTrail should provide useful API-call history for many services.

### Pass B — explicit handler-emitted traces

Goal: test whether minimal, production-shaped handler instrumentation can produce useful per-hop trace evidence.

For each family selected for Pass B:

1. Use a temporary worktree or throwaway branch.
2. Add minimal explicit X-Ray segment/subsegment emission in handlers.
3. Prove on one architecture first.
4. If the pattern works, apply it minimally to the remaining relevant architectures.
5. Re-run traffic and X-Ray probes.
6. Classify whether the trace data is sufficient for benchmark diagnostics.
7. Revert or isolate throwaway instrumentation unless a later implementation plan decides to productize it.

Pass B should distinguish:

- Lambda-local segments are visible.
- Sync downstream calls appear as useful subsegments.
- Async boundaries preserve, fork, or drop trace context.
- Trace IDs are discoverable enough for an evaluated model.

---

## Evidence requirements per probe

Every matrix row must include enough evidence for a reviewer to reproduce or reject the classification.

Required evidence:

- Architecture and hop family.
- Pass (`A-runtime-only` or `B-handler-emitted`).
- LocalStack version and edition.
- IAM enforcement state.
- Exact traffic command or functional test command.
- Exact probe command or MCP/API call.
- Raw signal summary:
  - counts of traces, segments, services, CloudTrail events, or log lines;
  - relevant event names / service names / error names;
  - trace ID(s) if any were produced.
- Classification label and rationale.
- Cleanup confirmation for resources named with the required namespace.

Do not paste secrets, tokens, or connection strings into reports. Redact credentials as `[REDACTED]`.

---

## Classification labels

Use these labels consistently in Markdown and JSON output:

| Label | Meaning |
|---|---|
| `supported` | Signal is available, stable enough, and real-AWS-shaped for benchmark use. |
| `partial` | Signal exists but misses some hop details, requires caveats, or needs scoped use. |
| `instrumentation_required` | Signal is absent in Pass A but available or plausibly available with explicit handler emission in Pass B. |
| `unsupported` | Signal is absent or non-functional for this hop/service on the tested LocalStack version. |
| `blocked` | Probe could not run due to environment/tooling issue; include exact blocker. |
| `out_of_scope` | Hop/service is not relevant to this family split. |

Per-hop gates are scoped. One `unsupported` row does not invalidate an entire architecture unless that unsupported hop is the core reason the family exists.

---

## Shared matrix schema

### Markdown matrix columns

Each family report must include this table shape:

| field | description |
|---|---|
| `architecture` | `arch01`, `arch02`, `arch08`, `arch12`, or extra Ultimate family name |
| `hop_family` | Human-readable hop/service family |
| `pass` | `A-runtime-only` or `B-handler-emitted` |
| `signal` | `xray_trace_summaries`, `xray_batch_get_traces`, `xray_service_graph`, `cloudtrail_lookup_events`, `cloudwatch_logs`, `iam_enforcement`, or other real-AWS API |
| `classification` | One of the classification labels above |
| `evidence` | Brief evidence summary with command/report pointers |
| `limitations` | Known caveats or missing coverage |
| `next_action` | `keep`, `scope`, `instrument`, `defer`, `drop`, or `needs_spike` |

### JSON matrix schema

Each family must also write `matrix.json`:

```json
{
  "schema_version": 1,
  "survey": "ultimate-hop-fidelity",
  "family": "<family>",
  "generated_at": "<ISO-8601 timestamp>",
  "repo_commit": "<git sha>",
  "protocol_branch": "ace/ultimate-hop-survey-protocol",
  "protocol_path": "docs/superpowers/specs/2026-06-15-ultimate-hop-fidelity-survey-protocol.md",
  "localstack": {
    "version": "<version or unknown>",
    "edition": "ultimate",
    "enforce_iam": true,
    "iam_soft_mode": false
  },
  "rows": [
    {
      "architecture": "arch01",
      "hop_family": "api-gateway-lambda-dynamodb",
      "pass": "A-runtime-only",
      "signal": "cloudtrail_lookup_events",
      "classification": "supported",
      "evidence": {
        "commands": ["<command>"],
        "counts": { "events": 0, "traces": 0, "services": 0 },
        "artifacts": ["<path>"]
      },
      "limitations": [],
      "next_action": "keep"
    }
  ]
}
```

---

## Report output paths

Family reports:

```text
docs/superpowers/reports/ultimate-hop-fidelity/<family>/report.md
docs/superpowers/reports/ultimate-hop-fidelity/<family>/matrix.json
```

Synthesis outputs:

```text
docs/superpowers/reports/ultimate-hop-fidelity/synthesis.md
docs/superpowers/reports/ultimate-hop-fidelity/synthesis.json
docs/superpowers/specs/2026-06-15-phase-2a-xray-implementation-split.md
```

---

## Markdown report template

````markdown
# Ultimate Hop-Fidelity Report — <family>

## Scope

- Protocol branch: `ace/ultimate-hop-survey-protocol`
- Protocol path: `docs/superpowers/specs/2026-06-15-ultimate-hop-fidelity-survey-protocol.md`
- Architectures/hops covered:

## Environment

- LocalStack version:
- LocalStack edition:
- `ENFORCE_IAM=1` verified by:
- `IAM_SOFT_MODE=0` verified by:
- Repo commit:

## Commands run

```bash
# traffic and probe commands here
```

## Matrix

| Architecture | Hop family | Pass | Signal | Classification | Evidence | Limitations | Next action |
|---|---|---|---|---|---|---|---|

## Existing MCP coverage

- Sufficient tools:
- Tools with caveats:

## Missing MCP coverage

- Required:
- Optional/deferred:
- Dropped:

## Cleanup

- Namespaces created:
- Cleanup command(s):
- Cleanup result:

## Recommendation

- Keep/scope/defer/drop guidance:
````

---

## Existing MCP tooling coverage assessment

Known useful existing signals:

- CloudWatch Logs observe tools for Lambda errors and IAM AccessDenied surfaced in handler logs.
- IAM role, caller identity, and policy simulation tools for permission reasoning.
- CloudTrail `ace_lookup_events` for API-call history where LocalStack records events.
- Service-specific observe/probe tools already present for current corpus services.

Family planners must verify exact tool names and behavior in the current MCP server before claiming coverage.

---

## Missing MCP tooling assessment

Likely missing or deferred tools to assess:

- `ace_get_trace_summaries` using X-Ray `GetTraceSummaries`.
- `ace_get_trace` using X-Ray `BatchGetTraces`.
- `ace_get_service_graph` only if revalidation shows `GetServiceGraph` works; otherwise keep dropped.
- Breadth-family tools for Ultimate-only services such as RDS/Aurora, ECS/EKS/ECR, Glue/Athena/EMR, MSK, EventBridge Pipes, Cognito, and AppSync.

Do not create implementation cards from this protocol. Missing tooling findings should feed the synthesis spec and later approved plans.

---

## Strict namespace and cleanup contract

Any live probe must use resources named:

```text
ace-hop-<family>-<task-id>-<resource>
```

Examples:

- `ace-hop-sqs-t_abc123-queue`
- `ace-hop-xray-t_abc123-function`
- `ace-hop-cognito-t_abc123-user-pool`

Each report must include cleanup commands and cleanup result. If cleanup fails, mark the row or report `blocked` and include the exact remaining resources.

---

## Family-specific planner task template

Create these as `todo` only, assigned to `ace-claude-planner`:

```text
Title: Plan Ultimate hop-fidelity survey: <family>

Repo:
/home/shubhan/projects/ACEDebugging-benchmark

Protocol branch:
ace/ultimate-hop-survey-protocol

Protocol spec:
docs/superpowers/specs/2026-06-15-ultimate-hop-fidelity-survey-protocol.md

Scope:
<architectures and hop families>

Required output:
- docs/superpowers/reports/ultimate-hop-fidelity/<family>/report.md
- docs/superpowers/reports/ultimate-hop-fidelity/<family>/matrix.json

Instructions:
- Plan the survey for this family only.
- Do not implement probes.
- Do not run Codex.
- Do not create reviewer tasks.
- If live LocalStack is required for a later execution card, state the exact prerequisite.
- Keep downstream Codex/reviewer suggestions parked as recommendations, not created cards.
```

Recommended initial family split:

1. `current-sync-and-data` — API Gateway / Function URL / Lambda / DynamoDB.
2. `current-async-queues-and-events` — SQS, SNS FIFO, S3 event notifications.
3. `current-streaming-and-storage` — Kinesis, Firehose, Elasticsearch/OpenSearch HTTP, S3 writes.
4. `xray-handler-instrumentation-pattern` — Pass B proof pattern across current architectures.
5. `ultimate-breadth-candidates` — RDS/Aurora, ECS/EKS/ECR, Glue/Athena/EMR, MSK, EventBridge Pipes, Cognito, AppSync.

The master planner may split or merge these if the rationale is documented, but must keep created tasks as planner-only `todo` tasks.

---

## Synthesis task template

Create synthesis only after family reports exist:

```text
Title: Synthesize Ultimate hop-fidelity survey findings

Inputs:
- docs/superpowers/reports/ultimate-hop-fidelity/*/report.md
- docs/superpowers/reports/ultimate-hop-fidelity/*/matrix.json
- Protocol: docs/superpowers/specs/2026-06-15-ultimate-hop-fidelity-survey-protocol.md

Outputs:
- docs/superpowers/reports/ultimate-hop-fidelity/synthesis.md
- docs/superpowers/reports/ultimate-hop-fidelity/synthesis.json
- docs/superpowers/specs/2026-06-15-phase-2a-xray-implementation-split.md

Instructions:
- Summarize supported/partial/instrumentation-required/unsupported signals.
- Recommend which X-Ray tools to implement, scope, defer, or drop.
- Recommend whether handler instrumentation should become a corpus rebuild requirement.
- Identify missing MCP tools for Ultimate breadth families.
- Preserve real-AWS-transferability as the primary gate.
```

---

## Coupling vs splitting guidance

Split work when:

- Hop families have independent evidence sources.
- A family can be planned without live LocalStack.
- A planner can produce a report template without handler changes.
- Breadth-family candidates require separate service maturity research.

Couple work when:

- A signal is useless without handler instrumentation.
- A probe requires deploying multiple services together to test one async boundary.
- A later implementation must update MCP tooling and scenario handlers in the same verified chain.

Default policy: split planner tasks broadly, then synthesize before creating implementation plans.

---

## Completion handoff requirements

When the master protocol task completes, it must report:

- branch name,
- spec path,
- commit SHA,
- family planner task IDs created,
- confirmation that family tasks are `todo`,
- split/coupling rationale,
- statement that no Codex implementation or reviewer cards were created.
