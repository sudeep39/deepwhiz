# DeepWhiz functionality and rollout review ? October 25, 2026

Assessment: suitable for a controlled, trusted shared-workspace pilot after the hosting checks below. Public multi-customer rollout is not approved by this local review. This review covers source behavior and offline regression tests; it is not a penetration test, live-provider quality certification, or hosted load test.

## Findings and fixes

| Priority | Finding | Implemented correction |
|---|---|---|
| High | A supplied X-Forwarded-Host could authorize an otherwise foreign Origin. | API mutation requests accept the actual origin or explicit ALLOWED_ORIGINS only. Forwarded host headers cannot grant access. The frontend proxy validates its own browser origin before rewriting it for the backend. |
| High | A caller could supply forwarding metadata through the local proxy and undermine IP-based controls. | The proxy rebuilds client IP and protocol from its connection and backend target; caller-supplied forwarded host is removed. |
| High | Workspace passwords were retained in browser session storage. | Passwords now stay in page memory; legacy stored values are cleared. Reload requires signing in again. This reduces persistent exposure; it does not substitute for XSS defenses or individual identity. |
| High | Production accepted arbitrarily short workspace secrets. | Production/Replit API access and readiness require a password of at least 12 characters. Use a long randomly generated value and HTTPS. |
| High | SEC imports could outlast the frontend timeout or buffer oversized documents. | Imports have a 90-second default overall deadline (configurable up to 100); requests stream with bounded response sizes (20 MB filing, 8 MB directory response). Timeouts clean up staged files and release admission. Existing 32-file/100 MB batch and transactional three-job admission limits remain. |
| Medium | Health returned success without distinguishing usable readiness. | GET /api/health remains liveness. GET /api/ready checks SQLite integrity, provider credential presence, production access control, maintenance and supervisor task state. It does not call external providers or prove their availability. |
| Medium | Evidence context limits could be overwritten as missing data. | Retrieval preserves evidence-budget failures through graph scope handling and requests a narrower question instead of claiming supporting data is absent. |
| Medium | The expected recovery bundle was missing from this source checkout. | Restored the original 4,990,829-byte bundle from the sibling source and verified its published SHA-256. Bootstrap and original-table regression tests now run. The active runtime was not replaced. |
| Medium | The main browser test referenced retired selectors and an obsolete upload banner. | Updated it for the current multi-company picker and upload acknowledgement; it runs a real offline Python upload and tests charts, calculations, sources, selection, and mobile layout. |
| Low | Job activity assumed every historical record had a createdAt string. | Missing timestamps sort safely rather than crashing the interface. |

