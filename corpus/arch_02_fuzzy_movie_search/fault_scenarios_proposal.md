# Fault Scenario Proposal — arch_02_fuzzy_movie_search

## Architecture Fault Class Analysis

This architecture's richest fault surface lives in **connectivity** and **data correctness**. The pipeline has five sequential integration hops (Lambda → Kinesis → Firehose → Elasticsearch) where each hop can be misconfigured independently without affecting the appearance of the upstream hop. Firehose is particularly fertile: it has a source configuration (Kinesis ARN + FirehoseRole ARN), a destination configuration (Elasticsearch DomainARN + IndexName + BufferingHints + S3 backup), and its own IAM role — any of these can be wrong in ways that produce different, subtly distinct failure modes. The `ace_describe_firehose_stream` tool's blind spot is critical: it does not expose the ElasticsearchDestinationDescription (IndexName, BufferingHints, RoleARN), only S3 bucket ARN and HTTP endpoint — meaning Firehose destination faults require multiple tool calls to triangulate.

**Data correctness** is rich because both Lambda handlers use environment variables (`STREAM_NAME`, `ELASTICSEARCH_ENDPOINT`, `ELASTICSEARCH_INDEX`) to locate resources, and the search handler uses `ELASTICSEARCH_INDEX` to build the URL path. A wrong index name in either the Firehose destination or the search function's env var produces a failure that looks like Elasticsearch is healthy but returns zero hits — the model must correlate two separate misconfigured values.

**Security** faults are moderately rich because the architecture has two IAM roles (LambdaRole, FirehoseRole) each with resource-scoped policies. A policy granting an action on the wrong ARN does not throw an obvious access-denied at the API surface — the Lambda function URL returns 200 but downstream effects are absent.

**Performance** faults have limited natural surface — there is only one Kinesis stream with one shard, and Lambda functions have a 30-second timeout with no SQS queue visibility timeout interaction. We include one performance scenario around Firehose BufferingHints which silently extends the time before Elasticsearch receives data past the functional test's 180-second window.

**Reliability** faults are deprioritized: there is no DLQ, no retry destination, and no event source mapping — the Kinesis→Firehose connection is native Firehose polling, not a Lambda ESM, so the reliability fault class has limited depth here.

---

## Scenarios

### FAULT-01 — Firehose writes to wrong Elasticsearch index; search function queries the correct index

**Class:** data_correctness
**Type:** coupled
**Chains with:** N/A
**Coupled properties:** `IngestFirehoseStream.ElasticsearchDestinationConfiguration.IndexName` (wrong value), `SearchFunction.Environment.ELASTICSEARCH_INDEX` (correct value)
**Fails assertions:** `document_indexed_in_elasticsearch`, `search_function_returns_document`

#### Misconfiguration

In `known_good.yaml`, the `IngestFirehoseStream` resource has `ElasticsearchDestinationConfiguration.IndexName: movies`. Change this to `IndexName: movie_index` (wrong value). The `SearchFunction` env var `ELASTICSEARCH_INDEX` remains `movies` (correct). The ingest path delivers documents into the `movie_index` index while the search function queries the `movies` index, which is empty. Neither resource alone is broken: Firehose successfully delivers to a valid (if unintended) index, and the search function successfully queries a valid (if empty) index.

Neither misconfiguration alone produces this symptom: if only IndexName is wrong but ELASTICSEARCH_INDEX is also wrong (both pointing to `movie_index`), search would succeed. If only IndexName is wrong, documents go to the wrong index and search returns empty. The subtlety is that both values look plausible in isolation — `movie_index` is a reasonable index name — so the model must correlate the Firehose destination index with the search function's target index.

#### Observable Symptom

`ingest_function_accepts_document` passes (200 OK from ingest URL). `document_indexed_in_elasticsearch` fails after the full 180-second wait — querying the `movies` index returns zero hits. `search_function_returns_document` fails for the same reason. Firehose reports ACTIVE status. Kinesis records are successfully put. There are no Lambda errors in logs.

#### Diagnostic Reasoning Path

Step 1 — Invoke the ingest endpoint directly and observe that it returns 200. Then call `ace_get_log_tail` on the IngestFunction to confirm the Kinesis PutRecord succeeded. At this point, the ingest path appears healthy.

Step 2 — Call `ace_describe_firehose_stream` on the IngestFirehoseStream. The tool reports ACTIVE status and shows the S3 backup bucket ARN — but does NOT expose the `ElasticsearchDestinationConfiguration.IndexName`. The Firehose looks healthy, which is misleading.

Step 3 — Call `ace_describe_kinesis_stream` on IngestStream. It is ACTIVE with records. This confirms records reach Kinesis and Firehose is polling. The pipeline looks intact but search still returns nothing.

Step 4 — Call `ace_get_environment_variables` on SearchFunction. The response shows `ELASTICSEARCH_INDEX: movies`. Then call `ace_describe_resource` on IngestFirehoseStream and examine the raw CloudFormation resource properties. The `ElasticsearchDestinationConfiguration.IndexName` value `movie_index` is visible and does not match `movies`. This mismatch identifies the root cause: Firehose indexes into `movie_index`, search queries `movies`.

