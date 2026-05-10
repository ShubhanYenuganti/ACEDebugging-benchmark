# Fault Scenario Proposals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run `/design-fault-scenarios` across all 4 corpus architectures, producing a `fault_scenarios_proposal.md` in each `corpus/<arch_dir>/` directory for human review.

**Architecture:** Each architecture is analyzed independently — its `traffic_flow.md`, `functional_test.py`, `known_good.yaml`, and Lambda handlers are read in full, then up to 10 fault scenarios are proposed and written to a proposal document. All 4 tasks are fully independent and can execute in parallel via subagents.

**Tech Stack:** Skill: `design-fault-scenarios` (reads architecture files + MCP tool catalog, writes proposal markdown). No code changes — output is documentation only.

---

## File Structure

Each task produces exactly one output file and touches no existing files:

| Task | Output file |
|------|-------------|
| Task 1 | `corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/fault_scenarios_proposal.md` |
| Task 2 | `corpus/arch_02_fuzzy_movie_search/fault_scenarios_proposal.md` |
| Task 3 | `corpus/arch_08_event_driven_architecture_with_sns_fifo_dynamodb_lambda_and_s3/fault_scenarios_proposal.md` |
| Task 4 | `corpus/arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3/fault_scenarios_proposal.md` |
| Task 5 | No files — reads task outputs only and reports |

---

### Task 1: Design fault scenarios for arch_01 (serverless microservices)

**Files:**
- Read: `corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/traffic_flow.md`
- Read: `corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/functional_test.py`
- Read: `corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/known_good.yaml`
- Read: all files under `corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/deployment/lambda/`
- Read: `harness/mcp_server/tools/probe.js`, `probe_extended.js`, `observe.js`, `observe_extended.js`
- Create: `corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/fault_scenarios_proposal.md`

- [ ] **Step 1: Invoke the design-fault-scenarios skill for arch_01**

  Run the skill by invoking it as a slash command:
  ```
  /design-fault-scenarios arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda
  ```
  The skill reads all architecture inputs and the MCP tool catalog, then writes the proposal document. Do not begin writing until all inputs have been read.

- [ ] **Step 2: Verify the proposal file was written**

  ```bash
  ls -lh corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/fault_scenarios_proposal.md
  ```
  Expected: file exists, size > 0.

- [ ] **Step 3: Verify proposal structure**

  ```bash
  grep -c "^### FAULT-" corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/fault_scenarios_proposal.md
  ```
  Expected: between 5 and 10 (at least 5 scenarios proposed).

  ```bash
  grep "Fails assertions:" corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/fault_scenarios_proposal.md
  ```
  Expected: one `Fails assertions:` line per scenario.

  ```bash
  grep -E "\*\*Type:\*\* coupled|\*\*Type:\*\* chained" corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/fault_scenarios_proposal.md | wc -l
  ```
  Expected: at least 1 coupled or chained scenario present.

  ```bash
  grep -c "template-only fix is insufficient" corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/fault_scenarios_proposal.md
  ```
  Expected: at least 1 (at least one scenario requires both template and handler changes).

- [ ] **Step 4: Report task 1 result**

  Print: "Task 1 complete — arch_01 proposal written with N scenarios."

---

### Task 2: Design fault scenarios for arch_02 (fuzzy movie search)

**Files:**
- Read: `corpus/arch_02_fuzzy_movie_search/traffic_flow.md`
- Read: `corpus/arch_02_fuzzy_movie_search/functional_test.py`
- Read: `corpus/arch_02_fuzzy_movie_search/known_good.yaml`
- Read: all files under `corpus/arch_02_fuzzy_movie_search/deployment/lambda/`
- Read: `harness/mcp_server/tools/probe.js`, `probe_extended.js`, `observe.js`, `observe_extended.js`
- Create: `corpus/arch_02_fuzzy_movie_search/fault_scenarios_proposal.md`

- [ ] **Step 1: Invoke the design-fault-scenarios skill for arch_02**

  Run the skill by invoking it as a slash command:
  ```
  /design-fault-scenarios arch_02_fuzzy_movie_search
  ```
  The skill reads all architecture inputs and the MCP tool catalog, then writes the proposal document. Do not begin writing until all inputs have been read.

