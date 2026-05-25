# Codebase Skills Validation Report

## Executive Summary

Strongest signals: Python backend/automation harness, AWS/serverless debugging, LocalStack-based cloud emulation, pytest testing, JavaScript/Node MCP tooling, AWS SDK usage, and shell/Unix automation. The repo is not a frontend app: React, React Native, Next.js, Redux, Flask, Django, SQL databases, OAuth, C, C++, and Java are not meaningfully supported.

Important caveat: much TypeScript, GitHub Actions, Docker, and AWS CDK evidence lives under `corpus/.../implementation`, which appears to be benchmark fixture/sample application code. It is usable as evidence only if you can honestly explain that you worked on or maintained those benchmark scenarios, not as primary app implementation.

## Evidence Table

| Skill | Classification | Evidence | Recommendation |
|---|---|---|---|
| Python | Strong evidence | 189 main `.py` files. `pyproject.toml:8` requires Python `>=3.11`; `harness/run.py:1` has Python CLI shebang; `harness/run.py:5` imports `asyncio`; `harness/tools/localstack_deployer.py:16` imports `boto3`; `harness/runner/scenario_runner.py:1-5` imports OS/subprocess/threading/time. | List high. Primary language. |
| Java | No evidence | No `.java` files found. | Remove. |
| TypeScript | Moderate evidence | 11 `.ts` files, all in corpus fixture app: `corpus/arch_01.../implementation/lib/friend-microservices-stack.ts:14` imports `aws-cdk-lib`; `.../lambda/frontHandler.ts` is Lambda handler code; `.../package.json` has `typescript`, `ts-node`, `aws-cdk`. | List only if comfortable explaining corpus scenario code. Otherwise omit. |
| JavaScript | Strong evidence | `harness/mcp_server/index.js:1-8` imports MCP SDK, Zod, and tool modules; `tests/test_mcp_server.js:1-14` uses Node test runner plus AWS SDK clients; `harness/mcp_server/package.json:7` starts `node index.js`. | List. Strong backend/tooling evidence. |
| SQL | No evidence | No `.sql` files and no PostgreSQL/SQLite usage found. | Remove. |
| C++ | No evidence | No `.cpp`, `.cc`, `.cxx`, or `.hpp` files found. | Remove. |
| C | No evidence | No `.c` or `.h` implementation files found. | Remove. |
| Bash | Strong evidence | `scripts/benchmark_gpt.sh:1` uses `#!/usr/bin/env bash`; `scripts/test_known_good.sh`; corpus scripts include `run.sh` and `run-integ-tests.sh`. | List as Shell/Bash if you can explain scripts. |
| YAML | Strong evidence as config/data format | 80 main `.yaml` files plus corpus templates. `corpus/arch_08.../implementation/template.yaml:63` defines `AWS::Serverless::Function`; many `known_good.yaml` files encode benchmark specs. | Do not list as a language. Mention YAML only under tools/config if needed. |
| JSON | Strong evidence as data/config format | 43 main `.json` files. `harness/mcp_server/package.json`; many `scenarios/*/fault_manifest.json`; `corpus/screen4_extraction_log.json`. | Do not list as a language. Mention JSON only as data/config if needed. |
| React | No evidence | No React imports found in application code. | Remove. |
| React Native | No evidence | No `react-native` dependency or imports found. | Remove. |
| Next.js | No evidence | No `next` dependency, config, or app structure found. | Remove. |
| Node.js | Strong evidence | `harness/mcp_server/package.json:7` uses `node index.js`; `harness/mcp_server/index.js` is ESM Node code; `tests/test_mcp_server.js:1-2` imports `node:test` and `node:assert/strict`. | List. |
| Express.js | Weak/No defensible evidence | Only lockfile/transitive or license false positives; no app code using `express()` found. | Remove. |
| Flask | No evidence | No Flask imports or app creation found. | Remove. |
| Django | No evidence | No Django project files or imports found. | Remove. |
| Redux | No evidence | No Redux dependencies or imports found. | Remove. |
| asyncio | Strong evidence | `harness/run.py:5` imports `asyncio`; `harness/run.py:381` calls `asyncio.run(...)`; `tests/test_agent_loop.py:1` imports `asyncio` and has multiple `asyncio.run(run_agent_loop(...))` tests. | List as Python async/asyncio if useful. |
| pytest | Strong evidence | `pyproject.toml:11-12` includes `pytest` and `pytest-mock`; `requirements.txt:2-3`; `tests/test_scoring.py:4` imports pytest; `tests/test_agent_loop.py` has many test functions. | List. Strong testing signal. |
| LLM APIs | Strong evidence | `pyproject.toml:17` depends on `litellm`; `harness/scoring/agent.py:3` imports `litellm`; `harness/scoring/agent.py:23` calls `litellm.completion(...)`; `harness/run.py:267` exposes model selection. | Rename to "LiteLLM / LLM API integration". |
| AWS | Strong evidence | `pyproject.toml:10` depends on `boto3`; `harness/shared/localstack_client.py:1` imports `boto3`; `harness/mcp_server/package.json:10-28` has many `@aws-sdk/client-*` packages; `harness/mcp_server/tools/observe.js:39-47` creates AWS clients for CloudFormation, Lambda, DynamoDB, SQS, SNS, S3, Kinesis; corpus includes Lambda/DynamoDB/SQS/SNS/S3/SAM/CDK architectures. | List high. Phrase as AWS serverless services/SDKs, not broad AWS production ops. |
| Google Cloud | Weak evidence | `harness/run.py` includes a `vertex_ai/` model prefix path, but no GCP SDK, infra, deployment, or app code. | Remove or do not emphasize. |
| Docker | Moderate evidence | Corpus fixture has `corpus/arch_02.../implementation/docker-compose.yml` using LocalStack and Docker socket; corpus GitHub Actions pull LocalStack Docker images. No main repo Dockerfile found. | List only as Docker/LocalStack dev environment, not containerized production services. |
| Git | Moderate evidence | `.gitignore`; docs/plans include git workflows and commit commands; repo itself is version-controlled. | OK to list, but low resume differentiation. |
| GitHub Actions | Moderate evidence | Workflows exist only inside corpus fixture apps: `corpus/arch_02.../implementation/.github/workflows/main.yml`, `arch_12...`, `arch_01...`, `arch_08...`. No top-level `.github/workflows` detected. | List only if you maintained those scenario workflows; otherwise omit. |
| Linux/Unix | Strong evidence | Bash scripts, Unix shebangs, subprocess automation, LocalStack endpoint tooling. `harness/run.py:1`; `scripts/benchmark_gpt.sh:1`; `harness/runner/scenario_runner.py:102` and `:131` run subprocess commands. | List as Linux/Unix CLI or shell tooling. |
| PostgreSQL | No evidence | No Postgres dependency, migrations, SQL, `psycopg`, `pg`, or database config found. | Remove. |
| SQLite | No evidence | No SQLite dependency or source usage found. | Remove. |
| OAuth | No evidence | No OAuth, NextAuth, Passport, Auth0, or Clerk usage found. | Remove. |
| Claude Code | Moderate evidence | `README.md:96` references registering diagnostic tools with Claude Code; docs mention Claude Code MCP setup. This is workflow/tooling, not application code. | Mention only if relevant to AI tooling workflow, not as a core engineering skill. |
| Codex | Weak evidence | Current AGENTS instructions mention Codex, but repository evidence is not meaningful source/workflow usage. | Do not list. |

