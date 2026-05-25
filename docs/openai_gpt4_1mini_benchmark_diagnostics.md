# GPT Benchmark Diagnostics: openai/gpt-4.1-mini

Run label: `openai_gpt4_1mini`

Summary file: `results/_benchmark_logs/openai_gpt4_1mini/summary.tsv`

## Run Summary

| Metric | Count |
|---|---:|
| Total scenarios | 20 |
| Script-level ok | 8 |
| Script-level fail | 12 |
| Zero-score scored runs | 2 |
| Non-zero scored runs | 6 |

## Failed Scenarios

These scenarios failed at the benchmark harness level or produced a zero score.

| Scenario | Exit | Score | Diagnostic |
|---|---:|---:|---|
| `arch01_fault01_connectivity` | 1 | n/a | Pre-agent baseline failed. `harness/verify/pass1_functional.py` timed out after 120s while running `corpus/arch_01_serverless_microservices_with_api_gateway_dynamodb_sqs_and_lambda/functional_test.py`. The scenario was never handed to the model for diagnosis. |
| `arch01_fault02_data_correctness` | 1 | n/a | Same arch01 baseline timeout. No model attempt or scenario-level fix was scored. |
| `arch01_fault03_reliability` | 1 | n/a | Same arch01 baseline timeout. No model attempt or scenario-level fix was scored. |
| `arch01_fault04_data_correctness` | 1 | n/a | Same arch01 baseline timeout. No model attempt or scenario-level fix was scored. |
| `arch01_fault07_connectivity` | 1 | n/a | Same arch01 baseline timeout. No model attempt or scenario-level fix was scored. |
| `arch01_fault09_connectivity` | 1 | n/a | Same arch01 baseline timeout. No model attempt or scenario-level fix was scored. |
| `arch01_fault10_data_correctness` | 1 | n/a | Same arch01 baseline timeout. No model attempt or scenario-level fix was scored. |
| `arch12_fault02_connectivity` | 1 | n/a | Pre-agent baseline failed. `harness/verify/pass1_functional.py` timed out after 120s while running `corpus/arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3/functional_test.py`. |
| `arch12_fault03_connectivity` | 1 | n/a | Same arch12 baseline timeout. No model attempt or scenario-level fix was scored. |
| `arch12_fault04_reliability` | 1 | n/a | Same arch12 baseline timeout. No model attempt or scenario-level fix was scored. |
| `arch12_fault05_reliability` | 1 | 0.0000 | Model reached the scenario, but submitted a fix that failed deployment linting. Scorer zero reason: `did_not_deploy`. Functional test skipped. |
| `arch12_fault06_reliability` | 0 | 0.0000 | Deployment passed, but functional test failed. Scorer zero reason: `quality_gate_failed`. The first submit also hit `Missing CloudFormation output: InventoryUpdatesBucketName`, then later scoring still found functional failure. |
| `arch12_fault08_data_correctness` | 1 | n/a | Same arch12 baseline timeout. No model attempt or scenario-level fix was scored. |
| `arch12_fault10_security` | 1 | n/a | Agent crashed during/after scenario execution. Root exception was another 120s timeout in `corpus/arch_12_event_driven_architecture_with_sqs_lambda_dynamodb_and_s3/functional_test.py`, wrapped by MCP/AnyIO `TaskGroup` errors. |

## Successful / Scored Faults

| Scenario | Score | Classification | Diagnostic |
|---|---:|---|---|
| `arch01_fault05_performance` | 0.8175 | `root_cause` | Best run. Fix passed all assertions. Scorer credited correct root cause: `FrontQueue.Properties.VisibilityTimeout` restored to 120. Lost points for incomplete explicit identification and inefficient repeated queue-depth checks. |
| `arch01_fault06_security` | 0.4275 | `workaround` | Functional tests passed, but scorer found poor root-cause identification. The model restored stream IAM access but added an unnecessary new role, so quality was marked as invalid-patch/workaround. |
| `arch01_fault08_connectivity` | 0.4575 | `workaround` | Functional tests passed. Scorer said the model changed the published URL/stage behavior to resolve the symptom, but did not fix the root cause around `ReadApiStage` / stage configuration. |
| `arch12_fault01_connectivity` | 0.4575 | `workaround` | Functional tests passed. Scorer said the model fixed the S3 permission `SourceArn` trailing slash symptom, but did not also correct the incorrect event field expected by the valid fix. |
| `arch12_fault07_data_correctness` | 0.4575 | `workaround` | Functional tests passed. Scorer said the model changed LocalStack/environment configuration instead of fixing the intended root cause in `lambda/sqs-to-dynamodb.py`. |
| `arch12_fault09_security` | 0.5175 | `workaround` | Functional tests passed. Scorer said the model changed Lambda code to handle a `KeyError`, but did not fix the intended IAM role policy root cause. |

## Pattern Diagnosis

Most hard failures were not model diagnosis failures. They were harness pre-flight or functional-test timeouts before scoring could begin:

- Arch01 timeout group: 7 scenarios blocked in the shared arch01 corpus functional test.
- Arch12 timeout group: 5 scenarios blocked in the shared arch12 corpus functional test or crashed after that timeout.
- Two scenarios reached scoring but received zero: one due failed deployment lint, one due failed functional quality gate.

Among the non-zero scored scenarios, `openai/gpt-4.1-mini` usually found a change that made tests pass, but often used workaround-style patches. The scorer repeatedly penalized missing explicit identification of the target CloudFormation resource/property before fix submission and inefficient repeated diagnostic calls.

## Log References

All per-scenario logs live under:

`results/_benchmark_logs/openai_gpt4_1mini/`

Full scorer result directories live under:

`results/openai_gpt4_1mini__<scenario>/`
