# Current DeepWhiz deployment recovery

The authoritative Replit launch command now restores
`data/recovery/deepwhiz-retained-library-v1.json.gz` into an **empty** registry.
It contains the seven retained Tesla filings (2025 Q1/Q2/Q3 and annual, plus 2026
Q1/Q2/Q3), source artifacts, parent tables and 2,416 existing vector identities.
It preserves the original job, file and namespace IDs. It contains public SEC data,
not credentials or embedding values. Its SHA-256 is
`ac6fa0d5f88e564977863565a91f1fdea76160184e62cbbee32866873db38555`.

Recovery verifies the snapshot and retained Pinecone namespace counts, restores the
local registry and retrieval artifacts, and creates **zero embeddings** with **zero
Pinecone vector writes**. A nonempty registry is left unchanged. Keep the existing
Pinecone key, Nebius index/model configuration and this packaged file when publishing.
Pull the latest code and republish to apply the updated launch command.

This snapshot recovers these known seven documents across publications. **Subsequent
uploads need App Storage checkpoints** to survive another publication; the snapshot
is not a replacement for continuous persistence. Follow the protection procedure in
`docs/RUNBOOK.md`, and require the Documents badge to read BACKUP VERIFIED before
republishing after adding new filings. The full private backup, including cached and
exact vector values, is retained locally under `.backups/` and is not published.

---

The following documents the older Finwhiz recovery bundle, retained for compatibility.

# Automatic recovery of the deployed Replit library

The Python deployment had an empty SQLite registry, while Pinecone still contained 3,332 retained Replit vectors. This is a missing local artifact/registry problem, not missing embeddings.

## What is included

`data/recovery/replit-library-v1.json.gz` is a checksum-verified recovery snapshot containing:

- The two retained ready Replit jobs, original job/source IDs and four namespaces.
- All 3,332 original vector IDs and metadata/text, without embedding values or credentials.
- All eight original SEC HTML filings (nine source references because Q2 was uploaded twice), verified against their source manifest checksums.
- Legacy parent tables reconstructed with the original `js-v1` parser and checked against every recovered chunk's section/kind/parent index.
- Fiscal metadata from each original filing's SEC DEI facts.

The bundle SHA-256 is `84e0f0b08f6bbe46d3b0891ed6003aeabe568e98e85dcb0923f7ac1c3b07b7d9`. Its compressed size is 4,990,829 bytes. It contains public SEC source data and application metadata, not passwords/API keys.

## Deploy

Deploy the updated project **including the recovery bundle**. `.replit` supplies its path/checksum, selects Nebius by default, clears the client-only shared-backend URL for the authoritative server, and starts Python. Existing API/provider secrets are still required. There is no need to re-upload or re-embed the filings.

The Python app also detects the packaged bundle automatically when `REPLIT_DEPLOYMENT` is set, so recovery still works if Replit retains an older custom Python run command. A ready-to-upload project archive is prepared at `release/replit-library-recovery-2026-10-22.zip`; it includes the recovery bundle and excludes actual `.env` files, runtime directories and generated data caches. The running deployment cannot pick up these new files until it is redeployed.

On startup, under the runtime owner lock, Python:

1. Leaves a nonempty registry unchanged.
2. Verifies the bundle and every source/artifact checksum.
3. Checks the retained Pinecone index fingerprint and namespace counts read-only.
4. Restores artifact blobs and inserts all ready jobs in one SQLite transaction.
5. Starts the API/worker only after successful recovery.

The frontend will choose an available provider on first load if a remembered provider has no documents. Later explicit provider changes remain respected. Recovery status appears in authenticated `/api/state` under `recovery`.

If the standalone duplicate has already been fully deleted through the maintenance API, startup preserves its old source IDs as aliases instead of recreating missing vector references. Partial deletions or missing canonical namespaces block recovery for inspection. The bootstrap never upserts/deletes vectors or calls an embedding/chat model.

## Limits and persistence

This snapshot restores the known library as of its creation. It does not recover arbitrary future uploads or replace persistent runtime storage. Configure `FINWHIZ_DATA_DIR` on durable storage and retain runtime backups to preserve subsequent uploads, maintenance manifests and changes across deployments. An ephemeral new deployment can restore this known snapshot again, but cannot reconstruct later changes that were never backed up.

The original runtime and any nonempty Python registry are never overwritten. A nonempty but incorrect registry requires a separately reviewed repair; bootstrap deliberately does not merge it automatically. The earlier failed two-file upload is not restored because it had no ready documents or indexed evidence.

For an isolated manual rehearsal, set `FINWHIZ_BOOTSTRAP_SHA256` to the above checksum and run:

```sh
python run.py recover-library --source=data/recovery/replit-library-v1.json.gz --data-dir=EMPTY_TEST_RUNTIME
python run.py recover-library --source=data/recovery/replit-library-v1.json.gz --data-dir=EMPTY_TEST_RUNTIME --apply
```

The first command validates locally. Apply checks Pinecone read-only before restoring local files. Do not target your active runtime from another process.