#### Resolution

- `corpus/arch_02_fuzzy_movie_search/known_good.yaml` — Change `IngestFirehoseStream.ElasticsearchDestinationConfiguration.IndexName` from `movie_index` back to `movies` so Firehose and the search function target the same index.

A template-only fix is sufficient here (handler does not duplicate the index name), but the fix requires understanding the coupling between the Firehose destination configuration and the search function's environment variable — a model that fixes only one of two mismatched values will not resolve the symptom.

#### Difficulty

**Rating:** medium

The misdirection is that `ace_describe_firehose_stream` hides the destination index name, requiring the model to pivot to `ace_describe_resource` and read raw CloudFormation properties to find the discrepancy.

---

### FAULT-02 — Ingest Lambda writes to a Kinesis stream whose name env var points to a nonexistent stream

**Class:** connectivity
**Type:** coupled
**Chains with:** N/A
**Coupled properties:** `IngestFunction.Environment.STREAM_NAME` (wrong value: `${AWS::StackName}-ingest-data` instead of `${AWS::StackName}-ingest-stream`), `IngestStream.Name` (correct value unchanged)
**Fails assertions:** `ingest_function_accepts_document`, `document_indexed_in_elasticsearch`, `search_function_returns_document`

#### Misconfiguration

In `known_good.yaml`, the `IngestFunction` environment variable `STREAM_NAME` is set via `!Ref IngestStream` which resolves to the stream's physical name. Change this to a hardcoded wrong name: `!Sub '${AWS::StackName}-ingest-data'` (the actual stream name ends in `-ingest-stream`). The `IngestStream` resource still exists and is ACTIVE. The ingest handler calls `kinesis.put_record(StreamName=os.environ["STREAM_NAME"], ...)` — because the env var resolves to a nonexistent stream, the SDK raises `ResourceNotFoundException`.

The second misconfigured property is in `ingest/index.py`: the handler does not catch this exception and re-raises it, causing Lambda to return a 500. This is the coupling: the template fault (wrong stream name) causes the handler to throw an uncaught exception. If the handler had a try/except that returned 200 regardless, the symptom would be different (silent drop vs. visible 500). The combination of an uncaught SDK exception path and a wrong env var produces a visible ingest failure.

#### Observable Symptom

`ingest_function_accepts_document` fails immediately — the ingest URL returns a 500 or Lambda error response. `document_indexed_in_elasticsearch` and `search_function_returns_document` fail as setup-dependent. Kinesis IncomingRecords metrics remain flat on `${StackName}-ingest-stream`.

#### Diagnostic Reasoning Path

Step 1 — Call `ace_invoke_lambda` directly on IngestFunction with a test payload. The response contains an error from the Lambda runtime. Call `ace_get_log_tail` on IngestFunction — the log shows `ResourceNotFoundException: Stream ... not found`.

Step 2 — The error names the stream it tried to write to. Call `ace_get_environment_variables` on IngestFunction — the response shows `STREAM_NAME: <stack>-ingest-data`. This name does not match the actual stream.

Step 3 — Call `ace_list_resources` filtered by `AWS::Kinesis::Stream`. The actual stream physical ID is `<stack>-ingest-stream`. The mismatch between the env var value and the actual stream name is now explicit and the fix is clear.

Step 4 — Additionally check `ace_describe_kinesis_stream` on the correct stream name to confirm it is ACTIVE and ready to receive records.

#### Resolution

- `corpus/arch_02_fuzzy_movie_search/known_good.yaml` — Change `IngestFunction.Environment.Variables.STREAM_NAME` back to `!Ref IngestStream` so it resolves to the actual physical stream name.

A template-only fix is sufficient. The handler's behavior is correct — it reads the env var name faithfully and the SDK correctly rejects the bad name. No handler change required.

#### Difficulty

**Rating:** medium

The error is directly visible in Lambda logs, but the model must correlate the logged stream name against the actual stream name via two separate tool calls (env vars + list resources) to confirm the mismatch rather than guessing at a typo.

---

### FAULT-03 — FirehoseRole lacks kinesis:GetShardIterator; records accumulate in Kinesis but never reach Elasticsearch

**Class:** security
**Type:** coupled
**Chains with:** N/A
**Coupled properties:** `FirehoseRole` inline policy (missing `kinesis:GetShardIterator` from the Kinesis resource statement), `IngestFirehoseStream.KinesisStreamSourceConfiguration.RoleARN` (correctly points to FirehoseRole — making the role look correct at a glance)
**Fails assertions:** `document_indexed_in_elasticsearch`, `search_function_returns_document`

#### Misconfiguration

In `known_good.yaml`, the `FirehoseRole` policy currently grants `kinesis:DescribeStream`, `kinesis:GetRecords`, `kinesis:GetShardIterator`, and `kinesis:ListShards` on the `IngestStream.Arn`. Remove `kinesis:GetShardIterator` from the action list. Firehose requires all four actions to poll from a Kinesis source: it calls `GetShardIterator` to position a cursor, then `GetRecords` to read. Without `GetShardIterator`, Firehose's internal polling loop silently fails — it cannot advance and delivers nothing to Elasticsearch.

