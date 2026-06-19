# Phase 2B-1 — RDS PostgreSQL Architecture (arch02)

**Date:** 2026-06-19
**Status:** Approved design — ready for writing-plans
**Predecessor:** `2026-06-14-next-phase-features-roadmap.md` (Phase 2B breadth track)
**Companion (depth track, complete):** `2026-06-17-phase-2a-xray-trace-tools-design.md`

---

## Purpose

Phase 2A closed the depth track for arch01 (CloudTrail + X-Ray trace tools + full
instrumentation). This spec opens the **breadth track**: the first
Ultimate-only architecture family beyond the original DynamoDB serverless app.

The chosen family is **RDS / Aurora**, selected for the highest LocalStack
Ultimate emulation fidelity (RDS is backed by real PostgreSQL/MySQL) and the
richest set of realistic, real-AWS-transferable fault classes that arch01 cannot
express: VPC/security-group connectivity, Secrets Manager credentials, DB
parameter groups, and KMS key-policy / encryption scope.

This is **Phase 2B-1** — the first of several breadth families, each its own
spec → plan → corpus build. It is scoped as a single implementation plan.

---

## Hard rules carried forward (from Phase 1 / roadmap)

- **Real-AWS-transferable bar.** Prefer real AWS APIs; avoid LocalStack-proprietary
  introspection (App Inspector, IAM Policy Streams) so diagnostic skill transfers
  to real AWS.
- **IAM enforcement.** LocalStack runs with `ENFORCE_IAM=1 IAM_SOFT_MODE=0`. Any
  fixture/scenario that creates Lambdas must define a real assumable role.
- **`fault_manifest.json` and `known_good.yaml` are never exposed to the model.**
- **Submission is final.** Existing runner invariants are unchanged by this phase.
- **Spike-gate before corpus build.** Mirrors the X-Ray emission gate: do not fan
  out the corpus until the de-risking spike (Task 1) passes.

---

## Architecture (arch02)

**Shape:** `API Gateway → Lambda (in VPC) → RDS PostgreSQL`, with **Secrets
Manager** holding DB credentials and a **VPC + private subnets + security group**
governing connectivity. A small domain CRUD service (e.g., notes/orders) backed
by a single Postgres table, reusing arch01's serverless scaffolding with
DynamoDB swapped for RDS. **arch01 is untouched.**

**Corpus deliverables** under `corpus/arch_02_<name>/`:
- `known_good.yaml` — the correct CloudFormation template (never exposed to model)
- `functional_test.py` — behavioral assertions for the working app
- `traffic_flow.md` — request/data-flow narrative

**Engine/topology:** a single `db.t3`-class **PostgreSQL** instance (not Aurora
cluster, not MySQL) — most common real-world pattern, best emulation fidelity,
simplest to provision.

**New infra elements vs arch01:**
- `AWS::RDS::DBInstance` (PostgreSQL, single instance)
- `AWS::RDS::DBSubnetGroup`
- `AWS::RDS::DBParameterGroup`
- `AWS::EC2::VPC`, `AWS::EC2::Subnet` (private), `AWS::EC2::SecurityGroup`
- Lambda `VpcConfig` (attaches handlers to the VPC/subnets/SG)
- `AWS::SecretsManager::Secret` (DB master credentials)
- `AWS::KMS::Key` (customer-managed CMK encrypting the credentials secret; the
  key policy / grants are the subject of the security fault)

**Handler dependency:** handlers vendor `psycopg2` (the same vendoring pattern
arch01 uses for `aws_xray_sdk`) and read credentials from Secrets Manager at
runtime. Whether to also carry forward arch01's X-Ray instrumentation into
arch02 handlers is **decided by the Task 1 spike** (see below): the spike probes
whether psycopg2 SQL subsegments capture cleanly on LocalStack. If they do and
the value justifies the vendoring cost, instrument; otherwise defer arch02
tracing to a later depth pass. Setting `TracingConfig: Active` without SDK
instrumentation is pointless — LocalStack does not auto-instrument Lambda
(arch01 spike finding), so it is all-or-nothing.

---

## Fault scenario set (initial)

