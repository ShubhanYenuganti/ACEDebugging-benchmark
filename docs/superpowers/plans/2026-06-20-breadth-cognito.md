# Cognito Auth Architecture (arch07) Implementation Plan

> **REQUIRED SUB-SKILL:** `superpowers:executing-plans` — run this plan with that skill active. Worker subagents use model `sonnet`; overseer uses model `opus`.

---

## Goal

Add arch07 — Cognito-authenticated API — as a new ACE-Bench architecture family. The corpus models: a Cognito User Pool + app client acting as the identity provider; an API Gateway REST API protected by a Cognito authorizer; a Lambda handler that receives only authenticated requests; and a DynamoDB table as the backing store. A client obtains a JWT via `InitiateAuth` / `AdminInitiateAuth`, attaches it as `Authorization: Bearer <token>`, and the API Gateway authorizer validates the token against the user pool before forwarding to Lambda.

## Architecture (arch07)

```
[Cognito User Pool]
       |
  InitiateAuth → JWT (IdToken / AccessToken)
       |
[API Gateway REST]  ← Cognito Authorizer (validates token: issuer, audience, expiry)
       |  (Authorization: Bearer <JWT>)
  [Lambda handler] → [DynamoDB table]
```

**Services:** `cognito-idp`, `apigateway`, `lambda`, `dynamodb`, `iam`, `logs`

**Tech Stack:**
- MCP tools: `harness/mcp_server/tools/probe_cognito.js` (new), SDK `@aws-sdk/client-cognito-identity-provider`
- Corpus: `corpus/arch_07_cognito_authenticated_api/`
- Scenarios: `scenarios/arch07_fault0N_<class>/`
- Handler: Python 3.11 Lambda (inline zip — no psycopg2; pure boto3/stdlib)

---

## Global Constraints

- LocalStack runs with `ENFORCE_IAM=1 IAM_SOFT_MODE=0`; any fixture/scenario creating Lambdas must define a real assumable IAM role.
- AWS creds: accessKeyId=`test`, secretAccessKey=`test`, region=`us-east-1`, endpoint `http://localhost:4566`. IAM fake account `000000000000`.
- Prefer real AWS APIs; never use LocalStack-proprietary introspection (App Inspector, IAM Policy Streams).
- `fault_manifest.json` and `known_good.yaml` are NEVER exposed to the model.
- **Fault design principle (mandatory):** every fault must produce an observable behavioral symptom Pass-1 functional verification detects; `scenario.md` states only the symptom, never the cause. No posture-only faults. Because all four candidate faults manifest as auth rejection, each MUST produce a **distinct error signature** (HTTP status code, error body, or Lambda log message that uniquely fingerprints the fault class — confirmed empirically in the spike).
- MCP tool files live in `harness/mcp_server/tools/` and are spread into `harness/mcp_server/index.js`. Each tool is `{ name, description, inputSchema, async handler(args) }` and returns a plain object (never throws).
- Corpus dir name: `corpus/arch_07_cognito_authenticated_api/`. Scenario dirs: `scenarios/arch07_fault0N_<class>/`.
- Node MCP tests: `node --test tests/test_mcp_server.js`. Spike + corpus run against a live LocalStack (`localstack start -d`).
- **Pre-flight:** `cd harness/mcp_server && npm install` before any Node step.

---

## Task 1: De-risking spike (the gate)

Exploratory, not TDD. Validates the family's fault premises on the current LocalStack build before any fan-out. **Do not start Task 2 until this passes.** Findings are recorded in this plan as a `## Task 1 findings` section appended by the executor.

**Files:**
- Create: `scratch/spike_cognito.mjs` (gitignored; `scratch/` is already in `.gitignore`)
- Create: `scratch/spike_cognito_stack.yaml` (minimal CFN: User Pool + app client + API GW with Cognito authorizer + Lambda + DynamoDB)

**Interfaces:**
- Consumes: nothing (standalone spike).
- Produces: a recorded decision per fault mechanism (primary vs fallback), a capability×fidelity matrix, and the JWT-issuance + authorizer-enforcement finding — written as notes in Step 6 below. Tasks 2–4 read these notes.

- [ ] **Step 1: Confirm LocalStack is up with IAM enforcement and Cognito is available**

Run:
```bash
# Start LocalStack Ultimate with IAM enforcement (required for valid scoring).
LOCALSTACK_AUTH_TOKEN=ls-... ENFORCE_IAM=1 IAM_SOFT_MODE=0 localstack start -d
until localstack status services 2>/dev/null | grep -q running; do sleep 2; done
# Confirm cognito-idp and apigateway are emulated:
curl -s localhost:4566/_localstack/health | grep -oE '"cognito-idp"\s*:\s*"[a-z]+"'
curl -s localhost:4566/_localstack/health | grep -oE '"apigateway"\s*:\s*"[a-z]+"'
# Record the LocalStack version:
curl -s localhost:4566/_localstack/info | grep -oE '"version":\s*"[^"]+"'
```
Expected: `cognito-idp` and `apigateway` both present. If LocalStack is not running, start it first then wait.

- [ ] **Step 2: Write the minimal spike CFN stack**

Create `scratch/spike_cognito_stack.yaml` with:
- `AWS::Cognito::UserPool` (name `spike-pool`, password policy: min length 8, no MFA)
- `AWS::Cognito::UserPoolClient` (name `spike-client`, ExplicitAuthFlows: `[ALLOW_USER_PASSWORD_AUTH, ALLOW_REFRESH_TOKEN_AUTH]`, no client secret, token validity defaults)
- `AWS::ApiGateway::RestApi` (name `spike-api`)
- `AWS::ApiGateway::Authorizer` (Type: `COGNITO_USER_POOLS`, ProviderARNs: `[!GetAtt SpikeUserPool.Arn]`, IdentitySource: `method.request.header.Authorization`)
- `AWS::ApiGateway::Resource` (path `/items`)
- `AWS::ApiGateway::Method` (GET, AuthorizationType: `COGNITO_USER_POOLS`, AuthorizerId: `!Ref SpikeAuthorizer`)
- Lambda function (inline handler: return `{statusCode: 200, body: JSON.stringify({ok: true})}`) with IAM role, wired to the GET method via `AWS_PROXY`
- `AWS::ApiGateway::Deployment` + `AWS::ApiGateway::Stage` (prod)
- `AWS::DynamoDB::Table` (PK: `item_id` S, PAY_PER_REQUEST)

Outputs: `UserPoolId`, `UserPoolClientId`, `ApiId`, `ApiUrl` (the full `https://<id>.execute-api.localhost.localstack.cloud:4566/prod` URL).

- [ ] **Step 3: Provisioning and tool-data fidelity check**

Write `scratch/spike_cognito.mjs` to deploy the stack (`CreateStack` + wait `stack_create_complete`). After deploy, call:
- `DescribeUserPools` / `DescribeUserPool` — record which fields populate (`Id`, `Name`, `Status`, `Policies`, `SchemaAttributes`, `MfaConfiguration`, `LambdaConfig`, `UserPoolTags`).
- `DescribeUserPoolClient` — record which fields populate (`ClientId`, `ClientName`, `ExplicitAuthFlows`, `AllowedOAuthFlows`, `AllowedOAuthScopes`, `IdTokenValidity`, `AccessTokenValidity`, `RefreshTokenValidity`, `TokenValidityUnits`).

Run: `node scratch/spike_cognito.mjs provision`
Expected: `CREATE_COMPLETE`; all candidate tool fields non-empty. Record in the matrix (Step 6).

- [ ] **Step 4: JWT issuance and API Gateway authorizer enforcement (the critical probes)**

Extend `scratch/spike_cognito.mjs` with these probes and run `node scratch/spike_cognito.mjs enforce`:

**Probe A — JWT issuance:**
1. Create a test user in the pool (`AdminCreateUser` with `SUPPRESS` message, then `AdminSetUserPassword` with permanent=true).
2. Call `InitiateAuth` (AUTH_FLOW: `USER_PASSWORD_AUTH`, ClientId from stack output) with the test credentials.
3. Record: does LocalStack return an `AuthenticationResult.IdToken` (a real JWT string)? Is it a validatable JWT (three `.`-separated base64 segments)?

