# DeepWhiz Financial Document Intelligence Submission

**Week 2 Project: Financial Document Intelligence Pipeline RAG**

**Evaluation date:** October 19, 2026

**Code repository:** https://github.com/sudeep39/DeepWhiz

DeepWhiz is a working financial research application that answers questions over uploaded SEC filings using table-aware hybrid retrieval. This submission combines the RAG framework, implementation decisions, measured chunking comparison, reranking impact analysis, and lessons from a balanced 16-trial pilot.

## Part 1 The RAG Framework

### The primer My one-liner

My RAG app helps investment analysts answer historical financial performance and comparison questions from user-uploaded SEC 10-K and 10-Q filings in a web research interface, targeting **95% faithfulness, 90% answer relevance, and p95 response latency of 15 seconds or less**.

These are acceptance targets. A live pilot now provides measured retrieval, answer-status, and latency results below; independent faithfulness and answer-relevance scores have not been established.

| Field | Response |
|---|---|
| Use case | DeepWhiz helps investment analysts investigate revenue, profitability, cash flow, and disclosed business drivers through a conversational web interface. Answers include source citations and server-computed financial calculations. |
| Corpus | The application accepts English SEC 10-K and 10-Q filings in HTML, readable PDF, and TXT formats, with issuer filings published through SEC EDGAR serving as the source of truth. The indexed comparison corpus contains 16 filings across Tesla and NVIDIA; hands-on development testing also examined Tesla's June 30, 2026 10-Q. |
| Ingestion and cleaning | Users upload filings through the browser, and background workers extract document metadata, remove hidden HTML and non-content markup, and normalize readable text. Table extraction preserves rows, column spans, and reporting-period headers so financial values retain their context. |
| Ingestion and freshness | New filings enter through explicit uploads, with a weekly operator review policy and an optional SEC freshness check to identify newer filings or amendments. Configured thresholds are a review every seven days and a stale-period flag after 150 days; automatic scheduled ingestion is not enabled. |
| Chunking and embedding | Fixed narrative chunks use roughly 700 tokens with 100-token overlap; semantic chunks use changes in sentence-embedding similarity, with bounded sizes and overlap. Both strategies use row-aware table splitting with repeated headers and Nebius Qwen/Qwen3-Embedding-8B embeddings at 4,096 dimensions. |
| Retrieve | Pinecone dense search retrieves the top five matches per company and reporting period, while application-side BM25 retrieves the top 20 keyword matches; reciprocal rank fusion merges the lists. Optional language-model reranking prioritizes combined candidates, and a bounded answer context uses up to 16 chunks, with eligible table chunks expanded to complete parent tables. |

**When evidence is insufficient:** DeepWhiz requests clarification for ambiguous questions and returns an insufficient-evidence response when selected filings cannot support an answer. Source-ID validation, numeric checks, signed cash-flow extraction for supported adjustment questions, and server-side calculations reduce citation, accounting-sign, and intermediate-arithmetic errors; they do not guarantee factual entailment.

## Implementation and prompts

DeepWhiz uses a JavaScript frontend, a Node.js API, background ingestion workers, Pinecone, and a local filesystem store. This is a custom code implementation rather than a LangChain/LangGraph or n8n workflow. Nebius Qwen/Qwen3-30B-A3B-Instruct-2507 supplies reranking and answer generation for this evaluation; OpenAI is also supported through a separate compatible index.

The ingestion pipeline validates files, extracts issuer/form/period metadata, cleans readable content, builds section and table parents, chunks documents, embeds text, and indexes vectors with source metadata. Pinecone stores vectors and metadata; the server's filesystem stores uploads, corpus/job records, BM25 chunks, parent tables, and caches. Consequently, deployments sharing Pinecone do not automatically share their corpus listings or lexical state.

The generation instructions require answers to use supplied evidence, cite source IDs, distinguish quarterly, year-to-date and annual periods, preserve accounting signs, and request clarification rather than guess. Calculation instructions pass original filing values to server operations, including revenue-minus-cost and gross margins with or without excluded revenue. Reranking instructions prioritize relevant evidence and treat source text as untrusted; the implementation handles incomplete ID rankings without discarding omitted candidates.

## Dataset and chunking comparison design

The evaluation corpus contains **16 checksum-verified English SEC HTML filings**, eight reporting endpoints each for NVIDIA and Tesla. Each company contributes two 10-Ks and six 10-Qs. A 10-K reports annual totals; a standalone Q4 amount requires subtracting the matching nine-month amount rather than treating the annual amount as Q4.