Four scenarios — one per prioritized fault class — to prove the family
end-to-end. Each ships `scenario.md`, `faulted.yaml`, `fault_manifest.json`
(never exposed), and `deployment/` handler code, following the arch01 scenario
layout. Each manifest's `optimal_tool_calls` / `optimal_files_changed` /
`optimal_lines_changed` are baselined against the actual diagnosis path.

| ID | Class | Injected fault | Optimal diagnosis path |
|----|-------|----------------|------------------------|
| `arch02_fault01_connectivity` | connectivity | Security group missing ingress on 5432 (primary); Lambda detached from VPC (fallback if SG not enforced) | `ace_describe_security_group` + `ace_check_db_connectivity` |
| `arch02_fault02_security` | security | DB-credentials secret is encrypted with a customer-managed KMS CMK, but the Lambda execution role / key policy lacks `kms:Decrypt` on that key (the secret ARN is correct and `GetSecretValue` is allowed — only Decrypt is the gap) | logs (KMS `AccessDeniedException`) + `ace_describe_kms_key` + `ace_simulate_policy` |
| `arch02_fault03_credentials` | credentials | Wrong secret ARN wired to Lambda, or missing `secretsmanager:GetSecretValue` IAM permission | `ace_describe_secret` / `ace_get_secret` + `ace_simulate_policy` + logs |
| `arch02_fault04_performance` | performance | Parameter-group `max_connections` too low → connection exhaustion (primary); undersized instance class + CloudWatch signal (fallback) | `ace_describe_db_parameters` + `ace_get_metric_statistics` |

Scope is deliberately **4 scenarios** for the first build; expansion (more faults
per class, async hops) is deferred to later 2B work.

**Fault design principle (mandatory, mirrors arch01).** Every fault must produce
an **observable behavioral symptom** that Pass-1 functional verification detects;
`scenario.md` states only that *symptom*, never the cause. A pure
posture/best-practice violation that does not break behavior (e.g.
`PubliclyAccessible: true`, an over-broad `0.0.0.0/0` ingress) is **not** an
acceptable fault here — it leaves the agent with nothing to diagnose unless the
answer is revealed, and Pass-1 passes before and after the "fix". The security
fault therefore manifests as broken behavior (a KMS `Decrypt` failure), with its
security character carried by the **root cause** and by `invalid_patches` that
forbid the insecure shortcut — exactly the `arch01_fault06_security` pattern
(valid fix = least-privilege scope; invalid = disable encryption, `kms:*` on `*`,
or a wildcard key-policy principal).

---

## New MCP tools

Most of the diagnostic surface already exists and is reused as-is:
`ace_describe_security_group`, `ace_describe_secret` / `ace_get_secret`,
`ace_describe_kms_key` (key policy/grants for the security fault),
`ace_get_iam_role` / `ace_simulate_policy`, and the CloudWatch tools
(`ace_get_metric_statistics`, `ace_get_lambda_metrics`). The security fault
needs **no new tool** — `ace_describe_kms_key` already exists.

Net-new tools, added in a new `harness/mcp_server/tools/probe_rds.js` and spread
into `index.js` alongside the existing tool arrays:

1. **`ace_describe_db_instance`** → `DescribeDBInstances`. Returns endpoint,
   port, status, engine/version, instance class, `PubliclyAccessible`,
   `StorageEncrypted`, attached VPC security group IDs, DB subnet group, DB
   parameter group name(s), MultiAZ, master username.
2. **`ace_describe_db_parameters`** → `DescribeDBParameters` for a named
   parameter group (supports the performance fault; filter/return key params
   such as `max_connections`).
3. **`ace_check_db_connectivity`** → opens a TCP socket to the DB
   endpoint:port and reports `reachable` / `refused` / `timeout` plus latency.
   Confirms the connectivity-class fault from the diagnostician's side.

**Tool count:** 58 → 61 diagnostic tools (model-accessible count moves
accordingly). Node tests for all three added to `tests/test_mcp_server.js`,
including a round-trip against a seeded RDS instance where feasible.

---

## De-risking spike (mandatory Task 1 — the gate)

