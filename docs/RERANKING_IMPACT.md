# DeepWhiz: reranking impact analysis

Measured 16-trial pilot, October 19, 2026 (America/New_York). Four unique questions, four conditions, one observation per question/condition. This is a reduced pilot, not the planned 128-trial evaluation.

## Paired method

For each chunker, compare the same four questions with reranking disabled and enabled. Positive latency deltas mean extra elapsed time; lower latency is preferable. These are end-to-end deltas, including generation and any validation repair, not isolated reranker execution times.

| Strategy | Query | Baseline seconds | Reranked seconds | Latency delta | Baseline status | Reranked status |
|---|---|---:|---:|---:|---|---|
| fixed | B01 | 19.63 | 29.90 | +10.27 s | answered | answered |
| fixed | B02 | 12.46 | 11.39 | -1.06 s | answered | answered |
| fixed | B03 | 6.12 | 10.26 | +4.14 s | insufficient_evidence | answered |
| fixed | B04 | 13.37 | 42.81 | +29.44 s | answered | answered |
| semantic | B01 | 9.97 | 56.30 | +46.33 s | answered | answered |
| semantic | B02 | 10.90 | 9.69 | -1.21 s | answered | answered |
| semantic | B03 | 6.02 | 10.80 | +4.78 s | insufficient_evidence | answered |
| semantic | B04 | 11.43 | 17.36 | +5.93 s | answered | answered |

## Aggregate paired changes

| Strategy | Pairs | Strict Recall@5 delta | Strict MRR@5 delta | Mean latency delta | Expected-status wins / ties / losses |
|---|---:|---:|---:|---:|---|
| fixed | 4 | +0.00 pp | +0.00 | +10.70 s | 1 / 3 / 0 |
| semantic | 4 | +0.00 pp | +0.00 | +13.96 s | 1 / 3 / 0 |

## Interpretation

Expected-status accuracy rose from 75% to 100% for both chunkers (+25 percentage points), driven by the annual-revenue question. Mean end-to-end latency increased by 10.70 seconds for fixed chunking and 13.96 seconds for semantic chunking. The separately labeled, post-hoc alias diagnostic rose from 75% to 100% Recall@5 and 0.625 to 1.000 MRR@5 for both chunkers; these exploratory scores are not the frozen primary metrics or faithfulness measurements.

The primary literal-label metric undercounts equivalent Revenue rows because the gold phrase is Total revenue; zero primary improvement must not be interpreted as proof that the reranker had no useful effect. The comparison report includes a separately labeled post-hoc alias diagnostic, using the unchanged rankings.

In the observed semantic B03 annual-revenue trial, the baseline answered insufficient_evidence, while the reranked condition returned an annual revenue answer. This is an observed expected-status improvement on one question, not proof of a faithful answer: the wording and its cited source still require review.

Reranking is an optional quality/latency trade-off. This four-question pilot is too small and too table-heavy to justify enabling or disabling it universally. Do not claim a percentage faithfulness improvement, statistical significance, or general semantic-chunking superiority from these results.

## Limitations

One trial per condition/question; no independent human review; four revenue questions for one issuer; development-set selection; cloud/model timing variability; all selected questions expect an answer. The original planned 32-question evaluation was reduced at the user's request. Primary results, all raw rankings and answer text are retained for audit.
