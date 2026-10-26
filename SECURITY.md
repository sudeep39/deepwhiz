# Security scope

DeepWhiz is a single shared analyst workspace. Use HTTPS and a strong, unique APP_PASSWORD. Production mode fails closed if that password is absent. Keep OpenAI and Pinecone keys in server environment secrets; Pinecone requires an API key, not a separate database connection password. Do not put provider keys in frontend code or Git.

Uploads are untrusted. Only supported document extensions are accepted, with batch/file limits and parser validation during processing. Source files are downloaded as attachments, never served as active HTML. Rendering uses text nodes, a same-origin CSP, and restricted source links. Model output is schema-constrained and citations/numeric literals are checked. These controls reduce risk but do not guarantee financial accuracy or prevent every prompt injection.

Do not deploy for multiple unrelated customers without per-user authentication, tenant isolation and document authorization. Filesystem state, backups, encryption, retention, provider data policies and disaster recovery are deployment responsibilities. Known limitations and fixes are documented in `docs/CODE_REVIEW.md`.

Report security issues privately to the repository owner. Do not include keys, uploaded confidential documents or exploit credentials in public issues. No public disclosure contact has been invented for this project.

Current hardening: mutation origins must match the actual origin or explicit `ALLOWED_ORIGINS`; the development proxy rebuilds forwarding metadata. Production workspace secrets must contain at least 12 characters. Browser passwords stay in memory, not session storage. The readiness endpoint reports nonsecret configuration/storage/worker checks without contacting external providers. See the current review in `docs/CODE_REVIEW.md` for remaining release gates.
