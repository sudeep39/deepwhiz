# Python backend operation and migration

The backend runs in Python with FastAPI, LangChain model interfaces and explicit LangGraph ingestion/research workflows. The existing frontend and public API are preserved. Start with `python run.py serve` from the DeepWhiz root after installing `backend/requirements.lock` into a virtual environment. `PORT`, `HOST` and the existing provider variables remain supported. Paths in `FINWHIZ_DATA_DIR` resolve relative to the project root.

## Setup

```sh
python -m venv .venv
# Activate the environment or use its Python executable explicitly.
python -m pip install -r backend/requirements.lock
python run.py evaluate
python run.py serve
```

On Windows use `.venv\Scripts\python.exe`; on Linux use `.venv/bin/python`. The lockfile includes offline verification dependencies. Run `npm ci` only for browser/legacy tooling. `npm start` selects the local virtual environment; `FINWHIZ_PYTHON` can select another interpreter. Production runs Python directly and does not require Node.

Source packages live under `backend/src/finwhiz`: API and configuration; storage/queue; parsing/chunking; providers; retrieval; financial arithmetic; ingestion and research graphs; evaluation; operational CLI. Prompt files retain the legacy financial instructions. No provider URLs or keys are accepted from browser requests.

LangGraph [checkpoints](https://docs.langchain.com/oss/python/langgraph/persistence) persist ingestion stage state; the job registry separately owns queue claims and user-visible status. LangChain [structured model output](https://docs.langchain.com/oss/python/integrations/chat/openai) is validated locally with strict Pydantic schemas. The embedding adapter implements LangChain's interface while retaining explicit response/provenance validation and Nebius query instructions.

## Import an existing runtime

Stop the Node server and all standalone ingestion commands. Drain the queue; imports refuse queued/processing jobs. Use fresh empty destination directories for every attempt.

```sh
python run.py snapshot --source=.runtime --to=.backups/before-python
python run.py migrate --source=.runtime --to=.runtime-python
python run.py migrate --source=.runtime --to=.runtime-python --apply
```

The second command is a dry run. Import copies and hashes legacy blobs, preserves all identifiers, verifies readback and imports jobs into SQLite. It does not call providers or change Pinecone. Set `FINWHIZ_DATA_DIR=.runtime-python` in `.env` or deployment secrets after a successful import. Startup refuses a legacy directory until it is explicitly imported.

Python retains access to legacy JSON/gzip caches, chunks, parents, sources and namespaces. New ingestion uses `py-v1` chunk IDs and namespace suffixes. Embeddings are reused only when legacy fingerprints/text match. An index containing compatible legacy vectors does not require wholesale re-embedding.

Run exactly one API instance with one worker owner per runtime. An OS-released lock excludes concurrent Python API/CLI owners; it cannot stop the legacy Node implementation, so never start Node against a Python runtime. Python workers are isolated child processes; a processing deadline terminates the child and retains cached work for retry. Checkpoints reference persisted artifacts rather than storing full filings/vectors.

## Operator commands

| Command | Behavior |
| --- | --- |
| `python run.py index [--provider=nebius]` | Local index plan; `--apply` creates/validates remotely |
| `python run.py collect` | Plan; `--apply` refreshes the SEC catalog, with optional `--tickers`, `--as-of`, `--periods` |
| `python run.py download` | Plan; `--apply` downloads catalog filings and records checksums |
| `python run.py export-experiment --apply` | Export frozen experiment JSON/CSV |
| `python run.py build-benchmark` | Revalidate source-derived label candidates against verified filings; no retrieval/model calls; `--apply --replace` explicitly replaces the suite |
| `python run.py load-corpus` | Plan; `--apply --base=URL` uploads experiment sources through the API |
| `python run.py ingest` | Plan; `--apply` performs standalone paid ingestion with checkpoints |
| `python run.py evaluate` | Validate suite offline; `--apply --answers [--judge]` runs paid evaluation |
| `python run.py score-reviews --run=PATH` | Write `MANUAL_ACCEPTANCE.json` from complete independent reviews |
| `python run.py freshness` | Local status; `--apply` checks SEC with `SEC_USER_AGENT` |
| `python run.py export-project --apply --to=PATH` | Export source to an empty directory with SHA-256 manifest and credential exclusions |

`--provider=openai|nebius` and `--data-dir=PATH` select a provider/runtime. Dry-run commands do not call paid providers. Stop the API before standalone ingestion, evaluation, freshness writes, snapshots or restores. Existing npm aliases now invoke the corresponding Python commands. Old `.mjs` commands remain legacy references.

Benchmark construction deliberately retains the frozen question/label definitions, re-extracts source quotes and clears review flags. It does not invent new labels for a changed corpus. New question sets need a separately reviewed suite. Results preserve four conditions, rotated order, source/suite hashes, failed-trial denominators and independent-review status. `--repetitions` accepts 1–5. Optional judge output is automated review.

## Backup and rollback

```sh
python run.py snapshot --to=.backups/python-runtime
python run.py restore --source=.backups/python-runtime --to=.runtime-restored
```

Snapshots verify source quiescence, copy SQLite databases through SQLite's backup API, and include hashed blobs plus a current legacy-readable state export. Restore validates filenames/checksums and never overwrites a populated directory. Backup/restore does not restore deleted Pinecone vectors. Store protected backups off the host and verify actual hosted disk durability.

To roll back the backend, stop Python, restore the pre-migration legacy backup to a new directory, set `FINWHIZ_DATA_DIR` to that directory, and run `npm run start:legacy`. Do not point Node at Python's SQLite runtime. Reverting the `.replit` build/run settings is also necessary for a hosted rollback. New uploads accepted only by Python need deliberate reconciliation before rolling back to the pre-migration snapshot.

## Verification record

On 2026-10-21:

- Legacy baseline: 40 Node tests, 16 preparation/loading tests and browser checks passed.
- Final Python suite: 31 tests passed. Checks cover frozen HTTP/domain contracts, OpenAI/Nebius wire formats, both real spawned workers, four retrieval conditions, local schema validation, citation repair, financial arithmetic, readable/scanned PDF handling, provider isolation and checkpoint replay after an interrupted upsert.
- The existing desktop/mobile browser suite passed against the Python API with mocked provider readiness/answers.
- Offline source audit: 16 verified filings, 2,924 fixed chunks, 48/48 gold evidence labels retained. This establishes extraction coverage, not answer faithfulness or retrieval effectiveness.
- A separate clean Python environment installed the exact lockfile and passed dependency checks.
- Local legacy runtime backup/restore/import readback verified two jobs and 532 blobs. Original `.runtime` remains intact. The imported runtime is `.runtime-python`; only the local runtime path setting was changed.

Ignored evidence files: `artifacts/migration/python-corpus-audit.json`, `artifacts/migration/runtime-rehearsal.json`; pre-migration backup/restore rehearsal: `.backups/python-migration-2026-10-21/`. Frozen baseline files remain under `specs/001-python-backend/baseline`.

The available local interpreter is Python 3.13.7. Python >=3.12 is the declared target; the Replit Linux/Python 3.12 deployment has not been exercised here. No hosted deployment or live provider run was performed. The user explicitly selected offline-only verification. Live benchmark budget/tolerance, provider capability confirmation, independent quality review and hosted storage durability remain future release gates. The checked-in hosting configuration is prepared for Python; it is not proof of a production cutover.

## Requirement coverage

| Requirements | Implementation and offline evidence |
| --- | --- |
| R01–R02 | `api.py`; frozen HTTP fixtures and desktop/mobile browser checks |
| R03–R04 | Multipart limits/cleanup, queue backpressure, atomic claims, real spawned workers, deadline termination and shutdown recovery tests |
| R05–R06 | `documents.py`; HTML/token/Unicode fixtures, readable/scanned PDF cases, full 16-document source audit |
| R07–R08 | `config.py`, `providers.py`; frozen fingerprints, both provider wire formats, local schema rejection, quota handling and index-provenance tests |
| R09–R12 | `retrieval.py`, `research.py`, `finance.py`, retained prompt files; four conditions, signed arithmetic, citation repair, chart rejection and policy fixtures. Live quality remains unmeasured. |
| R13 | `storage.py`, `recovery.py`; gzip compatibility, import readback and original-runtime backup/restore rehearsal |
| R14 | Authentication/origin/header/error contracts, queue limits and simultaneous-chat backpressure tests |
| R15–R16 | `cli.py`, `catalog.py`, `evaluation.py`; command inventory, suite validation, source-candidate dry run, metric/failure/manual-review fixtures |
| R17 | Pinned clean install, dependency check, real Uvicorn startup, Python startup/hosting configuration and rollback guide; hosted acceptance pending |
| R18–R20 | Separate domain/infrastructure modules, strict schemas, prompt files, typed graph state, SQLite checkpoints, correlated logs, operations fixtures and interruption/visibility replay tests |

## Known behavioral differences

- Python stores job state in SQLite and imports legacy state once. Editing the old hashed `state` file after import does not update the registry.
- New output uses `py-v1`; the fixed-chunk corpus count matches the legacy baseline, but identical global boundaries are not assumed from that count alone.
- PDF extraction uses pypdf instead of pdf-parse. A readable PDF and a blank scanned-style PDF are tested; complex real PDF layouts may differ and require source review.
- `build-benchmark` refreshes evidence for the frozen suite rather than generating all question templates afresh.
- API errors retain legacy public messages for known cases; unexpected internal failures return a generic message while logs record exception type and request/job correlation.
- Production startup, runtime locks and SQLite ownership intentionally reject unsafe in-place legacy adoption or concurrent owners.
