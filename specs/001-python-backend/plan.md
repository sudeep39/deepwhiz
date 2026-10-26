# Python backend migration technical plan

Status: Implemented locally; hosted release gates pending. Requirements: [spec.md](spec.md). Execution: [tasks.md](tasks.md).

## Architecture

Use `backend/pyproject.toml`, a dependency lockfile, `backend/src/finwhiz/` and `backend/tests/`. Keep existing browser assets at their present paths.

| Package | Responsibility |
| --- | --- |
| `api` and `schemas` | FastAPI routes, compatibility errors, middleware and typed public contracts |
| `providers` | Explicit provider snapshots, LangChain chat/embedding adapters, Pinecone adapter and resilience |
| `ingestion` | Parsers, metadata, tokenization, table-aware chunkers, graph and worker |
| `retrieval` | Dense search, BM25, RRF, diversity, reranking and parent expansion |
| `research` and `prompts` | Research graph, structured generation, validation and bounded repair |
| `finance` | Pure arithmetic, cash-flow extraction and period safeguards |
| `storage` | Legacy reader, file/cache repository, job registry, checkpoint integration and migration |
| `evaluation`, `operations`, `cli` | Benchmark/reporting, freshness, recovery and administrative commands |

Routes call application services; graph nodes call services or domain functions. Domain functions do not import HTTP handlers or require a live model. Public response serialization follows legacy contracts explicitly, including existing irregularities unless a difference is reviewed.

## Framework use

LangChain supplies document, model, embedding and structured-output interfaces. Select provider-specific adapters or a narrow compatibility adapter after testing the exact configured models. Verify preservation of query instructions, dimensions and metadata. Use the Pinecone SDK directly where the LangChain wrapper cannot retain existing behavior. Keep current BM25/RRF semantics initially.

LangGraph coordinates ingestion and research with typed state and conditional edges. Do not introduce an autonomous agent loop. Prompts are versioned files and schema validation remains local even with provider-native structured output.

```text
Ingestion:
validate -> parse -> metadata -> parents -> chunk -> embed -> upsert
         -> verify search visibility -> publish ready
Research:
policy -> dense + lexical retrieval -> fusion/diversity -> optional rerank
       -> evidence expansion -> deterministic financial path or generation
       -> validate -> at most one repair -> validate -> response
```

Each stage has explicit failure routes. Persist artifact references and batch progress rather than full source files/vectors in graph state. Derive stable graph run IDs from job identity and workflow version.

## Persistence and recovery

Retain hashed-key filesystem reads, JSON/gzip handling and original sources. Preserve legacy hashes for lookups; specify canonical bytes for new cross-language hashes. A new serializer must not silently invalidate every existing cache key.

SQLite registry owns transactional queue claims under an exclusive runtime owner, job transitions and progress; checkpoints own graph continuation. Job registry and graph checkpoints use separate database files. Local contention and checkpoint replay are covered by tests; hosted disk durability remains pending. Persist provider snapshots with jobs; workers never inherit another request's selected provider.

Migration is dry-run first: back up a quiescent runtime, inventory/hash artifacts, validate schemas and references, import into a separate destination, compare readback, then rehearse restore. Maintain an explicit legacy-to-new identifier map where required. Never rewrite a legacy parser version label onto changed Python output.

External effects must tolerate replay: stable vector IDs, compatible embedding cache keys and verified upsert acknowledgments. A crash after an upsert may repeat the same idempotent write. Publishing ready requires both strategies and visibility checks to succeed; a checkpoint alone cannot establish readiness.

## Increment order

I01 baseline -> I02 API/storage foundation -> I03 provider adapters -> I04 ingestion -> I05 research -> I06 tools -> I07 cutover. Domain-only work can be prepared earlier, but each increment must meet its dependencies before acceptance. Every increment should use a separate reviewable change set.

## Verification

Use pytest for domain, contract, storage and worker tests; fixtures replace remote providers. Retain existing JavaScript/browser tests as a reference and adapt server startup to run the same browser checks against Python. Add meaningful regression cases around financial evidence, concurrent provider isolation and crash boundaries.

Freeze source and suite hashes, prompts, model settings, retrieval budgets and parser versions for comparisons. Keep parser-boundary changes separate from runtime-only comparisons. Run paid smoke/benchmark checks only within the agreed budget. No historical test counts or quality claims substitute for fresh evidence.

## Framework options

Reviewed 2026-10-21. These are three shortlisted options, not a measured popularity ranking.

| Tool | Workflow and fit | Assessment for DeepWhiz |
| --- | --- | --- |
| [GitHub Spec Kit](https://github.com/github/spec-kit) | Specification, clarification, plan, tasks and consistency analysis | Recommended for explicit migration requirements, traceability and review gates |
| [OpenSpec](https://github.com/Fission-AI/OpenSpec) | Change proposals, design/spec deltas, tasks and archived completed changes | Good alternative for lightweight continuing changes to an existing codebase |
| [BMAD Method](https://github.com/bmad-code-org/BMAD-METHOD) | Broader AI-assisted product planning, architecture and story workflows | Useful for a larger product effort; more process than this bounded migration needs |

These files borrow the spec/plan/tasks separation without claiming generated framework compliance. After selection, inspect the chosen tool's current initialization behavior and apply it within `finwhiz`; do not overwrite existing documentation or alter sibling projects. No installation is part of this documentation increment.

## Implementation record

See [operation, verification and rollback](../../docs/PYTHON_MIGRATION.md). Existing frontend assets remain unchanged. Local data was copied to a separate Python runtime. Hosting configuration is prepared but no deployment was performed. Offline verification was explicitly selected by the user; live quality and hosted durability remain release gates.
