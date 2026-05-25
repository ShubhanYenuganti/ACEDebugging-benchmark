# Track A — MCP Observability Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fill two MCP diagnostic tool gaps so agents can see EventSourceMapping filter patterns and query DynamoDB ranges without falling back to CloudFormation template reading.

**Architecture:** A1 adds one field (`filter_criteria`) to the existing `ace_check_event_source` handler in `probe.js`. A2 adds a new `ace_scan_table_range` tool to `probe_extended.js` using DynamoDB's `QueryCommand`. Tests for both land in `tests/test_mcp_server.js`.

**Tech Stack:** Node.js v22+, `@aws-sdk/client-dynamodb`, `@aws-sdk/util-dynamodb`, LocalStack at `http://localhost:4566`

---

## File Structure

| File | Change |
|------|--------|
| `harness/mcp_server/tools/probe.js` | Modify: add `filter_criteria` field to `ace_check_event_source` map projection |
| `harness/mcp_server/tools/probe_extended.js` | Modify: add `DynamoDBClient`/`QueryCommand` imports + client + `ace_scan_table_range` tool |
| `tests/test_mcp_server.js` | Modify: add RANGE_TABLE setup, seed record, tests for A1 and A2 |

No changes to `harness/mcp_server/index.js` — `probeExtendedTools` is already spread there.

---

### Task 1: Add `filter_criteria` to `ace_check_event_source` (A1)

**Files:**
- Modify: `harness/mcp_server/tools/probe.js` (lines 176–182, the `.map()` callback)

- [ ] **Step 1: Open the file and locate the target block**

In `probe.js`, find the `ace_check_event_source` handler. The `.map()` at the end currently reads:

```js
return (res.EventSourceMappings ?? []).map(m => ({
  source_arn: m.EventSourceArn,
  source_type: m.EventSourceArn?.split(":")[2] ?? "unknown",
  enabled: m.State === "Enabled",
  batch_size: m.BatchSize,
  state: m.State,
}));
```

- [ ] **Step 2: Add `filter_criteria` field**

Change that block to:

```js
return (res.EventSourceMappings ?? []).map(m => ({
  source_arn: m.EventSourceArn,
  source_type: m.EventSourceArn?.split(":")[2] ?? "unknown",
  enabled: m.State === "Enabled",
  batch_size: m.BatchSize,
  state: m.State,
  filter_criteria: m.FilterCriteria ?? null,
}));
```

- [ ] **Step 3: Write the failing test**

In `tests/test_mcp_server.js`, find the existing `ace_check_event_source` test block (currently only asserts `Array.isArray(result)`). Add a shape assertion for `filter_criteria`:

```js
it("ace_check_event_source includes filter_criteria field", async () => {
  const t = tool(probeTools, "ace_check_event_source");
  const result = await t.handler({ function_name: FN });
  assert.ok(Array.isArray(result));
  for (const mapping of result) {
    assert.ok("filter_criteria" in mapping, "each mapping must have filter_criteria key");
  }
});
```

> Note: `FN = "test-identity-fn"` is already created in the `before()` block. The event source mapping created there may or may not have FilterCriteria — the test asserts the key exists (even if null), not that it has a value.

- [ ] **Step 4: Run the test to verify it fails (before the fix)**

```bash
node --test tests/test_mcp_server.js 2>&1 | grep -A 3 "filter_criteria"
```

Expected: `AssertionError` — `filter_criteria` key missing from mapping objects.

- [ ] **Step 5: Run full test suite to confirm fix passes**

```bash
node --test tests/test_mcp_server.js
```

Expected: all tests pass, including the new `filter_criteria` assertion.

- [ ] **Step 6: Commit**

```bash
git add harness/mcp_server/tools/probe.js tests/test_mcp_server.js
git commit -m "feat(mcp): expose FilterCriteria in ace_check_event_source response"
```

---

### Task 2: Add `ace_scan_table_range` tool to `probe_extended.js` (A2)

