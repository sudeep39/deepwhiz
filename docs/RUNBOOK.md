# Run, evaluate and recover

Run commands from the DeepWhiz root with Python >=3.12 and an activated virtual environment. See [Python migration and rollback](PYTHON_MIGRATION.md) before adopting existing data. Optional npm aliases use the local .venv and require Node >=22. Keep keys in `.env` or server environment secrets; never paste them into code or commit them. Pinecone uses its API key; there is no extra connection password.

## Application

```sh
python -m pip install -r backend/requirements.lock
python run.py serve
```

Open http://localhost:3100. Research starts blank and becomes available only after a completed ingestion job. Browser uploads continue processing after acknowledgement even if the user leaves. APP_PASSWORD is required in production/Replit deployment. Use a Reserved VM and a verified persistent `FINWHIZ_DATA_DIR`. Do not run the web server and the standalone ingestion CLI against the same queue simultaneously.

## Complete the controlled experiment

The last live attempt stopped at embedding because the configured OpenAI account reported exhausted credits. Stop the app, restore API quota, and run:

```sh
npm run ingest:evaluation -- --apply
npm run evaluate -- --apply --answers
```

The first command resumes the same job, caches embeddings by model/dimension/text, and indexes both strategies in separate namespaces. It does not duplicate a ready evaluation corpus. Run the second command only after indexing is ready. It makes paid retrieval/reranking/generation calls for all 32 questions in all four conditions.

Optionally add `--judge` for model-based claim review and `--repetitions=2` for repeated/counterbalanced trials. A model judge is advisory, not independent human proof. The index and corpus are fingerprinted in the results. Failed trials remain recorded and count against retrieval/status scores. Raw results are checkpointed in JSONL as trials finish.

To validate the suite without provider requests:

```sh
npm run evaluate
```

Outputs appear under `artifacts/evaluation/<timestamp>/`: `results.json`, `trials.jsonl`, `provenance.json`, `COMPARISON_REPORT.md`, and `manual-review.json`. Review every factual claim against its cited original filing and fill in reviewer name, supported/citationCorrect claims, numericCorrect, unitsCorrect, periodsCorrect, relevantAnswer, and reviewed. For a correct refusal, claims may be empty; mark nonapplicable numeric/unit checks true with an explanation. An answerable response requires at least one reviewed claim. Failed trials must never receive invented supported claims.

```sh
npm run score:reviews -- --run=artifacts/evaluation/YOUR_RUN_DIRECTORY
```

`MANUAL_ACCEPTANCE.json` stays pending when review is incomplete and fails when measured thresholds are missed. Include per-query regressions and unsuccessful trials in the submission. Recall@5 for an eight-label trend cannot exceed 5/8 when evidence is spread across eight chunks; inspect final-context evidence recall separately. Latency excludes the optional judge and ingestion, but includes generation when `--answers` is used.

On a fresh standalone export, downloaded HTML is intentionally omitted. With a suitable SEC_USER_AGENT contact header, `npm run download` restores the catalog's primary documents. This downloads the preserved ten-issuer archive, not just the active experiment. Do not refresh the frozen catalog cutoff before reproducing this experiment. The ingest command verifies checksums and selects only the two configured experiment issuers. Alternatively upload your own documents through the app for normal research.

## Weekly freshness and processing SLA

```sh
npm run freshness
npm run freshness -- --apply
```

The first command is a local status check. The second requires `SEC_USER_AGENT` with an app name and contact email; it queries SEC for uploaded issuers with known CIKs, reports newer/amended filings, and persists a completed review only after all checks succeed. Perform weekly; schedule this CLI with your platform's scheduler if desired. A freshness check does not silently change the frozen benchmark or upload new filings. Review/download/upload updates deliberately. Documents displays review-due/new-filing notices for loaded corpora; `/api/operations` exposes overdue queue/data and completed-job timings to authenticated operators.

## Recovery

Stop the server and let the queue drain. Snapshots contain uploaded documents and derived evidence; protect them as carefully as the corpus.

```sh
python run.py snapshot --to=.backups/runtime-snapshot
python run.py restore --source=.backups/runtime-snapshot --to=.runtime-restored
```

Both destinations must be empty. Checksums and filenames are verified before restore; existing data is never overwritten. Set FINWHIZ_DATA_DIR to the restored directory and restart. This restores source/state/cache, not deleted Pinecone vectors or namespaces. Retain the original sources and provider index; reingest if remote vectors were lost. Store a protected copy of backups off the deployment disk. Verify actual hosted durability with a restart/redeploy-and-restore drill before relying on it.


