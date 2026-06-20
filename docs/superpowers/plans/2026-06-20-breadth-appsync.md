# AppSync GraphQL Architecture (arch09) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Worker subagents use model `sonnet`; overseer uses model `opus`.

---

## Goal

Add arch09 — an AppSync GraphQL API — as a new ACE-Bench architecture family. The corpus models: an `AWS::AppSync::GraphQLApi` (API_KEY auth) exposing a small schema; unit resolvers that map GraphQL fields onto a DynamoDB data source via request/response mapping templates; and an API key a client attaches as `x-api-key` to issue queries/mutations. A client POSTs a GraphQL document to the API's GraphQL URL; AppSync runs the field's resolver against its data source and returns the resolved JSON.

## Architecture (arch09)

```
[GraphQL client]
       |  POST { query }  (header: x-api-key: <key>)
[AppSync GraphQLApi]  (schema: Query.getItem / Query.listItems / Mutation.putItem)
       |  unit resolver (request + response mapping template)
   [DynamoDB data source]  ← service role (dynamodb:GetItem/PutItem/Scan)
       |
   [DynamoDB table]
```

**Services:** `appsync`, `dynamodb`, `iam`, `logs`

**Tech Stack:**
- MCP tools: `harness/mcp_server/tools/probe_appsync.js` (new), SDK `@aws-sdk/client-appsync`
- Corpus: `corpus/arch_09_appsync_graphql_api/`
- Scenarios: `scenarios/arch09_fault0N_<class>/`
- Resolvers: VTL (Velocity) mapping templates on unit resolvers (no Lambda data source in the base corpus — keeps the family distinct from arch01/arch07 and isolates the AppSync resolution layer as the fault surface)

> **arch_NN allocation:** arch04 (Containers), arch05 (Streaming), arch06 (Pipes), arch07 (Cognito) are claimed by sibling breadth plans; arch08 (SNS FIFO) and arch12 (SQS) are existing corpora. The next free number is **arch09**. Confirm against the live `corpus/` listing at build time (`ls corpus/`) before creating the dir — if a sibling plan landed first and took arch09, use the next free number and update every `arch09` reference in this plan consistently.

---

## Global Constraints

- LocalStack runs with `ENFORCE_IAM=1 IAM_SOFT_MODE=0`; any data-source service role must be a real assumable `appsync.amazonaws.com` role.
- AWS creds: accessKeyId=`test`, secretAccessKey=`test`, region=`us-east-1`, endpoint `http://localhost:4566`. IAM fake account `000000000000`.
- Prefer real AWS APIs; never use LocalStack-proprietary introspection (App Inspector, IAM Policy Streams).
- `fault_manifest.json` and `known_good.yaml` are NEVER exposed to the model.
- **Fault design principle (mandatory):** every fault must produce an observable behavioral symptom Pass-1 functional verification detects; `scenario.md` states only the symptom, never the cause. No posture-only faults. Because most AppSync faults manifest as "query returns null / errors", each MUST produce a **distinct error signature** (GraphQL `errors[].errorType` / message, HTTP status, or empty-vs-error response — confirmed empirically in the spike).
- **AppSync resolution kill-gate:** if the Task 1 spike proves LocalStack does NOT execute resolvers (i.e. a query against a seeded DynamoDB data source returns no resolved data, or AppSync returns only API/resolver *config* with no live resolution), the family is **shelved** with a documented finding and Tasks 2–6 are skipped. Do not ship tools that cannot be validated against live resolution, and do not ship faults whose symptom cannot manifest.
- MCP tool files live in `harness/mcp_server/tools/` and are spread into `harness/mcp_server/index.js`. Each tool is `{ name, description, inputSchema, async handler(args) }` and returns a plain object (never throws).
- Corpus dir name: `corpus/arch_09_appsync_graphql_api/`. Scenario dirs: `scenarios/arch09_fault0N_<class>/`.
- Node MCP tests: `node --test tests/test_mcp_server.js`. Spike + corpus run against a live LocalStack (`localstack start -d`).
- **Pre-flight:** `cd harness/mcp_server && npm install` before any Node step (`node_modules` currently lacks `@aws-sdk/client-appsync`).

---

## Task 1: De-risking spike (the gate)

Exploratory, not TDD. Validates the family's resolution fidelity and fault premises on the current LocalStack build before any fan-out. **Do not start Task 2 until this passes.** The single biggest risk is that LocalStack accepts AppSync API/resolver/data-source config but does not actually *resolve queries* — this must be empirically confirmed first. Findings are recorded in this plan as a `## Task 1 findings` section appended by the executor.

**Files:**
- Create: `scratch/spike_appsync.mjs` (gitignored; `scratch/` is already in `.gitignore`)
- Create: `scratch/spike_appsync_stack.yaml` (minimal CFN: GraphQLApi + schema + DynamoDB data source + service role + one unit resolver + API key + table)

**Interfaces:**
- Consumes: nothing (standalone spike).
- Produces: a recorded decision per fault mechanism (primary vs fallback), a capability×fidelity matrix, the resolution-fidelity verdict (the kill-gate answer), and the X-Ray-instrumentation decision — written as notes in Step 6. Tasks 2–4 read these notes.

- [ ] **Step 1: Confirm LocalStack is up with IAM enforcement and AppSync is available**

Run:
```bash
# Start LocalStack Ultimate with IAM enforcement (required for valid scoring).
LOCALSTACK_AUTH_TOKEN=ls-... ENFORCE_IAM=1 IAM_SOFT_MODE=0 localstack start -d
until localstack status services 2>/dev/null | grep -q running; do sleep 2; done
# Confirm appsync and dynamodb are emulated on this build:
curl -s localhost:4566/_localstack/health | grep -oE '"appsync"\s*:\s*"[a-z]+"'
curl -s localhost:4566/_localstack/health | grep -oE '"dynamodb"\s*:\s*"[a-z]+"'
# Record the LocalStack version for the findings block:
curl -s localhost:4566/_localstack/info | grep -oE '"version":\s*"[^"]+"'
```
Expected: `appsync` and `dynamodb` both present. If `appsync` is absent or `disabled`, this is an immediate kill-gate hit — record "shelved: appsync not emulated" and stop.

- [ ] **Step 2: Write the minimal spike CFN stack**

Create `scratch/spike_appsync_stack.yaml` with:
- `AWS::DynamoDB::Table` (`spike-items`, PK `item_id` S, PAY_PER_REQUEST).
- `AWS::IAM::Role` (`spike-appsync-ds-role`, AssumeRole principal `appsync.amazonaws.com`, inline policy allowing `dynamodb:GetItem`, `dynamodb:PutItem`, `dynamodb:Scan` on the table ARN).
- `AWS::AppSync::GraphQLApi` (`spike-api`, AuthenticationType `API_KEY`, `XrayEnabled: false`).
- `AWS::AppSync::GraphQLSchema` (Definition: a `type Item { item_id: ID! data: String }`, `type Query { getItem(item_id: ID!): Item listItems: [Item] }`, `type Mutation { putItem(item_id: ID!, data: String): Item }`, plus a `schema { query: Query mutation: Mutation }` block).
- `AWS::AppSync::DataSource` (`spike_ddb`, Type `AMAZON_DYNAMODB`, `DynamoDBConfig` → table name + region, `ServiceRoleArn` → the role ARN).
- `AWS::AppSync::Resolver` for `Query.getItem` (Kind `UNIT`, DataSourceName `spike_ddb`, request mapping template = a `GetItem` VTL op keyed on `item_id`, response mapping template = `$util.toJson($ctx.result)`).
- `AWS::AppSync::Resolver` for `Mutation.putItem` (Kind `UNIT`, `PutItem` VTL op).
- `AWS::AppSync::ApiKey` (on the API).

Outputs: `GraphQLApiId`, `GraphQLUrl` (the `GraphQLApi.GraphQLUrl` attribute), `ApiKey` (the `ApiKey.ApiKey` attribute), `TableName`, `DataSourceRoleArn`.

- [ ] **Step 3: Provisioning and tool-data fidelity check**

Write `scratch/spike_appsync.mjs` to deploy the stack (`CreateStack` + wait `stack_create_complete`). After deploy, call and record which fields populate:
- `GetGraphqlApi` → `apiId`, `name`, `authenticationType`, `uris` (`GRAPHQL`/`REALTIME`), `xrayEnabled`, `tags`.
- `ListResolvers` (by `typeName: Query`) and `GetResolver(typeName, fieldName)` → `typeName`, `fieldName`, `dataSourceName`, `kind`, `requestMappingTemplate`, `responseMappingTemplate`.
- `GetDataSource(apiId, name)` → `name`, `type`, `dynamodbConfig` (table name, region), `serviceRoleArn`, `lambdaConfig`.
- `ListApiKeys` → `id`, `expires`.