The origin checks follow explicit proxy trust boundaries rather than trusting arbitrary forwarded headers. See [FastAPI proxy guidance](https://fastapi.tiangolo.com/advanced/behind-a-proxy/). Browser storage risk and the limitations of JavaScript-accessible credentials are described in [OWASP HTML5 security guidance](https://cheatsheetseries.owasp.org/cheatsheets/HTML5_Security_Cheat_Sheet.html).

## What the application already does well

- Retrieval is scoped to selected issuers, source filings, provider and embedding space. Numeric checks and deterministic calculations enforce important period/unit constraints.
- Agent-selected searches and table reads run within application-controlled tool and time limits. Execution receipts expose tasks, evidence excerpts, drafts and validation outcomes without claiming to reveal hidden model reasoning.
- Duplicate uploads are handled transactionally; ingestion runs in a supervised process with recovery checkpoints. Source downloads require workspace authentication.
- Unsupported evidence produces a concise response with actionable evidence gaps. Model/verification timeouts are distinguished from established missing-document gaps.
- API responses avoid caching; snippets and drafts render as text rather than executable HTML. Logs omit intermediate-output bodies.
- Snapshot/restore uses checksums and SQLite's backup mechanism. It requires the server to be stopped and its queue drained; do not replace it with a filesystem copy of a live WAL database. See [SQLite backup documentation](https://www.sqlite.org/backup.html).

## Remaining market-release gates

1. **Customer isolation:** the current password grants access to the whole workspace, all uploads and the shared library. Before accepting unrelated customers, implement individual identity, roles, tenant-aware database and vector authorization, audit attribution, account revocation and data deletion. Do not present this shared-password architecture as tenant isolation.
2. **Hosted durability:** attach a persistent FINWHIZ_DATA_DIR, protect and encrypt backups, retain remote-index configuration, and perform a restore drill after a deployment/restart. Local tests cannot certify a hosting provider's disk durability. SQLite/process ownership currently requires one application owner, not multiple independent web replicas against the same files.
3. **Research quality:** run the existing agentic eval suite against actual target companies/providers, review causal claims and citations manually, and include fiscal calendars, annual-versus-quarterly columns, conflicting values and unavailable periods. Existing numeric checks and citation presence do not independently prove every qualitative causal claim. Record failed trials, latency, token use and unsupported-claim rates; do not treat mock tests as measured market accuracy.
4. **Capacity and cost:** set intended user/load and response-time targets; load-test ingestion and research on the deployed instance. Existing queue, concurrency and timeout limits bound work but do not establish per-customer quotas, billing controls or storage retention limits. Provider outages, rate limits and the SEC's access policy remain external constraints.
5. **Privacy and operations:** publish the actual provider-processing, storage, retention and deletion policies; define support, incident ownership, access review and secret rotation. Minimize sensitive uploads until the appropriate controls are in place.
6. **Release operation:** enforce HTTPS at the trusted ingress, configure explicit public origins where needed, restrict which proxies can set forwarding headers, monitor readiness and request IDs, and preserve rollback snapshots. Readiness credential presence is not a successful paid-provider transaction.

## Verification

- Full offline backend suite: **186 passed** after the fixture and context-budget corrections.
- New regressions cover origin spoofing, explicit origins, readiness, weak production passwords, oversized SEC streams and import-deadline cleanup.
- Frontend proxy suite: **4 passed**, covering forwarded-header spoofing, origin translation, allowlisted static assets, remote origins and backend failure handling.
- The Python browser integration uses real uploads and synthetic model/provider readiness; it makes no paid research calls. Research and Documents layout checks also exercise the current UI.
- Installed Python dependency consistency check: no broken requirements. This is not a vulnerability-advisory scan.
- No external deployment, destructive corpus migration, customer-data deletion, live paid-provider evaluation, penetration test, dependency-advisory scan or secret rotation was performed.

---

> Historical pre-implementation review. For the current status see [RUBRIC_IMPLEMENTATION.md](RUBRIC_IMPLEMENTATION.md) and [RUNBOOK.md](RUNBOOK.md).

# DeepWhiz code and functionality review

Review date: October 18, 2026 (local). Scope: frontend, HTTP API, worker ingestion, chunking, providers, retrieval, filesystem storage, Python preparation/loading alternative, SEC collection, tests, deployment configuration and handout alignment. This is a source review plus offline verification, not an external penetration test or live provider certification.

## Preserved source

Before edits, 38 source files were backed up in `.backups/20261019T005620Z/source-before-review.zip` with a SHA-256 manifest. Secrets, uploaded data, caches, environments and dependency trees were excluded. No legacy Python tooling or archived company corpus was deleted. Both remain intentional project assets. Backups are local and excluded from the source export/Git.

## Fixed findings

| Severity | Finding | Resolution and verification |
|---|---|---|
| High | Production mode outside the Replit marker could expose an unauthenticated shared workspace | Require APP_PASSWORD for NODE_ENV=production as well; regression verifies 503 when absent |
| Medium | Malformed JSON roots and upload metadata caused errors or accepted arbitrary structured fields | Require JSON object, bounded scalar hints, allowed fields, real calendar dates, unique upload indexes; invalid-input tests |
| Medium | Rejected upload batches left orphan source files | Delete persisted batch sources on validation/enqueue failure; filesystem regression tests |
| Medium | Repeated chunks in a ranking inflated reciprocal-rank fusion | One vote per unique document ID per ranking; duplicate-invariance test |
| Medium | Ratio values could be labelled percent at the wrong scale | Separate percentage operation and reject percent-labelled fraction operation; deterministic arithmetic tests |
| Medium | Worker duration/memory unbounded | 768 MB V8 old-generation limit and 30-minute job timeout; not a total process RSS guarantee |
| Medium | Authentication guessing and paid-chat concurrency insufficiently bounded | 20 failed password attempts/minute per socket address; maximum two concurrent chats; retain 12 chats/minute per address |
| Medium | State persistence chain stayed rejected after a write failure | Subsequent saves can recover; current failed save still surfaces an error |
| Low | Generic upload depended on archived SEC manifest | Missing optional manifest no longer stops ingestion |
| Low | Reserved tokenizer strings in documents could fail encoding | Treat reserved strings literally; Unicode and special-token round-trip regression |
| Low | Source context lacked section metadata | Include parent section in newly ingested source metadata; old jobs require reingestion to gain it |
| Low | Browser test required archived filings in fresh checkout | Synthetic, explicitly test-only upload fixture; app still starts empty |

## Remaining release limitations

* **Quality evidence missing:** no real-provider retrieval comparison, reranking delta, faithfulness audit or load-test SLA. See RUBRIC_REVIEW.md. Offline provider fixtures verify orchestration, not semantic quality.
* **Durability:** source files, lexical chunks, canonical tables, queue and embedding cache live on filesystem. Pinecone retains vectors but cannot reconstruct all app state after disk loss. Reserved VM alone is not proof of disk durability across publishing/redeployment. Durable storage and recovery drill remain necessary.
* **Single workspace:** a shared bearer password, no individual accounts, tenant isolation or document ACLs. Limit deployment to trusted analysts. Rate limits use socket addresses, which may represent a shared reverse proxy, and are in memory.
* **Resource bounds:** uploads buffer up to 100 MB/batch; HTML parsing/tokenization can expand memory. Timeout/retry is available, but no distributed queue, transactional source/job write, dead-letter queue or provider cancellation on client disconnect. Aborted-transfer cleanup and crash recovery deserve additional fault-injection tests.
* **Retrieval:** lexical ranking runs in memory; namespace queries scale with jobs and periods. Maximum 16 evidence chunks can constrain broad comparisons. Re-uploading identical files still duplicates storage/provider calls, despite rank deduplication. Long-history/follow-up retrieval needs further design.
* **Financial semantics:** PDF extraction is text-first; complex table row/column associations, units, amended filings, restatements, fiscal calendars and non-GAAP normalization need source review. Numeric occurrence checks alone do not establish fact correctness. Unsupported forecasts should be refused; this is uncalibrated.
* **Security assurance:** no external penetration test, no malware scanner/OCR sandbox, no encryption-at-rest configuration in this project, no formal retention/deletion workflow, and no comprehensive adversarial prompt-injection benchmark. HTTP deployment must use platform HTTPS. Provider credentials remain server-side.
* **Maintainability:** several modules use dense one-line statements. A later formatting/module-boundary pass would improve reviewability; no mass rewrite was mixed into this audit.

## Verification evidence

Commands and final results are recorded in `docs/VERIFICATION.md`. Production npm audit reported **zero known vulnerabilities** (31 production dependency entries reported). This point-in-time advisory scan does not prove absence of vulnerabilities. Export checks reject common credential signatures and exclude runtime/secrets by allowlist; human review remains necessary.

The source exporter prepares a separate root with package files, Replit config, tests and small SEC metadata. It omits dependencies, runtime, secrets, backups and downloaded filing HTML. It does not initialize Git or publish. Archived filings remain in the original workspace and may be uploaded or re-downloaded later.