The second misconfigured property is the presence of the correct `RoleARN` reference in `IngestFirehoseStream.KinesisStreamSourceConfiguration` — this makes the role assignment look correct, directing the model toward examining the role's permissions rather than the role reference itself. The coupling is: the role is correctly bound to the Firehose stream (so resource-level checks look right), but the role's permission set is incomplete.

#### Observable Symptom

`ingest_function_accepts_document` passes. Kinesis shows records accumulating (IncomingRecords > 0). `document_indexed_in_elasticsearch` fails after 180 seconds — Elasticsearch never receives documents. Firehose reports ACTIVE status. `search_function_returns_document` fails.

#### Diagnostic Reasoning Path

Step 1 — Confirm ingest works: call `ace_invoke_lambda` on IngestFunction, then `ace_put_kinesis_record` directly to verify the stream accepts records. Both succeed, ruling out the ingest path.

Step 2 — Call `ace_describe_firehose_stream` on IngestFirehoseStream. Status is ACTIVE, source type is KinesisStreamAsSource — looks healthy. The tool does not expose delivery error counts or CloudWatch Firehose metrics, so the polling failure is invisible from this call alone.

Step 3 — Call `ace_get_metric_statistics` for the Firehose's `DeliveryToElasticsearch.Records` metric. The value is zero for the observation window, confirming Firehose is not delivering. This motivates examining the Firehose's IAM role.

Step 4 — Call `ace_get_iam_role` on FirehoseRole. The response lists the inline policy. Comparing the Kinesis actions against the required set (`DescribeStream`, `GetRecords`, `GetShardIterator`, `ListShards`), `kinesis:GetShardIterator` is absent. This is the root cause.

#### Resolution

- `corpus/arch_02_fuzzy_movie_search/known_good.yaml` — Add `kinesis:GetShardIterator` back to the Kinesis action list in the `FirehoseRole` inline policy statement.

A template-only fix is sufficient. No handler changes required.

#### Difficulty

**Rating:** hard

The first instinct is to check Firehose configuration, which reports ACTIVE. The missing permission does not surface in any single tool call — it requires pivoting from a zero-delivery metric to the IAM role and manually comparing the action list against the known requirements for Kinesis source polling.

---

### FAULT-04 — Search function ELASTICSEARCH_ENDPOINT env var points to wrong domain endpoint; but ingest succeeds through a different path

**Class:** data_correctness
**Type:** coupled
**Chains with:** N/A
**Coupled properties:** `SearchFunction.Environment.ELASTICSEARCH_ENDPOINT` (wrong value: a plausible but wrong endpoint string), `SearchFunction.Environment.ELASTICSEARCH_INDEX` (correct value: `movies`)
**Fails assertions:** `search_function_returns_document`

#### Misconfiguration

In `known_good.yaml`, `SearchFunction` environment variable `ELASTICSEARCH_ENDPOINT` is set via `!GetAtt MovieSearchDomain.DomainEndpoint`. Change this to a hardcoded wrong value: `localhost:9200` (a plausible local Elasticsearch endpoint, but not the LocalStack domain endpoint). The `ELASTICSEARCH_INDEX` env var remains `movies` (correct). The ingest pipeline is untouched — Firehose correctly delivers to MovieSearchDomain using the FirehoseRole's correct DomainARN. Documents are successfully indexed.

The search function handler constructs `url = f"http://{endpoint}/{index}/_search"` where `endpoint = os.environ["ELASTICSEARCH_ENDPOINT"]`. With the wrong endpoint, the `urllib.request.urlopen` call raises a connection error (since `localhost:9200` is not listening in LocalStack), and the handler raises an uncaught exception that becomes a Lambda 500. The coupling: the correct index name looks right, masking the fact that the connection never reaches the domain.

#### Observable Symptom

`ingest_function_accepts_document` passes. `document_indexed_in_elasticsearch` passes (the functional test queries Elasticsearch directly using the correct `ElasticsearchEndpoint` stack output). `search_function_returns_document` fails — the search function URL returns a 500 or connection error body. Only the search path is broken; ingest and direct ES queries succeed.

#### Diagnostic Reasoning Path

Step 1 — Call the search function URL directly with a query parameter. The response is a 500 with a connection error body. Call `ace_get_log_tail` on SearchFunction — the log shows `ConnectionRefusedError` or `URLError` when connecting to `localhost:9200`.

Step 2 — The log names the attempted endpoint. Call `ace_get_environment_variables` on SearchFunction. The response shows `ELASTICSEARCH_ENDPOINT: localhost:9200`. This value does not match what is expected from `!GetAtt MovieSearchDomain.DomainEndpoint`.

Step 3 — Call `ace_get_stack_outputs` to retrieve `ElasticsearchEndpoint` from the stack outputs. Compare it against the env var value — they differ. The correct endpoint is the stack output value. The root cause is the hardcoded wrong endpoint in the env var.

#### Resolution

