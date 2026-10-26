# DeepWhiz

**Live app:** [DeepWhiz on Replit (original application)](https://fin-whiz.replit.app/)

Choose **OpenAI** or **Nebius Token Factory** in the top-bar AI provider selector. Selection applies to ingestion, semantic chunking, embeddings, reranking and research. See [provider setup and index commands](docs/AI_PROVIDERS.md). Each provider uses a separate compatible Pinecone embedding space; existing OpenAI documents are preserved.

Financial Document Intelligence for investment analysts. A vanilla JavaScript frontend, Python FastAPI backend and isolated background worker answer questions over uploaded SEC filings using table-aware hybrid retrieval and Pinecone. LangChain provides model interfaces; LangGraph coordinates ingestion and research. SQLite stores job/checkpoint state; original documents and caches remain on disk. Research becomes available after ingestion completes.

## Start

For class responsibilities, method callers and end-to-end request traces, see
the [Python backend code guide](docs/BACKEND_CODE_GUIDE.md).

This workspace is configured to use Replit's shared library. Run `.\restart.cmd` and open http://127.0.0.1:5173. The launcher serves Replit's matching UI and API; it does not create a local library. `npm.cmd start` also honors `FINWHIZ_BACKEND_URL`. The standalone Python instructions below apply only when shared mode is explicitly disabled for an isolated development environment.

```sh
python -m venv .venv
# Activate .venv, then:
python -m pip install -r backend/requirements.lock
python run.py serve
```

Requires Python >=3.12. Open http://localhost:3100. On Windows, activation is optional: use `.venv\Scripts\python.exe` instead of `python`. Copy `.env.example` to `.env` and configure server-side OPENAI_API_KEY and/or NEBIUS_API_KEY, plus PINECONE_API_KEY. Set PINECONE_INDEX to the compatible dense/cosine/1,536-dimensional OpenAI index; the default is finwhiz-filings-v1. APP_PASSWORD is required for production/Replit deployment. Never commit keys.

Existing Node runtimes require a stopped-server import into a separate directory before switching `FINWHIZ_DATA_DIR`; follow the [Python migration and rollback guide](docs/PYTHON_MIGRATION.md). Python defaults to `.runtime-python`. The original Node backend remains available for rollback. Node >=22 and `npm ci` are needed only for legacy/browser checks and optional npm convenience commands; `npm start` now launches Python.

For the exact Windows launch command, latency controls and request timing diagnostics, see [launch and performance](docs/BACKEND_PERFORMANCE.md).

To run the frontend on port 5173 and the Python backend on port 8000 in separate terminals, follow [local development instructions](docs/LOCAL_DEVELOPMENT.md).

For a single library across local and hosted frontends, upload deduplication, and offline inventory before Pinecone cleanup, see [shared library setup](docs/SHARED_LIBRARY.md).

Company and fiscal-period mentions now narrow retrieval before vector/keyword search. See [query scope behavior and fiscal metadata review](docs/QUERY_SCOPE.md).

Research now uses bounded agentic hybrid RAG: after the initial retrieval, the
model can request up to two focused searches or complete-table reads before
answering. Scope, arithmetic, evidence budgets and citation validation remain
enforced by the backend. Complete local calculations bypass model planning.
See the [agentic RAG specification and rollback settings](specs/008-agentic-rag/spec.md).
Set `AGENTIC_RAG_ENABLED=false` to restore one-pass retrieval. Deploy the updated
Python source to apply this behavior to the shared Replit instance.

The Python backend provides an authenticated preview/apply API for merging fully duplicate ingestion jobs while preserving source links. See [duplicate-merge API and deployment instructions](docs/DEDUPLICATION_API.md).

Replit startup restores the seven retained DeepWhiz Tesla filings automatically when its registry is empty, using a verified snapshot and existing Pinecone vectors. Deploy `data/recovery/deepwhiz-retained-library-v1.json.gz` with the code; recovery creates no new embeddings. See [automatic library recovery](docs/LIBRARY_RECOVERY.md).

Browser uploads support HTML/HTM, readable PDF and TXT, up to 32 files, 20 MB/file and 100 MB/batch. Metadata is extracted from contents where available; explicit hints fill missing ticker/form/period fields. A saved upload receives HTTP 202; cleanup, chunking, embedding and indexing continue in the backend. The company selector, period filters and chat become available only when ready.

## Rubric and measured status

The current [rubric implementation map](docs/RUBRIC_IMPLEMENTATION.md) supersedes earlier audit assessments. The app has fixed/semantic chunking, dense+BM25/RRF retrieval, optional reranking, cited structured answers, charts/calculations, clarification/refusal, freshness/SLA monitoring, recovery and a four-condition evaluation/report workflow.

The controlled dataset is NVIDIA and Tesla across eight fiscal endpoints each (16 filings). It is an experiment configuration, not a hardcoded UI corpus. The ten-company source archive is preserved. A 10-K contains annual totals, not standalone Q4 results.

The Python migration's offline corpus audit reached 16 parsed filings and 2,924 fixed chunks; all 48 benchmark evidence labels are present. The previous Node live attempt reached Pinecone and then exhausted OpenAI quota. Live Python provider verification and quality measurements remain pending; no improvement or faithfulness score is claimed.

```sh
python run.py ingest --apply
python run.py evaluate --apply --answers
```

Stop the web server before standalone ingestion. These commands use paid providers. The first resumes the known corpus; the second runs the same 32 questions for fixed/semantic with reranking on/off. Optional `--judge` adds automated claim review. Plain `npm run evaluate` validates without provider calls. Results/report/manual review appear under artifacts/evaluation. Follow the [runbook](docs/RUNBOOK.md) for scoring, recovery and freshness.

## Verification and submission

```sh
npm run test:python
npm run test:browser
npm test # retained legacy regression suite
```

Browser checks exercise the Python API using installed Edge and mocked provider readiness/answers. The Python backend tests cover HTTP contracts, provider transports, worker processes, checkpoint recovery, financial validation and runtime recovery. Legacy Node and earlier Python preparation/loading tests remain available. These checks verify implementation, not live-provider retrieval quality. See the [migration verification record](docs/PYTHON_MIGRATION.md).

* [Submission overview, dataset, prompts, iterations and observations](docs/SUBMISSION.md)
* [Comparison report status](docs/COMPARISON_REPORT.md)
* [Demo script, at most five minutes](docs/DEMO_SCRIPT.md)
* [Verification record](docs/VERIFICATION.md)
* [Security scope](SECURITY.md)
* [Evaluation protocol](evaluation/README.md)

Manual faithfulness review, the real measured report, Google Doc import, personal reflections and recording remain submission actions. See the rubric map for exact boundaries.

## GitHub and hosting

```sh
npm run export:project
```

This creates an independent source root under release with a SHA-256 manifest and common credential-signature checks. It excludes .env, runtime/uploads, caches, dependencies, downloaded HTML and local backups; it does not publish or initialize Git. Follow [GitHub preparation](docs/GITHUB_PREPARATION.md) when publication is requested. Fresh exports can restore SEC HTML with npm run download and an appropriate SEC_USER_AGENT; source checksums must match the frozen experiment.

Replit can import the independent GitHub repository directly. The root .replit config targets a Reserved VM. Configure HTTPS, strong APP_PASSWORD, provider secrets and a verified persistent FINWHIZ_DATA_DIR. Pinecone stores vectors; local files retain queue/source/lexical/cache data. Backup/restore tooling does not guarantee hosted disk durability. This is a trusted shared workspace, not a multi-tenant system.

Source snapshots before both review passes are retained under ignored .backups. No legacy Python source or archived filings were discarded.
# On-demand SEC reports

Agentic research now includes a scope-checked task plan, targeted hybrid/lexical/dense
tools, task-linked execution receipts, financial evidence tables and explicit missing
filing recovery. See [agentic evaluation instructions](evaluation/AGENTIC_EVALS.md)
for paired single-pass/agentic trials and manual review scoring.

In **Documents → Fetch reports from the SEC**, enter company names or tickers
separated by commas, semicolons, or new lines (for example, `Tesla, AAPL, Microsoft`).
Choose **Use the latest** or **Last N quarters**, enter a count, and click
**Fetch & process**. Ambiguous names return ticker choices; enter the intended
ticker and submit again. Repeated imports reuse identical reports.

Set `SEC_USER_AGENT=DeepWhiz contact@your-domain.com` in this application's `.env`
using your actual contact email. Configure the selected AI provider and Pinecone
as for uploads. SEC retrieval needs no SEC API key. The import downloads reports
before responding; keep the page open until it confirms they are queued. Parsing
and indexing then run in the existing background worker.

The selection uses current SEC submissions, not the experiment's historical cutoff.
It includes 10-Q and 10-K reports, one per reporting period; a 10-K supplies the
year-end period and remains an annual report. Amendments, 8-K earnings releases,
and foreign issuer forms are excluded. Fewer available reports produce a warning.
Limits: 8 companies, 12 periods per company, 32 reports and 100 MB per request,
20 MB per report. A failed download cancels the batch; retry after fixing the error.

API: `POST /api/sec/import` with
`{"companies":"Tesla, AAPL","periods":4}` (or `"periods":"latest"`).
Uses the same authentication and provider header as uploads. Returns resolved
companies, filing source URLs, warnings, duplicate results, and an ingestion job.


Answer repair has its own `ANSWER_REPAIR_TIMEOUT_SECONDS` setting (default 12 seconds), separate from `GENERATION_TIMEOUT_SECONDS` (30 seconds). A repair-stage timeout returns independently checked excerpts or calculations where available, with validation gaps and a timeout warning; otherwise it returns an explicit insufficient-evidence result. Request cancellation and the overall research deadline still stop the request. Valid complete calendar dates are excluded from financial-value checks; adjacent amounts and invalid dates remain checked.


Execution paths for new research responses include expandable intermediate outputs: resolved scope, planned tasks, retrieved excerpts, selected actions, unvalidated drafts, repair outputs, and validation results. The delivered response appears last. Evidence snapshots show up to 12 sources with 1,800-character excerpts; long drafts are capped at 12,000 characters, with truncation identified. These are structured outputs, not hidden model reasoning. Output snapshots are excluded from normal application log messages.

Rollout status and implemented hardening are recorded in [the functionality and market-readiness review](docs/CODE_REVIEW.md). This remains a trusted shared workspace. Production requires a strong workspace secret, HTTPS, verified durable storage and restore procedures; public multi-customer use additionally requires individual identity and tenant isolation. Liveness is `/api/health`; configuration/storage readiness is `/api/ready`.


Library protection on Replit is available through verified App Storage checkpoints.
Before republishing an existing instance, preserve its registry, original sources,
retrieval artifacts and embedding cache; Pinecone alone does not restore its library.
Follow the [preservation and restore procedure](docs/RUNBOOK.md#preserve-the-deployed-library-before-republishing)
to seed a checkpoint and enable `FINWHIZ_CLOUD_LIBRARY=true`. The Documents badge
shows whether protection is configured, pending, failed or verified. The restore path
preserves existing namespace/vector identities and does not re-embed documents.

The seven Tesla questions were also exercised through FastAPI with the configured
Qwen model and the recovered deployment corpus. See the [live smoke-test record](evaluation/results/tesla-qwen-smoke-20261025.json)
for answers, timings and limitations; these results do not imply the published app
has already been redeployed.
