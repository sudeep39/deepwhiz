# Python backend migration specification

Status: Implemented locally; production acceptance pending  
Created: 2026-10-21  
Implementation: Python backend and local runtime migration complete; offline verification only  
Related documents: [Technical plan](plan.md), [Increment tracker](tasks.md)

## Purpose

Replace DeepWhiz's Node.js backend with a readable, modular Python backend while preserving the existing browser experience and financial research behavior. Use LangChain for AI component interfaces and LangGraph for explicit ingestion and research workflows. Deliver the migration in increments that can be reviewed, tested and resumed independently.

This document defines required outcomes. The plan records proposed implementation choices. The tracker records execution and evidence. Implementation was authorized on 2026-10-21. The user subsequently chose offline-only verification. No specification toolkit was installed; the repository uses the spec/plan/tasks workflow directly.

## Scope and boundaries

In scope: HTTP API, provider configuration, ingestion workers, parsing, chunking, embeddings, Pinecone access, retrieval, answer generation and validation, persistence compatibility, evaluation, operational commands, deployment and documentation.

The existing vanilla HTML/CSS/JavaScript frontend remains. Only API compatibility fixes needed by the migration belong in this scope and must be recorded. Node may remain for browser tests during transition; production backend execution must use Python after cutover.

Excluded: frontend framework adoption, visual redesign, OCR, new document types, new providers, streaming answers, persistent cross-session conversations, multi-user tenancy, autonomous agents, new financial features and mandatory hosted tracing services. Assignment enhancements receive separate specs referencing this migration.

## Existing baseline

Source references are relative to the DeepWhiz root:

- `lib/web-server.mjs`: routing, authentication, upload handling and worker queue.
- `lib/ingest.worker.mjs`, `lib/chunking.mjs`, `lib/filing-metadata.mjs`: ingestion and parsing.
- `lib/ai-config.mjs`, `lib/providers.mjs`: provider snapshots, embedding provenance and remote calls.
- `lib/retrieval.mjs`, `lib/query-policy.mjs`, `lib/cash-flow.mjs`: retrieval, answers and financial safeguards.
- `lib/storage.mjs`, `lib/recovery.mjs`: hashed filesystem storage and recovery.
- Evaluation modules, `scripts/`, `evaluation/benchmark.json`, `quality-policy.json`: operational and evaluation behavior.
- Existing Python scripts are an earlier optional pipeline; assess reuse against current behavior.

Baseline test counts and live quality results must be captured during increment I01. Documentation reports a historical quota blocker; it is not evidence of current provider availability or measured answer quality.

## User scenarios

### Analyst uploads filings

Given valid HTML, HTM, readable PDF or TXT filings, when an analyst uploads a batch, the API accepts it asynchronously and the UI displays progress. Research becomes available only after indexing visibility is verified. Invalid inputs produce actionable errors without leaving a ready corpus.

### Analyst researches financial evidence

Given ready filings and a provider selection, when an analyst selects companies, periods, chunking strategy and reranking behavior, the system retrieves matching evidence and returns grounded answers, citations and validated charts/calculations. Ambiguous requests receive clarification; unsupported requests receive insufficient-evidence responses.

### Operator resumes processing

Given a worker interruption or transient provider failure, when processing restarts or a failed job is retried, the system resumes safely, reuses compatible cached work and avoids duplicate jobs or falsely completed indexes.

### Maintainer picks up an increment

Given this specification and the tracker, a maintainer can identify dependencies, remaining work, relevant requirements and verification evidence without reconstructing a chat history.

## Requirements