## Missing but Evidenced Skills

- LocalStack: strong evidence in `harness/shared/localstack_client.py`, `harness/tools/localstack_deployer.py`, `tests/test_mcp_server.js`, and corpus Docker Compose.
- boto3: strong Python AWS SDK evidence in `pyproject.toml:10`, `harness/shared/localstack_client.py:1`, `harness/tools/localstack_deployer.py:16`.
- AWS SDK for JavaScript v3: strong evidence in `harness/mcp_server/package.json:10-28` and `tests/test_mcp_server.js:3-14`.
- Model Context Protocol (MCP): strong evidence in `harness/mcp_server/index.js:1-8` and the MCP server package.
- LiteLLM: strong evidence in `pyproject.toml:17` and `harness/scoring/agent.py:3,23`.
- CloudFormation/SAM/CDK: evidenced through `localstack-deployer`, corpus SAM templates, and corpus CDK TypeScript app.
- cfn-lint: dependency in `pyproject.toml:13` and harness lint helpers.
- Python packaging/CLI tools: `pyproject.toml` defines `ace-bench-harness` and `localstack-deployer`.
- Node test runner: `tests/test_mcp_server.js:1` imports `node:test`.

## Weak or Unsupported Skills

Remove or deprioritize: Java, C, C++, SQL, React, React Native, Next.js, Express.js, Flask, Django, Redux, PostgreSQL, SQLite, OAuth, Google Cloud, Codex.

