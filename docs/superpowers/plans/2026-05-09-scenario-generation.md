# Scenario Generation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Read every `fault_scenario_proposal.md` found under `corpus/arch_*/` and write a fully populated `scenarios/[scenario_id]/` directory for each proposed fault. Idempotent: skip any scenario directory that already exists unless `--force` was passed.

**Architecture:** 4 architecture tasks are independent and can run in parallel. Each produces N scenario directories. A final summary task runs after all architecture tasks complete.

**Tech Stack:** Read tool (proposal, known_good.yaml, functional_test.py, Lambda handlers) → Write tool (all output files). No external commands needed. No code execution.

---

## Naming Conventions

| Corpus arch dir short name | Prefix |
|---|---|
| arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda | arch01 |
| arch_02_fuzzy_movie_search | arch02 |
| arch_08_event_driven_architecture_with_sns_fifo_dynamodb_lambda_and_s3 | arch08 |
| arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3 | arch12 |

**Scenario ID:** `{arch_prefix}_fault{NN:02d}_{fault_class}` — e.g., `arch01_fault01_connectivity`, `arch01_fault06_security`.

**fault_class values:** `security` | `connectivity` | `performance` | `reliability` | `data_correctness`

---

## Output structure per scenario

```
scenarios/[scenario_id]/
├── scenario.md
├── faulted.yaml
├── faulted_annotated.yaml
├── fault_manifest.json
└── deployment/
    └── lambda/
        ├── [handler_name].py           # clean — given to model
        ├── [handler_name]_annotated.py # only if this handler was modified
        └── [every other handler].py    # copied verbatim from corpus
```

Every Lambda handler from `corpus/[arch_dir]/deployment/lambda/` must appear in the scenario's `deployment/lambda/`, regardless of whether the fault modifies it. Unmodified handlers are copied verbatim. Only modified handlers get an `_annotated.py` counterpart.

---

## File authoring rules

### scenario.md

- Written entirely in plain language with **no AWS service names, no CloudFormation resource names, no IAM/SQS/DynamoDB/Lambda/SNS/S3/API Gateway terminology**.
- Describes the **business purpose** and **intended behavior** of the system.
- Describes the **observable broken behavior** using behavior-level language strong enough for a skilled engineer with full diagnostic access to identify the root cause — but without naming the root cause, the resource, or the service.
- Includes three fixed sections: `## System overview`, `## What you have access to`, `## Reported symptom`, `## What correct behavior looks like`.
- `## What you have access to` always reads: "A deployed instance of this system is running in your local environment. The CloudFormation template (`faulted.yaml`) and all supporting deployment files are available to you directly. Diagnostic tools are available via MCP to probe the running system. The system deployed successfully."
- **Never** name the fault class, the scenario ID, or any internal benchmark metadata.

### faulted.yaml

- Start from `known_good.yaml` exactly. Apply **only** the property changes described in the fault proposal's `Misconfiguration` section. Every other line is byte-for-byte identical to `known_good.yaml`.
- No comments, no annotations, no markers. Indistinguishable from a normally authored template.
- YAML formatting, indentation, and key ordering must match `known_good.yaml`.

### faulted_annotated.yaml

- Identical content to `faulted.yaml` — same fault values — but with an inline `# FAULT INJECTION` comment appended to **every line that differs from known_good.yaml**.
- Never exposed to the model. For harness and human reviewers only.

### fault_manifest.json

Full schema (all fields required):

```json
{
  "fault_id": "arch01_fault01",
  "fault_class": "connectivity",
  "architecture": "arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda",
  "scenario_id": "arch01_fault01_connectivity",
  "target_resource": "<CloudFormation logical resource ID where primary fault lives>",
  "target_property": "<dot-path to faulted property, e.g. Properties.FilterCriteria.Filters[0].Pattern>",
  "injected_value": "<broken value as it appears in faulted.yaml — use exact type: string, array, int, null>",
  "original_value": "<correct value from known_good.yaml>",
  "valid_fixes": ["<description of acceptable fix approach 1>", "..."],
  "invalid_patches": ["<over-permissive or wrong-but-plausible fix 1>", "..."],
  "optimal_tool_calls": 3,
  "optimal_files_changed": 1,
  "optimal_lines_changed": 2,
  "optimal_diagnostic_path": [
    "tool_name(arg=value) → what this reveals",
    "..."
  ],
  "concurrency_probe_n": null,
  "deployment_check": "CREATE_COMPLETE",
  "observability_check": "<which assertion in functional_test.py fails, e.g. functional_test assertion 'accept_terminal_state' fails>",
  "observable_symptom": "<plain English description of what the model observes when probing the running system>",
  "root_cause": "<ground-truth explanation of exactly why the fault combination produces that symptom>",
  "corpus_path": "corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda",
  "functional_test_path": "corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/functional_test.py",
  "known_good_path": "corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/known_good.yaml"
}
```

