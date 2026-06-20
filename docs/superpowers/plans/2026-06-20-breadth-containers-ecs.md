# Containers (ECS) Architecture (arch04) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Containers breadth-track corpus architecture — an ECS Fargate service fronted by an Application Load Balancer (arch04) — with three new ECS/ECR MCP diagnostic tools and up to four behavior-manifesting fault scenarios (exact count locked in Task 1; fewer only if the spike limits enforced mechanisms).

**Architecture:** `ALB → ECS Fargate Service (task-def: container from ECR image) → DynamoDB`, with an IAM task role governing the container's AWS API access. A VPC with public subnets + security groups governs network connectivity, and the ALB target group health check provides the observable liveness signal. The pattern exercises ECR image pull, ECS task scheduling, and container runtime IAM — three distinct fault axes.

**Tech Stack:** CloudFormation (LocalStack Ultimate), Python 3.11 container app (`boto3`, served via `http.server`), Docker + ECR for image management, Node.js v22+ MCP server (`@aws-sdk/client-ecs`, `@aws-sdk/client-ecr`), pytest + `node:test`.

## Global Constraints

- LocalStack runs with `ENFORCE_IAM=1 IAM_SOFT_MODE=0`; any fixture/scenario creating ECS task definitions must define a real assumable `ecs-tasks.amazonaws.com` role.
- AWS creds: accessKeyId=`test`, secretAccessKey=`test`, region=`us-east-1`, endpoint `http://localhost:4566`. IAM fake account `000000000000`.
- Prefer real AWS APIs; never use LocalStack-proprietary introspection (App Inspector, IAM Policy Streams).
- `fault_manifest.json` and `known_good.yaml` are NEVER exposed to the model.
- **Fault design principle (mandatory):** every fault must produce an observable behavioral symptom Pass-1 functional verification detects; `scenario.md` states only the symptom, never the cause. No posture-only faults.
- **ECS Fargate kill-gate:** if the spike proves LocalStack does not actually schedule tasks to `RUNNING` status (i.e. tasks remain `PENDING` or `PROVISIONING` forever and no HTTP traffic is possible), the family is shelved with a documented finding and Tasks 2–6 are skipped. Do not ship tools that cannot be validated against live ECS data.
- MCP tool files live in `harness/mcp_server/tools/` and are spread into `harness/mcp_server/index.js`. Each tool is `{ name, description, inputSchema, async handler(args) }` and returns a plain object (never throws).
- **Pre-flight (executor must run before Task 2):** `cd harness/mcp_server && npm install` — `node_modules` currently lacks `@aws-sdk/client-ecs` and `@aws-sdk/client-ecr`. This installs both after Task 1's `package.json` additions.
- Corpus dir: `corpus/arch_04_containers_ecs_fargate/`. Scenario dirs: `scenarios/arch04_fault0N_<class>/`.
- Node MCP tests: `node --test tests/test_mcp_server.js`. Spike runs against a live LocalStack (`localstack start -d`).

---

## Task 1: De-risking spike (the gate)

Exploratory, not TDD. Validates the ECS/ECR family's premises on the current LocalStack build before any corpus fan-out. **Do not start Task 2 until this passes.** The biggest risk for this family is that LocalStack may not schedule Fargate tasks to `RUNNING` — this must be empirically confirmed. Findings are recorded in the `## Task 1 findings` section below (to be filled by the executor).

**Files:**
- Create: `scratch/spike_ecs.mjs` (gitignored; `scratch/` is already in `.gitignore`)
- Create: `scratch/spike_ecs_stack.yaml` (minimal CFN: VPC + subnets + SG + ECR repo + ECS cluster + task def + Fargate service + ALB + target group)

**Interfaces:**
- Consumes: nothing (standalone spike).
- Produces: a recorded decision per candidate tool and per fault mechanism (primary vs fallback or shelved), written as notes in the `## Task 1 findings` section. Tasks 2–4 read these notes.

- [ ] **Step 1: Start LocalStack and confirm ECS/ECR service emulation**

Run the Section 2 preamble verbatim:
```bash
# Start LocalStack Ultimate with IAM enforcement (required for valid scoring).
LOCALSTACK_AUTH_TOKEN=ls-... ENFORCE_IAM=1 IAM_SOFT_MODE=0 localstack start -d
until localstack status services 2>/dev/null | grep -q running; do sleep 2; done
# Confirm ECS and ECR appear as emulated services:
curl -s localhost:4566/_localstack/health | grep -oE '"ecs"\s*:\s*"[a-z]+"'
curl -s localhost:4566/_localstack/health | grep -oE '"ecr"\s*:\s*"[a-z]+"'
# Record the LocalStack version for the findings block:
curl -s localhost:4566/_localstack/info | grep -oE '"version":\s*"[^"]+"'
```
Expected: `"ecs": "available"` and `"ecr": "available"`. If either is absent, record "ECS/ECR not emulated — family shelved" in the findings block and stop.

- [ ] **Step 2: Build and push a minimal test image to LocalStack ECR**

Create a tiny Python HTTP server image that responds `{"status":"ok"}` on `GET /health`. Push it to a LocalStack ECR repo:
```bash
# Create the ECR repo via CLI (spike uses CLI for speed)
aws --endpoint-url=http://localhost:4566 ecr create-repository --repository-name spike-app --region us-east-1

# Build the image (Dockerfile in scratch/)
cat > scratch/Dockerfile.spike <<'EOF'
FROM python:3.11-slim
CMD ["python3", "-c", "import http.server, json; http.server.HTTPServer(('', 8080), type('H', (http.server.BaseHTTPRequestHandler,), {'do_GET': lambda s: (s.send_response(200), s.send_header('Content-Type','application/json'), s.end_headers(), s.wfile.write(json.dumps({'status':'ok'}).encode()))})).serve_forever()"]
EOF
docker build -f scratch/Dockerfile.spike -t spike-app:latest scratch/

# Tag and push to LocalStack ECR
REPO=$(aws --endpoint-url=http://localhost:4566 ecr describe-repositories --repository-names spike-app --query 'repositories[0].repositoryUri' --output text --region us-east-1)
docker tag spike-app:latest "$REPO:latest"
aws --endpoint-url=http://localhost:4566 ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin "$(echo $REPO | cut -d/ -f1)"
docker push "$REPO:latest"
```
Expected: push succeeds and `aws --endpoint-url=http://localhost:4566 ecr describe-images --repository-name spike-app` returns the `latest` tag.

- [ ] **Step 3: Write `scratch/spike_ecs_stack.yaml`**

Create a minimal CFN stack with:
- `AWS::EC2::VPC` (CIDR `10.0.0.0/16`)
- Two `AWS::EC2::Subnet` in different AZs (`10.0.1.0/24`, `10.0.2.0/24`; `MapPublicIpOnLaunch: true`)
- `AWS::EC2::InternetGateway` + `AWS::EC2::VPCGatewayAttachment`
- `AWS::EC2::RouteTable` + `AWS::EC2::Route` (default to IGW) + two `AWS::EC2::SubnetRouteTableAssociation`
- `AWS::EC2::SecurityGroup` for the service (ingress TCP/8080 from `0.0.0.0/0`; egress all)
- `AWS::ECS::Cluster`
- `AWS::IAM::Role` for the task (`AssumeRolePolicyDocument.Principal.Service: ecs-tasks.amazonaws.com`; policies: `logs:*`, `ecr:GetAuthorizationToken`, `ecr:BatchGetImage`, `ecr:GetDownloadUrlForLayer`)
- `AWS::ECS::TaskDefinition` (requiresCompatibilities: `[FARGATE]`, networkMode: `awsvpc`, cpu: `256`, memory: `512`, containerDefinitions with the ECR image URI, `portMappings: [{containerPort: 8080}]`, `logConfiguration: {logDriver: awslogs}`)
- `AWS::ECS::Service` (launchType: `FARGATE`, desiredCount: 1, networkConfiguration: awsvpcConfiguration pointing to the subnets + SG)
- `AWS::ElasticLoadBalancingV2::LoadBalancer`, `AWS::ElasticLoadBalancingV2::TargetGroup` (healthCheckPath: `/health`), `AWS::ElasticLoadBalancingV2::Listener`
- Outputs: `ClusterName`, `ServiceName`, `TaskDefinitionArn`, `EcrRepoUri`, `AlbDnsName`

- [ ] **Step 4: Provision the spike stack**

```bash
aws --endpoint-url=http://localhost:4566 cloudformation create-stack \
  --stack-name spike-ecs \
  --template-body file://scratch/spike_ecs_stack.yaml \
  --capabilities CAPABILITY_IAM \
  --region us-east-1
# Poll for completion (allow up to 5 minutes):
aws --endpoint-url=http://localhost:4566 cloudformation wait stack-create-complete \
  --stack-name spike-ecs --region us-east-1
aws --endpoint-url=http://localhost:4566 cloudformation describe-stacks \
  --stack-name spike-ecs --query 'Stacks[0].StackStatus' --region us-east-1
```
Expected: `CREATE_COMPLETE`. If `ROLLBACK_COMPLETE`, describe stack events and fix the template.

- [ ] **Step 5: Write `scratch/spike_ecs.mjs` — provision + traffic + fidelity + enforcement probes**

Create `scratch/spike_ecs.mjs`:
```javascript
import { ECSClient, DescribeServicesCommand, DescribeTasksCommand, ListTasksCommand, DescribeTaskDefinitionCommand } from "@aws-sdk/client-ecs";
import { ECRClient, DescribeImagesCommand } from "@aws-sdk/client-ecr";
import { CloudFormationClient, DescribeStacksCommand } from "@aws-sdk/client-cloudformation";

const cfg = { endpoint: "http://localhost:4566", region: "us-east-1", credentials: { accessKeyId: "test", secretAccessKey: "test" } };
const ecs = new ECSClient(cfg);
const ecr = new ECRClient(cfg);
const cf  = new CloudFormationClient(cfg);

async function output(key) {
  const r = await cf.send(new DescribeStacksCommand({ StackName: "spike-ecs" }));
  return r.Stacks[0].Outputs.find(o => o.OutputKey === key)?.OutputValue;
}

const cluster   = await output("ClusterName");
const service   = await output("ServiceName");
const ecrRepo   = await output("EcrRepoUri");
const albDns    = await output("AlbDnsName");

// --- PROBE A: Tool-data fidelity ---

// A1: ace_describe_ecs_service backing API
const svcResp = await ecs.send(new DescribeServicesCommand({ cluster, services: [service] }));
const svc = svcResp.services?.[0];
console.log(`[A1] ace_describe_ecs_service fidelity: status=${svc?.status}, desiredCount=${svc?.desiredCount}, runningCount=${svc?.runningCount}, deployments=${svc?.deployments?.length}, events_sample=${svc?.events?.[0]?.message?.slice(0,80)}`);

// A2: ace_describe_ecs_task backing API — wait for a task to appear first
const tasksResp = await ecs.send(new ListTasksCommand({ cluster }));
const taskArns = tasksResp.taskArns ?? [];
console.log(`[A2] ListTasks: found ${taskArns.length} task(s)`);
if (taskArns.length > 0) {
  const tdResp = await ecs.send(new DescribeTasksCommand({ cluster, tasks: taskArns }));
  const t = tdResp.tasks?.[0];
  console.log(`[A2] ace_describe_ecs_task fidelity: lastStatus=${t?.lastStatus}, desiredStatus=${t?.desiredStatus}, stoppedReason=${t?.stoppedReason}, container_exit=${t?.containers?.[0]?.exitCode}`);
} else {
  console.log(`[A2] NO TASKS RUNNING — ECS Fargate scheduling may be unimplemented on this build`);
}

// A3: ace_describe_ecr_image backing API
try {
  const repoName = ecrRepo.split("/").slice(1).join("/");
  const imgResp = await ecr.send(new DescribeImagesCommand({ repositoryName: repoName, imageIds: [{ imageTag: "latest" }] }));
  const img = imgResp.imageDetails?.[0];
  console.log(`[A3] ace_describe_ecr_image fidelity: digest=${img?.imageDigest?.slice(0,20)}, pushed=${img?.imagePushedAt}, size=${img?.imageSizeInBytes}`);
} catch (e) {
  console.log(`[A3] ace_describe_ecr_image error: ${e.message}`);
}

// --- PROBE B: Traffic (ALB health check → task) ---
try {
  const r = await fetch(`http://${albDns}/health`);
  const body = await r.json();
  console.log(`[B] ALB traffic: status=${r.status}, body=${JSON.stringify(body)}`);
} catch (e) {
  console.log(`[B] ALB traffic error (expected if Fargate not scheduling): ${e.message}`);
}