Rename:
- "LLM APIs" -> "LiteLLM / LLM API integration"
- "AWS" -> "AWS SDKs and serverless services: Lambda, DynamoDB, SQS, SNS, S3, CloudFormation, LocalStack"
- "YAML, JSON" -> "YAML/JSON configuration and data formats"
- "Docker" -> "Docker Compose / LocalStack development environment"

## Amazon SDE I Intern Alignment

Best Amazon-relevant signals:

1. Python backend and automation: harness CLI, runners, deployment tooling, result logging, scoring, and tests.
2. AWS serverless systems: Lambda, DynamoDB, SQS, SNS, S3, CloudFormation/SAM/CDK, plus AWS SDK usage in both Python and JavaScript.
3. Testing: substantial pytest coverage and Node integration tests.
4. Local cloud emulation: LocalStack workflows show practical debugging and reproducible infrastructure tests.
5. Distributed-system shape: event-driven architecture scenarios, queues, streams, retries, DLQs, Lambda handlers, and observability/probe tools.
6. Developer tooling: MCP server, CLI entry points, shell scripts, subprocess automation, package manifests.

Less useful for Amazon screening from this repo: frontend frameworks, relational databases, OAuth, C/C++/Java, and GCP.

## Final Resume Skills Section

Languages: Python, JavaScript, Bash; TypeScript; YAML/JSON

Frameworks & Libraries: pytest, asyncio, boto3, AWS SDK for JavaScript, LiteLLM, Model Context Protocol SDK, cfn-lint

Cloud & Tools: AWS Lambda, DynamoDB, SQS, SNS, S3, CloudFormation, AWS CDK/SAM, LocalStack, Docker Compose, Node.js, Git, Linux/Unix, GitHub Actions

Ranking for Amazon SDE I Intern screening:

1. Python
2. AWS Lambda/DynamoDB/SQS/SNS/S3/CloudFormation
3. pytest/testing
4. JavaScript/Node.js
5. LocalStack
6. boto3 and AWS SDK for JavaScript
7. Bash/Linux/Unix automation
8. asyncio
9. LiteLLM / LLM API integration
10. TypeScript/AWS CDK, if you can defend the corpus app work
11. Docker Compose
12. Git/GitHub Actions
13. YAML/JSON config formats

## Interview Defensibility Notes

Be ready to explain deeply:

- Python harness architecture: `harness/run.py`, `harness/runner/scenario_runner.py`, `harness/tools/localstack_deployer.py`.
- AWS and LocalStack flow: `harness/shared/localstack_client.py`, `harness/mcp_server/tools/probe.js`, `harness/mcp_server/tools/observe.js`, `tests/test_mcp_server.js`.
- Testing strategy: `tests/test_agent_loop.py`, `tests/test_scoring.py`, `tests/test_shared.py`.
- LLM scoring integration: `harness/scoring/agent.py`, `harness/run.py`, `tests/test_scoring.py`.
- MCP tooling: `harness/mcp_server/index.js` and `harness/mcp_server/package.json`.
- Event-driven/serverless concepts: corpus apps under `corpus/arch_01...`, `corpus/arch_08...`, `corpus/arch_12...`, especially queues, Lambda handlers, DynamoDB streams, and CloudFormation/SAM/CDK templates.

Do not list unsupported skills unless you have evidence outside this repository and can discuss them independently.
