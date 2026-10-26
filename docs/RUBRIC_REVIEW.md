> Historical pre-implementation review. For the current status see [RUBRIC_IMPLEMENTATION.md](RUBRIC_IMPLEMENTATION.md) and [RUNBOOK.md](RUNBOOK.md).

# Week 2 rubric review

Reviewed against the nine-page **Week 2 Project Handout ( Oct 2026 ).pdf**, supplied at `C:/Users/sudeep/Desktop/Week 2 Project Handout ( Oct 2026 ).pdf`. Review date: October 18, 2026, America/New_York. Source is the original local PDF, not a reference solution. This document supersedes earlier readiness claims in the README.

## Scope and verdict

DeepWhiz is a suitable Project 2 implementation: Financial Document Intelligence, table-aware hybrid retrieval, for investment analysts. NVIDIA and Tesla across eight reported fiscal periods each provide multi-document, table, temporal, and comparison questions. Fiscal quarter ends differ between issuers. Each company contributes two 10-Ks and six 10-Qs: a 10-K represents a year, not a standalone fourth quarter. Q4 requires a supported annual-minus-nine-month calculation.

The application is a working prototype with offline integration coverage. **The submission is incomplete until real retrieval comparisons and reranking-impact measurements are produced.** There are no established faithfulness, relevance, or latency scores. No production-readiness claim is made.

## Required framework and deliverables

| Handout expectation | Implementation/evidence | Status and remaining work |
|---|---|---|
| Define user, question type, corpus and surface (pp. 2–3) | Investment analysts; historical financial and risk questions; SEC 10-K/10-Q; JavaScript web application | Implemented. Uploaded corpus is dynamic; the two issuers are an evaluation dataset, not a UI allowlist. |
| Identify corpus count, format, language, source of truth | Active experiment: 16 English SEC HTML filings; original 10-issuer collection preserved; SEC URLs/accessions/checksums in data manifests | Implemented. PDF/TXT upload support is additional; scanned PDFs require external OCR. |
| Ingestion and cleaning | Background worker, visible-text extraction, hidden-resource removal, table row/column spans, metadata extraction, token limits and retries | Implemented heuristically. Complex tables and metadata still need human inspection. |
| Freshness cadence and SLA | Manual SEC refresh/download and explicit uploaded-source dates | Partial. No scheduled ingestion or freshness SLA monitoring. Proposal: manual refresh weekly, new uploads research-ready within 30 minutes for a supported batch; unmeasured target, not a guarantee. |
| Chunking/model justification | Fixed token windows versus embedding-distance semantic segmentation; common table-aware splitting; same embedding model/index dimensions | Implemented. Model is text-embedding-3-large, 1,536 dimensions, cosine. Parameters need quality evaluation rather than an “optimal” claim. |
| Retrieval: store, dense/sparse/hybrid and top-k | Pinecone dense top-5 per company/period plus local BM25 top-20; reciprocal-rank fusion to 80; candidate diversification; LLM rerank; max 16 evidence chunks | Implemented. Hybrid spans Pinecone and local lexical files. Persistent local state is necessary; Pinecone is not the only storage dependency. |
| Table-aware hybrid for investment analysts (p. 4) | Tables preserve headers, repeat context, and expand selected evidence with source table; numeric citations, deterministic calculations and charts | Implemented with limitations. Not an XBRL accounting engine; literal-value validation cannot establish that a metric or scale is semantically correct. |
| Fixed versus semantic on the SAME query set (Project 2, pp. 5–6) | Both runtime strategies; `scripts/evaluate.mjs`; 24-query draft | Tooling provided; **measured comparison report missing**. Evidence labels remain unreviewed. |
| Add reranking and MEASURE improvement | Rerank toggle and validated candidate ID permutations; evaluation runs each strategy with rerank on/off | Reranking implemented; **impact analysis missing**. Improvement must be measured, including regressions. |
| Grounded generation with citations | Structured answer schema, inline citations, checked IDs, source excerpts/downloads | Implemented checks, not proof of faithfulness. Human claim-level audit required. |
| “Could not find evidence” fallback | Insufficient-evidence response and generation instructions; reject unsupported chart/calculation literals | Partial: refusal calibration, ambiguous questions and adversarial documents not measured. No dedicated clarification response state. |
| Faithfulness, relevance target and latency ceiling | Proposed: ≥95% supported factual claims, ≥80% mean evidence recall@5, p95 end-to-end ≤15 seconds | Targets only; agree definitions, label evidence, run benchmark and report measured values. No target was specified numerically by the handout. |
| Working demo + GitHub/ZIP + comparison report | Local app, standalone source exporter, offline tests, source backup | Local prototype ready for review; hosted demo, real quality report and publication pending. |
| Google Doc overview, datasets, prompts, iterations, learnings (p. 8) | Repo README, review docs, dataset manifests, prompts in retrieval.mjs; evaluation guide | Repo material available. Google Doc and personal learnings still need authoring/submission. |
| ≤5-minute live demo: app, AI coding tools, results (p. 8) | Suggested flow below | Recording not produced. Show genuine measured results only. |