- [ ] **Step 2: Verify the proposal file was written**

  ```bash
  ls -lh corpus/arch_02_fuzzy_movie_search/fault_scenarios_proposal.md
  ```
  Expected: file exists, size > 0.

- [ ] **Step 3: Verify proposal structure**

  ```bash
  grep -c "^### FAULT-" corpus/arch_02_fuzzy_movie_search/fault_scenarios_proposal.md
  ```
  Expected: between 5 and 10.

  ```bash
  grep "Fails assertions:" corpus/arch_02_fuzzy_movie_search/fault_scenarios_proposal.md
  ```
  Expected: one `Fails assertions:` line per scenario.

  ```bash
  grep -E "\*\*Type:\*\* coupled|\*\*Type:\*\* chained" corpus/arch_02_fuzzy_movie_search/fault_scenarios_proposal.md | wc -l
  ```
  Expected: at least 1 coupled or chained scenario present.

  ```bash
  grep -c "template-only fix is insufficient" corpus/arch_02_fuzzy_movie_search/fault_scenarios_proposal.md
  ```
  Expected: at least 1.

- [ ] **Step 4: Report task 2 result**

  Print: "Task 2 complete — arch_02 proposal written with N scenarios."

---

### Task 3: Design fault scenarios for arch_08 (event-driven SNS FIFO)

**Files:**
- Read: `corpus/arch_08_event_driven_architecture_with_sns_fifo_dynamodb_lambda_and_s3/traffic_flow.md`
- Read: `corpus/arch_08_event_driven_architecture_with_sns_fifo_dynamodb_lambda_and_s3/functional_test.py`
- Read: `corpus/arch_08_event_driven_architecture_with_sns_fifo_dynamodb_lambda_and_s3/known_good.yaml`
- Read: all files under `corpus/arch_08_event_driven_architecture_with_sns_fifo_dynamodb_lambda_and_s3/deployment/lambda/`
- Read: `harness/mcp_server/tools/probe.js`, `probe_extended.js`, `observe.js`, `observe_extended.js`
- Create: `corpus/arch_08_event_driven_architecture_with_sns_fifo_dynamodb_lambda_and_s3/fault_scenarios_proposal.md`

- [ ] **Step 1: Invoke the design-fault-scenarios skill for arch_08**

  Run the skill by invoking it as a slash command:
  ```
  /design-fault-scenarios arch_08_event_driven_architecture_with_sns_fifo_dynamodb_lambda_and_s3
  ```
  The skill reads all architecture inputs and the MCP tool catalog, then writes the proposal document. Do not begin writing until all inputs have been read.

- [ ] **Step 2: Verify the proposal file was written**

  ```bash
  ls -lh corpus/arch_08_event_driven_architecture_with_sns_fifo_dynamodb_lambda_and_s3/fault_scenarios_proposal.md
  ```
  Expected: file exists, size > 0.

- [ ] **Step 3: Verify proposal structure**

  ```bash
  grep -c "^### FAULT-" corpus/arch_08_event_driven_architecture_with_sns_fifo_dynamodb_lambda_and_s3/fault_scenarios_proposal.md
  ```
  Expected: between 5 and 10.

  ```bash
  grep "Fails assertions:" corpus/arch_08_event_driven_architecture_with_sns_fifo_dynamodb_lambda_and_s3/fault_scenarios_proposal.md
  ```
  Expected: one `Fails assertions:` line per scenario.

  ```bash
  grep -E "\*\*Type:\*\* coupled|\*\*Type:\*\* chained" corpus/arch_08_event_driven_architecture_with_sns_fifo_dynamodb_lambda_and_s3/fault_scenarios_proposal.md | wc -l
  ```
  Expected: at least 1 coupled or chained scenario present.

  ```bash
  grep -c "template-only fix is insufficient" corpus/arch_08_event_driven_architecture_with_sns_fifo_dynamodb_lambda_and_s3/fault_scenarios_proposal.md
  ```
  Expected: at least 1.

- [ ] **Step 4: Report task 3 result**

  Print: "Task 3 complete — arch_08 proposal written with N scenarios."

---

### Task 4: Design fault scenarios for arch_12 (SQS/Lambda/DynamoDB/S3)