- `corpus/arch_02_fuzzy_movie_search/known_good.yaml` — Change `SearchFunction.Environment.Variables.ELASTICSEARCH_ENDPOINT` from the hardcoded wrong value back to `!GetAtt MovieSearchDomain.DomainEndpoint`.

A template-only fix is sufficient. The handler correctly reads the env var and constructs the URL — no handler change needed.

#### Difficulty

**Rating:** medium

The interesting aspect is that `document_indexed_in_elasticsearch` passes while `search_function_returns_document` fails — the model must recognize that these two assertions exercise different paths to Elasticsearch and focus investigation on the search function's configuration rather than the ingest pipeline.

---

### FAULT-05 — Firehose BufferingHints interval set to 900 seconds causing delivery window to exceed functional test timeout

**Class:** performance
**Type:** coupled
**Chains with:** N/A
**Coupled properties:** `IngestFirehoseStream.ElasticsearchDestinationConfiguration.BufferingHints.IntervalInSeconds` (wrong value: `900`), `IngestFirehoseStream.ElasticsearchDestinationConfiguration.BufferingHints.SizeInMBs` (wrong value: `128`)
**Fails assertions:** `document_indexed_in_elasticsearch`, `search_function_returns_document`

#### Misconfiguration

In `known_good.yaml`, the `IngestFirehoseStream.ElasticsearchDestinationConfiguration.BufferingHints` has `IntervalInSeconds: 60` and `SizeInMBs: 1`. Change both to `IntervalInSeconds: 900` and `SizeInMBs: 128`. Firehose buffers records until either threshold is reached. With a single small test document, the size threshold (128 MB) will never be reached. The interval threshold (900 seconds = 15 minutes) far exceeds the functional test's 180-second timeout. The ingest path is entirely functional — records enter Kinesis and Firehose begins buffering — but Elasticsearch never receives them within the test window.

Neither threshold alone causes this symptom: if only `IntervalInSeconds: 900` with `SizeInMBs: 1`, a single document is 1–2 KB and still would not fill 1 MB, so delivery would still wait for the interval. But `SizeInMBs: 128` combined with `IntervalInSeconds: 900` ensures both flush triggers are impossible within the test window, making the fault deterministic rather than probabilistic.

#### Observable Symptom

`ingest_function_accepts_document` passes. Kinesis shows records accepted. Firehose reports ACTIVE. `document_indexed_in_elasticsearch` times out after 180 seconds — Elasticsearch shows zero documents. `search_function_returns_document` fails. After the test completes, if one waits >15 minutes, documents eventually appear in Elasticsearch, revealing the buffering root cause.

#### Diagnostic Reasoning Path

Step 1 — Confirm ingest: call `ace_invoke_lambda` on IngestFunction and observe 200 with `{"status": "accepted"}`. Kinesis records are accumulating (confirmed via `ace_describe_kinesis_stream` showing a non-zero record count from `ace_put_kinesis_record` tests).

Step 2 — Call `ace_describe_firehose_stream` on IngestFirehoseStream. Status is ACTIVE. The tool does NOT expose `BufferingHints` — only S3 bucket ARN and HTTP endpoint. The delivery problem is not visible from this call.

Step 3 — Call `ace_get_metric_statistics` for Firehose `DeliveryToElasticsearch.Records` — value is zero. Also check `IncomingRecords` metric for the Kinesis stream — records are present. The combination confirms records enter Kinesis but Firehose does not flush to Elasticsearch.

Step 4 — Call `ace_describe_resource` on IngestFirehoseStream to retrieve the raw CloudFormation resource properties. The `ElasticsearchDestinationConfiguration.BufferingHints` shows `IntervalInSeconds: 900` and `SizeInMBs: 128`. Given a single small test document, neither threshold triggers within 180 seconds. This is the root cause.

#### Resolution

- `corpus/arch_02_fuzzy_movie_search/known_good.yaml` — Change `IngestFirehoseStream.ElasticsearchDestinationConfiguration.BufferingHints.IntervalInSeconds` from `900` back to `60` and `SizeInMBs` from `128` back to `1`.

A template-only fix is sufficient. No handler changes required.

#### Difficulty

**Rating:** hard

The fault is entirely silent — all upstream components appear healthy, and Firehose reports ACTIVE. The model must pivot from a zero-delivery metric to the raw CloudFormation resource properties (since `ace_describe_firehose_stream` hides `BufferingHints`) and then reason that the buffer thresholds cannot be reached within the test's time window.

---

### FAULT-06 — LambdaRole's Elasticsearch permission scoped to wrong ARN; ingest writes to Kinesis successfully but search function returns 403

**Class:** security
**Type:** coupled
**Chains with:** N/A
**Coupled properties:** `LambdaRole` inline policy ES resource ARN (wrong: hardcoded wrong domain ARN without wildcard sub-path suffix), `SearchFunction.Environment.ELASTICSEARCH_ENDPOINT` (correct)
**Fails assertions:** `search_function_returns_document`

#### Misconfiguration