**Probe B — Authorizer enforcement (the kill-gate question):**
1. Call the API Gateway URL (`GET /items`) with NO `Authorization` header. Record the HTTP status code.
2. Call with a **valid JWT** from Probe A as `Authorization: Bearer <token>`. Record status code.
3. Call with a **garbage/invalid token** (`Authorization: Bearer invalid.token.here`). Record status code.
4. Call with a JWT for a **different user pool** (create a second pool, issue a JWT from it, use it against the first pool's authorizer). Record status code.

**Probe C — Wrong-client-auth-flow enforcement:**
1. Create a second app client for the same pool with `ExplicitAuthFlows: [ALLOW_REFRESH_TOKEN_AUTH]` only (no `ALLOW_USER_PASSWORD_AUTH`).
2. Attempt `InitiateAuth` (USER_PASSWORD_AUTH) with the second client. Record whether LocalStack returns `NotAuthorizedException` / `InvalidParameterException` or succeeds silently.

**Probe D — Scope/claim enforcement at handler side:**
If the API Gateway authorizer passes all tokens equally (doesn't enforce scopes/claims), then scope-based faults would be posture-only. Record what claims LocalStack puts in the token's payload (decode the JWT body — no verification needed for inspection).

Record labeled output lines for each probe: `[PROBE_A] token_issued=true/false`, `[PROBE_B_NO_AUTH] status=401/403/200`, etc.

- [ ] **Step 5: Distinct error signature confirmation**

For each candidate fault, confirm the error signature is distinct from all others by checking the HTTP response body and Lambda log messages (if any):
- **fault01** (wrong auth flow on client): `InitiateAuth` fails before a token is issued → client-side error, no token → API call returns 401 (no token sent).
- **fault02** (wrong pool ARN on authorizer): valid JWT from correct pool is rejected by authorizer pointed at wrong pool → 401 from API GW, distinct from fault04 (which is 403).
- **fault03** (missing scope/required claim — handler-side check): token is valid and authorizer passes it, but handler rejects it with 403 (reads `event.requestContext.authorizer.claims` and checks a required custom attribute / scope).
- **fault04** (token expiry too short — `AccessTokenValidity: 1` with `TokenValidityUnits: minutes`): token expires immediately; subsequent calls with the same token return 401 with a "Token is expired" context.

If the API Gateway authorizer does NOT enforce invalid tokens (passes them through), the plan must fall back to handler-side validation for all token-rejection faults — record this as "authorizer-not-enforced" and pivot to handler-side JWT validation using `python-jose` or PyJWT in the Lambda handler.

- [ ] **Step 6: Record findings + lock decisions**

Append a `## Task 1 findings` section to THIS plan file (immediately after this task's steps) with:
- The LocalStack version string.
- A capability×fidelity matrix:

| Capability | API call | Status |
|---|---|---|
| Pool config fidelity | `DescribeUserPool` | ✅/⚠️/❌ |
| Client config fidelity | `DescribeUserPoolClient` | ✅/⚠️/❌ |
| JWT issuance | `InitiateAuth` | ✅/⚠️/❌ |
| JWT validation (authorizer) | API GW + valid token | ✅/⚠️/❌ |
| Auth flow enforcement | `InitiateAuth` wrong flow | ✅/⚠️/❌ |
| Wrong-pool rejection | API GW + cross-pool token | ✅/⚠️/❌ |

- Locked fault mechanisms (primary + fallback) per fault:
  - fault01 mechanism = wrong `ExplicitAuthFlows` on client (if auth-flow enforcement confirmed) else wrong `ClientId` env var on Lambda handler.
  - fault02 mechanism = wrong pool ARN on authorizer (if cross-pool rejection confirmed) else wrong `UserPoolId` env var used in handler-side validation.
  - fault03 mechanism = missing required custom attribute / scope check in handler (always handler-side; only ships if handler-side check produces 403 distinct from 401).
  - fault04 mechanism = `AccessTokenValidity: 1 + TokenValidityUnits: minutes` (if token-expiry enforcement confirmed) else wrong `IdentitySource` header name on authorizer (produces 401 on every call).
- JWT issuance decision: does LocalStack issue real JWTs or placeholder strings? If placeholder only, the authorizer enforcement tests are vacuous — record as "shelved" and document why.
- Fault count decision: if fewer than 3 faults have distinct enforcement, record remaining faults as shelved and reduce the plan scope accordingly (minimum viable = 2 behavior-manifesting, distinct-error-signature faults).

Commit this plan-file update:
```bash
git add docs/superpowers/plans/2026-06-20-breadth-cognito.md
git commit -m "docs(plan): record arch07 Cognito spike findings and locked fault mechanisms"
```

- [ ] **Step 7: Tear down the spike stack**

Run `node scratch/spike_cognito.mjs teardown` (DeleteStack + wait). No commit (scratch is gitignored).

---

## Task 1 findings

> **[EXECUTOR: fill this section after running the spike. Do NOT fabricate results. Lock the fault mechanisms here; Tasks 2–4 reference these decisions.]**

### LocalStack version

`version: <fill>`, `edition: <fill>`, IAM enforcement: `<active/inactive>`

### Capability × fidelity matrix

| Capability | API call | Status | Notes |
|---|---|---|---|
| Pool config fidelity | `DescribeUserPool` | | |
| Client config fidelity | `DescribeUserPoolClient` | | |
| JWT issuance | `InitiateAuth` (USER_PASSWORD_AUTH) | | |
| Authorizer: valid token | GET /items + valid JWT | | |
| Authorizer: no token | GET /items + no header | | |
| Authorizer: invalid token | GET /items + garbage JWT | | |
| Auth-flow enforcement | `InitiateAuth` with disallowed flow | | |
| Wrong-pool rejection | GET /items + cross-pool JWT | | |
| Token expiry enforcement | call after AccessTokenValidity expires | | |

### Locked fault mechanisms

- **fault01 mechanism:** PRIMARY = `<fill>`; FALLBACK = `<fill>`.
- **fault02 mechanism:** PRIMARY = `<fill>`; FALLBACK = `<fill>`.
- **fault03 mechanism:** PRIMARY = `<fill>`; FALLBACK = `<fill>`.
- **fault04 mechanism:** PRIMARY = `<fill>`; FALLBACK = `<fill>` (if JWT issuance and enforcement both live) OR SHELVED if not enough distinct error signatures.

### JWT issuance decision

`<real JWTs issued / placeholder only / issuance fails>`

### Distinct error signature summary

| Fault | Expected HTTP status | Expected error fingerprint |
|---|---|---|
| fault01 | `<fill>` | `<fill>` |
| fault02 | `<fill>` | `<fill>` |
| fault03 | `<fill>` | `<fill>` |
| fault04 | `<fill>` | `<fill>` |

### Authorizer enforcement decision

`<enforced / not-enforced → fall back to handler-side validation>`

---

## Task 2: Cognito MCP diagnostic tools (TDD)

Adds `harness/mcp_server/tools/probe_cognito.js` with three tools and wires it into `index.js`. TDD via `node:test`.

**Files:**
- Create: `harness/mcp_server/tools/probe_cognito.js`
- Modify: `harness/mcp_server/index.js` (import + spread `probeCognitoTools`)
- Modify: `harness/mcp_server/package.json` (add `@aws-sdk/client-cognito-identity-provider`)
- Test: `tests/test_mcp_server.js` (append import + tool tests)

**Interfaces:**
- Consumes: the `awsConfig` pattern from `probe_extended.js`; the `tool(list, name)` helper in `tests/test_mcp_server.js`.
- Produces: `export const probeCognitoTools` — an array of three tools:
  - `ace_describe_user_pool({ user_pool_id })` → `{ id, name, status, creation_date, mfa_configuration, password_policies, schema_attributes: [{ name, attribute_data_type, required, mutable }], lambda_config: { pre_token_generation, post_authentication, ... }, tags }` or `{ error }`.
  - `ace_describe_user_pool_client({ user_pool_id, client_id })` → `{ client_id, client_name, user_pool_id, explicit_auth_flows: string[], allowed_oauth_flows: string[], allowed_oauth_scopes: string[], id_token_validity, access_token_validity, refresh_token_validity, token_validity_units, prevent_user_existence_errors }` or `{ error }`.
  - `ace_probe_authorizer_token({ user_pool_id, client_id, username, password, api_url, path?, method? })` → `{ token_issued: boolean, auth_flow_outcome: "success"|"not_authorized"|"invalid_parameter"|"error", token_type: "id"|"access"|null, api_status_code: number|null, api_outcome: "authorized"|"unauthorized"|"forbidden"|"error"|null, detail?: string }` or `{ error }`.

**Tool descriptions (must satisfy static rubric — all three fields: API mapped, fields returned, when to reach for it):**
- `ace_describe_user_pool`: "Cognito DescribeUserPool: return one User Pool's configuration — id, name, status, mfa_configuration, password_policies, schema_attributes (name/attribute_data_type/required/mutable), lambda_config (pre_token_generation, post_authentication trigger ARNs), tags. Use to diagnose auth-flow faults where the pool config itself is misconfigured (wrong MFA setting, missing schema attribute, wrong lambda trigger)."
- `ace_describe_user_pool_client`: "Cognito DescribeUserPoolClient: return one app client's configuration — client_id, explicit_auth_flows (allowed InitiateAuth flows), allowed_oauth_flows, allowed_oauth_scopes, id/access/refresh_token_validity, token_validity_units, prevent_user_existence_errors. Use to diagnose authentication failures caused by a missing auth flow on the client (e.g. USER_PASSWORD_AUTH not in ExplicitAuthFlows) or by a token-validity fault."
- `ace_probe_authorizer_token`: "Cognito InitiateAuth + API Gateway probe: attempts InitiateAuth (USER_PASSWORD_AUTH) with the given credentials and client, then calls the API Gateway endpoint with the issued token as Bearer Authorization. Returns auth_flow_outcome (success/not_authorized/invalid_parameter/error), token_type (id or access), api_status_code, and api_outcome (authorized/unauthorized/forbidden/error). Use as the first tool when any request returns 401 or 403: it distinguishes token-issuance failures (client config fault) from authorizer-rejection failures (wrong pool / expired token / missing claim)."

- [ ] **Step 1: Add the Cognito SDK dependency**

Run:
```bash
cd harness/mcp_server && npm install @aws-sdk/client-cognito-identity-provider && cd -
```
Expected: `@aws-sdk/client-cognito-identity-provider` appears in `harness/mcp_server/package.json` dependencies.

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_mcp_server.js`. First add the import near the other tool imports at the top of the file:
```javascript
import { probeCognitoTools } from "../harness/mcp_server/tools/probe_cognito.js";
```
Then append the following tests (no seeded Cognito pool is required — they assert error handling):
```javascript
test("probeCognitoTools exposes the three Cognito tools", () => {
  for (const n of ["ace_describe_user_pool", "ace_describe_user_pool_client", "ace_probe_authorizer_token"]) {
    assert.ok(tool(probeCognitoTools, n), `missing ${n}`);
  }
});

test("ace_describe_user_pool: missing user_pool_id returns error", async () => {
  const res = await tool(probeCognitoTools, "ace_describe_user_pool").handler({});
  assert.ok(res.error);
});

test("ace_describe_user_pool: unknown pool id returns error", async () => {
  const res = await tool(probeCognitoTools, "ace_describe_user_pool").handler({ user_pool_id: "us-east-1_nonexistent99" });
  assert.ok(res.error);
});

test("ace_describe_user_pool_client: missing user_pool_id returns error", async () => {
  const res = await tool(probeCognitoTools, "ace_describe_user_pool_client").handler({ client_id: "abc" });
  assert.ok(res.error);
});

test("ace_describe_user_pool_client: missing client_id returns error", async () => {
  const res = await tool(probeCognitoTools, "ace_describe_user_pool_client").handler({ user_pool_id: "us-east-1_test" });
  assert.ok(res.error);
});

test("ace_describe_user_pool_client: unknown pool+client returns error", async () => {
  const res = await tool(probeCognitoTools, "ace_describe_user_pool_client").handler({ user_pool_id: "us-east-1_nonexistent99", client_id: "abc123" });
  assert.ok(res.error);
});

test("ace_probe_authorizer_token: missing required args returns error", async () => {
  const res = await tool(probeCognitoTools, "ace_probe_authorizer_token").handler({});
  assert.ok(res.error);
});

test("ace_probe_authorizer_token: bad pool returns not_authorized or error outcome", async () => {
  const res = await tool(probeCognitoTools, "ace_probe_authorizer_token").handler({
    user_pool_id: "us-east-1_doesnotexist",
    client_id: "fakeclientid",
    username: "nobody",
    password: "Badpassword1!",
    api_url: "http://localhost:4566",
  });
  // Either an error return or auth_flow_outcome indicates failure
  assert.ok(res.error || ["not_authorized", "invalid_parameter", "error"].includes(res.auth_flow_outcome));
});
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `node --test tests/test_mcp_server.js 2>&1 | grep -A2 'probeCognitoTools\|ace_describe_user_pool\|ace_probe_authorizer'`
Expected: FAIL — `Cannot find module '.../probe_cognito.js'`.

- [ ] **Step 4: Implement `probe_cognito.js`**

Create `harness/mcp_server/tools/probe_cognito.js`:
```javascript
import {
  CognitoIdentityProviderClient,
  DescribeUserPoolCommand,
  DescribeUserPoolClientCommand,
  InitiateAuthCommand,
} from "@aws-sdk/client-cognito-identity-provider";

const awsConfig = {
  endpoint: process.env.LOCALSTACK_ENDPOINT ?? "http://localhost:4566",
  region: "us-east-1",
  credentials: { accessKeyId: "test", secretAccessKey: "test" },
};

export const probeCognitoTools = [
  {
    name: "ace_describe_user_pool",
    description:
      "Cognito DescribeUserPool: return one User Pool's configuration — id, name, status, mfa_configuration, password_policies, schema_attributes (name/attribute_data_type/required/mutable), lambda_config (pre_token_generation, post_authentication trigger ARNs), tags. Use to diagnose auth-flow faults where the pool config itself is misconfigured (wrong MFA setting, missing schema attribute, wrong lambda trigger).",
    inputSchema: {
      type: "object",
      properties: {
        user_pool_id: { type: "string", description: "The Cognito User Pool ID (e.g. us-east-1_abc123)" },
      },
      required: ["user_pool_id"],
    },
    async handler(args) {
      if (!args?.user_pool_id) return { error: "user_pool_id is required" };
      try {
        const client = new CognitoIdentityProviderClient(awsConfig);
        const res = await client.send(new DescribeUserPoolCommand({ UserPoolId: args.user_pool_id }));
        const p = res.UserPool ?? {};
        return {
          id: p.Id ?? null,
          name: p.Name ?? null,
          status: p.Status ?? null,
          creation_date: p.CreationDate ?? null,
          mfa_configuration: p.MfaConfiguration ?? null,
          password_policies: p.Policies?.PasswordPolicy ?? null,
          schema_attributes: (p.SchemaAttributes ?? []).map((s) => ({
            name: s.Name ?? null,
            attribute_data_type: s.AttributeDataType ?? null,
            required: s.Required ?? null,
            mutable: s.Mutable ?? null,
          })),
          lambda_config: p.LambdaConfig ?? null,
          tags: p.UserPoolTags ?? null,
        };
      } catch (err) {
        return { error: String(err?.message ?? err) };
      }
    },
  },
  {
    name: "ace_describe_user_pool_client",
    description:
      "Cognito DescribeUserPoolClient: return one app client's configuration — client_id, explicit_auth_flows (allowed InitiateAuth flows), allowed_oauth_flows, allowed_oauth_scopes, id/access/refresh_token_validity, token_validity_units, prevent_user_existence_errors. Use to diagnose authentication failures caused by a missing auth flow on the client (e.g. USER_PASSWORD_AUTH not in ExplicitAuthFlows) or by a token-validity fault.",
    inputSchema: {
      type: "object",
      properties: {
        user_pool_id: { type: "string", description: "The Cognito User Pool ID" },
        client_id: { type: "string", description: "The app client ID" },
      },
      required: ["user_pool_id", "client_id"],
    },
    async handler(args) {
      if (!args?.user_pool_id) return { error: "user_pool_id is required" };
      if (!args?.client_id) return { error: "client_id is required" };
      try {
        const client = new CognitoIdentityProviderClient(awsConfig);
        const res = await client.send(
          new DescribeUserPoolClientCommand({ UserPoolId: args.user_pool_id, ClientId: args.client_id })
        );
        const c = res.UserPoolClient ?? {};
        return {
          client_id: c.ClientId ?? null,
          client_name: c.ClientName ?? null,
          user_pool_id: c.UserPoolId ?? null,
          explicit_auth_flows: c.ExplicitAuthFlows ?? [],
          allowed_oauth_flows: c.AllowedOAuthFlows ?? [],
          allowed_oauth_scopes: c.AllowedOAuthScopes ?? [],
          id_token_validity: c.IdTokenValidity ?? null,
          access_token_validity: c.AccessTokenValidity ?? null,
          refresh_token_validity: c.RefreshTokenValidity ?? null,
          token_validity_units: c.TokenValidityUnits ?? null,
          prevent_user_existence_errors: c.PreventUserExistenceErrors ?? null,
        };
      } catch (err) {
        return { error: String(err?.message ?? err) };
      }
    },
  },
  {
    name: "ace_probe_authorizer_token",
    description:
      "Cognito InitiateAuth + API Gateway probe: attempts InitiateAuth (USER_PASSWORD_AUTH) with the given credentials and client, then calls the API Gateway endpoint with the issued token as Bearer Authorization. Returns auth_flow_outcome (success/not_authorized/invalid_parameter/error), token_type (id or access), api_status_code, and api_outcome (authorized/unauthorized/forbidden/error). Use as the first tool when any request returns 401 or 403: it distinguishes token-issuance failures (client config fault) from authorizer-rejection failures (wrong pool / expired token / missing claim).",
    inputSchema: {
      type: "object",
      properties: {
        user_pool_id: { type: "string", description: "The Cognito User Pool ID" },
        client_id: { type: "string", description: "The app client ID to use for InitiateAuth" },
        username: { type: "string", description: "Username of the test user to authenticate" },
        password: { type: "string", description: "Password of the test user" },
        api_url: { type: "string", description: "Base URL of the API Gateway endpoint to probe (e.g. http://abc.execute-api.localhost.localstack.cloud:4566/prod)" },
        path: { type: "string", description: "Path to append after api_url (default: /items)" },
        method: { type: "string", description: "HTTP method (default: GET)" },
      },
      required: ["user_pool_id", "client_id", "username", "password", "api_url"],
    },
    async handler(args) {
      if (!args?.user_pool_id) return { error: "user_pool_id is required" };
      if (!args?.client_id) return { error: "client_id is required" };
      if (!args?.username) return { error: "username is required" };
      if (!args?.password) return { error: "password is required" };
      if (!args?.api_url) return { error: "api_url is required" };

      const cognitoClient = new CognitoIdentityProviderClient(awsConfig);
      let auth_flow_outcome = "error";
      let token_type = null;
      let issued_token = null;
      let detail = undefined;

      // Step 1: attempt token issuance
      try {
        const authRes = await cognitoClient.send(
          new InitiateAuthCommand({
            AuthFlow: "USER_PASSWORD_AUTH",
            ClientId: args.client_id,
            AuthParameters: { USERNAME: args.username, PASSWORD: args.password },
          })
        );
        if (authRes.AuthenticationResult?.IdToken) {
          auth_flow_outcome = "success";
          token_type = "id";
          issued_token = authRes.AuthenticationResult.IdToken;
        } else if (authRes.AuthenticationResult?.AccessToken) {
          auth_flow_outcome = "success";
          token_type = "access";
          issued_token = authRes.AuthenticationResult.AccessToken;
        } else {
          auth_flow_outcome = "error";
          detail = "InitiateAuth succeeded but no token in response";
        }
      } catch (err) {
        const code = err?.name ?? "";
        if (code === "NotAuthorizedException" || code === "UserNotFoundException") {
          auth_flow_outcome = "not_authorized";
        } else if (code === "InvalidParameterException") {
          auth_flow_outcome = "invalid_parameter";
        } else {
          auth_flow_outcome = "error";
        }
        detail = String(err?.message ?? err);
      }

      // Step 2: probe the API Gateway with the token (if issued)
      let api_status_code = null;
      let api_outcome = null;
      const path = args.path ?? "/items";
      const method = args.method ?? "GET";
      const target = `${args.api_url.replace(/\/$/, "")}${path}`;

      try {
        const headers = {};
        if (issued_token) headers["Authorization"] = `Bearer ${issued_token}`;
        const { default: https } = await import("node:https");
        const { default: http } = await import("node:http");
        const proto = target.startsWith("https") ? https : http;
        api_status_code = await new Promise((resolve, reject) => {
          const req = proto.request(target, { method, headers }, (res) => resolve(res.statusCode));
          req.on("error", reject);
          req.end();
        });
        if (api_status_code >= 200 && api_status_code < 300) api_outcome = "authorized";
        else if (api_status_code === 401) api_outcome = "unauthorized";
        else if (api_status_code === 403) api_outcome = "forbidden";
        else api_outcome = "error";
      } catch (err) {
        api_outcome = "error";
        detail = (detail ? detail + "; " : "") + `API probe error: ${String(err?.message ?? err)}`;
      }

      return { token_issued: auth_flow_outcome === "success", auth_flow_outcome, token_type, api_status_code, api_outcome, ...(detail ? { detail } : {}) };
    },
  },
];
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `node --test tests/test_mcp_server.js 2>&1 | grep -A2 'probeCognitoTools\|ace_describe_user_pool\|ace_probe_authorizer'`
Expected: all `probeCognitoTools` / `ace_describe_user_pool*` / `ace_probe_authorizer_token` tests PASS; no prior tests regress.

If any test fails, fix `probe_cognito.js` before continuing. Do not move to Step 6 with failing tests.

- [ ] **Step 6: Wire into `index.js`**

In `harness/mcp_server/index.js`, add the import alongside the other tool imports:
```javascript
import { probeCognitoTools } from "./tools/probe_cognito.js";
```
Add `...probeCognitoTools` to the spread in the `for` loop:
```javascript
for (const tool of [...probeTools, ...probeExtendedTools, ...observeTools, ...observeExtendedTools, ...observeTracingTools, ...probeRdsTools, ...probeCognitoTools, ...scoreTools]) {
```

- [ ] **Step 7: Verify `index.js` loads cleanly**

Run: `node --input-type=module <<'EOF'
import './harness/mcp_server/index.js';
EOF`
Expected: no import errors. (The MCP transport will hang waiting for stdin — that is fine; CTRL+C after 2 s.)

Alternatively: `node -e "import('./harness/mcp_server/index.js').catch(e=>{console.error(e);process.exit(1)})" && echo ok`

- [ ] **Step 8: Commit**

```bash
git add harness/mcp_server/tools/probe_cognito.js harness/mcp_server/index.js harness/mcp_server/package.json harness/mcp_server/package-lock.json tests/test_mcp_server.js
git commit -m "feat(mcp): add Cognito diagnostic tools (ace_describe_user_pool, ace_describe_user_pool_client, ace_probe_authorizer_token)"
```

---

## Task 3: arch07 corpus (known-good)

Builds the working Cognito-authenticated API architecture and proves it deploys clean and passes functional tests under IAM enforcement.

**Files:**
- Create: `corpus/arch_07_cognito_authenticated_api/known_good.yaml`
- Create: `corpus/arch_07_cognito_authenticated_api/functional_test.py`
- Create: `corpus/arch_07_cognito_authenticated_api/traffic_flow.md`
- Create: `corpus/arch_07_cognito_authenticated_api/deployment/lambda/api-handler/index.py`

**Interfaces:**
- Consumes: Task 1 findings (locked fault mechanisms inform which properties are "the correct value"); arch01's functional test conventions (`emit_pass`/`emit_fail`/`finalize`).
- Produces: a deployable `known_good.yaml` whose stack name is `ace-bench-stack`, exporting outputs `UserPoolId`, `UserPoolClientId`, `ApiId`, `ApiBaseUrl`, `TableName`, `LambdaFunctionName`. Task 4 faults and the functional test read these.

- [ ] **Step 1: Pre-flight**

```bash
cd harness/mcp_server && npm install && cd -
```

- [ ] **Step 2: Write `known_good.yaml`**

Create `corpus/arch_07_cognito_authenticated_api/known_good.yaml`. The CFN template must include:

```yaml
AWSTemplateFormatVersion: '2010-09-09'
Description: >
  ACE-Bench arch07: Cognito-authenticated API Gateway with Lambda and DynamoDB.
  A Cognito User Pool + app client protect a REST API via a Cognito authorizer.
  A seeded test user is created via a custom resource or deployment step.

Resources:
  # --- DynamoDB table ---
  ItemsTable:
    Type: AWS::DynamoDB::Table
    Properties:
      TableName: !Sub '${AWS::StackName}-items'
      BillingMode: PAY_PER_REQUEST
      AttributeDefinitions:
        - { AttributeName: item_id, AttributeType: S }
      KeySchema:
        - { AttributeName: item_id, KeyType: HASH }

  # --- Cognito User Pool ---
  UserPool:
    Type: AWS::Cognito::UserPool
    Properties:
      UserPoolName: !Sub '${AWS::StackName}-pool'
      Policies:
        PasswordPolicy:
          MinimumLength: 8
          RequireUppercase: false
          RequireLowercase: false
          RequireNumbers: false
          RequireSymbols: false
      MfaConfiguration: 'OFF'
      AutoVerifiedAttributes: []

  # --- Cognito App Client ---
  UserPoolClient:
    Type: AWS::Cognito::UserPoolClient
    Properties:
      ClientName: !Sub '${AWS::StackName}-client'
      UserPoolId: !Ref UserPool
      GenerateSecret: false
      ExplicitAuthFlows:
        - ALLOW_USER_PASSWORD_AUTH
        - ALLOW_REFRESH_TOKEN_AUTH
      IdTokenValidity: 60
      AccessTokenValidity: 60
      RefreshTokenValidity: 30
      TokenValidityUnits:
        IdToken: minutes
        AccessToken: minutes
        RefreshToken: days

  # --- Lambda execution role ---
  HandlerRole:
    Type: AWS::IAM::Role
    Properties:
      RoleName: !Sub '${AWS::StackName}-handler-role'
      AssumeRolePolicyDocument:
        Version: '2012-10-17'
        Statement:
          - Effect: Allow
            Principal: { Service: lambda.amazonaws.com }
            Action: sts:AssumeRole
      Policies:
        - PolicyName: handler-policy
          PolicyDocument:
            Version: '2012-10-17'
            Statement:
              - Effect: Allow
                Action:
                  - dynamodb:PutItem
                  - dynamodb:GetItem
                  - dynamodb:Scan
                Resource: !GetAtt ItemsTable.Arn
              - Effect: Allow
                Action:
                  - logs:CreateLogGroup
                  - logs:CreateLogStream
                  - logs:PutLogEvents
                Resource: '*'

  # --- Lambda function ---
  ApiHandlerFunction:
    Type: AWS::Lambda::Function
    Properties:
      FunctionName: !Sub '${AWS::StackName}-api-handler'
      Runtime: python3.11
      Handler: index.handler
      Role: !GetAtt HandlerRole.Arn
      Timeout: 30
      Environment:
        Variables:
          TABLE_NAME: !Ref ItemsTable
          REGION: !Sub '${AWS::Region}'
          DYNAMODB_ENDPOINT: 'http://localhost:4566'
      Code:
        ZipFile: |
          import json, os, uuid, boto3
          ENDPOINT = os.environ.get('DYNAMODB_ENDPOINT', None)
          TABLE = os.environ['TABLE_NAME']
          db = boto3.resource('dynamodb', endpoint_url=ENDPOINT, region_name=os.environ.get('REGION','us-east-1'))
          table = db.Table(TABLE)
          def handler(event, context):
              method = event.get('httpMethod', 'GET')
              claims = (event.get('requestContext') or {}).get('authorizer', {}).get('claims', {})
              sub = claims.get('sub', 'anonymous')
              if method == 'POST':
                  body = json.loads(event.get('body') or '{}')
                  item_id = str(uuid.uuid4())
                  table.put_item(Item={'item_id': item_id, 'owner': sub, 'data': body.get('data','')})
                  return {'statusCode': 201, 'body': json.dumps({'item_id': item_id})}
              items = table.scan().get('Items', [])
              return {'statusCode': 200, 'body': json.dumps({'items': items})}

  # --- API Gateway ---
  RestApi:
    Type: AWS::ApiGateway::RestApi
    Properties:
      Name: !Sub '${AWS::StackName}-api'

  CognitoAuthorizer:
    Type: AWS::ApiGateway::Authorizer
    Properties:
      Name: cognito-auth
      RestApiId: !Ref RestApi
      Type: COGNITO_USER_POOLS
      IdentitySource: method.request.header.Authorization
      ProviderARNs:
        - !GetAtt UserPool.Arn

  ItemsResource:
    Type: AWS::ApiGateway::Resource
    Properties:
      RestApiId: !Ref RestApi
      ParentId: !GetAtt RestApi.RootResourceId
      PathPart: items

  GetItemsMethod:
    Type: AWS::ApiGateway::Method
    Properties:
      RestApiId: !Ref RestApi
      ResourceId: !Ref ItemsResource
      HttpMethod: GET
      AuthorizationType: COGNITO_USER_POOLS
      AuthorizerId: !Ref CognitoAuthorizer
      Integration:
        Type: AWS_PROXY
        IntegrationHttpMethod: POST
        Uri: !Sub
          - 'arn:aws:apigateway:${AWS::Region}:lambda:path/2015-03-31/functions/${FnArn}/invocations'
          - FnArn: !GetAtt ApiHandlerFunction.Arn

  PostItemsMethod:
    Type: AWS::ApiGateway::Method
    Properties:
      RestApiId: !Ref RestApi
      ResourceId: !Ref ItemsResource
      HttpMethod: POST
      AuthorizationType: COGNITO_USER_POOLS
      AuthorizerId: !Ref CognitoAuthorizer
      Integration:
        Type: AWS_PROXY
        IntegrationHttpMethod: POST
        Uri: !Sub
          - 'arn:aws:apigateway:${AWS::Region}:lambda:path/2015-03-31/functions/${FnArn}/invocations'
          - FnArn: !GetAtt ApiHandlerFunction.Arn

  ApiPermissionGet:
    Type: AWS::Lambda::Permission
    Properties:
      Action: lambda:InvokeFunction
      FunctionName: !Ref ApiHandlerFunction
      Principal: apigateway.amazonaws.com
      SourceArn: !Sub 'arn:aws:execute-api:${AWS::Region}:${AWS::AccountId}:${RestApi}/*/GET/*'

  ApiPermissionPost:
    Type: AWS::Lambda::Permission
    Properties:
      Action: lambda:InvokeFunction
      FunctionName: !Ref ApiHandlerFunction
      Principal: apigateway.amazonaws.com
      SourceArn: !Sub 'arn:aws:execute-api:${AWS::Region}:${AWS::AccountId}:${RestApi}/*/POST/*'

  ApiDeployment:
    Type: AWS::ApiGateway::Deployment
    DependsOn:
      - GetItemsMethod
      - PostItemsMethod
    Properties:
      RestApiId: !Ref RestApi

  ApiStage:
    Type: AWS::ApiGateway::Stage
    Properties:
      RestApiId: !Ref RestApi
      DeploymentId: !Ref ApiDeployment
      StageName: prod

Outputs:
  UserPoolId:
    Value: !Ref UserPool
  UserPoolClientId:
    Value: !Ref UserPoolClient
  ApiId:
    Value: !Ref RestApi
  ApiBaseUrl:
    Value: !Sub 'http://${RestApi}.execute-api.localhost.localstack.cloud:4566/prod'
  TableName:
    Value: !Ref ItemsTable
  LambdaFunctionName:
    Value: !Ref ApiHandlerFunction
```

Write the complete file with no placeholders.

- [ ] **Step 3: Deploy the known-good stack and confirm `CREATE_COMPLETE`**

```bash
python3 - <<'EOF'
import boto3
cf = boto3.client('cloudformation', endpoint_url='http://localhost:4566', region_name='us-east-1',
    aws_access_key_id='test', aws_secret_access_key='test')
body = open('corpus/arch_07_cognito_authenticated_api/known_good.yaml').read()
cf.create_stack(StackName='ace-bench-stack', TemplateBody=body,
    Capabilities=['CAPABILITY_NAMED_IAM', 'CAPABILITY_AUTO_EXPAND'])
w = cf.get_waiter('stack_create_complete')
w.wait(StackName='ace-bench-stack')
print('CREATE_COMPLETE')
EOF
```
Expected: `CREATE_COMPLETE`. If it fails, inspect with:
```bash
aws --endpoint-url=http://localhost:4566 cloudformation describe-stack-events \
  --stack-name ace-bench-stack --query 'StackEvents[?ResourceStatus==`CREATE_FAILED`]' --output table
```
Fix the template and redeploy until it succeeds.

- [ ] **Step 4: Create the seeded test user**

After stack creation, seed a test user using the stack outputs:
```bash
python3 - <<'EOF'
import boto3
cf = boto3.client('cloudformation', endpoint_url='http://localhost:4566', region_name='us-east-1',
    aws_access_key_id='test', aws_secret_access_key='test')
cog = boto3.client('cognito-idp', endpoint_url='http://localhost:4566', region_name='us-east-1',
    aws_access_key_id='test', aws_secret_access_key='test')
outs = {o['OutputKey']: o['OutputValue'] for o in
    cf.describe_stacks(StackName='ace-bench-stack')['Stacks'][0]['Outputs']}
pool_id = outs['UserPoolId']
client_id = outs['UserPoolClientId']
# Create user
cog.admin_create_user(UserPoolId=pool_id, Username='testuser',
    TemporaryPassword='Temppass1', MessageAction='SUPPRESS')
# Set permanent password
cog.admin_set_user_password(UserPoolId=pool_id, Username='testuser',
    Password='Testpass1', Permanent=True)
# Confirm auth works
res = cog.initiate_auth(AuthFlow='USER_PASSWORD_AUTH', ClientId=client_id,
    AuthParameters={'USERNAME': 'testuser', 'PASSWORD': 'Testpass1'})
token = res['AuthenticationResult']['IdToken']
print(f'User created. Token issued: {bool(token)}. Pool: {pool_id}. Client: {client_id}')
EOF
```
Expected: `User created. Token issued: True.`

- [ ] **Step 5: Write `functional_test.py`**

Create `corpus/arch_07_cognito_authenticated_api/functional_test.py`:
```python
import json, sys, os
from urllib import request, error
import boto3
from harness.shared.functional_test_helpers import emit_fail, emit_pass, finalize

ENDPOINT = "http://localhost:4566"
REGION = "us-east-1"
CREDS = {"aws_access_key_id": "test", "aws_secret_access_key": "test"}
STACK = "ace-bench-stack"
TEST_USER = "testuser"
TEST_PASS = "Testpass1"


def client(svc):
    return boto3.client(svc, endpoint_url=ENDPOINT, region_name=REGION, **CREDS)


def output(key):
    st = client("cloudformation").describe_stacks(StackName=STACK)["Stacks"][0]
    return next(o["OutputValue"] for o in st["Outputs"] if o["OutputKey"] == key)


def main():
    pool_id = output("UserPoolId")
    client_id = output("UserPoolClientId")
    base_url = output("ApiBaseUrl")

    # --- Assert: token issuance works ---
    cog = client("cognito-idp")
    try:
        res = cog.initiate_auth(
            AuthFlow="USER_PASSWORD_AUTH",
            ClientId=client_id,
            AuthParameters={"USERNAME": TEST_USER, "PASSWORD": TEST_PASS},
        )
        token = res.get("AuthenticationResult", {}).get("IdToken")
        if token:
            emit_pass("token_issued", f"InitiateAuth returned IdToken for {TEST_USER}")
        else:
            emit_fail("token_issued", "InitiateAuth succeeded but no IdToken in result")
            token = None
    except Exception as e:
        emit_fail("token_issued", f"InitiateAuth raised: {e}")
        token = None

    # --- Assert: authenticated GET /items returns 200 ---
    if token:
        try:
            req = request.Request(
                f"{base_url}/items",
                headers={"Authorization": f"Bearer {token}"},
                method="GET",
            )
            with request.urlopen(req, timeout=30) as r:
                body = json.loads(r.read())
                if r.status == 200 and "items" in body:
                    emit_pass("authenticated_get", "GET /items with valid token returned 200 with items list")
                else:
                    emit_fail("authenticated_get", f"GET /items returned {r.status}, body={body}")
        except error.HTTPError as e:
            emit_fail("authenticated_get", f"GET /items returned HTTP {e.code}: {e.read()!r}")
        except Exception as e:
            emit_fail("authenticated_get", f"GET /items raised: {e}")
    else:
        emit_fail("authenticated_get", "skipped — no token available")

    # --- Assert: unauthenticated GET /items returns 401 ---
    try:
        req = request.Request(f"{base_url}/items", method="GET")
        with request.urlopen(req, timeout=10) as r:
            emit_fail("unauthenticated_rejected", f"Expected 401 but got {r.status}")
    except error.HTTPError as e:
        if e.code == 401:
            emit_pass("unauthenticated_rejected", f"GET /items without token returned 401 as expected")
        else:
            emit_fail("unauthenticated_rejected", f"Expected 401 but got HTTP {e.code}")
    except Exception as e:
        emit_fail("unauthenticated_rejected", f"Unexpected error: {e}")

    # --- Assert: authenticated POST /items creates an item ---
    if token:
        try:
            body_bytes = json.dumps({"data": "hello-world"}).encode()
            req = request.Request(
                f"{base_url}/items",
                data=body_bytes,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                method="POST",
            )
            with request.urlopen(req, timeout=30) as r:
                created = json.loads(r.read())
                if r.status == 201 and "item_id" in created:
                    emit_pass("item_created", f"POST /items returned 201 with item_id={created['item_id']}")
                else:
                    emit_fail("item_created", f"POST /items returned {r.status}, body={created}")
        except error.HTTPError as e:
            emit_fail("item_created", f"POST /items returned HTTP {e.code}: {e.read()!r}")
        except Exception as e:
            emit_fail("item_created", f"POST /items raised: {e}")
    else:
        emit_fail("item_created", "skipped — no token available")

    finalize()


if __name__ == "__main__":
    main()
    sys.exit(0)
```

- [ ] **Step 6: Run the functional test against the deployed known-good**

Run: `python corpus/arch_07_cognito_authenticated_api/functional_test.py`
Expected: `ASSERT pass token_issued`, `ASSERT pass authenticated_get`, `ASSERT pass unauthenticated_rejected`, `ASSERT pass item_created`. All four pass.

If any assertion fails, fix `known_good.yaml` or the handler code and redeploy. Do not proceed to Task 4 until all four pass.

- [ ] **Step 7: Write `traffic_flow.md`**

Create `corpus/arch_07_cognito_authenticated_api/traffic_flow.md`:
```markdown
# arch07 Traffic Flow — Cognito-Authenticated API

## Happy path (authenticated request)

1. Client calls `InitiateAuth` (flow: `USER_PASSWORD_AUTH`) with the app client ID and user credentials.
2. Cognito User Pool validates credentials and returns `AuthenticationResult.IdToken` (JWT).
3. Client sends `GET /items` or `POST /items` with `Authorization: Bearer <IdToken>` to the API Gateway URL.
4. API Gateway Cognito Authorizer validates the JWT: checks signature (against the User Pool JWKS), `iss` (must match pool), `aud` / `client_id` (must match the authorizer's ProviderARNs), and expiry.
5. On success, API Gateway invokes the Lambda handler with `event.requestContext.authorizer.claims` populated.
6. Lambda reads `TABLE_NAME` from env, calls DynamoDB `PutItem` / `Scan`, returns `{statusCode, body}`.

## Auth rejection paths (401 / 403)

- **No Authorization header:** API GW Cognito Authorizer returns 401 immediately.
- **Invalid / malformed token:** Authorizer rejects → 401.
- **Token from wrong User Pool:** `iss` mismatch → 401.
- **Expired token:** Authorizer validates expiry → 401.
- **Auth flow not allowed on client:** `InitiateAuth` fails client-side before a token is issued (`NotAuthorizedException`). The caller never sends a token → 401 on API call.
- **Handler-side claim check (fault03):** Authorizer passes, but handler returns 403 when a required claim / attribute is absent from `event.requestContext.authorizer.claims`.

## Key resource identifiers (populated from stack Outputs)

- `UserPoolId` — used by `ace_describe_user_pool` and `ace_probe_authorizer_token`
- `UserPoolClientId` — used by `ace_describe_user_pool_client` and `ace_probe_authorizer_token`
- `ApiBaseUrl` — base URL for `ace_probe_authorizer_token` and functional test
- `LambdaFunctionName` — used by `ace_get_environment_variables`, `ace_get_function`, `ace_tail_logs`
```

- [ ] **Step 8: Tear down the known-good stack**

```bash
aws --endpoint-url=http://localhost:4566 cloudformation delete-stack --stack-name ace-bench-stack
aws --endpoint-url=http://localhost:4566 cloudformation wait stack-delete-complete --stack-name ace-bench-stack
echo "torn down"
```

- [ ] **Step 9: Commit**

```bash
git add corpus/arch_07_cognito_authenticated_api/
git commit -m "feat(corpus): add arch07 Cognito-authenticated API corpus (known-good + functional test)"
```

---

## Task 4: Four fault scenarios

Each scenario = a copy of the corpus deployment with one injected fault, a symptom-only `scenario.md`, a `fault_manifest.json` (never exposed), and a verified reproduction. Use the Task 1 locked mechanisms.

**Files (per scenario `scenarios/arch07_fault0N_<class>/`):**
- Create: `faulted.yaml` (corpus `known_good.yaml` with ONE injected fault)
- Create: `scenario.md` (symptom only — behavioral description, never names the faulty resource/property)
- Create: `fault_manifest.json` (never exposed to the model)

> **KEY INVARIANT:** Because all four Cognito faults manifest as request rejection (401 or 403), each must produce a DISTINCT error signature — confirmed in the Task 1 spike (Gap B pattern). The fault set relies on the following distinct fingerprints:
> - **fault01** (missing auth flow on client): `InitiateAuth` fails → `NotAuthorizedException` or `InvalidParameterException` → caller never sends a token → 401 on every API call. Diagnosis: `ace_describe_user_pool_client` shows `ALLOW_USER_PASSWORD_AUTH` absent from `explicit_auth_flows`.
> - **fault02** (wrong pool on authorizer): `InitiateAuth` succeeds, token issued, but API GW rejects it with 401 (pool ARN mismatch). `ace_probe_authorizer_token` shows `auth_flow_outcome=success`, `api_outcome=unauthorized`. Diagnosis: `ace_describe_user_pool` shows the authorizer's pool ARN differs from the pool that issued the token.
> - **fault03** (missing required claim — handler-side): Token valid, authorizer passes (200 flow passes), but handler returns 403 because `event.requestContext.authorizer.claims` lacks a required custom attribute. Distinct from fault02 (which is 401) and fault01 (which fails before token issuance). Diagnosis: `ace_describe_user_pool` shows missing schema attribute; handler code reads the claim and rejects.
> - **fault04** (token-validity too short / wrong `IdentitySource`): Token may be issued but is immediately expired or the authorizer can't find it (401, different error body mentioning expiry or missing header). Distinct from fault02 (wrong pool vs. expiry/header). Fallback: wrong `IdentitySource` header name on the authorizer → every call 401 regardless of token.
>
> If the Task 1 spike finds fewer than 4 distinct enforced mechanisms, reduce the scenario count and document which are shelved. Do not ship posture-only faults.

**Interfaces:**
- Consumes: corpus `known_good.yaml` + handler (Task 3); the Cognito tools (Task 2); Task 1 findings.
- Produces: four scenario dirs each reproducing its fault and diagnosable via the intended path; each `fault_manifest.json` carries `optimal_tool_calls`/`optimal_files_changed`/`optimal_lines_changed` measured in Step 8.

- [ ] **Step 1: Scaffold all four scenario dirs from the corpus**

```bash
CORP=corpus/arch_07_cognito_authenticated_api
for s in arch07_fault01_auth_flow arch07_fault02_wrong_pool arch07_fault03_missing_claim arch07_fault04_token_validity; do
  mkdir -p scenarios/$s
  cp $CORP/known_good.yaml scenarios/$s/faulted.yaml
done
```

- [ ] **Step 2: Inject fault01 (missing auth flow on client)**

In `scenarios/arch07_fault01_auth_flow/faulted.yaml`, apply the Task 1-locked mechanism:
- **Primary** (auth-flow enforcement confirmed): remove `ALLOW_USER_PASSWORD_AUTH` from `UserPoolClient.Properties.ExplicitAuthFlows` (leave only `ALLOW_REFRESH_TOKEN_AUTH`).
- **Fallback** (auth-flow not enforced): change the `ApiHandlerFunction` env var `CLIENT_ID` (add it to the handler env) to a wrong/nonexistent client ID string. The handler's downstream SDK call fails with `ResourceNotFoundException` (distinct error path).

Record the exact `target_resource`/`target_property`/`original_value`/`injected_value` for the manifest.

- [ ] **Step 3: Inject fault02 (wrong pool ARN on authorizer)**

In `scenarios/arch07_fault02_wrong_pool/faulted.yaml`:
- **Primary** (cross-pool rejection confirmed): change `CognitoAuthorizer.Properties.ProviderARNs` to a hard-coded wrong ARN (e.g. `arn:aws:cognito-idp:us-east-1:000000000000:userpool/us-east-1_WRONGPOOL`). The token issued by the real pool does not match the authorizer's pool → 401.
- **Fallback** (authorizer not enforced): add a handler-side pool-ID check — the handler reads `USER_POOL_ID` from env and compares `event.requestContext.authorizer.claims.iss` to the expected pool URL; inject a wrong `USER_POOL_ID` env var. The handler returns 401 explicitly.

- [ ] **Step 4: Inject fault03 (missing required claim — handler-side)**

In `scenarios/arch07_fault03_missing_claim/faulted.yaml`:
- **Primary**: remove a custom schema attribute from `UserPool.Properties.Schema` that the handler checks in `event.requestContext.authorizer.claims` (e.g. `custom:role`). The handler's claim check fails → returns 403. Also add the claim-check logic to the handler in `faulted.yaml`'s `ZipFile` inline code: `if 'custom:role' not in claims: return {'statusCode': 403, 'body': json.dumps({'error': 'missing required claim'})}`. (The known-good handler must also include this claim check, sourced from `UserPool.Schema`.)

> Note: This fault requires that the KNOWN-GOOD `known_good.yaml` also includes the `custom:role` schema attribute on the User Pool and the claim check in the handler. If the known-good was authored without this attribute, the executor must add it (in both `known_good.yaml` and Task 3's handler code) before proceeding. Re-run functional_test.py after updating.

- **Fallback** (if Cognito schema attributes are not surfaced in claims): use a missing Lambda environment variable — the handler reads `REQUIRED_CLAIM_NAME` from env; in the faulted template the env var is removed entirely. The handler raises `KeyError` → 500. Distinct from 401 (fault01/02) and from 403-claim-check.

- [ ] **Step 5: Inject fault04 (token-validity too short / wrong IdentitySource)**

In `scenarios/arch07_fault04_token_validity/faulted.yaml`:
- **Primary** (token-expiry enforcement confirmed): set `UserPoolClient.Properties.AccessTokenValidity: 1` and `TokenValidityUnits.AccessToken: seconds`. Any token issued is immediately expired → API GW returns 401 with expiry context. Functional test fails: `authenticated_get` gets 401 even with freshly issued token.
- **Fallback** (expiry not enforced): change `CognitoAuthorizer.Properties.IdentitySource` to `method.request.header.X-Token` (non-standard header). Every call using the standard `Authorization` header is rejected with 401 ("Unauthorized" — API GW can't find the token). Distinct from fault02 (which has correct IdentitySource but wrong pool ARN).

- [ ] **Step 6: Write symptom-only `scenario.md` for each**

For each scenario, write `scenario.md` with this structure:

```markdown
## System overview
A REST API backed by Lambda and DynamoDB. Requests must be authenticated via a Cognito User Pool. Clients obtain a token using standard Cognito auth flows and include it as a Bearer token in the Authorization header.

## What you have access to
- `faulted.yaml` — the deployed CloudFormation template (one fault injected)
- `deployment/` — Lambda handler code (if applicable)
- All MCP diagnostic tools (see tool list)
- The stack has been deployed successfully (`CREATE_COMPLETE`)

## Reported symptom
<symptom-only description — see below per fault>

## What correct behavior looks like
Authenticated requests (valid Cognito token in Authorization header) return 200/201. Unauthenticated requests return 401.
```

Symptom descriptions (use EXACTLY these — no cause, no resource name):
- **fault01**: "All API requests return 401. A test script that calls `InitiateAuth` before each API request fails at the auth step with a `NotAuthorizedException` or `InvalidParameterException` error. The Cognito User Pool itself appears healthy. No token is ever issued."
- **fault02**: "A token is successfully obtained from Cognito (`InitiateAuth` succeeds and returns an IdToken). However, every API request that includes that token as `Authorization: Bearer <token>` returns 401. Requests without a token also return 401."
- **fault03**: "Authentication succeeds — `InitiateAuth` returns a valid token and the API Gateway authorizer accepts it (no 401). However, the API returns 403 Forbidden on every request. The Lambda function is invoked but returns 403."
- **fault04**: "A token can be obtained from Cognito. However, API requests made immediately after token issuance return 401. The behavior is consistent — freshly issued tokens are rejected."

- [ ] **Step 7: Write `fault_manifest.json` for each**

Follow the arch01/arch03 schema exactly. For each scenario, write the full JSON. Fault01 example (adapt others accordingly):
```json
{
  "fault_id": "arch07_fault01",
  "fault_class": "security",
  "architecture": "arch_07_cognito_authenticated_api",
  "scenario_id": "arch07_fault01_auth_flow",
  "target_resource": "UserPoolClient",
  "target_property": "Properties.ExplicitAuthFlows",
  "injected_value": "[\"ALLOW_REFRESH_TOKEN_AUTH\"]",
  "original_value": "[\"ALLOW_USER_PASSWORD_AUTH\", \"ALLOW_REFRESH_TOKEN_AUTH\"]",
  "valid_fixes": [
    "Add ALLOW_USER_PASSWORD_AUTH to UserPoolClient.Properties.ExplicitAuthFlows"
  ],
  "invalid_patches": [
    "Remove the Cognito authorizer from the API Gateway methods",
    "Grant the Lambda role cognito-idp:AdminInitiateAuth and switch to admin auth flow without fixing ExplicitAuthFlows",
    "Change AuthorizationType to NONE on all methods"
  ],
  "optimal_tool_calls": 3,
  "optimal_files_changed": 1,
  "optimal_lines_changed": 1,
  "optimal_diagnostic_path": [
    "ace_probe_authorizer_token(user_pool_id, client_id, username, password, api_url) → auth_flow_outcome=not_authorized or invalid_parameter; confirms failure is at InitiateAuth stage, not at API GW",
    "ace_get_stack_outputs(ace-bench-stack) → reveals UserPoolId and UserPoolClientId",
    "ace_describe_user_pool_client(user_pool_id, client_id) → explicit_auth_flows shows ALLOW_USER_PASSWORD_AUTH is missing; confirms root cause"
  ],
  "concurrency_probe_n": null,
  "deployment_check": "CREATE_COMPLETE",
  "observability_check": "functional_test assertion 'token_issued' fails; InitiateAuth returns NotAuthorizedException or InvalidParameterException",
  "observable_symptom": "All API requests return 401. InitiateAuth fails before a token is issued — the app client does not allow the USER_PASSWORD_AUTH flow.",
  "root_cause": "The Cognito UserPoolClient ExplicitAuthFlows list does not include ALLOW_USER_PASSWORD_AUTH. Calls to InitiateAuth with flow USER_PASSWORD_AUTH are rejected before a token is ever issued.",
  "corpus_path": "corpus/arch_07_cognito_authenticated_api",
  "functional_test_path": "corpus/arch_07_cognito_authenticated_api/functional_test.py",
  "known_good_path": "corpus/arch_07_cognito_authenticated_api/known_good.yaml"
}
```

Write complete JSON for all four faults. Fill `optimal_tool_calls`/`optimal_files_changed`/`optimal_lines_changed` after Step 8.

- [ ] **Step 8: Verify each scenario reproduces + is diagnosable**

For each scenario:
1. Deploy `faulted.yaml` as `ace-bench-stack`:
   ```bash
   python3 -c "
   import boto3; cf=boto3.client('cloudformation',endpoint_url='http://localhost:4566',region_name='us-east-1',aws_access_key_id='test',aws_secret_access_key='test')
   body=open('scenarios/arch07_fault01_auth_flow/faulted.yaml').read()
   cf.create_stack(StackName='ace-bench-stack',TemplateBody=body,Capabilities=['CAPABILITY_NAMED_IAM','CAPABILITY_AUTO_EXPAND'])
   cf.get_waiter('stack_create_complete').wait(StackName='ace-bench-stack'); print('CREATE_COMPLETE')
   "
   ```
   (Adjust path per scenario.)
2. Seed the test user (same `admin_create_user` + `admin_set_user_password` commands as Task 3 Step 4).
3. Run `python corpus/arch_07_cognito_authenticated_api/functional_test.py` and confirm the primary assertion FAILS (symptom reproduces). Record which assertion fails and the exact error/HTTP status.
4. Walk the intended diagnostic path using the Cognito MCP tools:
   ```bash
   # Example for fault01:
   node -e "import('./harness/mcp_server/tools/probe_cognito.js').then(async m => {
     const t = n => m.probeCognitoTools.find(x => x.name === n);
     // Step 1: probe the token + API
     const outs = JSON.parse(require('child_process').execSync(\"aws --endpoint-url=http://localhost:4566 cloudformation describe-stacks --stack-name ace-bench-stack --query 'Stacks[0].Outputs' --output json\").toString());
     const get = k => outs.find(o=>o.OutputKey===k).OutputValue;
     console.log(await t('ace_probe_authorizer_token').handler({user_pool_id:get('UserPoolId'),client_id:get('UserPoolClientId'),username:'testuser',password:'Testpass1',api_url:get('ApiBaseUrl')}));
     // Step 2: check pool client config
     console.log(await t('ace_describe_user_pool_client').handler({user_pool_id:get('UserPoolId'),client_id:get('UserPoolClientId')}));
   })"
   ```
   Confirm the tools surface the signal that pinpoints the fault.
5. If a scenario does NOT reproduce or the diagnostic path fails, switch to the Task 1 fallback mechanism and re-verify.
6. Tear down between scenarios:
   ```bash
   aws --endpoint-url=http://localhost:4566 cloudformation delete-stack --stack-name ace-bench-stack
   aws --endpoint-url=http://localhost:4566 cloudformation wait stack-delete-complete --stack-name ace-bench-stack
   ```

- [ ] **Step 9: Baseline `optimal_*` and finalize manifests**

For each scenario, set:
- `optimal_files_changed` = number of files changed in the minimal fix (typically 1)
- `optimal_lines_changed` = number of lines changed in the minimal fix (typically 1–2)
- `optimal_tool_calls` = count of MCP calls on the intended diagnostic path (Step 8 walk)

Write these into each `fault_manifest.json`.

- [ ] **Step 10: Commit**

```bash
git add scenarios/arch07_fault01_auth_flow scenarios/arch07_fault02_wrong_pool scenarios/arch07_fault03_missing_claim scenarios/arch07_fault04_token_validity
git commit -m "feat(scenarios): add four arch07 Cognito fault scenarios with manifests"
```

---

## Task 5: Discoverability QA gate

Run the four checks from §4 of the framework spec for every arch07 scenario. Record pass/fail for each check and any remediation applied.

**Files:**
- No new files created; results recorded inline below as the executor fills them in.

**Interfaces:**
- Consumes: all four deployed faulted scenarios (Task 4); the Cognito tools (Task 2).
- Produces: a pass/fail record per check per scenario; all four checks must pass before Task 6.

### Check 1 — Agent-exposure plumbing

Verify all three Cognito tools flow through `mcp_to_openai_tool` / `filter_model_tools` and appear in the model's runtime tool list; confirm `ace_verify_fix` and `ace_score_run` remain filtered.

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
            oai_tools = [mcp_to_openai_tool(t) for t in tools.tools]
            model_tools = filter_model_tools(oai_tools)
            names = [t["function"]["name"] for t in model_tools]
            for n in ["ace_describe_user_pool", "ace_describe_user_pool_client", "ace_probe_authorizer_token"]:
                status = "PASS" if n in names else "FAIL"
                print(f"[Check1] {n}: {status}")
            for n in ["ace_verify_fix", "ace_score_run"]:
                status = "PASS" if n not in names else "FAIL (score tool exposed)"
                print(f"[Check1] {n} filtered: {status}")

asyncio.run(check())
EOF
```
Expected: all three Cognito tools show `PASS`; both score tools show `PASS (filtered)`.

**Remediation:** if a tool is missing, check the `index.js` spread and re-run Step 6 of Task 2.

### Check 2 — Diagnostic-path reachability

For each scenario, deploy the faulted stack, walk the `optimal_diagnostic_path` with the real MCP tools, and confirm the tools surface the distinguishing signal:

```bash
# For each scenario (repeat 4x, adjust scenario dir and expected signal):
SCENARIO=arch07_fault01_auth_flow
# Deploy:
python3 -c "
import boto3; cf=boto3.client('cloudformation',endpoint_url='http://localhost:4566',region_name='us-east-1',aws_access_key_id='test',aws_secret_access_key='test')
body=open('scenarios/$SCENARIO/faulted.yaml').read()
cf.create_stack(StackName='ace-bench-stack',TemplateBody=body,Capabilities=['CAPABILITY_NAMED_IAM','CAPABILITY_AUTO_EXPAND'])
cf.get_waiter('stack_create_complete').wait(StackName='ace-bench-stack'); print('CREATE_COMPLETE')
"
# Seed user, then walk diagnostic path with probe_cognito tools (see Task 4 Step 8 commands).
# Confirm the tool output contains the fault-specific signal:
#   fault01: ace_describe_user_pool_client.explicit_auth_flows missing ALLOW_USER_PASSWORD_AUTH
#   fault02: ace_probe_authorizer_token.api_outcome=unauthorized despite auth_flow_outcome=success
#   fault03: ace_probe_authorizer_token.api_status_code=403; ace_describe_user_pool missing custom:role attribute
#   fault04: ace_probe_authorizer_token.api_outcome=unauthorized with auth_flow_outcome=success (token issued but rejected)
# Tear down after each.
aws --endpoint-url=http://localhost:4566 cloudformation delete-stack --stack-name ace-bench-stack
aws --endpoint-url=http://localhost:4566 cloudformation wait stack-delete-complete --stack-name ace-bench-stack
```

Record per scenario: `[Check2] arch07_fault0N: PASS | FAIL — <reason>`.

**Remediation:** if a tool does not surface the signal, update the tool's handler to expose the missing field, re-run tests, and re-verify.

### Check 3 — Blind-triggering

#### 3a — Static rubric (cheap pre-gate)

Verify all three tool descriptions satisfy:
(a) names the real AWS API it maps to,
(b) lists the concrete fields it returns,
(c) states when to reach for it (symptom / fault-class).

```bash
node -e "
import('./harness/mcp_server/tools/probe_cognito.js').then(m => {
  const checks = ['AWS API', 'fields', 'Use to'];
  for (const t of m.probeCognitoTools) {
    const d = t.description;
    for (const kw of checks) {
      const ok = d.toLowerCase().includes(kw.toLowerCase()) || (kw==='AWS API' && /Cognito \\w+:/.test(d));
      console.log(\`[3a] \${t.name} — \${kw}: \${ok ? 'PASS' : 'FAIL'}\`);
    }
  }
});
"
```
Expected: all 9 checks (3 tools × 3 rubric items) PASS.

**Remediation:** edit the failing description in `probe_cognito.js` to add the missing rubric element; re-run tests.

#### 3b — LLM-judge blind selection (N=5 trials per scenario)

Use a cheaper judge model (e.g. `claude-haiku-4` or `gpt-4o-mini`) that is DISTINCT from the primary eval target. For each scenario, run the judge with ONLY the scenario's symptom description (from `scenario.md` `## Reported symptom`) + the full list of tool names and descriptions. Ask it which tools it would call first (ordered list). Run N=5 trials.

Pass bar: every tool on the scenario's `optimal_diagnostic_path` appears in the judge's first-K picks in ≥3/5 trials, where K = (optimal path length) + 1.

```python
# scratch/blind_trigger_check.py (gitignored — run manually)
import os, json
import anthropic

client = anthropic.Anthropic()  # uses ANTHROPIC_API_KEY

def load_tools():
    import subprocess, ast
    # Load tool names+descriptions from the MCP server description output
    res = subprocess.check_output(["node", "-e", """
import('./harness/mcp_server/tools/probe_cognito.js').then(m => {
  console.log(JSON.stringify(m.probeCognitoTools.map(t=>({name:t.name,description:t.description}))));
})"""], text=True)
    return json.loads(res.strip())

SCENARIOS = {
    "arch07_fault01_auth_flow": {
        "symptom": "All API requests return 401. A test script that calls InitiateAuth before each API request fails at the auth step with a NotAuthorizedException or InvalidParameterException error. The Cognito User Pool itself appears healthy. No token is ever issued.",
        "optimal_path": ["ace_probe_authorizer_token", "ace_describe_user_pool_client"],
    },
    "arch07_fault02_wrong_pool": {
        "symptom": "A token is successfully obtained from Cognito (InitiateAuth succeeds and returns an IdToken). However, every API request that includes that token returns 401. Requests without a token also return 401.",
        "optimal_path": ["ace_probe_authorizer_token", "ace_describe_user_pool"],
    },
    "arch07_fault03_missing_claim": {
        "symptom": "Authentication succeeds — InitiateAuth returns a valid token and the API Gateway authorizer accepts it (no 401). However, the API returns 403 Forbidden on every request.",
        "optimal_path": ["ace_probe_authorizer_token", "ace_describe_user_pool"],
    },
    "arch07_fault04_token_validity": {
        "symptom": "A token can be obtained from Cognito. However, API requests made immediately after token issuance return 401. The behavior is consistent — freshly issued tokens are rejected.",
        "optimal_path": ["ace_probe_authorizer_token", "ace_describe_user_pool_client"],
    },
}

tools = load_tools()
tool_list_str = "\n".join(f"- {t['name']}: {t['description']}" for t in tools)

for scenario_id, info in SCENARIOS.items():
    passes = 0
    for trial in range(5):
        msg = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=512,
            messages=[{
                "role": "user",
                "content": f"""You are a cloud infrastructure debugger. You have these diagnostic tools available:

{tool_list_str}

A user reports this symptom:
{info['symptom']}

List the tools you would call FIRST (in order) to diagnose this symptom. Output only the tool names, one per line."""
            }]
        )
        picks = [line.strip().lstrip("- 0123456789.").strip() for line in msg.content[0].text.strip().splitlines() if line.strip()]
        K = len(info["optimal_path"]) + 1
        first_k = picks[:K]
        hit = all(t in first_k for t in info["optimal_path"])
        if hit:
            passes += 1
        print(f"[3b] {scenario_id} trial {trial+1}: picks={first_k}, hit={hit}")
    result = "PASS" if passes >= 3 else "FAIL"
    print(f"[3b] {scenario_id}: {passes}/5 → {result}")
```

Run: `python scratch/blind_trigger_check.py` (requires `ANTHROPIC_API_KEY` in env).
Expected: all four scenarios PASS (≥3/5 trials each tool on optimal path in first K picks).

**Remediation ladder:**
1. If a tool description fails 3b, strengthen the "when to reach for it" clause with the specific symptom pattern (e.g. "Use when GET /items returns 401 despite a valid token"). Re-run 3b.
2. If still failing after description update, check that the `optimal_diagnostic_path` is realistic — the judge may be right that a different tool should come first. Update the manifest's path and re-run.
3. If 2 or more scenarios fail after remediation, revisit the fault design (the symptom may not uniquely motivate the correct tool set).

### Check 4 — Trace + scoring pipeline

Verify the full runner pipeline works end-to-end for one scenario (fault01).

```bash
# Requires HARNESS_API_KEY in .env and LocalStack running.
python harness/run.py scenarios/arch07_fault01_auth_flow/ \
  --model anthropic/claude-haiku-4-5 \
  --api-key "$ANTHROPIC_API_KEY"
```
Confirm:
- `results/<run_id>/tool_call_trace.json` contains at least one Cognito tool call.
- `results/<run_id>/verify_result.json` is present.
- `results/<run_id>/score.json` is present (may be partial if the agent did not fix the fault, but the pipeline must complete without crashing).

**Remediation:** if the runner crashes on a Cognito tool, fix the tool's handler (error handling, return type) and re-run.

- [ ] **Step 1: Run Check 1 (plumbing)**

Run the plumbing script above. Record result:
```
[Check1] ace_describe_user_pool: __
[Check1] ace_describe_user_pool_client: __
[Check1] ace_probe_authorizer_token: __
[Check1] ace_verify_fix filtered: __
[Check1] ace_score_run filtered: __
```

- [ ] **Step 2: Run Check 2 (reachability) for all 4 scenarios**

Deploy each faulted stack, walk the diagnostic path, record:
```
[Check2] arch07_fault01_auth_flow: __
[Check2] arch07_fault02_wrong_pool: __
[Check2] arch07_fault03_missing_claim: __
[Check2] arch07_fault04_token_validity: __
```

- [ ] **Step 3a: Run static rubric check**

Run the node one-liner above. Record:
```
[3a] ace_describe_user_pool — AWS API: __
[3a] ace_describe_user_pool — fields: __
[3a] ace_describe_user_pool — Use to: __
[3a] ace_describe_user_pool_client — AWS API: __
[3a] ace_describe_user_pool_client — fields: __
[3a] ace_describe_user_pool_client — Use to: __
[3a] ace_probe_authorizer_token — AWS API: __
[3a] ace_probe_authorizer_token — fields: __
[3a] ace_probe_authorizer_token — Use to: __
```

- [ ] **Step 3b: Run blind-triggering judge (N=5 per scenario)**

Run `python scratch/blind_trigger_check.py`. Record:
```
[3b] arch07_fault01_auth_flow: __/5 → __
[3b] arch07_fault02_wrong_pool: __/5 → __
[3b] arch07_fault03_missing_claim: __/5 → __
[3b] arch07_fault04_token_validity: __/5 → __
```

- [ ] **Step 4: Run end-to-end pipeline (Check 4)**

```bash
python harness/run.py scenarios/arch07_fault01_auth_flow/ \
  --model anthropic/claude-haiku-4-5 \
  --api-key "$ANTHROPIC_API_KEY"
```
Record: pipeline completes without crash, trace/verify/score files present: `__`

- [ ] **Step 5: Apply any remediation and re-run failed checks**

For each failed check, follow the remediation ladder in the check description. Re-run until all four checks pass. If a fault cannot be made discoverable, shelve it and document here.

- [ ] **Step 6: Commit remediated tools/descriptions if changed**

```bash
# Only if probe_cognito.js or fault_manifest.json files were updated during remediation:
git add harness/mcp_server/tools/probe_cognito.js tests/test_mcp_server.js
git add scenarios/arch07_fault01_auth_flow/fault_manifest.json
git add scenarios/arch07_fault02_wrong_pool/fault_manifest.json
git add scenarios/arch07_fault03_missing_claim/fault_manifest.json
git add scenarios/arch07_fault04_token_validity/fault_manifest.json
git commit -m "fix(mcp): remediate Cognito tool descriptions for discoverability QA gate"
```

---

## Task 6: Documentation

Bring tool counts and architecture inventory in sync across the guides.

**Files:**
- Modify: `CLAUDE.md` (tool counts; Project Layout corpus/scenarios entries)
- Modify: `README.md` (Phase B tool inventory; repository layout)
- Modify: `RUN.md` (tool inventory; model-access count)

**Interfaces:**
- Consumes: the final tool list from Task 2 (3 new Cognito tools) and the arch07 corpus/scenarios from Tasks 3–4.
- Produces: consistent counts (diagnostic tools 61 → 64; the model-access count rises by 3 accordingly) and a documented arch07.

- [ ] **Step 1: Update `CLAUDE.md`**

Change the MCP server description from "61 diagnostic + 2 score tools across 28 LocalStack services" to "64 diagnostic + 2 score tools across 29 LocalStack services" (adding `cognito-idp`). Add `harness/mcp_server/tools/probe_cognito.js` (3 Cognito tools) to the `tools/` listing. Add `corpus/arch_07_cognito_authenticated_api/` and the four `scenarios/arch07_fault0N_*` entries to the Project Layout.

- [ ] **Step 2: Update `README.md` and `RUN.md`**

Bump the diagnostic tool count by 3 and the model-access count by 3 in both files; add the three Cognito tools to the tool tables; add arch07 to any architecture/corpus inventory.

- [ ] **Step 3: Verify counts are consistent**

```bash
grep -rEn "6[0-9]" CLAUDE.md README.md RUN.md | grep -iE "tool|diagnostic|model access" | head -20
node -e "
Promise.all([
  import('./harness/mcp_server/tools/probe.js'),
  import('./harness/mcp_server/tools/probe_extended.js'),
  import('./harness/mcp_server/tools/observe.js'),
  import('./harness/mcp_server/tools/observe_extended.js'),
  import('./harness/mcp_server/tools/observe_tracing.js'),
  import('./harness/mcp_server/tools/probe_rds.js'),
  import('./harness/mcp_server/tools/probe_cognito.js'),
  import('./harness/mcp_server/tools/score.js'),
]).then(mods => {
  const total = mods.reduce((acc, m) => acc + Object.values(m).find(Array.isArray).length, 0);
  console.log('total tools:', total, '(expected: 66 = 64 diagnostic + 2 score)');
});
"
```
Expected: printed total = 66 (64 diagnostic + 2 score); counts in docs agree.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md README.md RUN.md
git commit -m "docs: document arch07 Cognito-authenticated API and Cognito MCP tools (64 diagnostic tools)"
```

---

## Commit Cadence Summary

| Task | Commit message |
|---|---|
| Task 1 (after spike) | `docs(plan): record arch07 Cognito spike findings and locked fault mechanisms` |
| Task 2 | `feat(mcp): add Cognito diagnostic tools (ace_describe_user_pool, ace_describe_user_pool_client, ace_probe_authorizer_token)` |
| Task 3 | `feat(corpus): add arch07 Cognito-authenticated API corpus (known-good + functional test)` |
| Task 4 | `feat(scenarios): add four arch07 Cognito fault scenarios with manifests` |
| Task 5 (if remediation) | `fix(mcp): remediate Cognito tool descriptions for discoverability QA gate` |
| Task 6 | `docs: document arch07 Cognito-authenticated API and Cognito MCP tools (64 diagnostic tools)` |

---

## Self-Review Notes (author)

- **Spec coverage:** 6-task spine → all 6 tasks present. §2 LocalStack-load preamble → Task 1 Step 1 verbatim. §3 Realism gate → all tools map to real AWS SDK calls, no LocalStack-proprietary introspection. §4 Discoverability QA gate (four checks) → Task 5 Steps 1–4 with concrete commands and remediation ladders. Kill-gate → Task 1 gate rule enforced (no Task 2+ start until findings recorded). Primary+fallback → all four faults have primary and fallback mechanisms in Task 4 Steps 2–5.
- **Distinct error signatures (Gap B requirement):** fault01=InitiateAuth fails (no token, always 401); fault02=token issued but authorizer rejects (401, wrong-pool fingerprint); fault03=authorizer passes but handler returns 403 (distinct status code); fault04=token issued but immediately expired/missing (401 with expiry/header signal). All four confirmed empirically in Task 1 Probe A–D.
- **Authorizer-not-enforced fallback:** Task 1 Probe B is the kill-gate question; if authorizer does not enforce, Task 4 Steps 2–5 all switch to handler-side validation fallbacks, which are always enforceable. The plan does not leave the executor blocked.
- **Tool count:** current CLAUDE.md = 61 diagnostic + 2 score (28 services). Adding 3 Cognito tools → 64 diagnostic + 2 score (29 services, adding `cognito-idp`). Task 6 Step 3 verify script is source of truth.
- **No `scratch/blind_trigger_check.py` committed:** it is a gitignored scratch script for Task 5 only.