## Rollout health and proxy configuration

Use `GET /api/health` for process liveness and `GET /api/ready` for readiness.
Readiness returns HTTP 503 until storage, a configured provider, production access
control and the ingestion supervisor are ready. It checks configured credentials,
not the provider account's quota or live availability. Do not expose its checks as
a guarantee of answer accuracy.

Production/Replit requires `APP_PASSWORD` with at least 12 characters; prefer a
long generated secret. Browser credentials stay in page memory and must be entered
after reload. TLS must terminate at a trusted ingress. If the ingress preserves a
public origin differing from the backend origin, configure an exact JSON array,
for example `ALLOWED_ORIGINS=["https://research.example.com"]`. This is mutation
origin validation, not an open CORS policy. Arbitrary forwarded host headers are
not accepted as authorization. Configure Uvicorn/proxy trust for the actual ingress;
never trust forwarded headers from unrestricted clients.

`SEC_IMPORT_TIMEOUT_SECONDS=90` limits a complete import (maximum setting 100).
For large requests that exceed the budget, request fewer reporting periods.
The download reader enforces per-response and existing batch limits, and failed
imports remove staged source blobs. Retry only after the earlier request has ended.

Before external rollout, complete the remaining gates in [the current review](CODE_REVIEW.md).


## Preserve the deployed library before republishing

A Pinecone index is not the complete library. Keep the job registry, exact namespace
and source IDs, original filings, chunks, parent tables and embedding caches as well.
The persistence code never creates embeddings or writes/deletes Pinecone records.

1. Before replacing an older deployment, set `FINWHIZ_SHARED_BACKEND_PASSWORD` in
   your local `.env` to that deployment's workspace password. Do not commit it.
   Run the read-only rescue utility against the actual deployed origin:

   ```powershell
   python scripts/backup-deployed-library.py --url https://deepwhiz.replit.app --to .backups/NEW_BACKUP_DIRECTORY
   ```

   It downloads source files and existing vector values, reconstructs Python parent
   tables, verifies their identities, and creates a snapshot. It does not re-embed.
   Keep this backup off the deployment disk. It covers ready jobs; ingestion must
   finish first. Non-Python parser libraries require their original recovery bundle.

2. Create/select a Replit App Storage bucket. Keep its bucket ID and library prefix
   stable across publications. The official SDK is installed by `requirements.txt`.
   Set `FINWHIZ_STORAGE_BUCKET_ID` to its ID (or use the app's default bucket), and
   keep `FINWHIZ_LIBRARY_PREFIX=deepwhiz/library-v1`.

3. Upload the verified portable `deepwhiz-library.zip` to the Replit **workspace**.
   Do not commit it to GitHub. Seed App Storage before republishing:

   ```sh
   python scripts/protect-library.py --archive deepwhiz-library.zip --sha256 VERIFIED_ARCHIVE_SHA256
   ```

   This verifies every archived object, uploads an immutable checkpoint, reads it
   back to verify its checksum, and only then updates the latest checkpoint pointer.
   A checkpoint missing previously protected job IDs cannot replace the pointer.

4. Set `FINWHIZ_CLOUD_LIBRARY=true` in the published app secrets, then republish.
   On startup, an empty registry is restored from the verified cloud checkpoint
   before workers start. A nonempty registry is never overwritten. Restore preserves
   the existing namespaces and embedding cache; there is no automatic re-indexing.

5. The Documents badge must read **BACKUP VERIFIED** after uploads finish. Automatic
   checkpoints run once ingestion is idle and the registry changes. The badge reads
   pending or unconfigured when protection is not current; do not republish then.
   Authenticated `POST /api/library/checkpoint` forces a new checkpoint, and
   `GET /api/library/export` downloads a portable archive for off-platform retention.
   `GET /api/state` includes nonsecret `config.libraryProtection` status.

This is for one Reserved VM/library owner. An in-progress ingestion batch is not
checkpointed as complete. Missing source/retrieval artifacts, upload/download errors
or failed checksums prevent a new checkpoint from replacing the previous one. These
protections cannot restore vectors deleted directly in Pinecone; the rescue utility
also retains their values locally so a separate, reviewed vector restore can reuse
those values without an embedding model call.

For an offline one-time restore into an empty runtime, set
`FINWHIZ_LIBRARY_RESTORE_PATH` to a portable archive and
`FINWHIZ_LIBRARY_RESTORE_SHA256` to its verified checksum. For a normal hosted
installation prefer App Storage; setting a local runtime path alone is not durable.

Replit SDK reference: https://docs.replit.com/features/sdks/object-storage-python-sdk