In `known_good.yaml`, the `LambdaRole` policy grants `es:ESHttpGet` and `es:ESHttpPost` on both `!GetAtt MovieSearchDomain.DomainArn` and `!Sub '${MovieSearchDomain.DomainArn}/*'`. Change the second resource to `!Sub '${MovieSearchDomain.DomainArn}/other_index/*'`. The domain-level ARN permission (`DomainArn` without suffix) covers domain management actions but not index-level HTTP operations. The wildcard suffix on `other_index/*` does not cover the `movies` index path. The result: the search function's POST to `/{movies_index}/_search` receives a 403, because the effective permission only covers `other_index/*`.

The ingest path is unaffected — the `IngestFunction` only calls Kinesis, and the FirehoseRole (separate role) handles the Firehose→Elasticsearch write. The `LambdaRole` ES permission is only exercised by the SearchFunction.

The second coupled property is that the domain-level ARN still appears in the policy (`!GetAtt MovieSearchDomain.DomainArn`), making a quick policy review appear to show valid ES permissions — the model must read the full resource list carefully to spot that the wildcard suffix covers the wrong index path.

#### Observable Symptom

`ingest_function_accepts_document` passes. `document_indexed_in_elasticsearch` passes (Firehose uses FirehoseRole, not LambdaRole). `search_function_returns_document` fails — the search function URL returns a 500 with an Elasticsearch 403 error body logged. Logs show `HTTP Error 403: Forbidden` when the search function attempts `POST /movies/_search`.

#### Diagnostic Reasoning Path

Step 1 — Call `ace_get_log_tail` on SearchFunction. Logs show an HTTP 403 error from Elasticsearch when posting to `movies/_search`. The Elasticsearch endpoint and index appear correct in the log URL.

Step 2 — Call `ace_get_environment_variables` on SearchFunction. Both `ELASTICSEARCH_ENDPOINT` and `ELASTICSEARCH_INDEX` are correct. The configuration looks right, so the problem is not a misconfigured env var.

Step 3 — Call `ace_get_iam_role` on LambdaRole. The response lists the policy statements. The ES action list includes `es:ESHttpGet` and `es:ESHttpPost`. However, examining the Resource list, the wildcard suffix entry resolves to `arn:aws:es:us-east-1:000000000000:domain/<stack>-movie-search/other_index/*` — this does not cover `/movies/*`. The domain-level ARN alone does not authorize index-level HTTP calls.

Step 4 — Call `ace_simulate_policy` with the LambdaRole ARN, action `es:ESHttpPost`, and resource `arn:aws:es:...domain/.../movies/_search`. The simulation returns `implicitDeny`, confirming the policy gap.

#### Resolution

- `corpus/arch_02_fuzzy_movie_search/known_good.yaml` — Change the second ES resource in the `LambdaRole` policy back to `!Sub '${MovieSearchDomain.DomainArn}/*'` (covering all index paths under the domain).

A template-only fix is sufficient.

#### Difficulty

**Rating:** hard

The domain-level ARN presence in the policy makes the permission look valid at first glance. The model must read the full resource list, recognize that `other_index/*` does not cover `movies/_search`, and confirm via policy simulation — three distinct reasoning steps.

---

### FAULT-07 — Ingest handler reads a wrong environment variable name; Kinesis stream name is undefined at runtime, causing silent 500

**Class:** data_correctness
**Type:** coupled
**Chains with:** N/A
**Coupled properties:** `IngestFunction.Environment.Variables` adds a second key `KINESIS_STREAM_NAME` with the correct stream name value, while `STREAM_NAME` is removed; `ingest/index.py` still reads `os.environ["STREAM_NAME"]`
**Fails assertions:** `ingest_function_accepts_document`, `document_indexed_in_elasticsearch`, `search_function_returns_document`

#### Misconfiguration

In `known_good.yaml`, rename the env var key from `STREAM_NAME` to `KINESIS_STREAM_NAME` in `IngestFunction.Environment.Variables`, keeping the value (`!Ref IngestStream`) unchanged. In `deployment/lambda/ingest/index.py`, the handler still reads `os.environ["STREAM_NAME"]`. At runtime, `os.environ["STREAM_NAME"]` raises `KeyError` — Python does not catch this and Lambda returns a 500 runtime error.

The coupling: the infrastructure change (renaming the env var key) and the handler's hardcoded env var name `"STREAM_NAME"` are two properties that must match. If only the template is changed (STREAM_NAME removed, KINESIS_STREAM_NAME added) but the handler is not updated, the function crashes. If only the handler is changed to read `KINESIS_STREAM_NAME` but the template still supplies `STREAM_NAME`, the function also crashes. Only both correct values together (template supplies STREAM_NAME, handler reads STREAM_NAME) produce correct behavior. The fault introduces a mismatch that cannot be fixed in either file alone.

#### Observable Symptom

`ingest_function_accepts_document` fails — the ingest URL returns a 500 with a Lambda runtime error. `document_indexed_in_elasticsearch` and `search_function_returns_document` fail as ingest-dependent. Lambda logs show `KeyError: 'STREAM_NAME'`.

#### Diagnostic Reasoning Path