| Strategy | Narrative method | Table method | Indexed chunks |
|---|---|---|---:|
| Fixed | Approximately 700-token windows, less context-prefix overhead, with 100-token overlap | Row-aware splitting with repeated headers | 2,924 |
| Semantic | Sentence-unit embeddings and adjacent similarity changes, with bounded size and overlap | Same row-aware splitter | 3,101 |

Semantic chunking produced approximately **6.1% more chunks**. All 48 evidence labels from the original 32-question suite occur somewhere in both prepared chunk sets; this establishes evidence availability, not retrieval quality. Because tables use the same splitter, this pilot primarily contrasts narrative segmentation around a shared table-preserving approach.

## Live evaluation scope

To meet the reduced evaluation scope, the run used **four unique questions across four conditions: 16 trials total**. The original plan was 32 questions across four conditions, or 128 trials; that full run was not completed.

| Question | Financial period | Expected source revenue |
|---|---|---:|
| What total revenue does NVIDIA report for the three-month quarter ended July 26, 2026? | Quarterly | $96,221 million |
| What total revenue does NVIDIA report for the three-month quarter ended April 26, 2026? | Quarterly | $81,615 million |
| What total revenue does NVIDIA report for the full fiscal year ended January 25, 2026? | Annual | $215,938 million |
| What total revenue does NVIDIA report for the three-month quarter ended October 26, 2025? | Quarterly | $57,006 million |

Each original question also requested currency, scale, and a distinction between quarterly and year-to-date values. The pilot took the first four frozen questions without changing their labels or selecting them by outcome. Trials were serial, condition order rotated by question, and each question/condition had one observation. Eleven previously completed trials retained their original measured latencies; five additional trials completed the balanced sample.

The corpus includes both companies, but **all four pilot questions concern NVIDIA revenue**. This sample does not establish performance on Tesla questions, narrative risk factors, accounting signs, multi-company comparisons, or refusal behavior.

## Measured chunking comparison

| Condition | Trials | Failures | Strict Recall@5 | Strict MRR@5 | Status accuracy | Mean latency | Observed p95 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Fixed + hybrid | 4 | 0 | 0.0% | 0.000 | 75.0% | 12.90 s | 19.63 s |
| Fixed + hybrid + reranking | 4 | 0 | 0.0% | 0.000 | 100.0% | 23.59 s | 42.81 s |
| Semantic + hybrid | 4 | 0 | 0.0% | 0.000 | 75.0% | 9.58 s | 11.43 s |
| Semantic + hybrid + reranking | 4 | 0 | 0.0% | 0.000 | 100.0% | 23.54 s | 56.30 s |

Status accuracy measures expected answered/clarification/insufficient-evidence status, not factual accuracy or faithfulness. All four selected questions expect answered. There were 0 request/validation failures and 2 unexpected answer statuses. With four observations per condition, the reported empirical p95 is simply the maximum of those four latencies; it is not a stable population latency estimate.

### Evidence-label limitation and separate diagnostic

All frozen primary Recall@5 and MRR@5 scores were zero. The gold labels require the exact phrase **Total revenue** together with the amount, while relevant retrieved income-statement rows use **Revenue** with the same amount, such as `Revenue | $ | 96,221`. The literal evaluator therefore misses equivalent evidence passages; the primary results have been preserved rather than rewritten to improve the scores.

A separately labeled **post-hoc diagnostic** relaxed only the phrase Total revenue to Revenue, retaining issuer, reporting period, and exact numeric value, and rescored the unchanged rankings:

| Condition | Alias-relaxed Recall@5| Alias-relaxed MRR@5 |
|---|---:|---:|
| Fixed + hybrid | 75.0% | 0.625 |
| Fixed + hybrid + reranking | 100.0% | 1.000 |
| Semantic + hybrid | 75.0% | 0.625 |
| Semantic + hybrid + reranking | 100.0% | 1.000 |

These exploratory scores are not replacement acceptance metrics or faithfulness measurements. Future labels should identify equivalent evidence alternatives and row/column context before a new evaluation begins.

## Reranking impact analysis

| Strategy | Expected-status accuracy without reranking | With reranking | Mean end-to-end latency added |
|---|---:|---:|---:|
| Fixed | 75% | 100% | 10.70 seconds |
| Semantic | 75% | 100% | 13.96 seconds |

For the annual-revenue question, both baseline conditions returned insufficient evidence, while both reranked conditions returned an annual revenue answer. This accounts for the 25-percentage-point improvement in expected-status accuracy for each chunker. It is an observed status improvement on one question, not proof that all generated claims are faithful.