- `concurrency_probe_n`: integer N for performance fault_class; `null` otherwise.
- `deployment_check`: almost always `"CREATE_COMPLETE"` — the faulted template must deploy successfully for the scenario to be valid.
- `observability_check`: reference the specific assertion name(s) from `functional_test.py` that fail.
- `optimal_tool_calls`: minimum MCP diagnostic calls needed to identify root cause (exclude file reads).
- `optimal_files_changed`: minimum files a correct fix touches.
- `optimal_lines_changed`: minimum lines a correct fix changes across all touched files.

### Lambda handler files

**`[handler_name].py`** (clean):
- If the fault modifies this handler: apply exactly the changes described in the `Misconfiguration` section. No comments, no markers.
- If the fault does not modify this handler: copy verbatim from `corpus/[arch_dir]/deployment/lambda/[handler_name].py`.

**`[handler_name]_annotated.py`** (annotated, only for modified handlers):
- Same content as the clean version — same fault values — but with `# FAULT INJECTION` appended inline on every line that differs from the corpus original.
- Never exposed to the model.
- Unmodified handlers have no annotated counterpart.

---

## Idempotency behavior

Before writing any file in a scenario directory, check whether `scenarios/[scenario_id]/` already exists:
- If it exists and `--force` was NOT passed: **skip the entire scenario** and note it in the summary.
- If it exists and `--force` WAS passed: overwrite all files in that directory.
- If it does not exist: create it and write all files.

---

## Task 1: Generate scenarios for arch_01 (serverless microservices)

**Inputs to read (once, before writing any scenario):**
- `corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/fault_scenarios_proposal.md`
- `corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/known_good.yaml`
- `corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/functional_test.py`
- All handlers under `corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/deployment/lambda/`

**Fault list from proposal (extract from `### FAULT-NN` headings):**
Extract all `### FAULT-NN` sections from the proposal. For each, derive:
- `fault_number`: the two-digit integer (01–10)
- `fault_class`: from the `**Class:**` field
- `scenario_id`: `arch01_fault{NN:02d}_{fault_class}`
- `scenario_dir`: `scenarios/arch01_fault{NN:02d}_{fault_class}/`

- [ ] **Step 1: Read all arch_01 inputs**

  Read all 4 input categories listed above. Do not begin writing until all inputs are in context.

  For the Lambda handlers, note which handler subdirectories exist under `corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/deployment/lambda/` — each subdirectory name (e.g., `front-handler`, `request-state-handler`) corresponds to a handler file `index.py` inside it. In scenarios, flatten this: write as `[subdir-name].py` (e.g., `front-handler.py`) directly in `scenarios/[scenario_id]/deployment/lambda/`.