Step 1 — Call `ace_invoke_lambda` on IngestFunction with a test movie payload. The response contains a Lambda error with `errorType: KeyError` and `errorMessage: 'STREAM_NAME'`. Call `ace_get_log_tail` to see the full traceback.

Step 2 — Call `ace_get_environment_variables` on IngestFunction. The response shows `{"KINESIS_STREAM_NAME": "<stack>-ingest-stream"}` — `STREAM_NAME` is absent. The env var exists under the wrong key name.

Step 3 — Read the ingest handler source (`deployment/lambda/ingest/index.py`). The code reads `os.environ["STREAM_NAME"]`. The env var the handler expects (`STREAM_NAME`) differs from what is configured (`KINESIS_STREAM_NAME`).

Step 4 — The model must decide: fix the template to restore `STREAM_NAME` as the key, or fix the handler to read `KINESIS_STREAM_NAME`. The canonical resolution is to restore `STREAM_NAME` in the template (matching the known_good reference); changing only the handler would leave a non-canonical env var name in the template.

#### Resolution

- `corpus/arch_02_fuzzy_movie_search/known_good.yaml` — Rename the env var key back from `KINESIS_STREAM_NAME` to `STREAM_NAME` in `IngestFunction.Environment.Variables`.
- `corpus/arch_02_fuzzy_movie_search/deployment/lambda/ingest/index.py` — Restore `os.environ["STREAM_NAME"]` (no change needed if template is fixed; but if the model chose to fix the handler instead, it must change `"STREAM_NAME"` to `"KINESIS_STREAM_NAME"` and the template to supply `KINESIS_STREAM_NAME`).

A template-only fix is insufficient if the handler is also wrong (though in this scenario, the handler is unchanged from known-good — the template is the faulted property). However, **a template-only fix is insufficient** in the general form: both the env var key in the template and the env var name in the handler must agree. A code-only fix is also insufficient: changing the handler to read `KINESIS_STREAM_NAME` without ensuring the template provides that key would leave the function broken. This scenario explicitly requires reasoning about the contract between the template-supplied env var name and the handler's expected env var name.

**Explicitly stated:** a template-only fix is insufficient if the model changes only the handler (wrong direction). A code-only fix is insufficient because the template must supply a matching key. Both the template env var key and the handler read must be in agreement — the correct pair must be established across both files to fully resolve the fault.

#### Difficulty

**Rating:** medium

The `KeyError` in Lambda logs immediately names the missing key, making the symptom easy to read. The difficulty is in understanding that the fix requires coordinating the env var key name across template and handler — a model that changes only the template (renaming `KINESIS_STREAM_NAME` back to `STREAM_NAME`) succeeds, but a model that changes only the handler (to read `KINESIS_STREAM_NAME`) would need to also update the template, requiring the model to reason about the bidirectional contract.

---

### FAULT-08 — Search function handler uses wrong field name to extract hits from Elasticsearch response; returns empty array despite indexed documents