**Files:**
- Modify: `harness/mcp_server/tools/probe_extended.js` (top imports + awsConfig block + end of `probeExtendedTools` array)
- Test: `tests/test_mcp_server.js`

- [ ] **Step 1: Write failing tests first**

In `tests/test_mcp_server.js`, add a `RANGE_TABLE` constant alongside the existing `TABLE` constant:

```js
const RANGE_TABLE = "test-range-table";
```

In the `before()` block, after the existing `TABLE` creation, add:

```js
// create HASH+RANGE table for ace_scan_table_range tests
await dynamoClient.send(new CreateTableCommand({
  TableName: RANGE_TABLE,
  AttributeDefinitions: [
    { AttributeName: "pk", AttributeType: "S" },
    { AttributeName: "sk", AttributeType: "S" },
  ],
  KeySchema: [
    { AttributeName: "pk", KeyType: "HASH" },
    { AttributeName: "sk", KeyType: "RANGE" },
  ],
  BillingMode: "PAY_PER_REQUEST",
}));
// seed one item
await dynamoClient.send(new PutItemCommand({
  TableName: RANGE_TABLE,
  Item: marshall({ pk: "user-1", sk: "profile", name: "Alice" }),
}));
```

> `CreateTableCommand`, `PutItemCommand`, `marshall` are already imported at the top of the test file — verify and add if missing.

Add the tests:

```js
it("ace_scan_table_range returns items matching key condition", async () => {
  const t = probeExtendedTools.find(t => t.name === "ace_scan_table_range");
  assert.ok(t, "ace_scan_table_range tool must exist");
  const result = await t.handler({
    table_name: RANGE_TABLE,
    key_condition: "pk = :pk",
    expression_values: { ":pk": "user-1" },
  });
  assert.ok(!result.error, `unexpected error: ${result.error}`);
  assert.ok("items" in result);
  assert.ok("count" in result);
  assert.ok("scanned_count" in result);
  assert.strictEqual(result.count, 1);
  assert.strictEqual(result.items[0].pk, "user-1");
});

it("ace_scan_table_range clamps limit to 25", async () => {
  const t = probeExtendedTools.find(t => t.name === "ace_scan_table_range");
  const result = await t.handler({
    table_name: RANGE_TABLE,
    key_condition: "pk = :pk",
    expression_values: { ":pk": "user-1" },
    limit: 999,
  });
  assert.ok(!result.error);
  assert.ok(result.count <= 25);
});

it("ace_scan_table_range returns error for nonexistent table", async () => {
  const t = probeExtendedTools.find(t => t.name === "ace_scan_table_range");
  const result = await t.handler({
    table_name: "nonexistent-table-xyz",
    key_condition: "pk = :pk",
    expression_values: { ":pk": "x" },
  });
  assert.ok(result.error, "should return error for nonexistent table");
});

it("ace_scan_table_range returns error when table_name missing", async () => {
  const t = probeExtendedTools.find(t => t.name === "ace_scan_table_range");
  const result = await t.handler({ key_condition: "pk = :pk", expression_values: { ":pk": "x" } });
  assert.ok(result.error);
});

it("ace_scan_table_range returns error when key_condition missing", async () => {
  const t = probeExtendedTools.find(t => t.name === "ace_scan_table_range");
  const result = await t.handler({ table_name: RANGE_TABLE, expression_values: { ":pk": "x" } });
  assert.ok(result.error);
});

it("ace_scan_table_range returns error when expression_values missing", async () => {
  const t = probeExtendedTools.find(t => t.name === "ace_scan_table_range");
  const result = await t.handler({ table_name: RANGE_TABLE, key_condition: "pk = :pk" });
  assert.ok(result.error);
});
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
node --test tests/test_mcp_server.js 2>&1 | grep -E "(fail|ace_scan_table_range)"
```

Expected: `ace_scan_table_range` tests fail with "ace_scan_table_range tool must exist".