// --- PROBE C: Fault enforcement ---

// C1: Bad image tag — does task fail to start with a stoppedReason containing "CannotPullContainerError"?
// (Injecting a bad tag into a new task def revision and registering a new service is expensive;
//  instead, check whether the existing service surfaces pull failures in events/stoppedReason.)
console.log(`[C1] Bad-image-tag enforcement: check svc events for CannotPullContainerError or similar`);
const pullErrEvent = svc?.events?.find(e => /pull|image|ECR|CannotPull/i.test(e.message));
console.log(`[C1] pull-error event found: ${pullErrEvent ? pullErrEvent.message : "none (good — image pulled OK)"}`);

// C2: Task-role IAM enforcement — does a DynamoDB call from inside the task fail when the role lacks dynamo:*?
// Cannot exec into Fargate task on LocalStack; instead check whether IAM sim would block
// (proxy: describe the task role and confirm the role has no DynamoDB policy — enforcement is inferred).
console.log(`[C2] IAM task-role enforcement: cannot exec into Fargate task; must inject env-var fallback`);

// C3: Wrong container port / target-group mismatch — does the ALB return 502/503?
console.log(`[C3] Port mismatch enforcement: if task runs, wrong containerPort → TG health-check fails → ALB 502`);

// Teardown hint
console.log(`\n[TEARDOWN] Run: aws --endpoint-url=http://localhost:4566 cloudformation delete-stack --stack-name spike-ecs --region us-east-1`);
```

Run: `node scratch/spike_ecs.mjs`

- [ ] **Step 6: Evaluate fidelity and record findings**

For each probe, record the result in the `## Task 1 findings` section of this file (see template below). Key decisions:

- If probe A2 shows `NO TASKS RUNNING` (tasks never reach `RUNNING`): record "ECS Fargate scheduling not implemented — family shelved" and stop. Do not proceed to Task 2.
- If probe A2 shows tasks reach `RUNNING` but probe B fails (ALB does not route to the container): record "ECS scheduling works but ALB integration absent — use direct task-IP probing for functional test, ALB optional".
- If probe A1 returns empty `events` / zero `deployments` data: mark `ace_describe_ecs_service` as `⚠️ partial` and decide whether to keep or drop.
- If probe A3 fails (ECR DescribeImages error): mark `ace_describe_ecr_image` as `❌ dead` and drop it.
- Lock fault mechanisms using the primary+fallback table in findings.

- [ ] **Step 7: Tear down the spike stack**

```bash
aws --endpoint-url=http://localhost:4566 cloudformation delete-stack --stack-name spike-ecs --region us-east-1
aws --endpoint-url=http://localhost:4566 cloudformation wait stack-delete-complete --stack-name spike-ecs --region us-east-1
```
No commit (scratch is gitignored).

- [ ] **Step 8: Append findings + commit the plan update**

Fill the `## Task 1 findings` section below, then commit:
```bash
git add docs/superpowers/plans/2026-06-20-breadth-containers-ecs.md
git commit -m "docs(plan): record arch04 ECS spike findings and locked fault mechanisms"
```

---

## Task 1 findings

> **Executor fills this section after running Steps 1–7 above. Leave blank until the spike is run.**

### LocalStack version

```
version: <fill from curl -s localhost:4566/_localstack/info>
```

### ECS Fargate scheduling verdict

> CRITICAL: if tasks never reach `RUNNING`, record "SHELVED — ECS Fargate not scheduled on this LocalStack build" and skip Tasks 2–6.

```
Verdict: <VIABLE / SHELVED>
Tasks reached RUNNING: <yes/no>
ALB routing works: <yes/no/N/A>
```

### Capability × fidelity matrix

| Candidate tool | Backing API | Probe result | Fidelity | Decision |
|---|---|---|---|---|
| `ace_describe_ecs_service` | `DescribeServices` | `<fill>` | `<✅/⚠️/❌>` | `<keep/drop>` |
| `ace_describe_ecs_task` | `DescribeTasks` | `<fill>` | `<✅/⚠️/❌>` | `<keep/drop>` |
| `ace_describe_ecr_image` | `DescribeImages` | `<fill>` | `<✅/⚠️/❌>` | `<keep/drop>` |

**Locked tool list:** `<list tools kept from above>`

### Fault mechanism decisions

| Fault | Fault class | Primary mechanism | Primary enforced? | Fallback mechanism | Decision |
|---|---|---|---|---|---|
| fault01 | image-pull | Wrong ECR image tag (`nonexistent-tag`) in task def → task `stoppedReason: CannotPullContainerError` | `<yes/no>` | Inject env var `IMAGE_TAG=bad` and override entrypoint to exit 1 | `<PRIMARY / FALLBACK / DROPPED>` |
| fault02 | iam | Task role missing `dynamodb:PutItem` → `AccessDenied` at runtime | `<yes/no>` | Inject wrong `TABLE_NAME` env var → `ResourceNotFoundException` at runtime | `<PRIMARY / FALLBACK / DROPPED>` |
| fault03 | configuration | Wrong `containerPort` in task def (e.g. `9090`) → ALB TG health check fails → no healthy targets | `<yes/no>` | Inject wrong `APP_PORT` env var so app binds non-standard port | `<PRIMARY / FALLBACK / DROPPED>` |
| fault04 | configuration | Wrong `DYNAMODB_ENDPOINT` env var in container → connection refused | `<yes/no>` | Wrong region env var `AWS_DEFAULT_REGION=eu-west-1` → endpoint mismatch | `<PRIMARY / FALLBACK / DROPPED>` |

**X-Ray instrument ECS handlers?** `<yes/no — based on whether ace_get_trace_summaries returns segments from ECS tasks>`

### Locked fault count

`<N faults shipping (1–4); record if any were dropped and why>`

---

## Task 2: ECS/ECR MCP diagnostic tools (TDD)

> **Gate:** Do not start Task 2 unless Task 1 findings verdict is VIABLE.

Adds `harness/mcp_server/tools/probe_ecs.js` with the locked tool list (up to three tools) and wires it into `index.js`. TDD via `node:test`.

**Files:**
- Create: `harness/mcp_server/tools/probe_ecs.js`
- Modify: `harness/mcp_server/index.js` (import + spread `probeEcsTools`)
- Modify: `harness/mcp_server/package.json` (add `@aws-sdk/client-ecs` and `@aws-sdk/client-ecr`)
- Append: `tests/test_mcp_server.js` (ECS tool block)

**Interfaces:**
- Consumes: the locked tool list from Task 1 findings.
- Produces: `export const probeEcsTools` — an array of up to three tools:
  - `ace_describe_ecs_service({ cluster, service_name })` → `{ cluster, service_name, status, desired_count, running_count, pending_count, deployments: [{id, status, desired_count, running_count, failed_tasks, created_at}], events: [{created_at, message}] }` or `{ error }`. Calls `ecs:DescribeServices`. Use to diagnose services stuck in a partial-deployment state (running_count < desired_count), image-pull failures surfaced in events, and port/health-check faults (all tasks failing → zero running_count).
  - `ace_describe_ecs_task({ cluster, task_id })` → `{ task_arn, task_definition_arn, last_status, desired_status, stopped_reason, started_at, stopped_at, containers: [{name, image, last_status, exit_code, reason}] }` or `{ error }`. Calls `ecs:DescribeTasks`. Use to diagnose task-level faults: image-pull failures (`stoppedReason: CannotPullContainerError`), container exit codes (crash loops), IAM failures surfacing in `reason`.
  - `ace_describe_ecr_image({ repository_name, image_tag })` → `{ repository_name, image_tag, image_digest, image_pushed_at, image_size_bytes, image_tags }` or `{ error }`. Calls `ecr:DescribeImages`. Use to confirm whether a tagged image exists in ECR — reach for this when `ace_describe_ecs_task` shows an image-pull failure or when you need to verify the tag the task definition references actually exists.

> **If Task 1 dropped a tool:** omit it from the implementation and tests below; note the drop in comments.

- [ ] **Step 1: Add ECS and ECR SDK dependencies to `package.json`**

In `harness/mcp_server/package.json`, add to `"dependencies"`:
```json
"@aws-sdk/client-ecs": "^3.0.0",
"@aws-sdk/client-ecr": "^3.0.0"
```
Then install:
```bash
cd harness/mcp_server && npm install && cd -
```
Expected: both packages appear in `node_modules`.

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_mcp_server.js`. First add the import near the other tool imports at the top of the file:
```javascript
import { probeEcsTools } from "../harness/mcp_server/tools/probe_ecs.js";
```
Then append the test block:
```javascript
// ── ECS / ECR tools ──────────────────────────────────────────────────────────
test("probeEcsTools exposes the three ECS/ECR tools", () => {
  for (const n of ["ace_describe_ecs_service", "ace_describe_ecs_task", "ace_describe_ecr_image"]) {
    assert.ok(tool(probeEcsTools, n), `missing ${n}`);
  }
});

test("ace_describe_ecs_service: missing cluster returns error", async () => {
  const res = await tool(probeEcsTools, "ace_describe_ecs_service").handler({});
  assert.ok(res.error, "expected error when cluster missing");
});

test("ace_describe_ecs_service: missing service_name returns error", async () => {
  const res = await tool(probeEcsTools, "ace_describe_ecs_service").handler({ cluster: "my-cluster" });
  assert.ok(res.error, "expected error when service_name missing");
});

