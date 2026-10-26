# DeepWhiz: chunking strategy comparison report

Measured 16-trial pilot, October 19, 2026 (America/New_York). Four unique questions, four conditions, one observation per question/condition. This is a reduced pilot, not the planned 128-trial evaluation.

## Pipeline and experimental setup

The working pipeline cleans SEC HTML, preserves table rows and reporting-period headers, compares fixed and semantic narrative chunks, embeds them with Nebius Qwen/Qwen3-Embedding-8B at 4,096 dimensions, and stores vectors in Pinecone index finwhiz-nebius-v1. Retrieval combines dense top-5 per company/period with local BM25 top-20 using reciprocal rank fusion, followed by optional Qwen/Qwen3-30B-A3B-Instruct-2507 reranking and cited answer generation. Both strategies use the same table splitter and parent-table expansion.

The indexed corpus contains 16 checksum-verified NVIDIA/Tesla filings: 2,924 fixed chunks and 3,101 semantic chunks (6.1% more). All 48 labels from the original suite occur somewhere in both prepared chunk sets. To meet the requested reduced scope, this pilot uses the first four original questions without editing the questions or gold labels: three quarterly NVIDIA revenue lookups and one annual NVIDIA revenue lookup. It does not evaluate Tesla answers, narrative risk questions, multi-company comparisons, or refusal behavior.

- **B01:** What total revenue does NVDA report for the three-month quarter ended 2026-07-26? State currency and scale; distinguish quarterly from YTD values. Expected source value: **$96,221 million**.
- **B02:** What total revenue does NVDA report for the three-month quarter ended 2026-04-26? State currency and scale; distinguish quarterly from YTD values. Expected source value: **$81,615 million**.
- **B03:** What total revenue does NVDA report for the full fiscal year ended 2026-01-25? State currency and scale; distinguish quarterly from YTD values. Expected source value: **$215,938 million**.
- **B04:** What total revenue does NVDA report for the three-month quarter ended 2025-10-26? State currency and scale; distinguish quarterly from YTD values. Expected source value: **$57,006 million**.

Trials were serial; condition order rotated by query. Eleven already-completed trials were reused with their original measured latencies; five additional calls completed the balanced sample. No completed trial was selected based on its score. The abandoned 128-trial run's original log is retained separately.

## Measured results

| Condition | Trials | Failures | Strict Recall@5 | Strict MRR@5 | Status accuracy | Mean latency | Observed p95 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Fixed + hybrid | 4 | 0 | 0.0% | 0.000 | 75.0% | 12.90 s | 19.63 s |
| Fixed + hybrid + reranking | 4 | 0 | 0.0% | 0.000 | 100.0% | 23.59 s | 42.81 s |
| Semantic + hybrid | 4 | 0 | 0.0% | 0.000 | 75.0% | 9.58 s | 11.43 s |
| Semantic + hybrid + reranking | 4 | 0 | 0.0% | 0.000 | 100.0% | 23.54 s | 56.30 s |

Status accuracy measures expected answered/clarification/insufficient-evidence status, not factual accuracy or faithfulness. All four selected questions expect answered. There were 0 request/validation failures and 2 unexpected answer statuses. With four observations per condition, the reported empirical p95 is simply the maximum of those four latencies; it is not a stable population latency estimate.

## Evidence-label audit

The strict primary scores are intentionally preserved. Gold labels require the exact phrase **Total revenue** plus the exact amount, while valid retrieved income-statement rows use **Revenue** (for example, Revenue | $ | 96,221). The literal matcher can therefore assign zero to relevant financial evidence. Finding the label somewhere in the corpus did not establish that the label covered every equivalent source passage. Strict scores alone cannot establish that either chunker is better.

For transparency, the following **post-hoc diagnostic only** relaxes Total revenue to Revenue while retaining issuer, reporting period, and exact numeric value, and rescoring the original rankings. Neither ranking nor primary result was modified. This is not a replacement acceptance score or independent relevance judgment.

| Condition | Alias-relaxed Recall@5 | Alias-relaxed MRR@5 |
|---|---:|---:|
| Fixed + hybrid | 75.0% | 0.625 |
| Fixed + hybrid + reranking | 100.0% | 1.000 |
| Semantic + hybrid | 75.0% | 0.625 |
| Semantic + hybrid + reranking | 100.0% | 1.000 |

## Reranking impact

See [the paired reranking analysis](RERANKING_IMPACT.md). Strict retrieval deltas reflect the literal-label limitation above. Interpret answer-status changes and latency separately; do not infer overall answer quality from status alone.

## Conclusions and limitations

This pilot demonstrates an operational financial RAG pipeline and a reproducible four-condition comparison. It does not establish a statistically reliable chunking winner or an overall reranking benefit. Both strategies share table chunking, and these four table-heavy questions provide little evidence about semantic narrative segmentation.

The 15-second p95 goal is a target, not achieved performance certification; compare it with the measured maxima above. Independent faithfulness, citation entailment, numerical correctness and answer-relevance review remain pending. Answers that use rounded billions while describing a millions scale, or claim the source lacks data when only the retrieved context lacks it, need explicit review. These are development-set results, not held-out validation.

The next evaluation should add source-validated row/column-aware relevance judgments or equivalent evidence alternatives, freeze those before rerunning, add narrative and accounting-sign questions, and use repeated trials and a held-out set. Do not retroactively replace this pilot's strict scores.

## Reproducibility and artifacts

- Run directory: artifacts/evaluation/pilot-16
- Corpus SHA-256: 67e4ff2854b22e9d35dadbc10e898f84ae698214ec71324375ba214eca1796c8
- Four-question suite SHA-256: 96ea8d01aee04a194c82136ff3198142afef321ff4a9d7503454688d1e59ef3f
- Git revision: ce241dc5b28c111f3fe4b2f401d816598df5182e; source-file hashes in PREPARATION.json also capture the uncommitted storage compression change.
- Raw results: results.json and trials.jsonl; model/index configuration: provenance.json.
- Independent review template: manual-review.json, intentionally unfilled.
- Original 11-trial log: artifacts/evaluation/2026-10-19T04-33-15-264Z/trials.jsonl.

Ingestion initially exhausted disk space; lossless gzip compression recovered approximately 629 MB of cache storage and ingestion resumed. Embedding values, source documents, chunking rules and retrieval settings did not change. All 40 implementation tests passed after the storage change; those tests are not retrieval-quality scores.