- [ ] **Step 3: Add DynamoDB imports to `probe_extended.js`**

At the top of `probe_extended.js`, the existing imports include `DynamoDBStreamsClient`. Add `DynamoDBClient` and `QueryCommand` to the existing dynamodb import line. If there is no existing `@aws-sdk/client-dynamodb` import (only streams), add a new import line:

```js
import { DynamoDBClient, QueryCommand } from "@aws-sdk/client-dynamodb";
import { marshall, unmarshall } from "@aws-sdk/util-dynamodb";
```

> Check the existing imports first. `@aws-sdk/util-dynamodb` may already be imported. If `unmarshall` is already imported, add only `marshall` to that line. Don't duplicate imports.

- [ ] **Step 4: Add `dynamoClient` instance to `probe_extended.js`**

After the existing client instantiations (e.g., `const dynamoStreamsClient = new DynamoDBStreamsClient(awsConfig);`), add:

```js
const dynamoClient = new DynamoDBClient(awsConfig);
```

- [ ] **Step 5: Append `ace_scan_table_range` to `probeExtendedTools` array**

Find the closing `];` of the `probeExtendedTools` array. Insert the new tool object before it:

```js
  {
    name: "ace_scan_table_range",
    description: "Query a DynamoDB table or index with a key condition expression. Read-only. Returns up to 25 items.",
    inputSchema: {
      type: "object",
      properties: {
        table_name: { type: "string" },
        index_name: { type: "string" },
        key_condition: { type: "string" },
        expression_values: { type: "object" },
        limit: { type: "number" },
      },
      required: ["table_name", "key_condition", "expression_values"],
    },
    async handler({ table_name, index_name, key_condition, expression_values, limit = 10 } = {}) {
      if (!table_name) return { error: "table_name is required" };
      if (!key_condition) return { error: "key_condition is required" };
      if (!expression_values || typeof expression_values !== "object")
        return { error: "expression_values is required and must be an object" };
      const clampedLimit = Math.min(Math.max(1, limit ?? 10), 25);
      try {
        const params = {
          TableName: table_name,
          KeyConditionExpression: key_condition,
          ExpressionAttributeValues: marshall(expression_values),
          Limit: clampedLimit,
        };
        if (index_name) params.IndexName = index_name;
        const res = await dynamoClient.send(new QueryCommand(params));
        return {
          items: (res.Items ?? []).map(item => unmarshall(item)),
          count: res.Count ?? 0,
          scanned_count: res.ScannedCount ?? 0,
        };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "DYNAMO_ERROR" };
      }
    },
  },
```

- [ ] **Step 6: Run full test suite**

```bash
node --test tests/test_mcp_server.js
```

Expected: all tests pass, including all six new `ace_scan_table_range` tests.

- [ ] **Step 7: Commit**

```bash
git add harness/mcp_server/tools/probe_extended.js tests/test_mcp_server.js
git commit -m "feat(mcp): add ace_scan_table_range DynamoDB query tool"
```

---

## Self-Review

**Spec coverage:**
- A1 (filter_criteria field): Task 1 ✓
- A2 (ace_scan_table_range tool): Task 2 ✓
- Tests for A1 shape assertion: Task 1 Step 3 ✓
- Tests for A2 happy path, limit clamp, missing params, nonexistent table: Task 2 Step 1 ✓
- No index.js changes needed (probeExtendedTools already spread): confirmed ✓

**Placeholder scan:** No TBD/TODO/placeholder text found. All test assertions and code blocks are concrete.

**Type consistency:**
- `dynamoClient` used in the handler matches the `const dynamoClient = new DynamoDBClient(awsConfig)` added in Task 2 Step 4 ✓
- `marshall` used in handler matches import in Task 2 Step 3 ✓
- `unmarshall` used in handler matches import in Task 2 Step 3 ✓
- `QueryCommand` used in handler matches import in Task 2 Step 3 ✓
- `probeExtendedTools` export name matches what `index.js` spreads and what tests import ✓