test("ace_describe_ecs_service: unknown service returns error", async () => {
  const res = await tool(probeEcsTools, "ace_describe_ecs_service").handler({ cluster: "no-such-cluster", service_name: "no-such-service" });
  assert.ok(res.error, "expected error for unknown service");
});

test("ace_describe_ecs_task: missing cluster returns error", async () => {
  const res = await tool(probeEcsTools, "ace_describe_ecs_task").handler({});
  assert.ok(res.error, "expected error when cluster missing");
});

test("ace_describe_ecs_task: missing task_id returns error", async () => {
  const res = await tool(probeEcsTools, "ace_describe_ecs_task").handler({ cluster: "my-cluster" });
  assert.ok(res.error, "expected error when task_id missing");
});

test("ace_describe_ecs_task: unknown task returns error", async () => {
  const res = await tool(probeEcsTools, "ace_describe_ecs_task").handler({ cluster: "no-such-cluster", task_id: "arn:aws:ecs:us-east-1:000000000000:task/no-such" });
  assert.ok(res.error, "expected error for unknown task");
});

test("ace_describe_ecr_image: missing repository_name returns error", async () => {
  const res = await tool(probeEcsTools, "ace_describe_ecr_image").handler({});
  assert.ok(res.error, "expected error when repository_name missing");
});

test("ace_describe_ecr_image: missing image_tag returns error", async () => {
  const res = await tool(probeEcsTools, "ace_describe_ecr_image").handler({ repository_name: "my-repo" });
  assert.ok(res.error, "expected error when image_tag missing");
});

test("ace_describe_ecr_image: nonexistent image returns error", async () => {
  const res = await tool(probeEcsTools, "ace_describe_ecr_image").handler({ repository_name: "does-not-exist", image_tag: "latest" });
  assert.ok(res.error, "expected error for nonexistent image");
});
```

- [ ] **Step 3: Run the tests to verify they fail**

```bash
node --test tests/test_mcp_server.js 2>&1 | grep -A2 "probeEcsTools\|ace_describe_ecs"
```
Expected: FAIL — `Cannot find module '.../probe_ecs.js'`.

- [ ] **Step 4: Implement `harness/mcp_server/tools/probe_ecs.js`**

Create `harness/mcp_server/tools/probe_ecs.js`:
```javascript
import {
  ECSClient,
  DescribeServicesCommand,
  DescribeTasksCommand,
} from "@aws-sdk/client-ecs";
import {
  ECRClient,
  DescribeImagesCommand,
} from "@aws-sdk/client-ecr";

const awsConfig = {
  endpoint: process.env.LOCALSTACK_ENDPOINT ?? "http://localhost:4566",
  region: "us-east-1",
  credentials: { accessKeyId: "test", secretAccessKey: "test" },
};

const ecsClient = new ECSClient(awsConfig);
const ecrClient = new ECRClient(awsConfig);

