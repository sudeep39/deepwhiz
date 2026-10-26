# Migration baseline

Captured 2026-10-21 against DeepWhiz commit `a5182753366eb08049f0f8456b444e3ba19cc74f`. The migration spec directory was untracked at capture. `source-manifest.json` records exact relevant source bytes, including the dependency lockfile and benchmark. No credentials or user runtime files were captured.

## Verification

| Command from DeepWhiz root | Result |
| --- | --- |
| `npm.cmd test` | 40 passed, 0 failed |
| `.venv\Scripts\python.exe -m unittest discover -s scripts -p 'test_*.py'` | 16 passed |
| `npm.cmd run test:browser` | Passed desktop/mobile, navigation, uploads, status, filtering, chart, calculation and citation checks |
| `node scripts/capture-migration-baseline.mjs` | Created frozen artifacts |
| `node scripts/capture-migration-baseline.mjs --check` | Exact artifact comparison passed |

PowerShell resolves `npm` to an unsigned `npm.ps1` and rejects it under the current execution policy. Use `npm.cmd`; no policy change is needed. Browser and provider tests use offline mocks. Python tests print synthetic billing/index messages; these do not represent live provider operations.

## Frozen artifacts

- `http.json`: deterministic empty-state and negative HTTP contracts, including security headers and provider selection. Authorization is a synthetic test password.
- `domain.json`: synthetic financial HTML, parser output, parents, fixed/semantic chunk output, BM25/RRF scores and signed arithmetic.
- `embedding-fingerprints.json`: default configurations and exact legacy fingerprints for both providers.
- `source-manifest.json`: source/test/benchmark hashes. Existing tests also cover long semantic splits, Unicode, multipart acceptance, persisted jobs, mocked ingestion/research, citations and recovery.

The new HTML snapshot is deliberately small. Full PDF extraction parity needs a representative readable PDF fixture during T013; this baseline does not claim PDF golden coverage. Model prose and live quality are not frozen by offline fixtures.

Regeneration changes the reference behavior and requires review. Use `--check` for verification. The script uses a dedicated temporary directory, workers disabled and no configured provider keys. It does not load `.env`.

## Runtime format inventory and hazards

The filesystem repository maps logical UTF-8 keys to SHA-256 filenames without extensions. Values are raw bytes or UTF-8 JSON. Keys beginning `embedding/` use gzip for new writes; old plain JSON remains readable. Writes use a random sibling temporary file then rename.

Important logical keys: `state` (jobs array), `upload/{fileId}` (source bytes), `parents/{jobId}/{docId}`, `chunks/{jobId}/{strategy}`, `embedding/{hash}`, and `freshness-check`. Job records retain file keys/details, immutable AI snapshots, timestamps, status, namespaces and ready document summaries. Public job responses strip private file details.

Compatibility hazards:

1. Legacy hashes use compact JavaScript JSON serialization, including array order and number formatting. Python defaults insert spaces and can format numbers differently.
2. Embedding fingerprint hashes include provider, model, dimensions, index and the query-instruction version. Cache keys additionally include model, dimensions and text. Do not combine provider spaces.
3. Chunk IDs include `js-v1`; changed Python parsing must receive a new version rather than masquerading as identical output.
4. Chunks link to parent arrays through document/parent IDs and corpus IDs. Preserving vectors alone does not preserve BM25, source expansion or source downloads.
5. Startup resets processing jobs to queued. New queue/checkpoint ownership must be tested without allowing legacy and Python workers to share the same writable runtime.
6. A provider's full index provenance and search visibility are checked before readiness. A successful upsert is insufficient.
7. Existing FastAPI defaults would change malformed-input errors to HTTP 422 and different bodies. Preserve explicit legacy errors.

## Backend command inventory

| Existing script | Python responsibility |
| --- | --- |
| `collect-filings.mjs`, `download-filings.mjs` | SEC catalog refresh and source download |
| `export-experiment.mjs` | Experiment dataset export |
| `create-pinecone-index.mjs` | Index plan, validation and explicit creation |
| `load-evaluation-corpus.mjs`, `ingest-evaluation.mjs` | API upload and standalone benchmark ingestion |
| `build-benchmark.mjs` | Candidate evidence-label generation with write guards |
| `evaluate.mjs`, `run-benchmark.mjs` | Four-condition benchmark and report |
| `score-reviews.mjs` | Manual review scoring |
| `check-freshness.mjs` | Explicit freshness checks |
| `recovery.mjs` | Integrity-checked backup and restore |
| `export-project.mjs` | Source export and credential exclusion |
| `prepare_filings.py`, `load_pinecone.py` | Assess reuse; retain until current behavior is covered |

Browser checks and provider fixtures remain test tooling. Framework selection is GitHub Spec Kit's spec/plan/tasks approach; automated toolkit installation remains separate from implementing these approved documents.

## Implementation decisions and remaining gates

The user's 2026-10-21 instruction to start implementation authorizes the migration scope and proposed D01-D06 technical defaults, with validation at the relevant increments. Use FastAPI/Pydantic, Python 3.12, LangChain/LangGraph, filesystem artifacts, SQLite registry/checkpoints and one worker. Dependency versions will be resolved and locked in I02. SQLite suitability and hosted durable disk remain verification requirements rather than established guarantees.

D07 remains open: no live benchmark budget or nondeterministic tolerance has been approved. Proceed with offline implementation; settle D07 before paid comparison. Live quality evidence and production cutover remain pending. No real-provider capability or quota availability was tested.

## Post-migration verification

These artifacts remain the immutable pre-migration reference. Source hashes intentionally differ after the migration; running the capture script against current sources will therefore report a changed source manifest. Python tests consume the frozen HTTP/domain/fingerprint fixtures directly. Do not regenerate the original baseline to make migrated source hashes match. See [migration verification](../../../docs/PYTHON_MIGRATION.md) for current evidence.