**Files:**
- Read: `corpus/arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3/traffic_flow.md`
- Read: `corpus/arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3/functional_test.py`
- Read: `corpus/arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3/known_good.yaml`
- Read: all files under `corpus/arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3/deployment/lambda/`
- Read: `harness/mcp_server/tools/probe.js`, `probe_extended.js`, `observe.js`, `observe_extended.js`
- Create: `corpus/arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3/fault_scenarios_proposal.md`

- [ ] **Step 1: Invoke the design-fault-scenarios skill for arch_12**

  Run the skill by invoking it as a slash command:
  ```
  /design-fault-scenarios arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3
  ```
  The skill reads all architecture inputs and the MCP tool catalog, then writes the proposal document. Do not begin writing until all inputs have been read.

- [ ] **Step 2: Verify the proposal file was written**

  ```bash
  ls -lh corpus/arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3/fault_scenarios_proposal.md
  ```
  Expected: file exists, size > 0.

- [ ] **Step 3: Verify proposal structure**

  ```bash
  grep -c "^### FAULT-" corpus/arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3/fault_scenarios_proposal.md
  ```
  Expected: between 5 and 10.

  ```bash
  grep "Fails assertions:" corpus/arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3/fault_scenarios_proposal.md
  ```
  Expected: one `Fails assertions:` line per scenario.

  ```bash
  grep -E "\*\*Type:\*\* coupled|\*\*Type:\*\* chained" corpus/arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3/fault_scenarios_proposal.md | wc -l
  ```
  Expected: at least 1 coupled or chained scenario present.

  ```bash
  grep -c "template-only fix is insufficient" corpus/arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3/fault_scenarios_proposal.md
  ```
  Expected: at least 1.

- [ ] **Step 4: Report task 4 result**

  Print: "Task 4 complete — arch_12 proposal written with N scenarios."

---

### Task 5: Collect results and report

**Files:**
- Read: all 4 `fault_scenarios_proposal.md` files (check existence only, do not reprint them)

> **Note:** This task must run after Tasks 1–4 complete. It is the only task with a dependency.

- [ ] **Step 1: Check all 4 proposal files exist**

  ```bash
  for arch in \
    arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda \
    arch_02_fuzzy_movie_search \
    arch_08_event_driven_architecture_with_sns_fifo_dynamodb_lambda_and_s3 \
    arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3; do
    f="corpus/$arch/fault_scenarios_proposal.md"
    if [ -f "$f" ]; then
      count=$(grep -c "^### FAULT-" "$f")
      echo "PRESENT $arch — $count scenarios"
    else
      echo "MISSING $arch"
    fi
  done
  ```

- [ ] **Step 2: Count scenario breakdown by fault class across all proposals**

  ```bash
  grep "\*\*Class:\*\*" corpus/arch_*/fault_scenarios_proposal.md | \
    sed 's/.*\*\*Class:\*\* //' | sort | uniq -c | sort -rn
  ```
  Expected output: counts per class (security, connectivity, performance, reliability, data_correctness).

- [ ] **Step 3: Count coupled and chained scenarios across all proposals**

  ```bash
  grep "\*\*Type:\*\*" corpus/arch_*/fault_scenarios_proposal.md | \
    sed 's/.*\*\*Type:\*\* //' | sort | uniq -c
  ```

- [ ] **Step 4: Count scenarios requiring simultaneous template + handler changes**

  ```bash
  grep -l "template-only fix is insufficient" corpus/arch_*/fault_scenarios_proposal.md | wc -l
  ```

- [ ] **Step 5: Report final summary to user**

  Print a summary in this format:
  ```
  Fault Scenario Proposals — Complete

  arch_01: N scenarios (present/MISSING)
  arch_02: N scenarios (present/MISSING)
  arch_08: N scenarios (present/MISSING)
  arch_12: N scenarios (present/MISSING)

  Total scenarios: N
  Class breakdown: security=N, connectivity=N, performance=N, reliability=N, data_correctness=N
  Coupled/chained: N
  Require template+handler changes: N

  All proposals ready for human review.
  [or: The following proposals are missing: <list>]
  ```

---

## Parallelism Note

Tasks 1, 2, 3, and 4 are fully independent — they read disjoint sets of files and write to different output paths. When executing via `superpowers:subagent-driven-development`, dispatch all four as parallel subagents. Task 5 must wait for Tasks 1–4 to complete before running.
