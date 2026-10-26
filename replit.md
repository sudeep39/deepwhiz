# DeepWhiz on Replit

## Run

- Existing stack: Vanilla JavaScript frontend, Python FastAPI, an isolated ingestion worker, LangGraph, SQLite and Pinecone. Keep the imported structure.
- Python >=3.12 is required and configured; Node is optional browser/legacy tooling.
- Use the **Start application** workflow (`FINWHIZ_BACKEND_URL= FINWHIZ_BOOTSTRAP_LIBRARY= PORT=5000 python run.py serve`) or Replit's Run button.
- The preview serves on `0.0.0.0:5000`. Outside Replit, `python run.py serve` defaults to port 3100.
- Backend dependencies are pinned in `backend/requirements.lock` and included by root `requirements.txt`. Replit installs them automatically during publishing.
- Leave the custom publishing build command empty. Do not add a second `python -m pip install` step: publishing's build shell uses Nix-managed Python and rejects writes to its immutable environment (PEP 668).
- The publishing run command explicitly sets `PORT=5000` before `python run.py serve`, matching the configured port.

## Credentials and data

- Add `OPENAI_API_KEY` and `PINECONE_API_KEY` through Replit Secrets for the default OpenAI provider. Alternatively configure `NEBIUS_API_KEY` and choose Nebius in the app.
- Set `APP_PASSWORD` through Secrets to protect the shared workspace. It is required in production; the existing API rejects production requests without it.
- The UI and health endpoint can start without provider keys, but uploads and AI research cannot run until keys are configured.
- Development and publishing start an independent empty library without contacting the original Pinecone indexes. Publishing sets `FINWHIZ_BOOTSTRAP_AUTO=false` and clears `FINWHIZ_BOOTSTRAP_LIBRARY` to disable both explicit recovery and production bundle auto-detection. Existing runtime data is preserved; the bundled recovery data is not deleted.
- OpenAI expects a dense/cosine Pinecone index with 1,536 dimensions (`PINECONE_INDEX`, default `finwhiz-filings-v1`). Nebius uses its own 4,096-dimensional index (`PINECONE_NEBIUS_INDEX`, default `finwhiz-nebius-v1`).
- Runtime files live in ignored `.runtime-python/` by default. Before publishing, verify durable storage and backups for `FINWHIZ_DATA_DIR`; Pinecone stores vectors, not the local upload/queue/cache files.
- For existing data, stop Node, import to a separate runtime and update the runtime secret before starting Python; see docs/PYTHON_MIGRATION.md. Paid ingestion/evaluation is not needed just to start.

## Checks

- `npm run test:python` runs the backend suite; `npm test` retains the Node regression suite.
- `GET /api/health` returns `{"ok":true}`.
- The imported `npm run test:browser` script requires Microsoft Edge; it is not portable to this Linux environment without changes.