| ID | Requirement | Acceptance evidence |
| --- | --- | --- |
| R01 | Preserve the existing frontend API contract, static asset paths, multipart names, provider header and bearer authentication. | Baseline contract fixtures pass against Python; existing browser workflows pass. |
| R02 | Preserve request validation and externally visible error/status behavior. Adapt FastAPI defaults to the existing contract. | Positive and negative route cases, including malformed JSON, unsupported methods and unavailable providers. |
| R03 | Preserve upload limits: 32 files, 20 MiB per file, 100 MiB per batch; allowed formats and metadata hints. Clean up rejected uploads. | Boundary and interrupted-upload tests; no orphaned accepted-state records. |
| R04 | Persist queued/processing/ready/failed jobs, progress, retry and restart recovery. Retain three queued-or-processing batches, one ingestion worker and a 30-minute processing deadline initially. | Restart, timeout, retry, duplicate dispatch and queue-full scenarios. |
| R05 | Preserve metadata precedence, source checksums, table headers/rows, parent context, accounting signs and readable-text validation. Reject scanned/unreadable inputs. | Representative HTML/PDF/TXT fixtures and source-linked financial table assertions. |
| R06 | Support fixed and semantic chunking with explicit budgets, overlaps, tokenization and parser versions. Preserve existing boundary behavior where compatible; document justified differences. | Golden parsing/chunking fixtures and versioned divergence report. |
| R07 | Preserve OpenAI/Nebius selection, environment variable names, immutable job configuration and embedding-space isolation. Validate dimensions, provenance, finite nonzero vectors and response ordering. Preserve Nebius query instructions separately from document embedding. | Provider fixtures, concurrent-request isolation and incompatible-index/cache rejection. |
| R08 | Bound provider timeouts/retries, embedding batches and upsert payload sizes. Distinguish exhausted quota from transient failures; validate structured output locally. | Mocked timeout, quota, malformed schema, partial upsert and transient-retry cases. |
| R09 | Preserve dense plus BM25 retrieval, reciprocal-rank fusion, company/period filtering, coverage diversity, optional reranking and parent-table expansion. Preserve ranking semantics used by evaluation. | Deterministic ranking fixtures for all four strategy/rerank conditions, filters and rerank fallback. |
| R10 | Preserve answer statuses, follow-up handling, sources, inline citations, charts, calculations and frontend-consumed metrics. Permit at most one answer repair attempt. | Structured answer fixtures for answered, clarification, missing evidence and repair exhaustion. |
| R11 | Compute financial arithmetic in Python and validate literal inputs, citations, finite outputs, denominators, units and signs. Preserve cash-flow and gross-margin safeguards. | Financial regression cases, including parenthesized negatives, invalid charts and cited input rejection. |
| R12 | Preserve annual/quarterly/YTD and fiscal-period distinctions. Q4 derivation requires annual minus matching nine-month YTD evidence. Preserve unsupported-prediction/trading and out-of-corpus policies. | Paired supported/unsupported period scenarios and policy fixtures. |
| R13 | Preserve existing uploads, job state, parent sections, chunks, source downloads and compatible embedding caches. Version changed parser/hash behavior and write new output to separate namespaces. | Migration dry-run manifest and legacy runtime fixture readback without modifying the source. |
| R14 | Preserve authentication requirements in production, origin validation, response security headers, rate limits, two concurrent chats and secret isolation. | Authentication/origin/header/limit checks; no keys or internal file paths in public job records. |
| R15 | Provide Python equivalents for collection/download, experiment export, index plan/create, corpus loading, ingestion, benchmark generation/execution, scoring, freshness, recovery and project export. | CLI inventory maps each old command to an equivalent; dry runs and fixture round trips pass. |
| R16 | Preserve benchmark corpus/suite provenance, four conditions, metric definitions, failed-trial denominators, raw results and manual-review status. | Fixed expected metric fixtures; reports distinguish automated, human-reviewed, blocked and measured results. |
| R17 | Run production without a Node API process. Supply locked dependencies, Python startup/build instructions, hosting configuration and tested rollback. | Clean-install smoke test, browser verification and backup/restore rehearsal. |
| R18 | Separate API, workflows, domain functions and infrastructure. Use typed schemas, named services, external prompt files and injectable provider/storage interfaces. | Architecture review and tests exercising domain functions without network access. |
| R19 | Use persistent LangGraph checkpoints for ingestion with stable run IDs and idempotent external effects. Checkpoint storage does not replace the job queue or original-file storage. | Crash/restart tests around checkpoint, upsert and ready-state boundaries. |
| R20 | Record request/job correlation, graph stage, timings and failures without secrets. Preserve freshness/SLA reporting and existing quality-policy targets as targets. | Operational status fixtures and redacted failure logs. |

## API surface to freeze in I01

| Method | Path | Required behavior |
| --- | --- | --- |
| GET | `/api/health` | Public health response |
| GET | `/api/state` | Jobs, ready documents, companies, provider/configuration and capabilities |
| GET | `/api/operations` | Freshness and operational status |
| POST | `/api/uploads` | Multipart upload; HTTP 202 with public job |
| POST | `/api/jobs/{id}/retry` | Retry eligible failed job; HTTP 202 |
| GET | `/api/sources/{id}` | Authorized source download and attachment headers |
| POST | `/api/chat` | Question, tickers, periods, history, strategy, rerank; structured response |

Capture exact payload schemas, defaults, field omission/null behavior, status codes and headers from source and tests. This inventory is not a substitute for executable contracts. Preserve current static downloads as well.

## Quality and release criteria

1. All requirements have linked verification evidence; no critical financial, isolation or data-loss regression remains.
2. Deterministic functions and API behavior match frozen fixtures, except explicitly reviewed differences.
3. LLM prose is judged by grounded claims, citations, periods, calculations and coverage rather than byte equality.
4. Run the same frozen benchmark inputs/configuration for both implementations when provider access permits. Record latency, failures and quality separately. Agree any nondeterministic regression tolerance before a measured run.
5. Preserve `quality-policy.json` targets, including answer p95 of 15 seconds, as acceptance targets rather than measured guarantees. A blocked live run stays pending; offline fixtures cannot satisfy that evidence gate.
6. Preserve a verified legacy runtime backup and old deployment configuration. Rehearse rollback before production cutover; never run old and new workers against the same writable runtime.

## Decisions for review

| ID | Proposed choice | State |
| --- | --- | --- |
| D01 | FastAPI, Pydantic, LangChain components and LangGraph workflows | Implemented and verified locally |
| D02 | Python 3.12 baseline; exact package versions resolved and locked during foundation work | Implemented and verified locally |
| D03 | Retain filesystem document/cache storage; SQLite job registry and persistent graph checkpoints on durable disk | Local locking/recovery/import verified; hosted durability pending |
| D04 | One dedicated worker; explicit queue ownership; no Redis/Celery dependency initially | Implemented and verified locally |
| D05 | Read legacy artifacts; version new parser output and namespaces; no forced full re-ingestion | Implemented and verified locally |
| D06 | Use the spec/plan/tasks workflow; toolkit installation remains optional | Workflow in use; no toolkit installed |
| D07 | Offline-only verification selected by user on 2026-10-21 | Live budget/tolerances deferred until a future live run |

## Change control

Keep stable R, I and task IDs. Update affected requirements, plan decisions and tests before implementing a scope change. Record the decision date and rationale below. A task is complete only with evidence in the tracker. Add new assignment features as separate specs with explicit dependencies.

| Date | Change | Review state |
| --- | --- | --- |
| 2026-10-21 | Initial scope accepted through instructions to implement | Implementation authorized |
| 2026-10-21 | User selected offline-only verification; live acceptance and hosted cutover stay pending | Recorded |