export const probeEcsTools = [
  {
    name: "ace_describe_ecs_service",
    description:
      "ECS DescribeServices: return one ECS service's operational state — status, desiredCount, runningCount, pendingCount, deployment records (id, status, desired/running/failed task counts, created_at), and the 10 most recent service events (timestamp + message). Calls ecs:DescribeServices. Reach for this tool when the symptom is a service that won't stabilise (running < desired), when health checks are failing, or when you suspect an image-pull or port-configuration fault (all tasks stop → runningCount drops to 0 and events contain a CannotPullContainerError or health-check failure message).",
    inputSchema: {
      type: "object",
      properties: {
        cluster: {
          type: "string",
          description: "ECS cluster name or ARN",
        },
        service_name: {
          type: "string",
          description: "ECS service name or ARN",
        },
      },
      required: ["cluster", "service_name"],
    },
    async handler({ cluster, service_name } = {}) {
      if (!cluster) return { error: "cluster is required" };
      if (!service_name) return { error: "service_name is required" };
      try {
        const out = await ecsClient.send(
          new DescribeServicesCommand({ cluster, services: [service_name] })
        );
        const svc = (out.services ?? [])[0];
        if (!svc) return { error: `ECS service not found: ${service_name} in cluster ${cluster}` };
        return {
          cluster,
          service_name: svc.serviceName ?? service_name,
          status: svc.status ?? null,
          desired_count: svc.desiredCount ?? null,
          running_count: svc.runningCount ?? null,
          pending_count: svc.pendingCount ?? null,
          deployments: (svc.deployments ?? []).map((d) => ({
            id: d.id ?? null,
            status: d.status ?? null,
            desired_count: d.desiredCount ?? null,
            running_count: d.runningCount ?? null,
            failed_tasks: d.failedTasks ?? null,
            created_at: d.createdAt?.toISOString() ?? null,
          })),
          events: (svc.events ?? []).slice(0, 10).map((e) => ({
            created_at: e.createdAt?.toISOString() ?? null,
            message: e.message ?? null,
          })),
        };
      } catch (err) {
        return { error: String(err?.message ?? err) };
      }
    },
  },
  {
    name: "ace_describe_ecs_task",
    description:
      "ECS DescribeTasks: return one ECS task's runtime state — lastStatus, desiredStatus, stoppedReason, startedAt, stoppedAt, and per-container detail (name, image URI, lastStatus, exitCode, reason). Calls ecs:DescribeTasks. Reach for this tool when a task is not in RUNNING state: stoppedReason surfaces image-pull failures (CannotPullContainerError), IAM access-denied errors, or OOM kills; exitCode reveals application crashes. Pair with ace_describe_ecs_service to find the task ARN (look in service events or use ListTasks), then call this tool to read the per-task and per-container detail.",
    inputSchema: {
      type: "object",
      properties: {
        cluster: {
          type: "string",
          description: "ECS cluster name or ARN",
        },
        task_id: {
          type: "string",
          description: "ECS task ARN or short task ID",
        },
      },
      required: ["cluster", "task_id"],
    },
    async handler({ cluster, task_id } = {}) {
      if (!cluster) return { error: "cluster is required" };
      if (!task_id) return { error: "task_id is required" };
      try {
        const out = await ecsClient.send(
          new DescribeTasksCommand({ cluster, tasks: [task_id] })
        );
        const task = (out.tasks ?? [])[0];
        if (!task) return { error: `ECS task not found: ${task_id} in cluster ${cluster}` };
        return {
          task_arn: task.taskArn ?? null,
          task_definition_arn: task.taskDefinitionArn ?? null,
          last_status: task.lastStatus ?? null,
          desired_status: task.desiredStatus ?? null,
          stopped_reason: task.stoppedReason ?? null,
          started_at: task.startedAt?.toISOString() ?? null,
          stopped_at: task.stoppedAt?.toISOString() ?? null,
          containers: (task.containers ?? []).map((c) => ({
            name: c.name ?? null,
            image: c.image ?? null,
            last_status: c.lastStatus ?? null,
            exit_code: c.exitCode ?? null,
            reason: c.reason ?? null,
          })),
        };
      } catch (err) {
        return { error: String(err?.message ?? err) };
      }
    },
  },
  {
    name: "ace_describe_ecr_image",
    description:
      "ECR DescribeImages: confirm whether a Docker image with a given tag exists in an ECR repository, and return its digest, push timestamp, size in bytes, and all tags present. Calls ecr:DescribeImages. Reach for this tool when ace_describe_ecs_task shows a CannotPullContainerError or when you need to verify that the image tag referenced in a task definition actually exists — a missing or misspelled tag here is the root cause of image-pull fault class.",
    inputSchema: {
      type: "object",
      properties: {
        repository_name: {
          type: "string",
          description: "ECR repository name (without the registry prefix)",
        },
        image_tag: {
          type: "string",
          description: "Docker image tag to look up (e.g. 'latest', 'v1.2.3')",
        },
      },
      required: ["repository_name", "image_tag"],
    },
    async handler({ repository_name, image_tag } = {}) {
      if (!repository_name) return { error: "repository_name is required" };
      if (!image_tag) return { error: "image_tag is required" };
      try {
        const out = await ecrClient.send(
          new DescribeImagesCommand({
            repositoryName: repository_name,
            imageIds: [{ imageTag: image_tag }],
          })
        );
        const img = (out.imageDetails ?? [])[0];
        if (!img) return { error: `Image not found: ${repository_name}:${image_tag}` };
        return {
          repository_name: img.repositoryName ?? repository_name,
          image_tag,
          image_digest: img.imageDigest ?? null,
          image_pushed_at: img.imagePushedAt?.toISOString() ?? null,
          image_size_bytes: img.imageSizeInBytes ?? null,
          image_tags: img.imageTags ?? [],
        };
      } catch (err) {
        return { error: String(err?.message ?? err) };
      }
    },
  },
];
```

- [ ] **Step 5: Wire `probeEcsTools` into `harness/mcp_server/index.js`**

Add the import alongside the existing tool imports:
```javascript
import { probeEcsTools } from "./tools/probe_ecs.js";
```
Add `...probeEcsTools` to the spread in the `for` loop (insert before `...scoreTools`):
```javascript
for (const tool of [...probeTools, ...probeExtendedTools, ...observeTools, ...observeExtendedTools, ...observeTracingTools, ...probeRdsTools, ...probeEcsTools, ...scoreTools]) {
```

- [ ] **Step 6: Run the tests to verify they pass**

```bash
node --test tests/test_mcp_server.js 2>&1 | tail -30
```
Expected: all `probeEcsTools` / `ace_describe_ecs_*` / `ace_describe_ecr_image` tests PASS; no prior tests regress.

- [ ] **Step 7: Commit**

```bash
git add harness/mcp_server/tools/probe_ecs.js harness/mcp_server/index.js harness/mcp_server/package.json harness/mcp_server/package-lock.json tests/test_mcp_server.js
git commit -m "feat(mcp): add ECS/ECR diagnostic tools (describe_ecs_service, describe_ecs_task, describe_ecr_image)"
```

---

## Task 3: arch04 corpus (known-good)

> **Gate:** Task 1 findings verdict must be VIABLE.

Builds the clean known-good corpus for arch04: an ECS Fargate service running a Python HTTP app that reads/writes DynamoDB, exposed through an ALB, with full IAM enforcement and X-Ray instrumentation (if Task 1 approved it).

**Files:**
- Create: `corpus/arch_04_containers_ecs_fargate/known_good.yaml`
- Create: `corpus/arch_04_containers_ecs_fargate/functional_test.py`
- Create: `corpus/arch_04_containers_ecs_fargate/traffic_flow.md`
- Create: `corpus/arch_04_containers_ecs_fargate/deployment/app/Dockerfile`
- Create: `corpus/arch_04_containers_ecs_fargate/deployment/app/app.py`
- Create: `corpus/arch_04_containers_ecs_fargate/deployment/app/requirements.txt`

**Interfaces:**
- Consumes: Task 1 findings (task scheduling confirmed viable; X-Ray decision recorded).
- Produces: a corpus that deploys clean under IAM enforcement and whose `functional_test.py` passes end-to-end.

- [ ] **Step 1: Write the container app**

Create `corpus/arch_04_containers_ecs_fargate/deployment/app/requirements.txt`:
```
boto3==1.34.0
aws-xray-sdk==2.12.1
```

Create `corpus/arch_04_containers_ecs_fargate/deployment/app/app.py`:
```python
"""
arch04 corpus container app.
Serves a simple key-value store backed by DynamoDB.
Routes:
  POST /items          {"key": "k", "value": "v"}  → 201 {"key": "k"}
  GET  /items/<key>                                 → 200 {"key": "k", "value": "v"} or 404
  GET  /health                                      → 200 {"status": "ok"}
"""
import json
import os
import re
from http.server import BaseHTTPRequestHandler, HTTPServer

import boto3
from aws_xray_sdk.core import xray_recorder, patch_all

patch_all()

ENDPOINT  = os.environ.get("DYNAMODB_ENDPOINT", "http://localhost.localstack.cloud:4566")
REGION    = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
TABLE     = os.environ.get("TABLE_NAME")
PORT      = int(os.environ.get("APP_PORT", "8080"))

ddb = boto3.resource("dynamodb", endpoint_url=ENDPOINT, region_name=REGION)
table = ddb.Table(TABLE)

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # silence default access log
        pass

    def send_json(self, code, body):
        payload = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path == "/health":
            self.send_json(200, {"status": "ok"})
            return
        m = re.match(r"^/items/(.+)$", self.path)
        if m:
            key = m.group(1)
            try:
                resp = table.get_item(Key={"pk": key})
                item = resp.get("Item")
                if item:
                    self.send_json(200, {"key": item["pk"], "value": item["value"]})
                else:
                    self.send_json(404, {"error": "not found"})
            except Exception as e:
                self.send_json(500, {"error": str(e)})
            return
        self.send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/items":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            key = body.get("key")
            value = body.get("value")
            if not key or value is None:
                self.send_json(400, {"error": "key and value required"})
                return
            try:
                table.put_item(Item={"pk": key, "value": value})
                self.send_json(201, {"key": key})
            except Exception as e:
                self.send_json(500, {"error": str(e)})
            return
        self.send_json(404, {"error": "not found"})


if __name__ == "__main__":
    server = HTTPServer(("", PORT), Handler)
    print(f"Listening on :{PORT}", flush=True)
    server.serve_forever()
```

Create `corpus/arch_04_containers_ecs_fargate/deployment/app/Dockerfile`:
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
ENV APP_PORT=8080
EXPOSE 8080
CMD ["python3", "app.py"]
```

- [ ] **Step 2: Write `known_good.yaml`**

Create `corpus/arch_04_containers_ecs_fargate/known_good.yaml`. The template must include (all resource names use `!Sub '${AWS::StackName}-...'`):

```yaml
AWSTemplateFormatVersion: '2010-09-09'
Description: >
  arch04 corpus: ECS Fargate service (ALB → container → DynamoDB).
  Container reads/writes a DynamoDB table; ALB exposes /health and /items.

Parameters:
  ImageUri:
    Type: String
    Description: Full ECR image URI including tag (e.g. 000000000000.dkr.ecr.us-east-1.localhost.localstack.cloud:4566/arch04-app:latest)

Resources:
  # ── VPC ────────────────────────────────────────────────────────────────────
  VPC:
    Type: AWS::EC2::VPC
    Properties:
      CidrBlock: 10.0.0.0/16
      EnableDnsHostnames: true
      EnableDnsSupport: true

  SubnetA:
    Type: AWS::EC2::Subnet
    Properties:
      VpcId: !Ref VPC
      CidrBlock: 10.0.1.0/24
      AvailabilityZone: !Select [0, !GetAZs '']
      MapPublicIpOnLaunch: true

  SubnetB:
    Type: AWS::EC2::Subnet
    Properties:
      VpcId: !Ref VPC
      CidrBlock: 10.0.2.0/24
      AvailabilityZone: !Select [1, !GetAZs '']
      MapPublicIpOnLaunch: true

  IGW:
    Type: AWS::EC2::InternetGateway

  IGWAttach:
    Type: AWS::EC2::VPCGatewayAttachment
    Properties:
      VpcId: !Ref VPC
      InternetGatewayId: !Ref IGW

  RouteTable:
    Type: AWS::EC2::RouteTable
    Properties:
      VpcId: !Ref VPC

  DefaultRoute:
    Type: AWS::EC2::Route
    DependsOn: IGWAttach
    Properties:
      RouteTableId: !Ref RouteTable
      DestinationCidrBlock: 0.0.0.0/0
      GatewayId: !Ref IGW

  SubnetAAssoc:
    Type: AWS::EC2::SubnetRouteTableAssociation
    Properties:
      SubnetId: !Ref SubnetA
      RouteTableId: !Ref RouteTable

  SubnetBAssoc:
    Type: AWS::EC2::SubnetRouteTableAssociation
    Properties:
      SubnetId: !Ref SubnetB
      RouteTableId: !Ref RouteTable

  # ── Security groups ────────────────────────────────────────────────────────
  AlbSG:
    Type: AWS::EC2::SecurityGroup
    Properties:
      GroupDescription: ALB inbound HTTP
      VpcId: !Ref VPC
      SecurityGroupIngress:
        - IpProtocol: tcp
          FromPort: 80
          ToPort: 80
          CidrIp: 0.0.0.0/0
      SecurityGroupEgress:
        - IpProtocol: -1
          CidrIp: 0.0.0.0/0

  ContainerSG:
    Type: AWS::EC2::SecurityGroup
    Properties:
      GroupDescription: Container inbound from ALB
      VpcId: !Ref VPC
      SecurityGroupIngress:
        - IpProtocol: tcp
          FromPort: 8080
          ToPort: 8080
          SourceSecurityGroupId: !Ref AlbSG
      SecurityGroupEgress:
        - IpProtocol: -1
          CidrIp: 0.0.0.0/0

  # ── DynamoDB table ─────────────────────────────────────────────────────────
  ItemsTable:
    Type: AWS::DynamoDB::Table
    Properties:
      TableName: !Sub '${AWS::StackName}-items'
      BillingMode: PAY_PER_REQUEST
      AttributeDefinitions:
        - AttributeName: pk
          AttributeType: S
      KeySchema:
        - AttributeName: pk
          KeyType: HASH

  # ── ECR repository ─────────────────────────────────────────────────────────
  AppRepo:
    Type: AWS::ECR::Repository
    Properties:
      RepositoryName: !Sub '${AWS::StackName}-app'

  # ── IAM task execution role ────────────────────────────────────────────────
  TaskExecutionRole:
    Type: AWS::IAM::Role
    Properties:
      AssumeRolePolicyDocument:
        Version: '2012-10-17'
        Statement:
          - Effect: Allow
            Principal:
              Service: ecs-tasks.amazonaws.com
            Action: sts:AssumeRole
      Policies:
        - PolicyName: ecr-logs
          PolicyDocument:
            Version: '2012-10-17'
            Statement:
              - Effect: Allow
                Action:
                  - ecr:GetAuthorizationToken
                  - ecr:BatchCheckLayerAvailability
                  - ecr:GetDownloadUrlForLayer
                  - ecr:BatchGetImage
                Resource: '*'
              - Effect: Allow
                Action:
                  - logs:CreateLogGroup
                  - logs:CreateLogStream
                  - logs:PutLogEvents
                Resource: '*'

  # ── IAM task role (app permissions) ───────────────────────────────────────
  TaskRole:
    Type: AWS::IAM::Role
    Properties:
      AssumeRolePolicyDocument:
        Version: '2012-10-17'
        Statement:
          - Effect: Allow
            Principal:
              Service: ecs-tasks.amazonaws.com
            Action: sts:AssumeRole
      Policies:
        - PolicyName: dynamo-readwrite
          PolicyDocument:
            Version: '2012-10-17'
            Statement:
              - Effect: Allow
                Action:
                  - dynamodb:GetItem
                  - dynamodb:PutItem
                  - dynamodb:DeleteItem
                  - dynamodb:Query
                  - dynamodb:Scan
                Resource: !GetAtt ItemsTable.Arn
              - Effect: Allow
                Action:
                  - xray:PutTraceSegments
                  - xray:PutTelemetryRecords
                Resource: '*'

  # ── ECS cluster + task def + service ──────────────────────────────────────
  Cluster:
    Type: AWS::ECS::Cluster
    Properties:
      ClusterName: !Sub '${AWS::StackName}-cluster'

  LogGroup:
    Type: AWS::Logs::LogGroup
    Properties:
      LogGroupName: !Sub '/ecs/${AWS::StackName}'
      RetentionInDays: 7

  TaskDef:
    Type: AWS::ECS::TaskDefinition
    Properties:
      Family: !Sub '${AWS::StackName}-task'
      RequiresCompatibilities: [FARGATE]
      NetworkMode: awsvpc
      Cpu: '256'
      Memory: '512'
      ExecutionRoleArn: !GetAtt TaskExecutionRole.Arn
      TaskRoleArn: !GetAtt TaskRole.Arn
      ContainerDefinitions:
        - Name: app
          Image: !Ref ImageUri
          PortMappings:
            - ContainerPort: 8080
              Protocol: tcp
          Environment:
            - Name: TABLE_NAME
              Value: !Ref ItemsTable
            - Name: DYNAMODB_ENDPOINT
              Value: http://localhost.localstack.cloud:4566
            - Name: APP_PORT
              Value: '8080'
            - Name: AWS_DEFAULT_REGION
              Value: !Ref AWS::Region
          LogConfiguration:
            LogDriver: awslogs
            Options:
              awslogs-group: !Ref LogGroup
              awslogs-region: !Ref AWS::Region
              awslogs-stream-prefix: app

  Service:
    Type: AWS::ECS::Service
    DependsOn: Listener
    Properties:
      ServiceName: !Sub '${AWS::StackName}-service'
      Cluster: !Ref Cluster
      TaskDefinition: !Ref TaskDef
      LaunchType: FARGATE
      DesiredCount: 1
      NetworkConfiguration:
        AwsvpcConfiguration:
          AssignPublicIp: ENABLED
          Subnets:
            - !Ref SubnetA
            - !Ref SubnetB
          SecurityGroups:
            - !Ref ContainerSG
      LoadBalancers:
        - ContainerName: app
          ContainerPort: 8080
          TargetGroupArn: !Ref TargetGroup

  # ── ALB ────────────────────────────────────────────────────────────────────
  ALB:
    Type: AWS::ElasticLoadBalancingV2::LoadBalancer
    Properties:
      Name: !Sub '${AWS::StackName}-alb'
      Scheme: internet-facing
      Type: application
      Subnets:
        - !Ref SubnetA
        - !Ref SubnetB
      SecurityGroups:
        - !Ref AlbSG

  TargetGroup:
    Type: AWS::ElasticLoadBalancingV2::TargetGroup
    Properties:
      Name: !Sub '${AWS::StackName}-tg'
      Port: 8080
      Protocol: HTTP
      TargetType: ip
      VpcId: !Ref VPC
      HealthCheckPath: /health
      HealthCheckIntervalSeconds: 10
      HealthyThresholdCount: 2
      UnhealthyThresholdCount: 3

  Listener:
    Type: AWS::ElasticLoadBalancingV2::Listener
    Properties:
      LoadBalancerArn: !Ref ALB
      Port: 80
      Protocol: HTTP
      DefaultActions:
        - Type: forward
          TargetGroupArn: !Ref TargetGroup

Outputs:
  ClusterName:
    Value: !Ref Cluster
  ServiceName:
    Value: !GetAtt Service.Name
  TaskDefinitionArn:
    Value: !Ref TaskDef
  EcrRepoUri:
    Value: !Sub '${AWS::AccountId}.dkr.ecr.${AWS::Region}.${AWS::URLSuffix}/${AppRepo}'
  AlbDnsName:
    Value: !GetAtt ALB.DNSName
  ItemsTableName:
    Value: !Ref ItemsTable
```

- [ ] **Step 3: Build and push the app image to LocalStack ECR**

```bash
# Deploy the stack first (Step 4) to create the ECR repo, then push the image.
# Build the image:
docker build -f corpus/arch_04_containers_ecs_fargate/deployment/app/Dockerfile \
  -t arch04-app:latest corpus/arch_04_containers_ecs_fargate/deployment/app/
```
(Push step is integrated in Step 5 after the stack exists.)

- [ ] **Step 4: Deploy `known_good.yaml` as `ace-bench-stack`**

```bash
# Use a placeholder ImageUri for initial stack creation; update after image push.
aws --endpoint-url=http://localhost:4566 cloudformation create-stack \
  --stack-name ace-bench-stack \
  --template-body file://corpus/arch_04_containers_ecs_fargate/known_good.yaml \
  --parameters ParameterKey=ImageUri,ParameterValue=placeholder:latest \
  --capabilities CAPABILITY_IAM \
  --region us-east-1
aws --endpoint-url=http://localhost:4566 cloudformation wait stack-create-complete \
  --stack-name ace-bench-stack --region us-east-1
```
If `ROLLBACK_COMPLETE`, run `aws --endpoint-url=http://localhost:4566 cloudformation describe-stack-events --stack-name ace-bench-stack` and fix the template.

- [ ] **Step 5: Push the image and update the service**

```bash
REPO_URI=$(aws --endpoint-url=http://localhost:4566 cloudformation describe-stacks \
  --stack-name ace-bench-stack \
  --query 'Stacks[0].Outputs[?OutputKey==`EcrRepoUri`].OutputValue' \
  --output text --region us-east-1)

aws --endpoint-url=http://localhost:4566 ecr get-login-password --region us-east-1 \
  | docker login --username AWS --password-stdin "$(echo $REPO_URI | cut -d/ -f1)"

docker tag arch04-app:latest "$REPO_URI:latest"
docker push "$REPO_URI:latest"

# Update the stack with the real image URI:
aws --endpoint-url=http://localhost:4566 cloudformation update-stack \
  --stack-name ace-bench-stack \
  --template-body file://corpus/arch_04_containers_ecs_fargate/known_good.yaml \
  --parameters ParameterKey=ImageUri,ParameterValue="$REPO_URI:latest" \
  --capabilities CAPABILITY_IAM \
  --region us-east-1
aws --endpoint-url=http://localhost:4566 cloudformation wait stack-update-complete \
  --stack-name ace-bench-stack --region us-east-1
```

- [ ] **Step 6: Write `functional_test.py`**

Create `corpus/arch_04_containers_ecs_fargate/functional_test.py`:
```python
"""
arch04 functional test.
Verifies the ECS Fargate service is reachable via the ALB and can
read/write DynamoDB items end-to-end.

Exit codes: 0 = all assertions passed, non-zero = at least one failed.
Output: lines matching 'ASSERT (pass|fail) <test_name> [detail]'
"""
import json
import sys
import time
import urllib.error
import urllib.request

import boto3

ENDPOINT = "http://localhost:4566"
REGION   = "us-east-1"
CREDS    = {"aws_access_key_id": "test", "aws_secret_access_key": "test"}
STACK    = "ace-bench-stack"

_passes = 0
_fails  = 0


def emit_pass(name, detail=""):
    global _passes
    _passes += 1
    print(f"ASSERT pass {name}" + (f" {detail}" if detail else ""), flush=True)


def emit_fail(name, detail=""):
    global _fails
    _fails += 1
    print(f"ASSERT fail {name}" + (f" {detail}" if detail else ""), flush=True)


def finalize():
    print(f"SUMMARY passes={_passes} fails={_fails}", flush=True)
    if _fails > 0:
        sys.exit(1)


def cf_client():
    return boto3.client("cloudformation", endpoint_url=ENDPOINT, region_name=REGION, **CREDS)


def stack_output(key):
    stacks = cf_client().describe_stacks(StackName=STACK)["Stacks"]
    outputs = stacks[0].get("Outputs", [])
    return next((o["OutputValue"] for o in outputs if o["OutputKey"] == key), None)


def wait_for_alb(base_url, retries=20, delay=5):
    """Poll /health until the ALB+container are responsive."""
    for _ in range(retries):
        try:
            r = urllib.request.urlopen(f"{base_url}/health", timeout=5)
            if r.status == 200:
                return True
        except Exception:
            pass
        time.sleep(delay)
    return False


def http_post(url, body):
    data = json.dumps(body).encode()
    req  = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        r = urllib.request.urlopen(req, timeout=10)
        return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())
    except Exception as e:
        return None, {"error": str(e)}


def http_get(url):
    try:
        r = urllib.request.urlopen(url, timeout=10)
        return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())
    except Exception as e:
        return None, {"error": str(e)}


def main():
    alb_dns = stack_output("AlbDnsName")
    if not alb_dns:
        emit_fail("stack_outputs", "AlbDnsName output missing")
        finalize()
        return

    base = f"http://{alb_dns}"

    # test_health: ALB routes to container /health
    if wait_for_alb(base):
        status, body = http_get(f"{base}/health")
        if status == 200 and body.get("status") == "ok":
            emit_pass("health_check")
        else:
            emit_fail("health_check", f"status={status} body={body}")
    else:
        emit_fail("health_check", "ALB not reachable after retries")
        finalize()
        return

    # test_put_item: POST /items writes to DynamoDB
    status, body = http_post(f"{base}/items", {"key": "test-key-1", "value": "hello-arch04"})
    if status == 201 and body.get("key") == "test-key-1":
        emit_pass("put_item")
    else:
        emit_fail("put_item", f"status={status} body={body}")

    # test_get_item: GET /items/<key> reads back from DynamoDB
    status, body = http_get(f"{base}/items/test-key-1")
    if status == 200 and body.get("value") == "hello-arch04":
        emit_pass("get_item")
    else:
        emit_fail("get_item", f"status={status} body={body}")

    # test_get_missing: GET /items/<nonexistent> returns 404
    status, body = http_get(f"{base}/items/does-not-exist-xyz")
    if status == 404:
        emit_pass("get_missing_404")
    else:
        emit_fail("get_missing_404", f"expected 404, got status={status}")

    finalize()


if __name__ == "__main__":
    main()
```

- [ ] **Step 7: Run the functional test and confirm it passes**

```bash
python corpus/arch_04_containers_ecs_fargate/functional_test.py
```
Expected: all `ASSERT pass` lines, exit 0. If any fail, debug the container (check `ace_describe_ecs_service` + `ace_describe_ecs_task` against the live stack) and fix.

- [ ] **Step 8: Write `traffic_flow.md`**

Create `corpus/arch_04_containers_ecs_fargate/traffic_flow.md`:
```markdown
# arch04 Traffic Flow — ECS Fargate + ALB + DynamoDB

## Write path
Client → ALB (port 80) → Target Group (port 8080) → Fargate task (container: app)
→ DynamoDB PutItem (arch04 items table) → 201 {"key": "..."}

## Read path
Client → ALB (port 80) → Target Group (port 8080) → Fargate task
→ DynamoDB GetItem → 200 {"key": "...", "value": "..."} or 404

## Health check path
ALB Target Group → Fargate task GET /health → 200 {"status": "ok"}
(ALB marks target healthy after 2 consecutive 200 responses at 10s intervals)

## IAM chain
Fargate task assumes TaskRole (ecs-tasks.amazonaws.com) →
  dynamodb:GetItem, dynamodb:PutItem on ItemsTable
  xray:PutTraceSegments on *

ECS agent assumes TaskExecutionRole →
  ecr:BatchGetImage (pull image from ECR)
  logs:PutLogEvents (push container stdout to CloudWatch Logs)

## Fault axes
- ECR image pull: task definition references ECR repo URI + tag; wrong tag → CannotPullContainerError
- Task role IAM: missing dynamodb:PutItem → AccessDenied at runtime
- Container port: task def containerPort must match app APP_PORT env var and TG port
- App config: TABLE_NAME / DYNAMODB_ENDPOINT env vars must be correct for DynamoDB writes
```

- [ ] **Step 9: Tear down and commit**

```bash
aws --endpoint-url=http://localhost:4566 cloudformation delete-stack --stack-name ace-bench-stack --region us-east-1
aws --endpoint-url=http://localhost:4566 cloudformation wait stack-delete-complete --stack-name ace-bench-stack --region us-east-1

git add corpus/arch_04_containers_ecs_fargate/
git commit -m "feat(corpus): add arch04 ECS Fargate corpus (known_good, functional_test, traffic_flow, app)"
```

---

## Task 4: Fault scenarios

> **Gate:** Task 1 findings verdict must be VIABLE. Use only locked mechanisms from the Task 1 findings table. If a mechanism is listed as DROPPED, skip that fault.

Each scenario = corpus `known_good.yaml` with ONE injected fault, a symptom-only `scenario.md`, and a `fault_manifest.json` (never exposed). The primary mechanism is used if Task 1 confirmed it enforced; otherwise the fallback is used.

**Files (per scenario `scenarios/arch04_fault0N_<class>/`):**
- Create: `faulted.yaml`
- Create: `scenario.md`
- Create: `fault_manifest.json`
- Create: `deployment/app/` (copy of corpus app — Dockerfile, app.py, requirements.txt)

**Interfaces:**
- Consumes: corpus `known_good.yaml` + app (Task 3); the ECS tools (Task 2); Task 1 locked mechanisms.
- Produces: scenario dirs each reproducing their fault and diagnosable via the intended tool path; each `fault_manifest.json` carries `optimal_tool_calls`/`optimal_files_changed`/`optimal_lines_changed` measured in Step 9.

- [ ] **Step 1: Scaffold all scenario dirs from the corpus**

```bash
CORP=corpus/arch_04_containers_ecs_fargate
for s in arch04_fault01_image_pull arch04_fault02_iam arch04_fault03_port arch04_fault04_configuration; do
  mkdir -p scenarios/$s
  cp $CORP/known_good.yaml scenarios/$s/faulted.yaml
  cp -r $CORP/deployment scenarios/$s/deployment
done
```
(If Task 1 dropped a fault, skip creating that scenario dir.)

- [ ] **Step 2: Inject fault01 (image-pull)**

**Primary mechanism (if confirmed enforced):** In `scenarios/arch04_fault01_image_pull/faulted.yaml`, change the `ImageUri` default in the Parameters block — or, since ImageUri is a parameter, add a hard-coded bad tag directly in the `TaskDef` container `Image` property by adding a fixed wrong URI override:

Change in `TaskDef.ContainerDefinitions[0].Image`:
```yaml
# from (when stack was created with the real URI via parameter):
Image: !Ref ImageUri
# to (hard-code a nonexistent tag so the parameter is ignored):
Image: !Sub '${AWS::AccountId}.dkr.ecr.${AWS::Region}.${AWS::URLSuffix}/arch04-app:nonexistent-tag-9999'
```
Note: the ECR repo name must match the stack's repo. Use `!Sub` with the stack-name prefix:
```yaml
Image: !Sub '${AWS::AccountId}.dkr.ecr.${AWS::Region}.${AWS::URLSuffix}/${AWS::StackName}-app:nonexistent-tag-9999'
```

**Fallback mechanism (if primary not enforced):** Inject `APP_PORT=9999` in the container environment (container listens on 9999 but TG checks port 8080 → health check never passes → service has zero healthy targets). In `faulted.yaml` `TaskDef.ContainerDefinitions[0].Environment`, change:
```yaml
- Name: APP_PORT
  Value: '9999'
```

Create `scenarios/arch04_fault01_image_pull/scenario.md`:
```markdown
# arch04 — Scenario: Service won't stabilise

The ECS Fargate service `${STACK_NAME}-service` is deployed in cluster `${STACK_NAME}-cluster`.
The desired task count is 1 but no tasks are reaching RUNNING state.
The ALB at `${ALB_DNS}` returns no healthy targets (HTTP 502 or 503 on all requests).

Diagnose the root cause and fix the deployment so that:
1. At least one task reaches RUNNING.
2. `GET /health` returns HTTP 200 `{"status": "ok"}` via the ALB.
```

Create `scenarios/arch04_fault01_image_pull/fault_manifest.json`:
```json
{
  "fault_id": "arch04_fault01",
  "fault_class": "image_pull",
  "architecture": "arch04",
  "scenario_id": "arch04_fault01_image_pull",
  "target_resource": "TaskDef",
  "target_property": "ContainerDefinitions[0].Image",
  "injected_value": "<stack-name>-app:nonexistent-tag-9999",
  "original_value": "<real-ecr-uri>:latest",
  "valid_fixes": [
    "Change the Image tag in the task definition to a tag that exists in ECR (e.g. 'latest')",
    "Push a Docker image with the tag 'nonexistent-tag-9999' to the ECR repository"
  ],
  "invalid_patches": [
    "Change the service desired count to 0",
    "Delete and recreate the ECR repository",
    "Open ALB security group to all traffic"
  ],
  "optimal_diagnostic_path": ["ace_describe_ecs_service", "ace_describe_ecs_task", "ace_describe_ecr_image"],
  "deployment_check": "CREATE_COMPLETE",
  "observability_check": "ace_describe_ecs_task returns stoppedReason containing 'CannotPullContainerError' or similar; ace_describe_ecr_image returns error for the referenced tag",
  "observable_symptom": "ECS service running_count=0; ALB returns 502/503; tasks stop immediately after launch",
  "root_cause": "Task definition references an ECR image tag that does not exist in the repository",
  "corpus_path": "corpus/arch_04_containers_ecs_fargate",
  "functional_test_path": "corpus/arch_04_containers_ecs_fargate/functional_test.py",
  "known_good_path": "corpus/arch_04_containers_ecs_fargate/known_good.yaml",
  "optimal_tool_calls": null,
  "optimal_files_changed": 1,
  "optimal_lines_changed": 1,
  "concurrency_probe_n": 1
}
```

- [ ] **Step 3: Inject fault02 (IAM — task role)**

**Primary mechanism (if IAM enforcement confirmed):** In `scenarios/arch04_fault02_iam/faulted.yaml`, remove `dynamodb:PutItem` from the `TaskRole` policy. Change the `dynamo-readwrite` policy statement `Action` list to:
```yaml
Action:
  - dynamodb:GetItem
  - dynamodb:DeleteItem
  - dynamodb:Query
  - dynamodb:Scan
```
(PutItem removed — `POST /items` will return 500 with AccessDenied from DynamoDB.)

**Fallback mechanism (if IAM not enforced):** Inject wrong `TABLE_NAME` env var. Change in `TaskDef.ContainerDefinitions[0].Environment`:
```yaml
- Name: TABLE_NAME
  Value: !Sub '${AWS::StackName}-wrong-table-name'
```
(DynamoDB GetItem/PutItem target a table that does not exist → `ResourceNotFoundException` at runtime.)

Create `scenarios/arch04_fault02_iam/scenario.md`:
```markdown
# arch04 — Scenario: Write operations fail

The ECS Fargate service is running and the ALB health check passes (`GET /health` returns 200).
However, `POST /items` consistently returns HTTP 500.
Read operations (`GET /items/<key>`) return 404 for all keys (because no items can be written).

Diagnose the root cause and fix the deployment so that `POST /items` returns HTTP 201.
```

Create `scenarios/arch04_fault02_iam/fault_manifest.json`:
```json
{
  "fault_id": "arch04_fault02",
  "fault_class": "iam",
  "architecture": "arch04",
  "scenario_id": "arch04_fault02_iam",
  "target_resource": "TaskRole",
  "target_property": "Policies[0].PolicyDocument.Statement[0].Action",
  "injected_value": "[dynamodb:GetItem, dynamodb:DeleteItem, dynamodb:Query, dynamodb:Scan]",
  "original_value": "[dynamodb:GetItem, dynamodb:PutItem, dynamodb:DeleteItem, dynamodb:Query, dynamodb:Scan]",
  "valid_fixes": [
    "Add dynamodb:PutItem to the TaskRole policy statement Action list"
  ],
  "invalid_patches": [
    "Grant dynamodb:* on Resource '*' (over-privileged)",
    "Replace the task role with a role that has AdministratorAccess",
    "Disable IAM enforcement"
  ],
  "optimal_diagnostic_path": ["ace_describe_ecs_service", "ace_describe_ecs_task"],
  "deployment_check": "CREATE_COMPLETE",
  "observability_check": "ace_describe_ecs_task shows task RUNNING (healthy) but container reason/exit_code on a subsequent stopped task reveals AccessDenied; or CloudWatch log inspection shows AccessDeniedException on PutItem",
  "observable_symptom": "Service healthy (running_count=1) but POST /items returns 500 with an access denied error in the response body",
  "root_cause": "TaskRole is missing the dynamodb:PutItem action; PutItem calls return AccessDeniedException",
  "corpus_path": "corpus/arch_04_containers_ecs_fargate",
  "functional_test_path": "corpus/arch_04_containers_ecs_fargate/functional_test.py",
  "known_good_path": "corpus/arch_04_containers_ecs_fargate/known_good.yaml",
  "optimal_tool_calls": null,
  "optimal_files_changed": 1,
  "optimal_lines_changed": 1,
  "concurrency_probe_n": 1
}
```

- [ ] **Step 4: Inject fault03 (container port mismatch)**

**Primary mechanism (if TG health-check enforcement confirmed):** In `scenarios/arch04_fault03_port/faulted.yaml`, change the `APP_PORT` environment variable on the container from `'8080'` to `'9090'`. The app binds port 9090; the target group health check probes port 8080 → timeout → all tasks unhealthy → no healthy targets.

Change in `TaskDef.ContainerDefinitions[0].Environment`:
```yaml
- Name: APP_PORT
  Value: '9090'
```

**Fallback mechanism (if ALB/TG health check not enforced by LocalStack):** Change `containerPort` in the task definition port mapping from `8080` to `9090`, so ECS registers the wrong port with the target group and traffic is never forwarded:
```yaml
PortMappings:
  - ContainerPort: 9090
    Protocol: tcp
```

Create `scenarios/arch04_fault03_port/scenario.md`:
```markdown
# arch04 — Scenario: ALB returns no healthy targets

The ECS Fargate service shows desired_count=1 and running_count=1 — the task is RUNNING.
However, the ALB consistently returns HTTP 502 or 503. `GET /health` via the ALB fails,
but direct access to the task IP on port 9090 succeeds (if reachable).

Diagnose the root cause and fix the deployment so that `GET /health` returns 200 via the ALB.
```

Create `scenarios/arch04_fault03_port/fault_manifest.json`:
```json
{
  "fault_id": "arch04_fault03",
  "fault_class": "configuration",
  "architecture": "arch04",
  "scenario_id": "arch04_fault03_port",
  "target_resource": "TaskDef",
  "target_property": "ContainerDefinitions[0].Environment[APP_PORT]",
  "injected_value": "9090",
  "original_value": "8080",
  "valid_fixes": [
    "Change APP_PORT environment variable back to '8080' so the app binds the port the target group expects",
    "Change the target group HealthCheckPort and the task definition containerPort to 9090 to match the app (less preferred — non-minimal fix)"
  ],
  "invalid_patches": [
    "Change the ALB listener port to 9090",
    "Open security group to allow all ports",
    "Set desired count to 0"
  ],
  "optimal_diagnostic_path": ["ace_describe_ecs_service", "ace_describe_ecs_task"],
  "deployment_check": "CREATE_COMPLETE",
  "observability_check": "ace_describe_ecs_service shows running_count=1 but events contain health-check failure messages; ace_describe_ecs_task shows task RUNNING (not stopped, so no stoppedReason) — the signal is the discrepancy between running task and ALB 502",
  "observable_symptom": "ECS service running_count=1 (task not crashing) but ALB returns 502/503; target group has no healthy targets",
  "root_cause": "Container app listens on port 9090 (APP_PORT=9090) but the target group health check probes port 8080 — mismatch causes all health checks to time out",
  "corpus_path": "corpus/arch_04_containers_ecs_fargate",
  "functional_test_path": "corpus/arch_04_containers_ecs_fargate/functional_test.py",
  "known_good_path": "corpus/arch_04_containers_ecs_fargate/known_good.yaml",
  "optimal_tool_calls": null,
  "optimal_files_changed": 1,
  "optimal_lines_changed": 1,
  "concurrency_probe_n": 1
}
```

- [ ] **Step 5: Inject fault04 (wrong DynamoDB endpoint)**

**Primary mechanism:** In `scenarios/arch04_fault04_configuration/faulted.yaml`, change the `DYNAMODB_ENDPOINT` environment variable to a non-existent host:
```yaml
- Name: DYNAMODB_ENDPOINT
  Value: http://localhost.localstack.cloud:9999
```
The container starts and `/health` returns 200, but any DynamoDB call (`POST /items`, `GET /items/<key>`) fails with a connection error because the endpoint is unreachable.

**Fallback (if connection errors are silently swallowed):** Change `AWS_DEFAULT_REGION` to `eu-west-1` so boto3 constructs the wrong regional endpoint and DynamoDB calls fail with an endpoint resolution error.

Create `scenarios/arch04_fault04_configuration/scenario.md`:
```markdown
# arch04 — Scenario: Service is up but all data operations fail

The ECS Fargate service is RUNNING and the ALB health check passes (`GET /health` returns 200).
However, both `POST /items` and `GET /items/<key>` return HTTP 500 with a connection error.
No items can be written to or read from the backing store.

Diagnose the root cause and fix the deployment so that `POST /items` and `GET /items/<key>` work correctly.
```

Create `scenarios/arch04_fault04_configuration/fault_manifest.json`:
```json
{
  "fault_id": "arch04_fault04",
  "fault_class": "configuration",
  "architecture": "arch04",
  "scenario_id": "arch04_fault04_configuration",
  "target_resource": "TaskDef",
  "target_property": "ContainerDefinitions[0].Environment[DYNAMODB_ENDPOINT]",
  "injected_value": "http://localhost.localstack.cloud:9999",
  "original_value": "http://localhost.localstack.cloud:4566",
  "valid_fixes": [
    "Change DYNAMODB_ENDPOINT back to http://localhost.localstack.cloud:4566"
  ],
  "invalid_patches": [
    "Remove the DYNAMODB_ENDPOINT variable entirely without setting the correct value",
    "Change the DynamoDB table name to work around the endpoint issue",
    "Grant additional IAM permissions to the task role"
  ],
  "optimal_diagnostic_path": ["ace_describe_ecs_service", "ace_describe_ecs_task"],
  "deployment_check": "CREATE_COMPLETE",
  "observability_check": "ace_describe_ecs_task shows task RUNNING (not stopped); ace_describe_ecs_service shows running_count=1 but events may not reveal env-var faults — the signal is that health check passes (HTTP) but data ops fail (DynamoDB) pointing to a config-level issue rather than IAM",
  "observable_symptom": "ECS task is RUNNING, ALB /health returns 200, but POST /items and GET /items/<key> return 500 with a connection refused or timeout error body",
  "root_cause": "DYNAMODB_ENDPOINT environment variable points to port 9999 which is not listening; all boto3 DynamoDB calls fail with a connection error",
  "corpus_path": "corpus/arch_04_containers_ecs_fargate",
  "functional_test_path": "corpus/arch_04_containers_ecs_fargate/functional_test.py",
  "known_good_path": "corpus/arch_04_containers_ecs_fargate/known_good.yaml",
  "optimal_tool_calls": null,
  "optimal_files_changed": 1,
  "optimal_lines_changed": 1,
  "concurrency_probe_n": 1
}
```

- [ ] **Step 6: Deploy and reproduce each fault**

For each scenario (deploy → verify symptom → tear down before next):

```bash
# Pattern for each scenario (replace SCENARIO_DIR with the actual dir):
SCENARIO_DIR=scenarios/arch04_fault01_image_pull

# First push the real image to ECR (needed for the stack's ECR repo to exist)
# then deploy faulted.yaml passing the bad image URI as parameter where applicable.
aws --endpoint-url=http://localhost:4566 cloudformation create-stack \
  --stack-name ace-bench-stack \
  --template-body file://$SCENARIO_DIR/faulted.yaml \
  --parameters ParameterKey=ImageUri,ParameterValue="000000000000.dkr.ecr.us-east-1.localhost.localstack.cloud:4566/ace-bench-stack-app:nonexistent-tag-9999" \
  --capabilities CAPABILITY_IAM \
  --region us-east-1
aws --endpoint-url=http://localhost:4566 cloudformation wait stack-create-complete \
  --stack-name ace-bench-stack --region us-east-1

# Confirm symptom reproduces (functional test MUST fail):
python corpus/arch_04_containers_ecs_fargate/functional_test.py
# Expected: at least one ASSERT fail line; exit non-zero

# Tear down before next scenario:
aws --endpoint-url=http://localhost:4566 cloudformation delete-stack --stack-name ace-bench-stack --region us-east-1
aws --endpoint-url=http://localhost:4566 cloudformation wait stack-delete-complete --stack-name ace-bench-stack --region us-east-1
```

Walk the intended diagnostic path with the actual MCP tools for each scenario, for example for fault01:
```bash
# Get cluster/service names from stack outputs:
CLUSTER=$(aws --endpoint-url=http://localhost:4566 cloudformation describe-stacks --stack-name ace-bench-stack --query 'Stacks[0].Outputs[?OutputKey==`ClusterName`].OutputValue' --output text --region us-east-1)
SERVICE=$(aws --endpoint-url=http://localhost:4566 cloudformation describe-stacks --stack-name ace-bench-stack --query 'Stacks[0].Outputs[?OutputKey==`ServiceName`].OutputValue' --output text --region us-east-1)

# Call tools directly:
node -e "
import('./harness/mcp_server/tools/probe_ecs.js').then(async m => {
  const t = name => m.probeEcsTools.find(x => x.name === name);
  console.log(JSON.stringify(await t('ace_describe_ecs_service').handler({ cluster: '$CLUSTER', service_name: '$SERVICE' }), null, 2));
});"
```
Confirm the tool output surfaces the fault signal (e.g. `events` containing CannotPullContainerError for fault01, `running_count=1` but 500s from app for fault02, etc.). If a scenario does NOT reproduce, switch to its Task 1 fallback mechanism and re-verify.

- [ ] **Step 7: Baseline `optimal_tool_calls` and finalize manifests**

For each scenario, count the MCP tool calls on the intended diagnostic path walked in Step 6 and write `optimal_tool_calls` into each `fault_manifest.json`. Set `optimal_files_changed` to 1 (one property changed in `faulted.yaml`) and `optimal_lines_changed` to 1–2 as appropriate.

- [ ] **Step 8: Commit**

```bash
git add scenarios/arch04_fault01_image_pull/ scenarios/arch04_fault02_iam/ \
        scenarios/arch04_fault03_port/ scenarios/arch04_fault04_configuration/
git commit -m "feat(scenarios): add arch04 ECS fault scenarios (fault01-04: image_pull, iam, port, configuration)"
```

---

## Task 5: Discoverability QA gate

> Run for every scenario that shipped. Record pass/fail per check below.

For each scenario the gate runs four checks (Section 4 of the framework spec). Checks 1 and 3a can be run without LocalStack. Checks 2 and 3b require a live stack.

### Check 1 — Agent-exposure plumbing

Verify the ECS tools flow through `mcp_to_openai_tool` / `filter_model_tools` and appear in the model's runtime tool list; `ace_verify_fix` and `ace_score_run` remain absent.

- [ ] **Step 1: Verify tool names appear in the harness tool list**

```bash
grep -n "ace_describe_ecs_service\|ace_describe_ecs_task\|ace_describe_ecr_image" \
  harness/mcp_server/tools/probe_ecs.js harness/mcp_server/index.js
```
Expected: all three names appear in `probe_ecs.js` (definitions) and the spread in `index.js`.

- [ ] **Step 2: Verify `mcp_to_openai_tool` and `filter_model_tools` pass the new tools through**

```bash
python -c "
import asyncio, sys
sys.path.insert(0, '.')
from harness.agent.tools import mcp_to_openai_tool, filter_model_tools, FILE_TOOL_DEFINITIONS

# Simulate what the agent does: mcp_to_openai_tool converts MCP tool dicts.
# The ECS tools are served by the MCP server, not hardcoded here; check filter_model_tools
# does not strip them by name.
dummy = [
  {'name': 'ace_describe_ecs_service', 'description': 'test', 'inputSchema': {'type': 'object', 'properties': {}}},
  {'name': 'ace_describe_ecs_task',    'description': 'test', 'inputSchema': {'type': 'object', 'properties': {}}},
  {'name': 'ace_describe_ecr_image',   'description': 'test', 'inputSchema': {'type': 'object', 'properties': {}}},
  {'name': 'ace_verify_fix',           'description': 'test', 'inputSchema': {'type': 'object', 'properties': {}}},
  {'name': 'ace_score_run',            'description': 'test', 'inputSchema': {'type': 'object', 'properties': {}}},
]
converted = [mcp_to_openai_tool(t) for t in dummy]
filtered  = filter_model_tools(converted)
names = [t['function']['name'] for t in filtered]
assert 'ace_describe_ecs_service' in names, 'ace_describe_ecs_service missing from filtered tools'
assert 'ace_describe_ecs_task'    in names, 'ace_describe_ecs_task missing from filtered tools'
assert 'ace_describe_ecr_image'   in names, 'ace_describe_ecr_image missing from filtered tools'
assert 'ace_verify_fix'  not in names, 'ace_verify_fix must be filtered out'
assert 'ace_score_run'   not in names, 'ace_score_run must be filtered out'
print('Check 1 PASS: ECS tools in model list; score tools filtered out')
"
```
Expected: `Check 1 PASS`.

**Check 1 results (fill after running):**
```
arch04_fault01: <PASS/FAIL>
arch04_fault02: <PASS/FAIL>
arch04_fault03: <PASS/FAIL>
arch04_fault04: <PASS/FAIL>
```

### Check 2 — Diagnostic-path reachability

Deploy each faulted stack and walk the `optimal_diagnostic_path` with the real MCP tools. Confirm the tool output surfaces the signal that pinpoints the fault.

- [ ] **Step 3: For each scenario, deploy + walk + confirm + tear down**

Use the same deploy/walk/teardown pattern from Task 4 Step 6. For each scenario, record whether the tool output contained the expected signal:

| Scenario | Expected signal | Tool output contained it? |
|---|---|---|
| fault01 (image_pull) | `stoppedReason` contains `CannotPullContainerError`; `ace_describe_ecr_image` returns error for the bad tag | `<PASS/FAIL>` |
| fault02 (iam) | task RUNNING but app returns 500; container `reason` or CloudWatch log shows `AccessDeniedException` | `<PASS/FAIL>` |
| fault03 (port) | `running_count=1` but ALB returns 502; events show health-check failure | `<PASS/FAIL>` |
| fault04 (configuration) | task RUNNING, `/health` 200, but DynamoDB calls return connection error in response body | `<PASS/FAIL>` |

For any FAIL: apply the remediation ladder — first improve the tool description, then sharpen `scenario.md`, then re-baseline `optimal_diagnostic_path`. Do not leak the faulted resource into the symptom description.

**Check 2 results (fill after running):**
```
arch04_fault01: <PASS/FAIL — note signal observed>
arch04_fault02: <PASS/FAIL — note signal observed>
arch04_fault03: <PASS/FAIL — note signal observed>
arch04_fault04: <PASS/FAIL — note signal observed>
```

### Check 3a — Static rubric pre-gate

Verify every tool description states: (a) the real AWS API it maps to, (b) the concrete fields/signals it returns, (c) when to reach for it (symptom / fault-class). This is a read-only check on the tool source.

- [ ] **Step 4: Audit tool descriptions against the rubric**

```bash
node -e "
import('./harness/mcp_server/tools/probe_ecs.js').then(m => {
  for (const t of m.probeEcsTools) {
    const d = t.description;
    const hasApi    = /DescribeServices|DescribeTasks|DescribeImages/.test(d);
    const hasFields = /desiredCount|runningCount|stoppedReason|imageDigest|exitCode/.test(d);
    const hasWhen   = /Reach for|when.*symptom|fault.class/i.test(d);
    console.log(t.name + ': api=' + hasApi + ' fields=' + hasFields + ' when=' + hasWhen + ((!hasApi||!hasFields||!hasWhen)?' FAIL':' pass'));
  }
});
"
```
Expected: all three tools print `pass`. If any print `FAIL`, update the description in `probe_ecs.js`, re-run the node tests to confirm no regression, and recommit.

**Check 3a results (fill after running):**
```
ace_describe_ecs_service: <pass/FAIL>
ace_describe_ecs_task:    <pass/FAIL>
ace_describe_ecr_image:   <pass/FAIL>
```

### Check 3b — LLM-judge blind selection

An LLM judge (a cheaper model distinct from the primary eval target — use `claude-haiku-4-5` or `gpt-4o-mini`) is given only the symptom + full tool list (no manifest) and asked which tools to call in order. Run N=5 trials per scenario. Pass = every tool on `optimal_diagnostic_path` named in the judge's first-K picks (K = path length + 1) in ≥3/5 trials.

- [ ] **Step 5: Run the blind-trigger judge for each scenario**

For each scenario, construct the prompt and run 5 trials via the LiteLLM API. A reusable script:

```python
# scratch/blind_trigger_judge.py
"""
Usage: python scratch/blind_trigger_judge.py <scenario_dir>
Prints pass/fail for each trial and a final verdict.
"""
import json, sys, os
from pathlib import Path
import litellm

JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "claude-haiku-4-5")  # cheaper, distinct from eval target
N_TRIALS    = 5

def load_tool_list():
    """Import the full tool list from the MCP server (excluding score tools)."""
    import subprocess, json as _json
    # node one-liner to dump all tool names+descriptions from index.js
    result = subprocess.run(
        ["node", "-e", """
import('./harness/mcp_server/index.js').catch(()=>{});
// Instead, read tool list from all tool files directly:
Promise.all([
  import('./harness/mcp_server/tools/probe.js'),
  import('./harness/mcp_server/tools/probe_extended.js'),
  import('./harness/mcp_server/tools/observe.js'),
  import('./harness/mcp_server/tools/observe_extended.js'),
  import('./harness/mcp_server/tools/observe_tracing.js'),
  import('./harness/mcp_server/tools/probe_rds.js'),
  import('./harness/mcp_server/tools/probe_ecs.js'),
]).then(([p,pe,o,oe,ot,pr,pecs]) => {
  const all = [...p.probeTools,...pe.probeExtendedTools,...o.observeTools,...oe.observeExtendedTools,...ot.observeTracingTools,...pr.probeRdsTools,...pecs.probeEcsTools];
  console.log(JSON.stringify(all.map(t=>({name:t.name,description:t.description,inputSchema:t.inputSchema}))));
});
"""],
        cwd=".", capture_output=True, text=True
    )
    return json.loads(result.stdout)

def judge_trial(symptom, tools):
    tool_list_text = "\n".join(
        f"- {t['name']}: {t['description'][:200]}" for t in tools
    )
    prompt = f"""You are an AWS cloud engineer debugging a production incident.

SYMPTOM:
{symptom}

AVAILABLE DIAGNOSTIC TOOLS (name: description):
{tool_list_text}

Which tools would you call, in order, to diagnose this symptom?
List only tool names, one per line, most important first.
Do not explain — just list tool names."""
    resp = litellm.completion(
        model=JUDGE_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=200,
    )
    return resp.choices[0].message.content.strip().splitlines()

def main():
    scenario_dir = Path(sys.argv[1])
    manifest = json.loads((scenario_dir / "fault_manifest.json").read_text())
    symptom  = (scenario_dir / "scenario.md").read_text()
    optimal  = manifest["optimal_diagnostic_path"]
    K        = len(optimal) + 1

    tools = load_tool_list()
    passes = 0
    for trial in range(1, N_TRIALS + 1):
        picks = judge_trial(symptom, tools)
        top_k = [p.strip() for p in picks[:K] if p.strip()]
        hit   = all(t in top_k for t in optimal)
        status = "PASS" if hit else "FAIL"
        if hit:
            passes += 1
        print(f"  Trial {trial}: {status} | judge picks={top_k} | optimal={optimal}")

    verdict = "PASS" if passes >= 3 else "FAIL"
    print(f"\nScenario {scenario_dir.name}: {verdict} ({passes}/{N_TRIALS} trials passed)")

if __name__ == "__main__":
    main()
```

Run for each scenario:
```bash
JUDGE_MODEL=claude-haiku-4-5 python scratch/blind_trigger_judge.py scenarios/arch04_fault01_image_pull
JUDGE_MODEL=claude-haiku-4-5 python scratch/blind_trigger_judge.py scenarios/arch04_fault02_iam
JUDGE_MODEL=claude-haiku-4-5 python scratch/blind_trigger_judge.py scenarios/arch04_fault03_port
JUDGE_MODEL=claude-haiku-4-5 python scratch/blind_trigger_judge.py scenarios/arch04_fault04_configuration
```

**Pass bar:** ≥3/5 trials where every tool on `optimal_diagnostic_path` appears in the judge's first-K picks.

**Remediation ladder on FAIL (cheapest first):**
1. Improve the tool description (add the specific signal + fault-class it serves). Re-run 3a rubric check first.
2. Sharpen `scenario.md` symptom to be more faithful to the observable error (never name the cause).
3. Re-baseline `optimal_diagnostic_path` to a route the judge naturally takes.
4. Last resort: redesign or drop the fault.
Hard guardrail: never leak the faulted resource/property into `scenario.md` or pad descriptions with hints.

**Check 3b results (fill after running):**
```
arch04_fault01: <PASS/FAIL — N/5 trials>
arch04_fault02: <PASS/FAIL — N/5 trials>
arch04_fault03: <PASS/FAIL — N/5 trials>
arch04_fault04: <PASS/FAIL — N/5 trials>
```

### Check 4 — Trace + scoring integration

Verify that scenarios integrate cleanly with the harness's verify loop and scoring pipeline.

- [ ] **Step 6: Confirm `verify_result.json` and `score.json` are produced for a dry-run**

Deploy a faulted scenario (e.g. fault01), run the harness in a dry-run mode to confirm the verify pipeline recognizes the scenario:
```bash
python harness/run.py scenarios/arch04_fault01_image_pull/ --model anthropic/claude-haiku-4-5 2>&1 | head -40
```
Expected: harness starts, builds context, no crash on scenario load. (Full scoring requires HARNESS_API_KEY; this check only confirms the harness does not error on arch04 scenario structure.)

Tear down after:
```bash
aws --endpoint-url=http://localhost:4566 cloudformation delete-stack --stack-name ace-bench-stack --region us-east-1
```

**Check 4 results (fill after running):**
```
Harness loads arch04 scenario without error: <PASS/FAIL>
```

- [ ] **Step 7: Commit QA gate results**

```bash
git add docs/superpowers/plans/2026-06-20-breadth-containers-ecs.md
git commit -m "docs(plan): record arch04 discoverability QA gate results (Task 5)"
```

---

## Task 6: Documentation

Bring tool counts and architecture inventory in sync across the project guides.

**Files:**
- Modify: `CLAUDE.md` (tool counts; Project Layout corpus/scenarios entries)
- Modify: `README.md` (Phase B tool inventory; repository layout)
- Modify: `RUN.md` (tool inventory; model-access count)

**Interfaces:**
- Consumes: the final tool list from Task 2 (3 new ECS/ECR tools) and the arch04 corpus/scenarios from Tasks 3–4.
- Produces: consistent counts (diagnostic tools 61 → 64) and documented arch04 corpus.

- [ ] **Step 1: Update `CLAUDE.md`**

Find the current MCP server description line (currently "61 diagnostic + 2 score tools across 28 LocalStack services"). Change the count to reflect the 3 new ECS tools:
```bash
grep -n "diagnostic.*score.*tools" CLAUDE.md
```
Update the count (61 → 64; services count up by 2 for ECS + ECR if not already listed). Add the `harness/mcp_server/tools/probe_ecs.js` entry to the `tools/` listing with the annotation `# 3 ECS/ECR tools: ace_describe_ecs_service, ace_describe_ecs_task, ace_describe_ecr_image`. Add `corpus/arch_04_containers_ecs_fargate/` and the four `scenarios/arch04_fault0N_*` entries to the Project Layout section.

- [ ] **Step 2: Update `README.md` and `RUN.md`**

Bump the diagnostic tool count by 3 and the model-access count by 3 in both files. Add the three ECS tools to any tool tables. Add arch04 to any architecture/corpus inventory.

- [ ] **Step 3: Verify counts are consistent**

```bash
grep -rEn "[0-9]+" CLAUDE.md README.md RUN.md | grep -iE "tool|diagnostic|model.access" | head -20

# Confirm the actual registered tool count matches docs:
node -e "
Promise.all([
  import('./harness/mcp_server/tools/probe.js'),
  import('./harness/mcp_server/tools/probe_extended.js'),
  import('./harness/mcp_server/tools/observe.js'),
  import('./harness/mcp_server/tools/observe_extended.js'),
  import('./harness/mcp_server/tools/observe_tracing.js'),
  import('./harness/mcp_server/tools/probe_rds.js'),
  import('./harness/mcp_server/tools/probe_ecs.js'),
  import('./harness/mcp_server/tools/score.js'),
]).then(([p,pe,o,oe,ot,pr,pecs,sc]) => {
  const diagnostic = [...p.probeTools,...pe.probeExtendedTools,...o.observeTools,...oe.observeExtendedTools,...ot.observeTracingTools,...pr.probeRdsTools,...pecs.probeEcsTools];
  const score = [...sc.scoreTools];
  console.log('diagnostic:', diagnostic.length, 'score:', score.length, 'total:', diagnostic.length + score.length);
});
"
```
Expected: `diagnostic: 64  score: 2  total: 66`. Adjust docs if the number differs.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md README.md RUN.md
git commit -m "docs: update tool counts and project layout for arch04 ECS/ECR (64 diagnostic + 2 score tools)"
```

---

## Task 1 findings (to be completed by executor)

> This section is filled by the executor after running Task 1. It gates all downstream tasks.

```
LocalStack version: <fill>
ECS Fargate scheduling: <VIABLE / SHELVED>
Reason if shelved: <fill or N/A>
Locked tools: <list>
Locked faults: <list with primary/fallback decisions>
X-Ray instrumentation: <yes/no>
```
