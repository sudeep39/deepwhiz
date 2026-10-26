# Separate GitHub project

DeepWhiz has its own Git repository in the `deepwhiz` folder. Publish that repository only; do not publish the parent `ai` workspace. To prepare an optional clean export:

```sh
node scripts/export-project.mjs
```

The timestamped folder under `release/` is suitable for its own Git repository and root-level Replit import. It includes a SHA-256 source manifest. Downloaded SEC HTML, secrets, runtime uploads, caches, dependencies, screenshots and backups stay in the original workspace. Small SEC catalog/checksum metadata is retained for tests and provenance. The web app does not preload that catalog into Research. To reproduce downloads, configure the SEC user agent described in README and run the download scripts.

Review the export and run `npm ci`, `npm test`, and `npm run test:browser` (installed Edge required). Python tools are optional and require `requirements.txt`. No API keys are needed for offline tests. Do not copy `.env` into Git. No license has been chosen on your behalf.

For a new exported checkout without Git history, the following creates a public repository. The working `deepwhiz` folder already has Git history; do not initialize it again:

```sh
git init -b main
git add .
git commit -m "Initial DeepWhiz financial document intelligence application"
gh repo create deepwhiz --public --source=. --remote=origin --push
```

Choose visibility deliberately. Replit can import the resulting GitHub repository directly; no manually pre-created DeepWhiz project is necessary. Configure provider secrets and APP_PASSWORD in the imported app. Require a verified durable FINWHIZ_DATA_DIR before treating hosted uploads as persistent. Do not use Autoscale for this in-process queue.

The original-source backup is `.backups/20261019T005620Z/source-before-review.zip`; retain it outside the published repository. No unused source has been discarded in this review.