Before any corpus fan-out, a spike validates on the **current** LocalStack
Ultimate build that the architecture and its fault premises actually hold. Do
not proceed to corpus build until this passes. Findings recorded in this spec's
companion plan (mirroring the X-Ray spike's documented findings).

The spike must confirm:
1. **Provisioning.** An RDS PostgreSQL instance provisions via CloudFormation and
   reaches `available`; `DescribeDBInstances` returns every attribute the tools
   and faults depend on.
2. **Connectivity enforcement (key risk).** A VPC Lambda connects to the DB via
   `psycopg2` in the known-good config, **and a security-group-blocked config
   actually fails to connect.** If LocalStack does not enforce SG/VPC
   reachability for RDS, the connectivity fault is not reproducible as specified.
3. **Parameter enforcement (key risk).** `DescribeDBParameters` works **and a
   lowered `max_connections` is actually enforced** (connections beyond the limit
   are refused). This is the performance fault's premise.
4. **KMS Decrypt enforcement (key risk).** A secret encrypted with a customer
   CMK is retrievable when the role has `kms:Decrypt`, **and retrieval fails with
   an `AccessDeniedException` when the role/key policy lacks it** (under
   `ENFORCE_IAM=1`). This is the security fault's premise.
5. **X-Ray/psycopg2 capture (informational, not a gate).** With `aws_xray_sdk`
   `dbapi2` patching enabled on a handler, probe whether a SQL call produces a
   nested subsegment via `ace_get_trace`. The result **decides** whether arch02
   handlers are X-Ray-instrumented in this phase (instrument if capture is clean
   and worth the vendoring cost; otherwise defer to a later depth pass). This
   check does not block the corpus build — it only sets the instrumentation
   decision.

**Explicit fallbacks (carried, not improvised):**
- If SG/VPC reachability is not enforced → `arch02_fault01` uses a mechanism
  LocalStack does enforce: wrong DB endpoint/port wired to the handler, or Lambda
  `VpcConfig` removed/misconfigured so the handler cannot resolve/route to the DB.
- If `max_connections` is not enforced → `arch02_fault04` uses an
  instance-class/CloudWatch-observable performance mechanism instead, keeping the
  performance class represented.

The credentials fault (03) depends only on IAM/Secrets behavior already
validated on this build, so it carries low spike risk. The security fault (02)
adds the KMS-Decrypt-enforcement check above; if LocalStack does not enforce
`kms:Decrypt` under IAM enforcement, the fallback is to relocate the missing
permission to `secretsmanager:GetSecretValue` on the role while keeping the
secret-encryption framing (the symptom and diagnosis path are equivalent), or to
fold the security class into the credentials scenario and drop to 3 scenarios.

---

## Testing & sequencing

1. **Task 1 — Spike gate.** Validate provisioning + the three enforcement risks
   (SG/VPC, `max_connections`, KMS `Decrypt`) and run the informational
   psycopg2/X-Ray capture probe; record findings; lock each fault mechanism
   (primary or fallback) and the arch02 X-Ray instrumentation decision.
2. **Task 2 — MCP tools.** Implement `probe_rds.js` (3 tools), wire into
   `index.js`, add Node tests; confirm agent exposure.
3. **Task 3 — Corpus known-good.** Build `corpus/arch_02_*/known_good.yaml`,
   `functional_test.py`, `traffic_flow.md`; deploy clean under IAM enforcement;
   vendor `psycopg2`; confirm functional tests pass.
4. **Task 4 — Fault scenarios.** Create the 4 scenarios + `fault_manifest.json`
   each; verify each reproduces its fault and is diagnosable via the intended
   path; baseline `optimal_*` counts.
5. **Task 5 — Documentation.** Bump tool counts (58→61) and document arch02
   across `CLAUDE.md`, `README.md`, `RUN.md`; verify counts consistent.

**Workflow:** this spec → `writing-plans` → `subagent-driven-development`
(workers `claude-sonnet-4-6`, overseer `claude-opus-4-8`, per `CLAUDE.md`).

---

## Open questions deferred to the plan (not blocking)

- Exact domain/table schema for the CRUD app (cosmetic; pick the simplest that
  exercises a real query path).
- Whether arch02 handlers also carry arch01's X-Ray instrumentation — **resolved
  by the Task 1 spike's psycopg2/SQL-capture probe** (instrument if clean and
  worthwhile, else defer to a later depth pass).
- Precise `optimal_*` baselines (measured during Task 4, not guessed here).