## Analyst query-pattern coverage

The handout names a retrieval pattern, not a mandatory exhaustive financial query list. The following acceptance matrix expands that pattern for this application. Draft questions are in `evaluation/queries.json`; none is a validated gold answer.

| Pattern | Current behavior | Verification still required |
|---|---|---|
| Single-filing numeric lookup | Table-aware evidence, citations | Correct row, column, period and unit |
| Exact terms and footnotes | BM25 plus semantic retrieval | Export controls, regulatory credits, stock compensation |
| Quarter versus YTD versus annual | Prompt distinguishes periods | Disambiguation and numerical correctness |
| Q4 derivation | Cited subtraction supported | Annual and matching nine-month inputs; refusal if missing |
| Eight-period trends | Company/period diversification, chart | Coverage of every requested period; annual-quarter conversion |
| Cross-company comparisons | Dynamic compare scope, per-company evidence | Fiscal calendar differences, compatible metrics/scales |
| Risk narrative comparison | Dense+lexical+reranking | Evidence relevance and changes over time |
| Ratios/growth/margins | Server arithmetic, explicit percentage operation | Input meaning, currency, scale, negative baselines |
| Ambiguous query | Model instructions only | Clarification UX not explicitly implemented |
| Missing period/corpus, predictions | Insufficient-evidence instructions | Refusal accuracy; no unsupported financial advice |
| Follow-up questions | Last four history messages passed to generation | Retrieval embeds only current question; pronoun-only follow-ups may fail |
| Malicious filing instructions | Documents treated as untrusted, constrained output and safe rendering | Prompt-injection red-team testing; no absolute guarantee |

## Requirements that do not apply

LangChain/LangGraph and n8n are alternative build tracks; this custom JavaScript implementation is permitted. Nebius/model suggestions are not fixed requirements. Project 1's 15-query specification, Project 3's graph/node counts, and Project 4's multilingual evaluation/human escalation are separate projects. They should not be claimed as unmet Project 2 requirements. SEC filings, insurance claims, loans and transcripts are example corpora; implementing all is unnecessary.

## Submission sequence

1. Load the same 16 verified filings with both strategies; record corpus checksum, model, dimensions and parameters.
2. Human-label an answerable subset of the draft queries against source passages, not system output; retain negative and ambiguous cases for separate end-to-end evaluation.
3. Run all four conditions and publish per-query and aggregate recall@5/MRR@5, rerank deltas, latency and failures. Review answer claims, citations, units, calculations and refusals manually.
4. Write the comparison/impact report, including poor results and limitations. Create the Google Doc and record the ≤5-minute demo: upload/status, grounded lookup, multi-period comparison, calculation/chart, missing-evidence response, measured comparison.
5. Review the isolated source export, create the separate GitHub repository, and publish only when requested. Configure deployment secrets and durable storage before using a hosted live corpus.