Run: `node scratch/spike_appsync.mjs provision`
Expected: `CREATE_COMPLETE`; the candidate tool fields non-empty. Record in the matrix (Step 6). If `GetResolver` returns empty mapping templates or `GetDataSource` returns no `dynamodbConfig`, mark those ⚠️/❌.

- [ ] **Step 4: Resolution fidelity (the kill-gate probe)**

Extend `scratch/spike_appsync.mjs` with these probes and run `node scratch/spike_appsync.mjs resolve`:

**Probe A — Mutation resolution (write path):**
1. POST to `GraphQLUrl` (header `x-api-key: <ApiKey>`) the document `mutation { putItem(item_id: "i1", data: "hello") { item_id data } }`.
2. Record: HTTP status, and whether the response has `data.putItem.item_id == "i1"` (resolved) or a non-empty `errors` array (not resolved).
3. Independently `GetItem` on the DynamoDB table for `item_id=i1` and record whether the item was actually written by the resolver.

**Probe B — Query resolution (read path):**
1. POST `query { getItem(item_id: "i1") { item_id data } }` with the API key.
2. Record whether `data.getItem.data == "hello"` (resolution works end-to-end) or `data.getItem == null` / `errors` present.
3. POST `query { listItems { item_id } }` and record whether the Scan resolver returns the seeded item.

**Probe C — Auth enforcement:**
1. POST a valid query with NO `x-api-key` header. Record HTTP status / GraphQL `errors[].errorType` (expect `UnauthorizedException` if enforced).
2. POST with a garbage API key. Record the rejection signature.

**Probe D — Fault-enforcement reconnaissance (only if A+B resolve):**
For each candidate fault, temporarily mutate the live config and re-run Probe B, recording the resulting error signature:
- **Broken request mapping template** (e.g. key on `wrong_id` instead of `item_id`): does `getItem` now return null / a `MappingTemplate` error? Record `errors[].errorType`.
- **Wrong data source name on resolver** (point `Query.getItem` at a nonexistent data source via `UpdateResolver`): does the query fail with a distinct error?
- **Service role missing `dynamodb:GetItem`** (strip the action from the role policy): does the resolver fail with an access-denied / `errorType` distinct from the mapping error? (This is the IAM-enforcement question — under `ENFORCE_IAM=1`, confirm AppSync→DynamoDB is actually authorized through the service role, not bypassed.)
- **Wrong table name in `dynamodbConfig`** (`UpdateDataSource` to a nonexistent table): does the query return empty/null or a resource-not-found error?

Record one labeled output line per probe: `[PROBE_A] mutation_resolved=true/false written=true/false`, `[PROBE_B] query_resolved=true/false`, `[PROBE_C_NO_KEY] status=... errorType=...`, `[PROBE_D_<mech>] errorType=... distinct=...`.

- [ ] **Step 5: Distinct error signature confirmation**

For each candidate fault that Probe D enforced, confirm its error signature is distinct from the others (different `errorType`, message substring, or null-vs-error outcome). The fault set ships only mechanisms that (a) resolve in the known-good and (b) break with a distinct, Pass-1-detectable signature when injected. If Probe A/B show that LocalStack does NOT resolve at all, **stop here** — record the kill-gate verdict "shelved" in Step 6 and do not proceed to Task 2.

- [ ] **Step 6: Record findings + lock decisions**