- [ ] **Step 2: Generate scenarios for each FAULT-NN in the arch_01 proposal**

  For **each** fault in the proposal (process sequentially in FAULT-NN order):

  a. Derive `scenario_id` = `arch01_fault{NN:02d}_{fault_class}` (e.g., `arch01_fault01_connectivity`).

  b. Check if `scenarios/{scenario_id}/` already exists. If yes, skip and record as skipped. If no, continue.

  c. Write `scenarios/{scenario_id}/scenario.md`:
     - Read the fault's `#### Observable Symptom` and `#### Misconfiguration` sections from the proposal.
     - Write in plain language with no AWS service names. Describe the system's business purpose (derive from traffic_flow or functional_test context). Describe what is observed to be broken using behavior-level language — strong enough to identify the issue given full diagnostic access, but without naming resources, services, or the root cause.
     - Use exactly these four sections: `## System overview`, `## What you have access to`, `## Reported symptom`, `## What correct behavior looks like`.

  d. Write `scenarios/{scenario_id}/faulted.yaml`:
     - Start from `known_good.yaml`. Apply exactly the property changes described in `#### Misconfiguration`. Only the named properties change; every other line is identical to `known_good.yaml`.
     - No comments, no annotations.

  e. Write `scenarios/{scenario_id}/faulted_annotated.yaml`:
     - Same content as `faulted.yaml` but add `# FAULT INJECTION` as an inline comment on every line that differs from `known_good.yaml`.

  f. Write `scenarios/{scenario_id}/fault_manifest.json`:
     - Populate all fields from the schema above using data from the proposal's `**Class:**`, `**Coupled properties:**`, `**Fails assertions:**`, `#### Observable Symptom`, `#### Diagnostic Reasoning Path`, `#### Resolution`, and `#### Difficulty` sections plus known_good.yaml and functional_test.py.
     - `fault_id`: `arch01_fault{NN:02d}`
     - `optimal_diagnostic_path`: derive from the numbered steps in `#### Diagnostic Reasoning Path`.
     - `concurrency_probe_n`: set to an integer (e.g., 10) if fault_class is `performance`; else `null`.

  g. Write all Lambda handlers to `scenarios/{scenario_id}/deployment/lambda/`:
     - For each handler subdirectory in `corpus/.../deployment/lambda/`:
       - If this handler is modified by the fault (described in `#### Misconfiguration` or `#### Resolution`): apply the described changes and write as `{subdir-name}.py` (clean); also write `{subdir-name}_annotated.py` with `# FAULT INJECTION` on each changed line.
       - If not modified: write `{subdir-name}.py` verbatim from corpus. No annotated counterpart.

  h. Print: `Written scenarios/{scenario_id}/`.

- [ ] **Step 3: Report arch_01 result**

  Print:
  ```
  arch_01 — N scenarios written, M skipped.
  Written: [list of scenario_ids written]
  Skipped: [list of scenario_ids skipped]
  ```

---

## Task 2: Generate scenarios for arch_02 (fuzzy movie search)

**Inputs to read (once, before writing any scenario):**
- `corpus/arch_02_fuzzy_movie_search/fault_scenarios_proposal.md`
- `corpus/arch_02_fuzzy_movie_search/known_good.yaml`
- `corpus/arch_02_fuzzy_movie_search/functional_test.py`
- All handlers under `corpus/arch_02_fuzzy_movie_search/deployment/lambda/`

**scenario_id pattern:** `arch02_fault{NN:02d}_{fault_class}`

- [ ] **Step 1: Read all arch_02 inputs**

  Read all 4 input categories. Do not begin writing until all inputs are in context.

- [ ] **Step 2: Generate scenarios for each FAULT-NN in the arch_02 proposal**

  Apply the same authoring rules as Task 1 Step 2 (a–h), substituting arch_02 paths and the `arch02` prefix throughout.

- [ ] **Step 3: Report arch_02 result**

  Print:
  ```
  arch_02 — N scenarios written, M skipped.
  Written: [list]
  Skipped: [list]
  ```

---

## Task 3: Generate scenarios for arch_08 (event-driven SNS FIFO)

**Inputs to read (once, before writing any scenario):**
- `corpus/arch_08_event_driven_architecture_with_sns_fifo_dynamodb_lambda_and_s3/fault_scenarios_proposal.md`
- `corpus/arch_08_event_driven_architecture_with_sns_fifo_dynamodb_lambda_and_s3/known_good.yaml`
- `corpus/arch_08_event_driven_architecture_with_sns_fifo_dynamodb_lambda_and_s3/functional_test.py`
- All handlers under `corpus/arch_08_event_driven_architecture_with_sns_fifo_dynamodb_lambda_and_s3/deployment/lambda/`

**scenario_id pattern:** `arch08_fault{NN:02d}_{fault_class}`

- [ ] **Step 1: Read all arch_08 inputs**

  Read all 4 input categories. Do not begin writing until all inputs are in context.

- [ ] **Step 2: Generate scenarios for each FAULT-NN in the arch_08 proposal**

  Apply the same authoring rules as Task 1 Step 2 (a–h), substituting arch_08 paths and the `arch08` prefix throughout.

- [ ] **Step 3: Report arch_08 result**

  Print:
  ```
  arch_08 — N scenarios written, M skipped.
  Written: [list]
  Skipped: [list]
  ```

