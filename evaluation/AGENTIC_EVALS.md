# DeepWhiz agentic RAG evaluations

The suite compares the same indexed corpus, provider, model, semantic chunking and
reranking with agent planning/tools enabled versus single-pass RAG. Each case runs
in both conditions; order alternates to reduce ordering bias. Fresh provider clients
prevent cross-condition query-embedding cache reuse. The baseline retains financial
validation and deterministic calculation safeguards.

## Windows launcher for the current workspace

This launcher uses deepwhiz/.venv if available, otherwise the existing sibling
finwhiz/.venv. It does not modify that environment or the original application's data.
Run from deepwhiz:

```powershell
node scripts/evaluate-agentic.mjs
node scripts/evaluate-agentic.mjs --apply --provider nebius --repetitions 2 --to artifacts/agent-evals/trial-01
node scripts/evaluate-agentic.mjs --review-run artifacts/agent-evals/trial-01
```

The first command is a no-call preview. Only the second performs live paid calls.
Run the third after filling the manual review file.

## Offline verification

From deepwhiz, with the backend dependencies installed:

```powershell
python -m pytest -c backend/pyproject.toml backend/tests/test_agentic.py backend/tests/test_task_planning.py backend/tests/test_execution_path.py backend/tests/test_sec_import.py -q
```

This verifies scripted routing, planner boundary checks, targeted search arguments,
conservative task statuses, failure reporting, and import behavior. It does not
measure financial accuracy on live model output.

## Prepare a live corpus

Use Documents to import and process the required filings using the same AI provider
that you will evaluate. The supplied suite needs Tesla Q1/Q2 2026, Tesla Q2 2025,
Tesla 2025 annual and Q3 year-to-date, plus Apple and Microsoft fiscal Q2 2026.
Import the last 8?12 reporting periods where needed. Check actual fiscal metadata;
company fiscal Q2 is not necessarily the calendar quarter ending June 30.

Edit agentic-suite.json to match the periods you have indexed. Unavailable or future
periods intentionally return insufficient evidence. A missing required corpus does
not count as successful retrieval. Preflight-only trials are recorded separately.

Before executing, add reviewed evidenceLabels to answerable cases, for example:

```json
{"ticker":"TSLA","reportDate":"2026-06-30","contains":["Total revenues"]}
```

Use exact phrases you have checked in the source. Evidence-label recall checks only
source phrase coverage; it is not a claim-support or numerical-accuracy score.
Empty labels yield null recall, never an invented perfect score.

## Preview without network calls

```powershell
python run.py agent-evaluate --queries evaluation/agentic-suite.json
```

## Execute paid model/vector calls

```powershell
python run.py agent-evaluate --queries evaluation/agentic-suite.json --provider nebius --repetitions 2 --to artifacts/agent-evals/trial-01 --apply
```

Use openai instead of nebius if that is your configured/indexed provider. Repetitions
range from 1 to 5. Each output directory must be new. Settings are read from this
application's .env; the evaluation neither downloads nor indexes documents. Queries
and short-lived model caches are read-only with respect to the indexed corpus.

Outputs:

- manifest.json: model configuration, source identities, prompt hashes and suite hash.
- trials.jsonl: every result or failure, full execution path and per-case checks.
- summary.json: status accuracy, failures, scope violations, median answer latency,
  model calls, vector queries and reported token counts for each condition.
- manual-review.json: fill claim support, numeric accuracy, period/unit accuracy,
  completeness and reviewer identity for each successful trial.

Reviewers should score each manual dimension from 0 to 1 using the same rubric in
both conditions. Check every numerical claim against its cited source, verify fiscal
period and quarterly/YTD/annual basis, and mark omissions even when the response is
fluent. Keep failed trials in the outcome denominator. Do not treat a needs-clarification
answer to an answerable question as correct. Report abstention separately.

## What to demonstrate

1. A simple question where the agent stops without unnecessary tools.
2. A comparison where the planner splits company/period/metric tasks and additional
   targeted retrieval supplies missing evidence.
3. A failed search followed by a different retrieval mode or table read.
4. A missing filing that offers explicit Fetch missing reports & continue.
5. A question where the agent abstains because evidence or time is insufficient.

Compare completeness and reviewed accuracy against token/call/latency overhead.
A larger number of steps is not itself an improvement. No measured live improvement
is claimed until these evaluations have been executed and reviewed.

After filling manual-review.json, aggregate the paired scores with:

```powershell
python run.py agent-score-reviews --run artifacts/agent-evals/trial-01
```

This writes review-summary.json and agentic-minus-single-pass deltas. Incomplete
reviews remain pending; failed trials contribute zero rather than disappearing.
For optional extracted-value checks, add expectedFacts to a case. Each entry needs
ticker, period, metric, value and unit, with an optional absolute tolerance. For
example, use a manually verified value for revenue and period 2026-Q2 in millions.
The resulting extractedFactRecall measures returned evidence facts, not the truth
of every sentence in the generated answer. Human claim review remains required.

Optional browser checks (requires npm ci, installed Microsoft Edge and the running
local app; these intercept API calls and do not call paid providers):

```powershell
node scripts/check-agentic-ui.mjs
node scripts/check-agent-recovery.mjs
```

Set FINWHIZ_BROWSER_BASE to use another local server URL.


## Tesla Q2 2026 demonstration questions

The seven questions and source-checked expected results are in
`evaluation/tesla-q2-2026.json`. Run the offline regression from the repository root:

```sh
python -m pytest backend/tests/test_tesla_demo.py -q
```

The 14 cases exercise statement calculations and the complete research graph against
the original filing artifacts in the bundled recovery snapshot. No API keys or paid
provider calls are needed. Six numerical questions must complete from local statement
evidence; the causal question checks retrieval of the disclosed management discussion
and uses scripted synthesis. This verifies evidence delivery and validation, not live
model answer quality. The deployed app still needs an indexed Tesla Q2 2026 filing
under its selected provider; passing these tests does not upload the fixture into the
application's library.