Append a `## Task 1 findings` section to THIS plan file (immediately after this task's steps) with:
- The LocalStack version string + edition + IAM-enforcement state.
- A capability×fidelity matrix (table below).
- The **resolution-fidelity verdict** — the kill-gate answer: does LocalStack execute resolvers end-to-end? If NO → "shelved" and Tasks 2–6 are skipped.
- Locked fault mechanisms (primary + fallback) per fault.
- The X-Ray instrumentation decision for AppSync handlers (only meaningful if the corpus adds a Lambda data source later; the base VTL-only corpus has no Python handler to instrument — record "N/A, VTL-only corpus").
- Fault count decision: if fewer than 3 faults have distinct enforced signatures, reduce scope (minimum viable = 2 behavior-manifesting, distinct-signature faults) and document which are shelved.

Commit this plan-file update:
```bash
git add docs/superpowers/plans/2026-06-20-breadth-appsync.md
git commit -m "docs(plan): record arch09 AppSync spike findings and locked fault mechanisms"
```

- [ ] **Step 7: Tear down the spike stack**

Run `node scratch/spike_appsync.mjs teardown` (DeleteStack + wait). No commit (scratch is gitignored).

---

## Task 1 findings

> **[EXECUTOR: fill this section after running the spike. Do NOT fabricate results. Lock the fault mechanisms here; Tasks 2–4 reference these decisions. If the resolution-fidelity verdict is "shelved", stop after this section.]**

### LocalStack version

`version: <fill>`, `edition: <fill>`, IAM enforcement: `<active/inactive>`

### Capability × fidelity matrix

| Capability | API call | Status | Notes |
|---|---|---|---|
| API config fidelity | `GetGraphqlApi` | | |
| Resolver config fidelity | `GetResolver` / `ListResolvers` | | |
| Data source config fidelity | `GetDataSource` | | |
| API key listing | `ListApiKeys` | | |
| **Mutation resolution (write)** | POST `mutation putItem` | | |
| **Query resolution (read)** | POST `query getItem` | | |
| Scan resolution | POST `query listItems` | | |
| Auth enforcement (no key) | POST without `x-api-key` | | |
| Mapping-template enforcement | broken request template | | |
| Data-source-name enforcement | resolver → bad data source | | |
| Service-role IAM enforcement | strip `dynamodb:GetItem` | | |
| Table-name enforcement | `dynamodbConfig` → bad table | | |

### Resolution-fidelity verdict (KILL-GATE)

`<resolvers execute end-to-end / config-only, no live resolution → SHELVED>`

### Locked fault mechanisms

- **fault01 mechanism (resolver mapping):** PRIMARY = `<fill>`; FALLBACK = `<fill>`.
- **fault02 mechanism (wrong data source):** PRIMARY = `<fill>`; FALLBACK = `<fill>`.
- **fault03 mechanism (data-source IAM):** PRIMARY = `<fill>`; FALLBACK = `<fill>` (only ships if service-role IAM is enforced; else SHELVED).
- **fault04 mechanism (wrong table / data-source config):** PRIMARY = `<fill>`; FALLBACK = `<fill>`.

### Distinct error signature summary

| Fault | Expected outcome | Expected error fingerprint |
|---|---|---|
| fault01 | `<fill>` | `<fill>` |
| fault02 | `<fill>` | `<fill>` |
| fault03 | `<fill>` | `<fill>` |
| fault04 | `<fill>` | `<fill>` |

### X-Ray decision

`N/A — base corpus is VTL-only (no Python handler to instrument). Revisit only if a Lambda data source is added.`

---

## Task 2: AppSync MCP diagnostic tools (TDD)

Adds `harness/mcp_server/tools/probe_appsync.js` with four tools and wires it into `index.js`. TDD via `node:test`. **Do not start until Task 1's resolution-fidelity verdict is "resolvers execute end-to-end".**

**Files:**
- Create: `harness/mcp_server/tools/probe_appsync.js`
- Modify: `harness/mcp_server/index.js` (import + spread `probeAppsyncTools`)
- Modify: `harness/mcp_server/package.json` (add `@aws-sdk/client-appsync`)
- Test: `tests/test_mcp_server.js` (append import + tool tests)

**Interfaces:**
- Consumes: the `awsConfig` pattern from `probe_extended.js`; the `tool(list, name)` helper in `tests/test_mcp_server.js`.
- Produces: `export const probeAppsyncTools` — an array of four tools:
  - `ace_get_graphql_api({ api_id })` → `{ api_id, name, authentication_type, graphql_url, realtime_url, xray_enabled, tags }` or `{ error }`.
  - `ace_get_resolver({ api_id, type_name, field_name })` → `{ type_name, field_name, data_source_name, kind, request_mapping_template, response_mapping_template, max_batch_size }` or `{ error }`.
  - `ace_get_data_source({ api_id, name })` → `{ name, type, service_role_arn, dynamodb_table_name, dynamodb_region, lambda_function_arn }` or `{ error }`.
  - `ace_probe_graphql_query({ graphql_url, api_key, query, variables? })` → `{ http_status, has_data: boolean, data: object|null, errors: [{ error_type, message }], outcome: "resolved"|"null_result"|"graphql_error"|"unauthorized"|"error" }` or `{ error }`.

**Tool descriptions (must satisfy static rubric — all three fields: API mapped, fields returned, when to reach for it):**
- `ace_get_graphql_api`: "AppSync GetGraphqlApi: return one GraphQL API's configuration — api_id, name, authentication_type (API_KEY / AWS_IAM / etc.), graphql_url, realtime_url, xray_enabled, tags. Use to confirm the API endpoint and auth mode when GraphQL requests are rejected before resolution (e.g. unauthorized errors) or to verify the URL a client should call."
- `ace_get_resolver`: "AppSync GetResolver: return one field's resolver — type_name, field_name, data_source_name, kind (UNIT/PIPELINE), request_mapping_template, response_mapping_template, max_batch_size. Use to diagnose a query that returns null or a mapping error: inspect whether the request template keys the correct attribute and whether the resolver points at the intended data source."
- `ace_get_data_source`: "AppSync GetDataSource: return one data source's config — name, type (AMAZON_DYNAMODB/AWS_LAMBDA/NONE), service_role_arn, dynamodb_table_name, dynamodb_region, lambda_function_arn. Use to diagnose resolution failures caused by a wrong backing table, a missing/under-privileged service role, or a data source the resolver references but that is misconfigured."
- `ace_probe_graphql_query`: "AppSync GraphQL query probe: POST a GraphQL document to the API's graphql_url with x-api-key auth and report http_status, has_data, the resolved data, any errors (error_type + message), and an outcome label (resolved/null_result/graphql_error/unauthorized/error). Use as the FIRST tool when a GraphQL field returns nothing or errors: it distinguishes auth rejection (unauthorized) from resolver/data-source faults (graphql_error or null_result), narrowing which config tool to call next."

- [ ] **Step 1: Add the AppSync SDK dependency**

```bash
cd harness/mcp_server && npm install @aws-sdk/client-appsync && cd -
```
Expected: `@aws-sdk/client-appsync` appears in `harness/mcp_server/package.json` dependencies.

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_mcp_server.js`. First add the import near the other tool imports at the top of the file:
```javascript
import { probeAppsyncTools } from "../harness/mcp_server/tools/probe_appsync.js";
```
Then append the following tests (no seeded AppSync API is required — they assert membership + error handling):
```javascript
test("probeAppsyncTools exposes the four AppSync tools", () => {
  for (const n of ["ace_get_graphql_api", "ace_get_resolver", "ace_get_data_source", "ace_probe_graphql_query"]) {
    assert.ok(tool(probeAppsyncTools, n), `missing ${n}`);
  }
});

test("ace_get_graphql_api: missing api_id returns error", async () => {
  const res = await tool(probeAppsyncTools, "ace_get_graphql_api").handler({});
  assert.ok(res.error);
});

test("ace_get_graphql_api: unknown api id returns error", async () => {
  const res = await tool(probeAppsyncTools, "ace_get_graphql_api").handler({ api_id: "doesnotexist123" });
  assert.ok(res.error);
});

test("ace_get_resolver: missing args returns error", async () => {
  const res = await tool(probeAppsyncTools, "ace_get_resolver").handler({ api_id: "x" });
  assert.ok(res.error);
});

test("ace_get_resolver: unknown resolver returns error", async () => {
  const res = await tool(probeAppsyncTools, "ace_get_resolver").handler({ api_id: "doesnotexist123", type_name: "Query", field_name: "getItem" });
  assert.ok(res.error);
});

test("ace_get_data_source: missing args returns error", async () => {
  const res = await tool(probeAppsyncTools, "ace_get_data_source").handler({ api_id: "x" });
  assert.ok(res.error);
});

test("ace_get_data_source: unknown data source returns error", async () => {
  const res = await tool(probeAppsyncTools, "ace_get_data_source").handler({ api_id: "doesnotexist123", name: "ddb" });
  assert.ok(res.error);
});

test("ace_probe_graphql_query: missing args returns error", async () => {
  const res = await tool(probeAppsyncTools, "ace_probe_graphql_query").handler({});
  assert.ok(res.error);
});

test("ace_probe_graphql_query: unreachable url returns error outcome", async () => {
  const res = await tool(probeAppsyncTools, "ace_probe_graphql_query").handler({
    graphql_url: "http://localhost:4566/graphql/doesnotexist",
    api_key: "da2-fake",
    query: "query { getItem(item_id: \"x\") { item_id } }",
  });
  assert.ok(res.error || res.outcome === "error" || res.outcome === "graphql_error" || res.outcome === "unauthorized");
});
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `node --test tests/test_mcp_server.js 2>&1 | grep -A2 'probeAppsyncTools\|ace_get_graphql_api\|ace_get_resolver\|ace_get_data_source\|ace_probe_graphql_query'`
Expected: FAIL — `Cannot find module '.../probe_appsync.js'`.

- [ ] **Step 4: Implement `probe_appsync.js`**

Create `harness/mcp_server/tools/probe_appsync.js`:
```javascript
import {
  AppSyncClient,
  GetGraphqlApiCommand,
  GetResolverCommand,
  GetDataSourceCommand,
} from "@aws-sdk/client-appsync";

const awsConfig = {
  endpoint: process.env.LOCALSTACK_ENDPOINT ?? "http://localhost:4566",
  region: "us-east-1",
  credentials: { accessKeyId: "test", secretAccessKey: "test" },
};

export const probeAppsyncTools = [
  {
    name: "ace_get_graphql_api",
    description:
      "AppSync GetGraphqlApi: return one GraphQL API's configuration — api_id, name, authentication_type (API_KEY / AWS_IAM / etc.), graphql_url, realtime_url, xray_enabled, tags. Use to confirm the API endpoint and auth mode when GraphQL requests are rejected before resolution (e.g. unauthorized errors) or to verify the URL a client should call.",
    inputSchema: {
      type: "object",
      properties: {
        api_id: { type: "string", description: "The AppSync GraphQL API ID" },
      },
      required: ["api_id"],
    },
    async handler(args) {
      if (!args?.api_id) return { error: "api_id is required" };
      try {
        const client = new AppSyncClient(awsConfig);
        const res = await client.send(new GetGraphqlApiCommand({ apiId: args.api_id }));
        const a = res.graphqlApi ?? {};
        return {
          api_id: a.apiId ?? null,
          name: a.name ?? null,
          authentication_type: a.authenticationType ?? null,
          graphql_url: a.uris?.GRAPHQL ?? null,
          realtime_url: a.uris?.REALTIME ?? null,
          xray_enabled: a.xrayEnabled ?? null,
          tags: a.tags ?? null,
        };
      } catch (err) {
        return { error: String(err?.message ?? err) };
      }
    },
  },
  {
    name: "ace_get_resolver",
    description:
      "AppSync GetResolver: return one field's resolver — type_name, field_name, data_source_name, kind (UNIT/PIPELINE), request_mapping_template, response_mapping_template, max_batch_size. Use to diagnose a query that returns null or a mapping error: inspect whether the request template keys the correct attribute and whether the resolver points at the intended data source.",
    inputSchema: {
      type: "object",
      properties: {
        api_id: { type: "string", description: "The AppSync GraphQL API ID" },
        type_name: { type: "string", description: "The GraphQL type (e.g. Query, Mutation)" },
        field_name: { type: "string", description: "The field name the resolver is attached to (e.g. getItem)" },
      },
      required: ["api_id", "type_name", "field_name"],
    },
    async handler(args) {
      if (!args?.api_id) return { error: "api_id is required" };
      if (!args?.type_name) return { error: "type_name is required" };
      if (!args?.field_name) return { error: "field_name is required" };
      try {
        const client = new AppSyncClient(awsConfig);
        const res = await client.send(
          new GetResolverCommand({ apiId: args.api_id, typeName: args.type_name, fieldName: args.field_name })
        );
        const r = res.resolver ?? {};
        return {
          type_name: r.typeName ?? null,
          field_name: r.fieldName ?? null,
          data_source_name: r.dataSourceName ?? null,
          kind: r.kind ?? null,
          request_mapping_template: r.requestMappingTemplate ?? null,
          response_mapping_template: r.responseMappingTemplate ?? null,
          max_batch_size: r.maxBatchSize ?? null,
        };
      } catch (err) {
        return { error: String(err?.message ?? err) };
      }
    },
  },
  {
    name: "ace_get_data_source",
    description:
      "AppSync GetDataSource: return one data source's config — name, type (AMAZON_DYNAMODB/AWS_LAMBDA/NONE), service_role_arn, dynamodb_table_name, dynamodb_region, lambda_function_arn. Use to diagnose resolution failures caused by a wrong backing table, a missing/under-privileged service role, or a data source the resolver references but that is misconfigured.",
    inputSchema: {
      type: "object",
      properties: {
        api_id: { type: "string", description: "The AppSync GraphQL API ID" },
        name: { type: "string", description: "The data source name" },
      },
      required: ["api_id", "name"],
    },
    async handler(args) {
      if (!args?.api_id) return { error: "api_id is required" };
      if (!args?.name) return { error: "name is required" };
      try {
        const client = new AppSyncClient(awsConfig);
        const res = await client.send(new GetDataSourceCommand({ apiId: args.api_id, name: args.name }));
        const d = res.dataSource ?? {};
        return {
          name: d.name ?? null,
          type: d.type ?? null,
          service_role_arn: d.serviceRoleArn ?? null,
          dynamodb_table_name: d.dynamodbConfig?.tableName ?? null,
          dynamodb_region: d.dynamodbConfig?.awsRegion ?? null,
          lambda_function_arn: d.lambdaConfig?.lambdaFunctionArn ?? null,
        };
      } catch (err) {
        return { error: String(err?.message ?? err) };
      }
    },
  },
  {
    name: "ace_probe_graphql_query",
    description:
      "AppSync GraphQL query probe: POST a GraphQL document to the API's graphql_url with x-api-key auth and report http_status, has_data, the resolved data, any errors (error_type + message), and an outcome label (resolved/null_result/graphql_error/unauthorized/error). Use as the FIRST tool when a GraphQL field returns nothing or errors: it distinguishes auth rejection (unauthorized) from resolver/data-source faults (graphql_error or null_result), narrowing which config tool to call next.",
    inputSchema: {
      type: "object",
      properties: {
        graphql_url: { type: "string", description: "The AppSync GraphQL endpoint URL (from ace_get_graphql_api.graphql_url)" },
        api_key: { type: "string", description: "The API key to send as the x-api-key header" },
        query: { type: "string", description: "The GraphQL query or mutation document" },
        variables: { type: "object", description: "Optional GraphQL variables object" },
      },
      required: ["graphql_url", "api_key", "query"],
    },
    async handler(args) {
      if (!args?.graphql_url) return { error: "graphql_url is required" };
      if (!args?.api_key) return { error: "api_key is required" };
      if (!args?.query) return { error: "query is required" };
      try {
        const body = JSON.stringify({ query: args.query, variables: args.variables ?? {} });
        const resp = await fetch(args.graphql_url, {
          method: "POST",
          headers: { "Content-Type": "application/json", "x-api-key": args.api_key },
          body,
        });
        const http_status = resp.status;
        let payload = {};
        try {
          payload = await resp.json();
        } catch {
          payload = {};
        }
        const errors = (payload.errors ?? []).map((e) => ({
          error_type: e.errorType ?? null,
          message: e.message ?? null,
        }));
        const data = payload.data ?? null;
        const hasData = !!data && Object.values(data).some((v) => v !== null && v !== undefined);
        let outcome;
        if (http_status === 401 || errors.some((e) => /unauthor/i.test(e.error_type ?? "") || /unauthor/i.test(e.message ?? ""))) {
          outcome = "unauthorized";
        } else if (errors.length > 0) {
          outcome = "graphql_error";
        } else if (hasData) {
          outcome = "resolved";
        } else if (data !== null) {
          outcome = "null_result";
        } else {
          outcome = "error";
        }
        return { http_status, has_data: hasData, data, errors, outcome };
      } catch (err) {
        return { error: String(err?.message ?? err), outcome: "error" };
      }
    },
  },
];
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `node --test tests/test_mcp_server.js 2>&1 | grep -A2 'probeAppsyncTools\|ace_get_graphql_api\|ace_get_resolver\|ace_get_data_source\|ace_probe_graphql_query'`
Expected: all `probeAppsyncTools` / `ace_*` tests PASS; no prior tests regress. Fix `probe_appsync.js` before continuing if any fail.

- [ ] **Step 6: Wire into `index.js`**

Add the import alongside the other tool imports:
```javascript
import { probeAppsyncTools } from "./tools/probe_appsync.js";
```
Add `...probeAppsyncTools` to the spread in the `for` loop:
```javascript
for (const tool of [...probeTools, ...probeExtendedTools, ...observeTools, ...observeExtendedTools, ...observeTracingTools, ...probeRdsTools, ...probeAppsyncTools, ...scoreTools]) {
```
> If sibling breadth plans (Cognito/Streaming/etc.) have already added their spreads, keep all of them — just insert `...probeAppsyncTools` before `...scoreTools`.

- [ ] **Step 7: Verify `index.js` loads cleanly**

Run: `node -e "import('./harness/mcp_server/index.js').catch(e=>{console.error(e);process.exit(1)})" && echo ok`
Expected: `ok` with no import errors (the MCP transport waits on stdin — that is fine).

- [ ] **Step 8: Commit**

```bash
git add harness/mcp_server/tools/probe_appsync.js harness/mcp_server/index.js harness/mcp_server/package.json harness/mcp_server/package-lock.json tests/test_mcp_server.js
git commit -m "feat(mcp): add AppSync diagnostic tools (ace_get_graphql_api, ace_get_resolver, ace_get_data_source, ace_probe_graphql_query)"
```

---

## Task 3: arch09 corpus (known-good)

Builds the working AppSync GraphQL architecture and proves it deploys clean and resolves queries under IAM enforcement.

**Files:**
- Create: `corpus/arch_09_appsync_graphql_api/known_good.yaml`
- Create: `corpus/arch_09_appsync_graphql_api/functional_test.py`
- Create: `corpus/arch_09_appsync_graphql_api/traffic_flow.md`

> No `deployment/` Lambda dir — the base corpus uses VTL unit resolvers only. (If Task 1 found VTL resolution unsupported but Lambda data sources DO resolve, the fallback corpus uses an `AWS_LAMBDA` data source with a Python resolver Lambda under `deployment/lambda/resolver/index.py`; record that pivot in Task 1 findings and add the dir accordingly.)

**Interfaces:**
- Consumes: Task 1 findings (the locked resolution mode + fault mechanisms); arch01 functional-test conventions (`emit_pass`/`emit_fail`/`finalize` from `harness.shared.functional_test_helpers`).
- Produces: a deployable `known_good.yaml` whose stack name is `ace-bench-stack`, exporting outputs `GraphQLApiId`, `GraphQLUrl`, `ApiKey`, `TableName`, `DataSourceName`, `DataSourceRoleArn`. Task 4 faults and the functional test read these.

- [ ] **Step 1: Pre-flight**

```bash
cd harness/mcp_server && npm install && cd -
```

- [ ] **Step 2: Write `known_good.yaml`**

Create `corpus/arch_09_appsync_graphql_api/known_good.yaml`. The CFN template must include (use the EXACT VTL forms the Task 1 spike confirmed resolve):

```yaml
AWSTemplateFormatVersion: '2010-09-09'
Description: >
  ACE-Bench arch09: AppSync GraphQL API backed by DynamoDB via unit resolvers.
  An API_KEY-authenticated GraphQL API exposes getItem/listItems/putItem; unit
  resolvers map each field onto a DynamoDB data source through VTL mapping templates.

Resources:
  ItemsTable:
    Type: AWS::DynamoDB::Table
    Properties:
      TableName: !Sub '${AWS::StackName}-items'
      BillingMode: PAY_PER_REQUEST
      AttributeDefinitions:
        - { AttributeName: item_id, AttributeType: S }
      KeySchema:
        - { AttributeName: item_id, KeyType: HASH }

  DataSourceRole:
    Type: AWS::IAM::Role
    Properties:
      RoleName: !Sub '${AWS::StackName}-ds-role'
      AssumeRolePolicyDocument:
        Version: '2012-10-17'
        Statement:
          - Effect: Allow
            Principal: { Service: appsync.amazonaws.com }
            Action: sts:AssumeRole
      Policies:
        - PolicyName: ddb-access
          PolicyDocument:
            Version: '2012-10-17'
            Statement:
              - Effect: Allow
                Action:
                  - dynamodb:GetItem
                  - dynamodb:PutItem
                  - dynamodb:Scan
                Resource: !GetAtt ItemsTable.Arn

  GraphQLApi:
    Type: AWS::AppSync::GraphQLApi
    Properties:
      Name: !Sub '${AWS::StackName}-api'
      AuthenticationType: API_KEY
      XrayEnabled: false

  GraphQLSchema:
    Type: AWS::AppSync::GraphQLSchema
    Properties:
      ApiId: !GetAtt GraphQLApi.ApiId
      Definition: |
        type Item { item_id: ID! data: String }
        type Query {
          getItem(item_id: ID!): Item
          listItems: [Item]
        }
        type Mutation {
          putItem(item_id: ID!, data: String): Item
        }
        schema { query: Query mutation: Mutation }

  DdbDataSource:
    Type: AWS::AppSync::DataSource
    Properties:
      ApiId: !GetAtt GraphQLApi.ApiId
      Name: items_ddb
      Type: AMAZON_DYNAMODB
      ServiceRoleArn: !GetAtt DataSourceRole.Arn
      DynamoDBConfig:
        TableName: !Ref ItemsTable
        AwsRegion: !Sub '${AWS::Region}'

  GetItemResolver:
    Type: AWS::AppSync::Resolver
    DependsOn: GraphQLSchema
    Properties:
      ApiId: !GetAtt GraphQLApi.ApiId
      TypeName: Query
      FieldName: getItem
      DataSourceName: !GetAtt DdbDataSource.Name
      Kind: UNIT
      RequestMappingTemplate: |
        {
          "version": "2017-02-28",
          "operation": "GetItem",
          "key": { "item_id": $util.dynamodb.toDynamoDBJson($ctx.args.item_id) }
        }
      ResponseMappingTemplate: "$util.toJson($ctx.result)"

  ListItemsResolver:
    Type: AWS::AppSync::Resolver
    DependsOn: GraphQLSchema
    Properties:
      ApiId: !GetAtt GraphQLApi.ApiId
      TypeName: Query
      FieldName: listItems
      DataSourceName: !GetAtt DdbDataSource.Name
      Kind: UNIT
      RequestMappingTemplate: |
        { "version": "2017-02-28", "operation": "Scan" }
      ResponseMappingTemplate: "$util.toJson($ctx.result.items)"

  PutItemResolver:
    Type: AWS::AppSync::Resolver
    DependsOn: GraphQLSchema
    Properties:
      ApiId: !GetAtt GraphQLApi.ApiId
      TypeName: Mutation
      FieldName: putItem
      DataSourceName: !GetAtt DdbDataSource.Name
      Kind: UNIT
      RequestMappingTemplate: |
        {
          "version": "2017-02-28",
          "operation": "PutItem",
          "key": { "item_id": $util.dynamodb.toDynamoDBJson($ctx.args.item_id) },
          "attributeValues": { "data": $util.dynamodb.toDynamoDBJson($ctx.args.data) }
        }
      ResponseMappingTemplate: "$util.toJson($ctx.result)"

  ApiKey:
    Type: AWS::AppSync::ApiKey
    Properties:
      ApiId: !GetAtt GraphQLApi.ApiId
      Description: ace-bench arch09 key

Outputs:
  GraphQLApiId:
    Value: !GetAtt GraphQLApi.ApiId
  GraphQLUrl:
    Value: !GetAtt GraphQLApi.GraphQLUrl
  ApiKey:
    Value: !GetAtt ApiKey.ApiKey
  TableName:
    Value: !Ref ItemsTable
  DataSourceName:
    Value: !GetAtt DdbDataSource.Name
  DataSourceRoleArn:
    Value: !GetAtt DataSourceRole.Arn
```

Write the complete file with no placeholders. If the Task 1 spike found a different VTL `version` string or a `GraphQLUrl`-attribute name quirk on this LocalStack build, use the exact form the spike confirmed resolves.

- [ ] **Step 3: Deploy the known-good stack and confirm `CREATE_COMPLETE`**

```bash
python3 - <<'EOF'
import boto3
cf = boto3.client('cloudformation', endpoint_url='http://localhost:4566', region_name='us-east-1',
    aws_access_key_id='test', aws_secret_access_key='test')
body = open('corpus/arch_09_appsync_graphql_api/known_good.yaml').read()
cf.create_stack(StackName='ace-bench-stack', TemplateBody=body,
    Capabilities=['CAPABILITY_NAMED_IAM', 'CAPABILITY_AUTO_EXPAND'])
cf.get_waiter('stack_create_complete').wait(StackName='ace-bench-stack')
print('CREATE_COMPLETE')
EOF
```
Expected: `CREATE_COMPLETE`. If it fails, inspect with:
```bash
aws --endpoint-url=http://localhost:4566 cloudformation describe-stack-events \
  --stack-name ace-bench-stack --query 'StackEvents[?ResourceStatus==`CREATE_FAILED`]' --output table
```
Fix the template and redeploy until it succeeds.

- [ ] **Step 4: Write `functional_test.py`**

Create `corpus/arch_09_appsync_graphql_api/functional_test.py`:
```python
import json, sys
from urllib import request, error
import boto3
from harness.shared.functional_test_helpers import emit_fail, emit_pass, finalize

ENDPOINT = "http://localhost:4566"
REGION = "us-east-1"
CREDS = {"aws_access_key_id": "test", "aws_secret_access_key": "test"}
STACK = "ace-bench-stack"


def client(svc):
    return boto3.client(svc, endpoint_url=ENDPOINT, region_name=REGION, **CREDS)


def output(key):
    st = client("cloudformation").describe_stacks(StackName=STACK)["Stacks"][0]
    return next(o["OutputValue"] for o in st["Outputs"] if o["OutputKey"] == key)


def gql(url, api_key, query, variables=None, omit_key=False):
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    headers = {"Content-Type": "application/json"}
    if not omit_key:
        headers["x-api-key"] = api_key
    req = request.Request(url, data=body, headers=headers, method="POST")
    try:
        with request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def main():
    url = output("GraphQLUrl")
    api_key = output("ApiKey")

    # --- Assert: mutation resolves and writes ---
    status, resp = gql(url, api_key,
        'mutation { putItem(item_id: "i1", data: "hello") { item_id data } }')
    if status == 200 and resp.get("data", {}).get("putItem", {}).get("item_id") == "i1":
        emit_pass("mutation_resolves", "putItem mutation returned the written item")
    else:
        emit_fail("mutation_resolves", f"putItem returned status={status} resp={resp}")

    # --- Assert: query resolves the written item ---
    status, resp = gql(url, api_key,
        'query { getItem(item_id: "i1") { item_id data } }')
    item = (resp.get("data") or {}).get("getItem")
    if status == 200 and item and item.get("data") == "hello":
        emit_pass("query_resolves", "getItem returned data='hello'")
    else:
        emit_fail("query_resolves", f"getItem returned status={status} resp={resp}")

    # --- Assert: listItems Scan resolver returns the item ---
    status, resp = gql(url, api_key, 'query { listItems { item_id } }')
    items = (resp.get("data") or {}).get("listItems") or []
    if status == 200 and any(i.get("item_id") == "i1" for i in items):
        emit_pass("scan_resolves", "listItems returned the seeded item")
    else:
        emit_fail("scan_resolves", f"listItems returned status={status} resp={resp}")

    # --- Assert: unauthenticated request is rejected ---
    status, resp = gql(url, api_key,
        'query { getItem(item_id: "i1") { item_id } }', omit_key=True)
    errs = resp.get("errors") or []
    if status in (401, 403) or any("nauthor" in (e.get("errorType","") + e.get("message","")) for e in errs):
        emit_pass("unauthenticated_rejected", f"request without x-api-key was rejected (status={status})")
    else:
        emit_fail("unauthenticated_rejected", f"expected rejection but got status={status} resp={resp}")

    finalize()


if __name__ == "__main__":
    main()
    sys.exit(0)
```

> If Task 1 found AppSync does NOT enforce API_KEY auth on this LocalStack build, drop the `unauthenticated_rejected` assertion (record the drop in Task 1 findings) — do not assert behavior LocalStack does not emulate.

- [ ] **Step 5: Run the functional test against the deployed known-good**

Run: `python corpus/arch_09_appsync_graphql_api/functional_test.py`
Expected: `ASSERT pass mutation_resolves`, `ASSERT pass query_resolves`, `ASSERT pass scan_resolves`, `ASSERT pass unauthenticated_rejected` (or 3/3 if auth assertion dropped). If any assertion fails, fix `known_good.yaml` (VTL templates / role) and redeploy. Do not proceed to Task 4 until the functional test passes.

- [ ] **Step 6: Write `traffic_flow.md`**

Create `corpus/arch_09_appsync_graphql_api/traffic_flow.md`:
```markdown
# arch09 Traffic Flow — AppSync GraphQL API

## Happy path (authenticated query)

1. Client POSTs a GraphQL document to `GraphQLUrl` with header `x-api-key: <ApiKey>`.
2. AppSync authenticates the request via the API key.
3. AppSync selects the field's unit resolver (e.g. `Query.getItem`).
4. The resolver's REQUEST mapping template renders a DynamoDB operation (GetItem/Scan/PutItem), keyed on `$ctx.args`.
5. AppSync calls DynamoDB through the data source's `ServiceRoleArn` (must allow the op).
6. The resolver's RESPONSE mapping template (`$util.toJson($ctx.result)`) shapes the result into the GraphQL response.
7. Client receives `{ "data": { ... } }`.

## Failure paths (null result / graphql_error / unauthorized)

- **No / wrong `x-api-key`:** AppSync rejects before resolution → `UnauthorizedException`.
- **Broken request mapping template** (keys the wrong attribute): GetItem finds nothing → `data.getItem == null`, or a mapping/`errorType` error.
- **Resolver points at the wrong/nonexistent data source:** resolution fails → `errors` with a data-source error.
- **Service role missing the DynamoDB action:** AppSync→DynamoDB call is denied → `errors` with an access-denied `errorType`.
- **Data source `dynamodbConfig` points at a wrong table:** the op targets a nonexistent/empty table → null result or resource-not-found error.

## Key resource identifiers (populated from stack Outputs)

- `GraphQLApiId` — used by `ace_get_graphql_api`, `ace_get_resolver`, `ace_get_data_source`
- `GraphQLUrl` — used by `ace_probe_graphql_query` and the functional test
- `ApiKey` — used by `ace_probe_graphql_query` and the functional test
- `DataSourceName` — used by `ace_get_data_source`
```

- [ ] **Step 7: Tear down the known-good stack**

```bash
aws --endpoint-url=http://localhost:4566 cloudformation delete-stack --stack-name ace-bench-stack
aws --endpoint-url=http://localhost:4566 cloudformation wait stack-delete-complete --stack-name ace-bench-stack
echo "torn down"
```

- [ ] **Step 8: Commit**

```bash
git add corpus/arch_09_appsync_graphql_api/
git commit -m "feat(corpus): add arch09 AppSync GraphQL API corpus (known-good + functional test)"
```

---

## Task 4: Fault scenarios

Each scenario = a copy of the corpus with ONE injected fault, a symptom-only `scenario.md`, a `fault_manifest.json` (never exposed), and a verified reproduction. Use the Task 1 locked mechanisms. Target 4 faults; ship fewer only if Task 1 confirmed fewer distinct enforced signatures (minimum 2).

**Files (per scenario `scenarios/arch09_fault0N_<class>/`):**
- Create: `faulted.yaml` (corpus `known_good.yaml` with ONE injected fault)
- Create: `scenario.md` (symptom only — never names the faulty resource/property)
- Create: `fault_manifest.json` (never exposed to the model)

> **KEY INVARIANT:** AppSync faults mostly manifest as "query returns null or errors", so each MUST carry a DISTINCT error signature confirmed in Task 1 Probe D:
> - **fault01** (broken resolver request mapping template): the resolver keys the wrong attribute → `getItem` returns `null` (or a mapping `errorType`). Diagnosis: `ace_get_resolver(Query,getItem).request_mapping_template` shows the wrong key.
> - **fault02** (resolver → wrong data source): `Query.getItem.DataSourceName` references a data source that does not back the table → resolution error. Diagnosis: `ace_get_resolver` shows the unexpected `data_source_name`; `ace_get_data_source` confirms the mismatch.
> - **fault03** (data-source service role missing `dynamodb:GetItem`): resolution fails with an access-denied `errorType` (distinct from null). Diagnosis: `ace_get_data_source` shows the `service_role_arn`; the role lacks the action. Only ships if Task 1 confirmed service-role IAM is enforced.
> - **fault04** (data source `DynamoDBConfig.TableName` wrong): the op targets a nonexistent table → null/resource-not-found. Diagnosis: `ace_get_data_source.dynamodb_table_name` differs from the real table.
>
> If Task 1 found fewer than 4 distinct enforced mechanisms, reduce the count and document which are shelved. Do not ship posture-only faults.

**Interfaces:**
- Consumes: corpus `known_good.yaml` (Task 3); the AppSync tools (Task 2); Task 1 findings.
- Produces: scenario dirs each reproducing its fault and diagnosable via the intended path; each `fault_manifest.json` carries `optimal_tool_calls`/`optimal_files_changed`/`optimal_lines_changed` measured in Step 7.

- [ ] **Step 1: Scaffold the scenario dirs from the corpus**

```bash
CORP=corpus/arch_09_appsync_graphql_api
for s in arch09_fault01_data_correctness arch09_fault02_connectivity arch09_fault03_security arch09_fault04_connectivity; do
  mkdir -p scenarios/$s
  cp $CORP/known_good.yaml scenarios/$s/faulted.yaml
done
```
(Drop any scenario whose mechanism Task 1 shelved.)

- [ ] **Step 2: Inject fault01 (broken resolver request mapping template)**

In `scenarios/arch09_fault01_data_correctness/faulted.yaml`, apply the Task 1-locked mechanism:
- **Primary:** in `GetItemResolver.Properties.RequestMappingTemplate`, change the key from `item_id` to a nonexistent attribute (e.g. `"id"`): `"key": { "id": $util.dynamodb.toDynamoDBJson($ctx.args.item_id) }`. GetItem keyed on a non-key attribute returns null/validation error.
- **Fallback** (mapping not enforced as expected): change `ResponseMappingTemplate` to reference a wrong field (`$util.toJson($ctx.result.WRONG)`) so the response is always null.

Record exact `target_resource`/`target_property`/`original_value`/`injected_value`.

- [ ] **Step 3: Inject fault02 (resolver → wrong data source)**

In `scenarios/arch09_fault02_connectivity/faulted.yaml`:
- **Primary:** add a second `AWS::AppSync::DataSource` of Type `NONE` (name `none_ds`) and change `GetItemResolver.Properties.DataSourceName` to `none_ds`. A NONE data source cannot resolve a DynamoDB GetItem → resolution returns null/error.
- **Fallback:** change `GetItemResolver.Properties.DataSourceName` to a string that names no data source (e.g. `missing_ds`) — if LocalStack accepts the dangling reference at deploy and fails at resolution, this is the distinct signature.

- [ ] **Step 4: Inject fault03 (data-source service role missing `dynamodb:GetItem`)**

In `scenarios/arch09_fault03_security/faulted.yaml`:
- **Primary:** in `DataSourceRole.Properties.Policies[0].PolicyDocument.Statement[0].Action`, remove `dynamodb:GetItem` (leave `PutItem`, `Scan`). Mutations and Scan still work; `getItem` fails with an access-denied `errorType` — distinct from fault01's null and fault02's data-source error.
- **Fallback** (service-role IAM not enforced — Task 1 Probe D showed bypass): SHELVE fault03 and document; do not substitute a posture-only fault.

- [ ] **Step 5: Inject fault04 (wrong data source table)**

In `scenarios/arch09_fault04_connectivity/faulted.yaml`:
- **Primary:** change `DdbDataSource.Properties.DynamoDBConfig.TableName` from `!Ref ItemsTable` to a hard-coded nonexistent table name (e.g. `nonexistent-items`). Every resolver targets a table that does not exist → null/resource-not-found on all fields (distinct: ALL operations fail, including the mutation — unlike fault01/03 which spare some ops).
- **Fallback:** change `DynamoDBConfig.AwsRegion` to a wrong region so the data source points at an empty regional table.

- [ ] **Step 6: Write symptom-only `scenario.md` for each**

For each scenario, write `scenario.md` with this structure:

```markdown
## System overview
A GraphQL API (AppSync) backed by DynamoDB. Clients authenticate with an API key (`x-api-key`) and issue GraphQL queries/mutations (`getItem`, `listItems`, `putItem`). Resolvers map each field onto the DynamoDB table.

## What you have access to
- `faulted.yaml` — the deployed CloudFormation template (one fault injected)
- All MCP diagnostic tools (see tool list)
- The stack has been deployed successfully (`CREATE_COMPLETE`)

## Reported symptom
<symptom-only description — see below per fault>

## What correct behavior looks like
`putItem` writes an item; `getItem` and `listItems` return previously written items as JSON. Authenticated requests succeed; requests without a valid API key are rejected.
```

Symptom descriptions (use EXACTLY these — no cause, no resource name):
- **fault01**: "A `putItem` mutation succeeds and the item is written. But a `getItem` query for that same `item_id` returns `null` in the response data — no error is raised, the field simply resolves to null. `listItems` still returns the item."
- **fault02**: "Every `getItem` query returns an error in the GraphQL `errors` array instead of data. Mutations (`putItem`) still succeed and `listItems` still returns data — only the single-item lookup is broken."
- **fault03**: "`putItem` mutations succeed and `listItems` returns data, but `getItem` queries fail with an authorization/access error in the `errors` array. The failure is specific to reading a single item by key."
- **fault04**: "All GraphQL operations fail to return data: `putItem` appears to succeed at the API but the item is never retrievable, and both `getItem` and `listItems` return null/empty or errors. The API itself is reachable and the API key is accepted."

- [ ] **Step 7: Write `fault_manifest.json` for each**

Follow the arch01/arch03/arch07 schema exactly. fault01 example (adapt others):
```json
{
  "fault_id": "arch09_fault01",
  "fault_class": "data_correctness",
  "architecture": "arch_09_appsync_graphql_api",
  "scenario_id": "arch09_fault01_data_correctness",
  "target_resource": "GetItemResolver",
  "target_property": "Properties.RequestMappingTemplate",
  "injected_value": "key on \"id\" (non-key attribute)",
  "original_value": "key on \"item_id\" (table HASH key)",
  "valid_fixes": [
    "Restore the GetItem request mapping template to key on item_id"
  ],
  "invalid_patches": [
    "Add a global secondary index on id to make the wrong key resolve",
    "Switch the resolver to a Scan with a filter on id",
    "Change AuthenticationType or remove the API key requirement"
  ],
  "optimal_tool_calls": 2,
  "optimal_files_changed": 1,
  "optimal_lines_changed": 1,
  "optimal_diagnostic_path": [
    "ace_probe_graphql_query(graphql_url, api_key, 'query { getItem(item_id:\"i1\"){item_id data} }') → outcome=null_result; confirms resolution returns null, not an auth error",
    "ace_get_resolver(api_id, Query, getItem) → request_mapping_template keys 'id' not 'item_id'; confirms root cause"
  ],
  "concurrency_probe": null,
  "concurrency_probe_n": null,
  "deployment_check": "CREATE_COMPLETE",
  "observability_check": "functional_test assertion 'query_resolves' fails; getItem returns data.getItem == null",
  "observable_symptom": "getItem returns null for an item that putItem successfully wrote; listItems still returns it.",
  "root_cause": "The Query.getItem resolver's request mapping template keys the DynamoDB GetItem on a non-key attribute ('id') instead of the table HASH key ('item_id'), so the lookup never matches and resolves to null.",
  "corpus_path": "corpus/arch_09_appsync_graphql_api",
  "functional_test_path": "corpus/arch_09_appsync_graphql_api/functional_test.py",
  "known_good_path": "corpus/arch_09_appsync_graphql_api/known_good.yaml"
}
```

Write complete JSON for every shipped fault. Fill `optimal_*` after Step 8.

- [ ] **Step 8: Verify each scenario reproduces + is diagnosable**

For each scenario:
1. Deploy `faulted.yaml` as `ace-bench-stack` (same `create_stack` + waiter as Task 3 Step 3, adjusting the path).
2. Run `python corpus/arch_09_appsync_graphql_api/functional_test.py` and confirm the primary assertion FAILS (symptom reproduces). Record which assertion fails and the exact GraphQL response.
3. Walk the intended diagnostic path with the AppSync MCP tools:
   ```bash
   node -e "import('./harness/mcp_server/tools/probe_appsync.js').then(async m => {
     const t = n => m.probeAppsyncTools.find(x => x.name === n);
     const cp = require('child_process');
     const outs = JSON.parse(cp.execSync(\"aws --endpoint-url=http://localhost:4566 cloudformation describe-stacks --stack-name ace-bench-stack --query 'Stacks[0].Outputs' --output json\").toString());
     const get = k => outs.find(o=>o.OutputKey===k).OutputValue;
     console.log(await t('ace_probe_graphql_query').handler({graphql_url:get('GraphQLUrl'),api_key:get('ApiKey'),query:'query { getItem(item_id:\"i1\"){item_id data} }'}));
     console.log(await t('ace_get_resolver').handler({api_id:get('GraphQLApiId'),type_name:'Query',field_name:'getItem'}));
     console.log(await t('ace_get_data_source').handler({api_id:get('GraphQLApiId'),name:get('DataSourceName')}));
   })"
   ```
   Confirm the tools surface the signal that pinpoints the fault.
4. If a scenario does NOT reproduce or the diagnostic path fails, switch to the Task 1 fallback mechanism and re-verify.
5. Tear down between scenarios (same delete-stack + waiter as Task 3 Step 7).

- [ ] **Step 9: Baseline `optimal_*` and finalize manifests**

For each scenario set `optimal_files_changed` (typically 1), `optimal_lines_changed` (typically 1), `optimal_tool_calls` (count of MCP calls on the Step 8 walk). Write these into each `fault_manifest.json`.

- [ ] **Step 10: Commit**

```bash
git add scenarios/arch09_fault01_data_correctness scenarios/arch09_fault02_connectivity scenarios/arch09_fault03_security scenarios/arch09_fault04_connectivity
git commit -m "feat(scenarios): add arch09 AppSync fault scenarios with manifests"
```

---

## Task 5: Discoverability QA gate

Run the four checks from §4 of the framework spec for every arch09 scenario. Record pass/fail and any remediation.

**Files:** none created; results recorded inline as the executor fills them.

**Interfaces:**
- Consumes: all deployed faulted scenarios (Task 4); the AppSync tools (Task 2).
- Produces: a pass/fail record per check per scenario; all four checks must pass before Task 6.

### Check 1 — Agent-exposure plumbing

Verify all four AppSync tools flow through `mcp_to_openai_tool` / `filter_model_tools` and appear in the model's runtime tool list; confirm `ace_verify_fix` / `ace_score_run` remain filtered.

```bash
python3 - <<'EOF'
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from harness.agent.tools import mcp_to_openai_tool, filter_model_tools

async def check():
    params = StdioServerParameters(command="node", args=["harness/mcp_server/index.js"])
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [t["function"]["name"] for t in filter_model_tools([mcp_to_openai_tool(t) for t in tools.tools])]
            for n in ["ace_get_graphql_api", "ace_get_resolver", "ace_get_data_source", "ace_probe_graphql_query"]:
                print(f"[Check1] {n}: {'PASS' if n in names else 'FAIL'}")
            for n in ["ace_verify_fix", "ace_score_run"]:
                print(f"[Check1] {n} filtered: {'PASS' if n not in names else 'FAIL (exposed)'}")

asyncio.run(check())
EOF
```
Expected: all four AppSync tools PASS; both score tools filtered.

### Check 2 — Diagnostic-path reachability

For each scenario, deploy the faulted stack, walk the `optimal_diagnostic_path` with the real MCP tools (Task 4 Step 8 commands), and confirm the tools surface the distinguishing signal. Record per scenario: `[Check2] arch09_fault0N: PASS | FAIL — <reason>`. **Remediation:** if a tool does not surface the signal, expose the missing field in the handler, re-run tests, re-verify.

### Check 3 — Blind-triggering

#### 3a — Static rubric (cheap pre-gate)

Verify all four descriptions name (a) the AWS API, (b) the fields returned, (c) when to reach for it.
```bash
node -e "
import('./harness/mcp_server/tools/probe_appsync.js').then(m => {
  for (const t of m.probeAppsyncTools) {
    const d = t.description;
    const api = /AppSync \\w+:/.test(d);
    const fields = /return|report/i.test(d);
    const useto = /Use (to|as|when)/i.test(d);
    console.log(\`[3a] \${t.name} — AWS API:\${api?'PASS':'FAIL'} fields:\${fields?'PASS':'FAIL'} Use-to:\${useto?'PASS':'FAIL'}\`);
  }
});
"
```
Expected: all 12 checks (4 tools × 3) PASS. **Remediation:** edit the failing description in `probe_appsync.js`; re-run tests.

#### 3b — LLM-judge blind selection (N=5 per scenario)

Use a cheaper judge model (e.g. `claude-haiku-4-5`) DISTINCT from the primary eval target. For each scenario, give the judge ONLY the `## Reported symptom` text + the full tool list (names + descriptions); ask which tools it would call first (ordered). Run N=5. Pass = every tool on `optimal_diagnostic_path` in the first-K picks (K = path length + 1) in ≥3/5 trials. Reuse the `scratch/blind_trigger_check.py` shape from the Cognito plan, swapping in the arch09 tool loader (`probe_appsync.js`), the four arch09 symptoms, and their optimal paths.

**Remediation ladder:** (1) strengthen the "when to reach for it" clause with the specific symptom pattern (e.g. "Use when a GraphQL field returns null despite a successful write"); (2) re-baseline `optimal_diagnostic_path` to the route the judge naturally takes if it is equally short; (3) if ≥2 scenarios fail after remediation, revisit the fault design. **Guardrail:** never leak the faulted resource/property into the symptom or pad a description with hints.

### Check 4 — Trace + scoring pipeline

```bash
python harness/run.py scenarios/arch09_fault01_data_correctness/ \
  --model anthropic/claude-haiku-4-5 \
  --api-key "$ANTHROPIC_API_KEY"
```
Confirm `results/<run_id>/tool_call_trace.json` contains ≥1 AppSync tool call; `verify_result.json` and `score.json` are present; the pipeline completes without crashing. **Remediation:** if the runner crashes on an AppSync tool, fix its handler and re-run.

- [ ] **Step 1: Run Check 1 (plumbing)** — record the four tool + two filter results.
- [ ] **Step 2: Run Check 2 (reachability) for every scenario** — record PASS/FAIL each.
- [ ] **Step 3a: Run static rubric** — record the 12 results.
- [ ] **Step 3b: Run blind-triggering judge (N=5/scenario)** — record `__/5 → PASS|FAIL` each.
- [ ] **Step 4: Run end-to-end pipeline (Check 4)** — record completion + file presence.
- [ ] **Step 5: Apply remediation and re-run failed checks** — repeat until all pass; shelve+document any fault that cannot be made discoverable.
- [ ] **Step 6: Commit remediated tools/manifests if changed**

```bash
# Only if probe_appsync.js or fault_manifest.json files changed during remediation:
git add harness/mcp_server/tools/probe_appsync.js tests/test_mcp_server.js scenarios/arch09_fault0*/fault_manifest.json
git commit -m "fix(mcp): remediate AppSync tool descriptions for discoverability QA gate"
```

---

## Task 6: Documentation

Bring tool counts and architecture inventory in sync across the guides.

**Files:**
- Modify: `CLAUDE.md` (tool counts; Project Layout corpus/scenarios entries)
- Modify: `README.md` (Phase B tool inventory; repository layout)
- Modify: `RUN.md` (tool inventory; model-access count)

**Interfaces:**
- Consumes: the final tool list from Task 2 (4 new AppSync tools) and the arch09 corpus/scenarios from Tasks 3–4.
- Produces: consistent counts (diagnostic tools 61 → 65; model-access count rises by 4) and a documented arch09.

> **Counting note:** each breadth plan is written against the committed baseline of **61 diagnostic + 2 score tools (28 services)**. If sibling breadth plans (Cognito +3, Streaming +N, ECS +3, Pipes +2) have already merged, the executor reconciles to the actual current count via the verify script below rather than assuming 61. Adding the 4 AppSync tools introduces the `appsync` service (+1 service).

- [ ] **Step 1: Update `CLAUDE.md`** — bump the MCP server line by +4 diagnostic tools and +1 service (`appsync`); add `harness/mcp_server/tools/probe_appsync.js` (4 AppSync tools) to the `tools/` listing; add `corpus/arch_09_appsync_graphql_api/` and the `scenarios/arch09_fault0N_*` entries to the Project Layout.

- [ ] **Step 2: Update `README.md` and `RUN.md`** — bump the diagnostic tool count and model-access count by 4 in both; add the four AppSync tools to the tool tables; add arch09 to any architecture/corpus inventory.

- [ ] **Step 3: Verify counts are consistent**

```bash
node -e "
Promise.all([
  import('./harness/mcp_server/tools/probe.js'),
  import('./harness/mcp_server/tools/probe_extended.js'),
  import('./harness/mcp_server/tools/observe.js'),
  import('./harness/mcp_server/tools/observe_extended.js'),
  import('./harness/mcp_server/tools/observe_tracing.js'),
  import('./harness/mcp_server/tools/probe_rds.js'),
  import('./harness/mcp_server/tools/probe_appsync.js'),
  import('./harness/mcp_server/tools/score.js'),
]).then(mods => {
  const total = mods.reduce((acc, m) => acc + Object.values(m).find(Array.isArray).length, 0);
  console.log('total tools (this plan + baseline):', total);
});
"
```
Confirm the printed total equals the count claimed in the docs (baseline diagnostic + 4 AppSync + 2 score; reconcile against any merged sibling plans). **Note:** the snippet imports only the baseline + AppSync tool files — if sibling plans added files (`probe_cognito.js`, etc.), include them in the `Promise.all` to get the true total.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md README.md RUN.md
git commit -m "docs: document arch09 AppSync GraphQL API and AppSync MCP tools (+4 diagnostic tools)"
```

---

## Commit Cadence Summary

| Task | Commit message |
|---|---|
| Task 1 (after spike) | `docs(plan): record arch09 AppSync spike findings and locked fault mechanisms` |
| Task 2 | `feat(mcp): add AppSync diagnostic tools (ace_get_graphql_api, ace_get_resolver, ace_get_data_source, ace_probe_graphql_query)` |
| Task 3 | `feat(corpus): add arch09 AppSync GraphQL API corpus (known-good + functional test)` |
| Task 4 | `feat(scenarios): add arch09 AppSync fault scenarios with manifests` |
| Task 5 (if remediation) | `fix(mcp): remediate AppSync tool descriptions for discoverability QA gate` |
| Task 6 | `docs: document arch09 AppSync GraphQL API and AppSync MCP tools (+4 diagnostic tools)` |

---

## Self-Review Notes (author)

- **Spec coverage:** 6-task spine → all six tasks present. §2 LocalStack-load preamble → Task 1 Step 1 verbatim. §3 Realism gate → every tool maps to a real AWS AppSync SDK call (`GetGraphqlApi`/`GetResolver`/`GetDataSource`) or a real GraphQL POST; no LocalStack-proprietary introspection. §4 Discoverability QA gate (four checks) → Task 5 with concrete commands + remediation ladders. Kill-gate → Task 1 resolution-fidelity verdict gates all of Tasks 2–6; if resolvers don't execute, the family is shelved. Primary+fallback → all four faults carry both in Task 4.
- **Why VTL-only (no Lambda data source):** isolates the AppSync resolution layer (resolver mapping templates + data source wiring + service-role IAM) as the fault surface, keeping arch09 distinct from arch01/arch07 (which already exercise API-GW→Lambda→DynamoDB). The Lambda-data-source fallback is documented in Task 3 in case Task 1 finds VTL resolution unsupported.
- **Kill-gate honesty:** AppSync emulation fidelity is the family's defining risk — Task 1 Probe A/B (mutation+query resolution) is the explicit gate, and Probe D conditions every fault on real enforcement, mirroring the RDS-spike lesson (no posture-only faults).
- **Distinct error signatures:** fault01=null result (mapping); fault02=graphql_error (data source); fault03=access-denied errorType (service-role IAM); fault04=all-ops-fail (wrong table). Each verified in Task 1 Probe D before shipping.
- **arch_NN allocation:** arch04–07 claimed by sibling breadth plans; arch08/arch12 are existing corpora; arch09 is the next free — confirm against live `corpus/` at build time.
- **Tool count:** +4 AppSync tools, +1 service (`appsync`). Each breadth plan is written against the committed 61-diagnostic baseline; Task 6 Step 3 verify script is the source of truth for reconciliation across merged siblings.
- **No scratch files committed:** `scratch/spike_appsync*.{mjs,yaml}` and `scratch/blind_trigger_check.py` are gitignored.
