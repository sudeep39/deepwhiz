# Question-aware retrieval scope

DeepWhiz resolves companies and periods before search. The local/frontend company and period selections remain the starting scope. An explicit question can narrow an all-period or multi-company selection; it cannot silently override a conflicting explicit selection. Clarification messages tell you what to select or specify next.

For example, "Tesla Q2 2026" targets TSLA's verified June 30, 2026 filing. "Using Tesla's Q2 2026 filing, what were Q1 and Q2 results?" retains both requested quarters while searching the Q2 filing for quarterly and first-half evidence. The resolver does not calculate financial metrics or guarantee evidence coverage.

Fiscal periods come from SEC `DocumentFiscalYearFocus` and `DocumentFiscalPeriodFocus` facts. They are not inferred from the month of the filing date. For example, an NVIDIA fiscal Q2 filing can end in July of the preceding calendar year. Historical HTML sources are read locally once per file modification and cached; missing/unverified fiscal information requires an exact filing date or clarification. PDF/TXT sources without verified fiscal labels still work with UI/exact-date scoping.

The UI previews scope through `/api/scope`, displays the resolved companies/filing dates during search, and includes the effective scope and requested financial periods with the answer. `/api/chat` resolves independently; clients cannot bypass checks by sending a precomputed scope. Pinecone filters ticker/date pairs, and BM25 uses the same eligible chunks. Reranking and generation receive the resolved scope.

Follow-ups retain prior resolved scope when compatible with current selections. "Q2" without a year asks for one when several years are available. Relative expressions such as "next quarter" or "last year" require explicit periods in this increment. Company recognition uses catalog names/tickers plus curated Tesla/NVIDIA aliases; it does not identify arbitrary unknown company names through an LLM.

## Review fiscal metadata backfill

Newly ingested chunks contain verified `fiscal_year`, `fiscal_quarter`, and `period_type` when available. Existing Pinecone records do not need those fields for the current ticker/date filters to work. Preview additive metadata updates offline:

```powershell
.\.venv\Scripts\python.exe run.py fiscal-metadata-plan > fiscal-metadata-plan.json
```

The plan contains index, namespace, existing vector ID and `setMetadata` fields. It does not contact Pinecone, change local chunks, re-embed anything or apply updates. `--apply` is intentionally rejected: review the authoritative deployment's registry and remote records first. Do not apply the local plan blindly to another instance.

Restart locally with `.\restart.cmd`. In shared-backend mode retain your `-BackendUrl` argument and deploy this code to that backend first.