The post-hoc alias diagnostic also rose from 75% to 100% Recall@5 and from 0.625 to 1.000 MRR@5 for each chunker. The frozen strict metrics show zero improvement because of their literal-label limitation.

### Paired trial timings

| Strategy | Query |Baseline seconds | Reranked seconds | Latency delta | Baseline status | Reranked status |
|---|---|---:|---:|---:|---|---|
| fixed | B01 | 19.63 | 29.90 | +10.27 s | answered | answered |
| fixed | B02 | 12.46 | 11.39 | -1.06 s | answered | answered |
| fixed | B03 | 6.12 | 10.26 | +4.14 s | insufficient_evidence | answered |
| fixed | B04 | 13.37 | 42.81 | +29.44 s | answered | answered |
| semantic | B01 | 9.97 | 56.30 | +46.33 s | answered | answered |
| semantic | B02 | 10.90 | 9.69 | -1.21 s | answered | answered |
| semantic | B03 | 6.02 | 10.80 | +4.78 s | insufficient_evidence | answered |
| semantic | B04 | 11.43 | 17.36 | +5.93 s | answered | answered |

These are end-to-end latency differences, including retrieval, generation, and any validation repair, rather than isolated reranker execution times. A faster individual reranked trial does not show that reranking reduces computation; provider timing and generation variability can outweigh the extra step in a single observation.

## Conclusions and acceptance status

The pilot demonstrates a functioning financial RAG pipeline, two implemented chunking strategies, and a reproducible four-condition evaluation. Semantic retrieval without reranking had the lowest observed mean latency at **9.58 seconds**, but neither chunker can be declared superior from four table-heavy questions. Both had 75% expected-status accuracy without reranking and 100% with reranking.

Reranking recovered an answer for the annual-revenue question but increased mean end-to-end latency. Observed empirical p95 values were 19.63 seconds for fixed baseline, 42.81 seconds for fixed reranked, 11.43 seconds for semantic baseline, and 56.30 seconds for semantic reranked. Only the semantic baseline's observed p95 was below the 15-second target in this small sample; that does not establish a production latency guarantee.

Independent faithfulness, citation entailment, numeric accuracy, and answer-relevance review remain pending. The sample is small, contains development questions, has no repeated trials, and lacks narrative/refusal coverage. Status accuracy is not factual answer accuracy. Rounded-billions answers described using a millions scale, and claims that a filing lacks data when only the retrieved context lacks it, require explicit review.

## Iterations and lessons

- Preserving table headers and periods matters as much as semantic similarity: quarterly, annual, and year-to-date values can share the same row labels.
- Model-produced arithmetic needs server validation. Development fixes added checks for operation input counts, denominators, intermediate values, and margin comparisons rendered from validated results.
- Accounting signs need explicit treatment. Parenthesized cash-flow adjustments must be identified as deductions, not reported only as unsigned magnitudes.
- Evaluation labels can undercount valid evidence. The Revenue versus Total revenue mismatch exposed a weakness in exact-phrase scoring, and the original results were retained alongside a transparent diagnostic.
- Reranking is a measured quality/latency trade-off. The pilot improved answer status for one question while increasing average response time.
- Operational constraints affect reproducibility. Ingestion initially exhausted disk space; lossless embedding-cache compression recovered approximately 629 MB and allowed ingestion to resume without changing vector values. All **40 implementation tests passed** after that change; these are not retrieval-quality scores.

Codex assisted with implementation, regression tests, source checks, benchmark execution, and report preparation. Gold labels identify Codex source review; independent human review has not been completed.

## Reproducibility and supporting assets

- Code: https://github.com/sudeep39/DeepWhiz
- Corpus SHA-256: `67e4ff2854b22e9d35dadbc10e898f84ae698214ec71324375ba214eca1796c8`
- Four-question suite SHA-256: `96ea8d01aee04a194c82136ff3198142afef321ff4a9d7503454688d1e59ef3f`
- Evaluation source revision: `ce241dc5b28c111f3fe4b2f401d816598df5182e`; PREPARATION.json also records source-file hashes, including the storage compression change.
- Local raw evidence and review template: `artifacts/evaluation/pilot-16/`, including results.json, trials.jsonl, provenance.json, PREPARATION.json, label-sensitivity.json, TRIALS.csv, and manual-review.json.
- Supporting report package: `artifacts/evaluation/finwhiz-pilot-16-report.zip`.

This consolidated submission contains the framework, chunking comparison, and reranking impact analysis. A demo recording of five minutes or less must be supplied separately for the course submission. Generated raw evaluation artifacts and the Word export remain local and are excluded from Git.