**Class:** data_correctness
**Type:** coupled
**Chains with:** FAULT-01 (prerequisite — if FAULT-01 is present, documents are indexed in the wrong ES index and the search function receives no hits regardless; FAULT-01 must be resolved before FAULT-08's symptom becomes distinguishable)
**Coupled properties:** `search/index.py` uses `hit.get("_source", {})` but also applies a wrong field extraction path for the top-level hits container (`result.get("results", {})` instead of `result.get("hits", {})`), `SearchFunction.Environment.ELASTICSEARCH_INDEX` (correct: `movies`)
**Fails assertions:** `search_function_returns_document`

#### Misconfiguration

In `deployment/lambda/search/index.py`, change the top-level hit extraction from `result.get("hits", {}).get("hits", [])` to `result.get("results", {}).get("hits", [])`. Elasticsearch returns `{"hits": {"hits": [...]}}` — the top-level key is `"hits"`, not `"results"`. With `result.get("results", {})`, the outer get returns an empty dict, `.get("hits", [])` returns an empty list, and `movies` is always `[]`. The function returns 200 with an empty JSON array regardless of how many documents are indexed.

The second coupled property is `ELASTICSEARCH_ENDPOINT` being correct — the ES request succeeds (200 from ES) and the function returns 200, making the failure look like an Elasticsearch query problem or missing documents rather than a code parsing bug.

The chaining with FAULT-01: if FAULT-01 is active (Firehose writing to `movie_index` while search queries `movies`), Elasticsearch itself returns `{"hits": {"hits": []}}` — zero results. With FAULT-08 active simultaneously, both produce an empty response from the search URL, making them indistinguishable. Only after FAULT-01 is resolved (documents appear in `movies`) does the FAULT-08 symptom become distinct: ES returns actual hits, but the handler still extracts `[]` because it reads the wrong field.

#### Observable Symptom

`ingest_function_accepts_document` passes. `document_indexed_in_elasticsearch` passes (the functional test queries ES directly, bypassing the search function). `search_function_returns_document` fails — the search URL returns `[]` (empty array, status 200). Elasticsearch logs and metrics show queries are hitting the correct index and returning documents, but the search function consistently returns an empty list.

#### Diagnostic Reasoning Path

Step 1 — Call the search function URL with a known movie title. Observe that it returns HTTP 200 with an empty JSON array `[]`. Call `ace_get_log_tail` on SearchFunction. The log shows successful ES HTTP calls (200 from ES) but the returned movie list is empty.

Step 2 — Add a debug check: call the Elasticsearch endpoint directly using `ace_invoke_lambda` on SearchFunction with a logging modification — not possible without code change. Instead, call `ace_put_firehose_record` to push a test record, wait, then query ES directly via the functional test's `elasticsearch_search` path. Confirm ES actually returns hits — it does.

Step 3 — Read the search handler code. The line `for hit in result.get("results", {}).get("hits", [])` uses `"results"` as the top-level key. The Elasticsearch response schema uses `"hits"` at the top level. `result.get("results", {})` returns `{}` because the key does not exist, causing the hits loop to be empty.

Step 4 — The fix is clear: change `"results"` to `"hits"` in the handler. No template change is needed — the Elasticsearch endpoint, index, and IAM permissions are all correct.

#### Resolution

- `corpus/arch_02_fuzzy_movie_search/deployment/lambda/search/index.py` — Change `result.get("results", {}).get("hits", [])` to `result.get("hits", {}).get("hits", [])`.

A template-only fix is insufficient — the template has no bearing on the response field extraction logic. A code-only fix resolves the fault completely.

**Explicitly stated:** a template-only fix is insufficient because the misconfiguration is entirely in the handler's field extraction logic. Only the handler change resolves the symptom.

#### Difficulty

**Rating:** hard

The search function returns 200 with `[]` — no error, no exception, no 5xx — making the failure look like an empty Elasticsearch index. The model must recognize that `document_indexed_in_elasticsearch` passes (ES has data) while `search_function_returns_document` fails (function returns empty), deduce the function's parsing logic is wrong, and read the handler to find the wrong field key.

---

### FAULT-09 — IngestFunctionUrlPermission omits the FunctionUrlAuthType field causing function URL to reject all unauthenticated requests

**Class:** security
**Type:** coupled
**Chains with:** N/A
**Coupled properties:** `IngestFunctionUrlPermission.FunctionUrlAuthType` (missing/wrong: set to `AWS_IAM` instead of `NONE`), `IngestFunctionUrl.AuthType` (correct: `NONE`)
**Fails assertions:** `ingest_function_accepts_document`, `document_indexed_in_elasticsearch`, `search_function_returns_document`

#### Misconfiguration

In `known_good.yaml`, `IngestFunctionUrlPermission` has `FunctionUrlAuthType: NONE` matching the `IngestFunctionUrl.AuthType: NONE`. Change `IngestFunctionUrlPermission.FunctionUrlAuthType` to `AWS_IAM`. With `AuthType: NONE` on the URL but `FunctionUrlAuthType: AWS_IAM` on the permission, the Lambda service applies the stricter of the two: the permission's `AWS_IAM` requirement means the function URL rejects unsigned requests with 403. Signed (SigV4) requests would succeed, but the functional test sends unsigned HTTP POSTs.

The second coupled property is `IngestFunctionUrl.AuthType: NONE` remaining correct — this makes the function URL configuration look correct at first glance (AuthType is NONE), but the permission resource overrides the effective auth enforcement.

#### Observable Symptom

`ingest_function_accepts_document` fails with HTTP 403. `document_indexed_in_elasticsearch` and `search_function_returns_document` fail as ingest-dependent. The ingest URL is reachable (no connection error) but all requests are rejected with a 403 authorization error.

#### Diagnostic Reasoning Path

Step 1 — POST to the ingest function URL and receive HTTP 403. Call `ace_get_log_tail` on IngestFunction — no log entries appear, confirming the function was never invoked. The rejection happens at the Lambda URL layer before the function executes.

Step 2 — Call `ace_describe_resource` on `IngestFunctionUrl`. The resource properties show `AuthType: NONE`. This looks correct and is misleading — the URL itself is configured for public access.

Step 3 — Call `ace_list_resources` filtered to `AWS::Lambda::Permission`. The IngestFunctionUrlPermission physical resource is returned. Call `ace_describe_resource` on `IngestFunctionUrlPermission`. The raw properties show `FunctionUrlAuthType: AWS_IAM`, which conflicts with the URL's `AuthType: NONE`. The permission's auth type overrides the effective enforcement.

Step 4 — Compare against `SearchFunctionUrlPermission` for reference: it correctly has `FunctionUrlAuthType: NONE`. The mismatch between `IngestFunctionUrlPermission` and `SearchFunctionUrlPermission` confirms the root cause.

#### Resolution

- `corpus/arch_02_fuzzy_movie_search/known_good.yaml` — Change `IngestFunctionUrlPermission.FunctionUrlAuthType` from `AWS_IAM` back to `NONE` to match the function URL's `AuthType: NONE`.

A template-only fix is sufficient.

#### Difficulty

**Rating:** hard

The function URL's `AuthType: NONE` makes the URL look correctly configured for public access, but the Permission resource's `FunctionUrlAuthType` field is the effective enforcement mechanism. A model that only checks `IngestFunctionUrl` properties will conclude the auth is correct and be misdirected into investigating other causes.

---

### FAULT-10 — FirehoseRole's S3 backup permissions scoped to wrong bucket; Firehose silently drops failed records instead of writing to backup

**Class:** reliability
**Type:** chained
**Chains with:** FAULT-03 (prerequisite — FAULT-03 must be resolved first; while FAULT-03 is active, Firehose never polls Kinesis at all, so it never attempts to write backup records. Only after FAULT-03 is fixed does Firehose begin polling and encountering its S3 backup permission failure)
**Coupled properties:** `FirehoseRole` inline policy S3 resource ARNs (wrong: hardcoded wrong bucket ARN instead of `!GetAtt SkippedDocumentsBucket.Arn`), `IngestFirehoseStream.ElasticsearchDestinationConfiguration.S3Configuration.BucketARN` (correct: `!GetAtt SkippedDocumentsBucket.Arn`)
**Fails assertions:** `document_indexed_in_elasticsearch`, `search_function_returns_document`

#### Misconfiguration

In `known_good.yaml`, the `FirehoseRole` S3 policy grants permissions on `!GetAtt SkippedDocumentsBucket.Arn` and `!Sub '${SkippedDocumentsBucket.Arn}/*'`. Change these to a hardcoded wrong ARN: `arn:aws:s3:::wrong-backup-bucket` and `arn:aws:s3:::wrong-backup-bucket/*`. The `IngestFirehoseStream.ElasticsearchDestinationConfiguration.S3Configuration.BucketARN` correctly references `!GetAtt SkippedDocumentsBucket.Arn`.

When Firehose encounters a delivery failure (any document it cannot index to Elasticsearch), it attempts to write the failed record to the S3 backup bucket. With the role's S3 permission scoped to `wrong-backup-bucket`, the S3 write fails silently — Firehose emits a CloudWatch error metric but does not retry the ES delivery. Under normal operation (all ES writes succeed), this fault has no effect. The functional test does not verify skipped documents directly. However, if Elasticsearch delivery is impaired (e.g., a brief ES connectivity issue causes one document to fail), the document is silently dropped rather than backed up for retry.

**Chaining:** While FAULT-03 is active, Firehose cannot poll Kinesis at all (`GetShardIterator` is denied), so it never reaches the point of attempting ES delivery or S3 backup. FAULT-03 completely masks FAULT-10's symptom — the backup path is never exercised. Only after FAULT-03 is resolved does Firehose begin polling, deliver to ES, and encounter the backup permission failure when any ES write fails.

#### Observable Symptom

(After FAULT-03 is resolved) Under normal conditions, the functional tests may pass — if all documents are successfully indexed to Elasticsearch, the S3 backup path is never triggered. The fault surfaces when Elasticsearch is temporarily unavailable or rejects a document: the failed record is silently dropped, `document_indexed_in_elasticsearch` eventually fails (document never appears), and no backup record appears in `SkippedDocumentsBucket`.

#### Diagnostic Reasoning Path

Step 1 — Confirm Firehose is polling (FAULT-03 was the prerequisite and is already resolved). Call `ace_get_metric_statistics` for Firehose's `BackupToS3.Success` and `BackupToS3.DataFreshness` metrics. If any S3 backup attempts have failed, `BackupToS3.Failure` will be non-zero.

Step 2 — Call `ace_check_s3_object` on `SkippedDocumentsBucket` to verify that expected backup objects are absent (they were silently dropped). The bucket is empty when it should contain failed delivery records.

Step 3 — Call `ace_get_iam_role` on FirehoseRole. Inspect the S3 policy statement resource ARNs. They reference `arn:aws:s3:::wrong-backup-bucket` and `arn:aws:s3:::wrong-backup-bucket/*`. The Firehose stream's S3 configuration correctly points to `SkippedDocumentsBucket`, but the role's permission covers a different bucket.

Step 4 — The mismatch between the role's S3 resource ARN and the actual backup bucket confirms the cause. The Firehose stream uses `SkippedDocumentsBucket` but the role cannot write to it.

#### Resolution

- `corpus/arch_02_fuzzy_movie_search/known_good.yaml` — Restore the `FirehoseRole` S3 policy resource ARNs to `!GetAtt SkippedDocumentsBucket.Arn` and `!Sub '${SkippedDocumentsBucket.Arn}/*'`.

A template-only fix is sufficient.

**Explicitly stated:** a template-only fix is insufficient only if the model misidentifies the fault as a Firehose stream configuration issue. The correct fix is entirely in the IAM role's resource ARNs — the Firehose stream's S3Configuration.BucketARN is correct.

#### Difficulty

**Rating:** very_hard

FAULT-10 is masked entirely by FAULT-03 until the prerequisite is resolved. Even after FAULT-03 is fixed, FAULT-10 only manifests when Elasticsearch delivery fails for a document — under happy-path conditions, all tests pass. The model must observe the absence of backup records and correlate it with the IAM role's S3 resource scope, a non-obvious two-level diagnostic requiring both a metric check and a policy analysis.