---

## Task 4: Generate scenarios for arch_12 (SQS/Lambda/DynamoDB/S3)

**Inputs to read (once, before writing any scenario):**
- `corpus/arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3/fault_scenarios_proposal.md`
- `corpus/arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3/known_good.yaml`
- `corpus/arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3/functional_test.py`
- All handlers under `corpus/arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3/deployment/lambda/`

**scenario_id pattern:** `arch12_fault{NN:02d}_{fault_class}`

- [ ] **Step 1: Read all arch_12 inputs**

  Read all 4 input categories. Do not begin writing until all inputs are in context.

- [ ] **Step 2: Generate scenarios for each FAULT-NN in the arch_12 proposal**

  Apply the same authoring rules as Task 1 Step 2 (a–h), substituting arch_12 paths and the `arch12` prefix throughout.

- [ ] **Step 3: Report arch_12 result**

  Print:
  ```
  arch_12 — N scenarios written, M skipped.
  Written: [list]
  Skipped: [list]
  ```

---

## Task 5: Final summary

> **Note:** Runs after Tasks 1–4 complete. Only task with a dependency.

- [ ] **Step 1: Count all scenario directories written**

  ```bash
  find scenarios/ -maxdepth 1 -mindepth 1 -type d | sort
  ```

- [ ] **Step 2: Verify required files in each scenario directory**

  For each scenario directory, confirm these files exist:
  - `scenario.md`
  - `faulted.yaml`
  - `faulted_annotated.yaml`
  - `fault_manifest.json`
  - `deployment/lambda/` (non-empty)

  Flag any scenario directory that is missing any required file.

- [ ] **Step 3: Count scenarios by fault class across all architectures**

  ```bash
  for f in scenarios/*/fault_manifest.json; do
    python3 -c "import json,sys; d=json.load(open('$f')); print(d['fault_class'])"
  done | sort | uniq -c | sort -rn
  ```

- [ ] **Step 4: Count scenarios requiring both template and handler changes**

  ```bash
  for f in scenarios/*/fault_manifest.json; do
    python3 -c "import json,sys; d=json.load(open('$f')); print(d.get('optimal_files_changed',0))"
  done | awk '$1>=2' | wc -l
  ```

- [ ] **Step 5: Report final summary**

  Print in this format:
  ```
  Scenario Generation — Complete

  Architectures scanned: 4
  arch_01: N scenarios written, M skipped
  arch_02: N scenarios written, M skipped
  arch_08: N scenarios written, M skipped
  arch_12: N scenarios written, M skipped

  Total scenarios written: N
  Total scenarios skipped: M

  Fault class breakdown: security=N, connectivity=N, performance=N, reliability=N, data_correctness=N
  Scenarios requiring template+handler changes: N

  All scenario directories ready.
  [or: Missing required files in: <list of scenario_ids>]
  ```

---

## Parallelism Note

Tasks 1, 2, 3, and 4 are fully independent — they read disjoint corpus directories and write to non-overlapping `scenarios/` subdirectories. When executing via `superpowers:subagent-driven-development`, dispatch all four as parallel subagents. Task 5 must wait for Tasks 1–4 to complete before running.

---

## Quality Checklist (per scenario, self-review before marking complete)

- [ ] `scenario.md` contains zero AWS service names (no "Lambda", "DynamoDB", "SQS", "SNS", "API Gateway", "CloudFormation", "IAM", "S3", "Kinesis", "Firehose")
- [ ] `faulted.yaml` deploys with `CREATE_COMPLETE` — no syntax errors, no references to non-existent resources
- [ ] `faulted_annotated.yaml` content matches `faulted.yaml` exactly (same values, same structure) with `# FAULT INJECTION` added to changed lines only
- [ ] `fault_manifest.json` passes JSON lint and all required fields are populated with non-null, non-empty values (except `concurrency_probe_n` which is null for non-performance faults)
- [ ] Every handler from corpus is present in `deployment/lambda/` (no missing handlers)
- [ ] Only modified handlers have an `_annotated.py` counterpart
- [ ] `scenario_id` in `fault_manifest.json` matches the directory name exactly
- [ ] `corpus_path`, `functional_test_path`, `known_good_path` are relative paths that exist on disk
