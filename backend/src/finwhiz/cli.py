"""Operator commands. Remote or corpus-changing actions require --apply."""

import argparse
import json
import logging
import multiprocessing
import re
import shutil
import sys
import time
import uuid
from pathlib import Path

import httpx

from .catalog import (
    benchmark_candidates,
    collect,
    download,
    evaluation_entries,
    export_experiment,
    load,
    sec_request,
    write,
)
from .config import ROOT, AppError, Settings, digest, index_config
from .evaluation import comparison_report, run_benchmark, score_reviews, validate_suite
from .ingestion import worker_main
from .operations import operational_status
from .providers import Providers
from .recovery import migrate_legacy, restore, snapshot
from .storage import Jobs, Storage, now, runtime_lock


def parser():
    command = argparse.ArgumentParser(description="DeepWhiz Python backend and operator tools")
    command.add_argument(
        "command",
        choices=[
            "serve",
            'agent-evaluate',
            'agent-score-reviews',
            "recover-library",
            "library-inventory",
            "fiscal-metadata-plan",
            "index",
            "collect",
            "download",
            "export-experiment",
            "build-benchmark",
            "load-corpus",
            "ingest",
            "evaluate",
            "score-reviews",
            "freshness",
            "snapshot",
            "restore",
            "migrate",
            "export-project",
        ],
    )
    command.add_argument("--apply", action="store_true")
    command.add_argument("--provider", choices=["openai", "nebius"])
    command.add_argument("--data-dir", type=Path)
    command.add_argument("--source", type=Path)
    command.add_argument("--to", type=Path)
    command.add_argument("--host")
    command.add_argument("--port", type=int)
    command.add_argument("--queries", type=Path, default=ROOT / "evaluation/benchmark.json")
    command.add_argument("--answers", action="store_true")
    command.add_argument("--judge", action="store_true")
    command.add_argument("--repetitions", type=int, default=1)
    command.add_argument("--run", type=Path)
    command.add_argument("--replace", action="store_true")
    command.add_argument("--as-of")
    command.add_argument("--tickers")
    command.add_argument("--periods", type=int)
    command.add_argument("--base", default="http://localhost:3100")
    return command


def ensure_index(provider):
    config = index_config(provider.config)
    headers = {"Api-Key": provider.settings.pinecone_api_key, "X-Pinecone-Api-Version": "2025-10"}
    url = "https://api.pinecone.io/indexes/" + provider.config.indexName
    existing = provider.client.get(url, headers=headers)
    if existing.status_code == 404:
        provider.request("https://api.pinecone.io/indexes", config, headers)
    elif not existing.is_success:
        raise AppError(f"Pinecone HTTP {existing.status_code}")
    for _ in range(60):
        try:
            return dict(host=provider.pinecone_host(), index=config["name"])
        except AppError as error:
            if "initializing" not in str(error):
                raise
            time.sleep(2)
    raise AppError("Pinecone index provisioning timed out.")


def ingest_corpus(settings, config, entries):
    if settings.finwhiz_backend_url:
        raise AppError('Shared-backend mode is active. Upload through the shared application instead of indexing a second local copy.')
    store = Storage(settings.finwhiz_data_dir)
    with runtime_lock(store.directory):
        jobs = Jobs(store)
        if any(j["status"] in ("queued", "processing") for j in jobs.all()):
            raise AppError("An ingestion job is active. Drain the queue before CLI ingestion.")
        corpus_id = digest(
            config.fingerprint + "".join(sorted(e["sha256"] for e in entries)) + "py-v1"
        )[:32]
        job = next((j for j in jobs.all() if j["id"] == corpus_id), None)
        if job and job["status"] == "ready":
            return dict(status="ready", message="This evaluation corpus is already indexed.")
        if not job:
            job = dict(
                id=corpus_id,
                aiConfig=config.model_dump(),
                createdAt=now(),
                strategies=["fixed", "semantic"],
                status="queued",
                phase="queued",
                percent=0,
                files=[
                    dict(
                        id=e["sha256"][:32],
                        name=Path(e["localPath"]).name,
                        samplePath=e["localPath"],
                        known=e,
                        size=e.get("bytes", 0),
                    )
                    for e in entries
                ],
            )
            jobs.add(job)
        jobs.update(job["id"], status="processing", startedAt=now(), error=None)
        process = multiprocessing.get_context("spawn").Process(
            target=worker_main, args=(str(store.directory), job, settings.model_dump(mode="json"))
        )
        process.start()
        try:
            process.join(1800)
        finally:
            if process.is_alive():
                process.terminate()
                process.join(5)
                if process.is_alive():
                    process.kill()
                    process.join()
                jobs.update(
                    job["id"],
                    status="failed",
                    error="Processing exceeded 30 minutes or was interrupted. Retry to resume cached work.",
                )
            process.close()
        current = next(j for j in jobs.all() if j["id"] == job["id"])
        if current["status"] != "ready":
            if current["status"] == "processing":
                jobs.update(job["id"], status="failed", error="Worker stopped unexpectedly.")
            raise AppError(current.get("error") or "Ingestion did not complete.")
        return dict(
            status="ready", summaries=current["summaries"], documents=len(current["documents"])
        )


def freshness(settings, apply):
    store = Storage(settings.finwhiz_data_dir)
    with runtime_lock(store.directory):
        jobs = Jobs(store).all()
        if not apply:
            return operational_status(jobs, store.json("freshness-check"))
        docs = [d for j in jobs if j["status"] == "ready" for d in j.get("documents", [])]
        issuers = list(dict.fromkeys(d["cik"] for d in docs if d.get("cik")))
        if not issuers:
            raise AppError("No loaded CIK identifiers; verify filing metadata before checking SEC.")
        updates = []
        for cik in issuers:
            if not re.fullmatch(r"\d{1,10}", cik):
                raise AppError("Invalid CIK")
            recent = sec_request(
                f"https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json", settings
            )["filings"]["recent"]
            known = [d for d in docs if d["cik"] == cik]
            latest = max(d["reportDate"] for d in known)
            for i, form in enumerate(recent["form"]):
                if (
                    form in ("10-K", "10-Q", "10-K/A", "10-Q/A")
                    and recent["reportDate"][i] >= latest
                    and not any(d.get("accession") == recent["accessionNumber"][i] for d in known)
                ):
                    updates.append(
                        dict(
                            cik=cik,
                            ticker=known[0]["ticker"],
                            form=form,
                            reportDate=recent["reportDate"][i],
                            accession=recent["accessionNumber"][i],
                        )
                    )
            time.sleep(0.3)
        result = dict(checkedAt=now(), updates=updates, checkedCiks=issuers)
        store.put_json("freshness-check", result)
        return result


def export_project(destination):
    destination = destination.resolve()
    if (
        destination == ROOT
        or ROOT.is_relative_to(destination)
        or (destination.exists() and any(destination.iterdir()))
    ):
        raise AppError("Export destination must be a separate empty directory.")
    allowed_dirs = {"backend", "lib", "scripts", "docs", "evaluation", "pinecone", "specs", "data"}
    excluded = {
        ".git",
        ".venv",
        "node_modules",
        ".cache",
        ".pytest_cache",
        ".test-tmp",
        "__pycache__",
        "artifacts",
        "release",
        ".backups",
        ".runtime",
        ".runtime-evaluation",
        ".runtime-python",
    }
    files = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(ROOT)
        if destination in path.parents or any(
            p in excluded or p.startswith(".runtime-") or p.endswith(".egg-info")
            for p in relative.parts
        ):
            continue
        if len(relative.parts) > 1 and relative.parts[0] not in allowed_dirs:
            continue
        if relative.parts[:2] in {('data', 'embeddings'), ('data', 'chunks'), ('data', 'parents'), ('data', 'parsed'), ('data', 'raw')}:
            continue  # Generated caches are restored from the verified bundle, not deployed separately.
        if (
            (path.name.startswith(".env") and path.name != ".env.example")
            or path.suffix in (".pyc", ".sqlite3", ".db")
            or (relative.parts[:2] == ("data", "filings") and path.suffix in (".html", ".htm"))
        ):
            continue
        data = path.read_bytes()
        if re.search(
            rb"sk-[A-Za-z0-9_-]{20,}|nvapi-[A-Za-z0-9_-]{20,}|pcsk_[A-Za-z0-9_-]{20,}", data
        ):
            raise AppError("Credential signature found in export source: " + relative.as_posix())
        files.append((relative, data))
    destination.mkdir(parents=True, exist_ok=True)
    manifest = []
    for relative, data in files:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        manifest.append(dict(path=relative.as_posix(), sha256=digest(data)))
    write(destination / "SOURCE_MANIFEST.json", manifest)
    return dict(files=len(files), destination=str(destination))


def execute(args, settings):
    if args.command == 'agent-score-reviews':
        from .agent_evaluation import score_agent_reviews
        if not args.run:
            raise AppError('Specify --run=AGENT_EVALUATION_DIRECTORY.')
        return score_agent_reviews(args.run)
    if args.command == 'agent-evaluate':
        from .agent_evaluation import run_agent_evaluation
        return run_agent_evaluation(settings, args.queries, args.to or ROOT/'artifacts/agent-evals'/str(uuid.uuid4()),
                                    args.apply,args.provider,args.repetitions)
    if args.command == 'recover-library':
        from .bootstrap import recover_empty_library
        path = args.source or settings.finwhiz_bootstrap_library
        if not path:
            raise AppError('Specify --source=RECOVERY_BUNDLE or FINWHIZ_BOOTSTRAP_LIBRARY.')
        path = path if path.is_absolute() else ROOT / path
        store = Storage(settings.finwhiz_data_dir)
        with runtime_lock(store.directory):
            return recover_empty_library(store, Jobs(store), settings, path,
                                         settings.finwhiz_bootstrap_sha256, Providers, args.apply)
    if args.command in ('library-inventory', 'fiscal-metadata-plan'):
        from .library import inventory, compare_inventories
        import sqlite3
        path = settings.finwhiz_data_dir / 'jobs.sqlite3'
        if not path.is_file():
            raise AppError('No Python registry found. Export the deployed runtime before comparing libraries.')
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
            registered = [json.loads(row[0]) for row in db.execute('SELECT data FROM jobs ORDER BY ordinal')]
        store = Storage(settings.finwhiz_data_dir)
        if args.command == 'fiscal-metadata-plan':
            from .fiscal import metadata_backfill_plan
            if args.apply:
                raise AppError('This command produces a review plan only; it does not apply Pinecone changes.')
            return metadata_backfill_plan(registered, store)
        report = inventory(store, registered)
        if args.source:
            # json.loads(bytes) detects UTF-8/16 BOMs, including PowerShell redirects.
            return compare_inventories(report, json.loads(args.source.read_bytes()))
        return report
    config = settings.provider(args.provider)
    if args.command == "serve":
        import uvicorn
        from .api import create_app

        uvicorn.run(
            create_app(settings),
            host=args.host or settings.host,
            port=args.port or settings.port,
            workers=1,
            log_level="info",
        )
        return
    if args.command == "migrate":
        if not args.source or not args.to:
            raise AppError("Use migrate --source=LEGACY --to=EMPTY_DIRECTORY [--apply].")
        return migrate_legacy(args.source, args.to, args.apply)
    if args.command in ("snapshot", "restore"):
        if not args.to:
            raise AppError("Supply --to=EMPTY_DIRECTORY.")
        if args.command == "snapshot":
            return snapshot(args.source or settings.finwhiz_data_dir, args.to)
        if not args.source:
            raise AppError("Supply --source=BACKUP_DIRECTORY.")
        return restore(args.source, args.to)
    if args.command == "index":
        if not args.apply:
            return dict(dryRun=True, index=index_config(config))
        provider = Providers(settings, config)
        try:
            return ensure_index(provider)
        finally:
            provider.close()
    if args.command in ("collect", "download", "export-experiment", "export-project"):
        if not args.apply:
            return dict(
                dryRun=True,
                command=args.command,
                message="Add --apply to write output or contact SEC.",
            )
        if args.command == "collect":
            from .documents import iso_date

            if (
                args.as_of
                and not iso_date(args.as_of)
                or args.periods is not None
                and not 1 <= args.periods <= 40
            ):
                raise AppError("Use a valid --as-of date and --periods from 1 to 40.")
            return collect(
                settings,
                args.as_of,
                args.tickers.upper().split(",") if args.tickers else None,
                args.periods,
            )
        if args.command == "download":
            return download(settings)
        if args.command == "export-experiment":
            return export_experiment()
        return export_project(args.to or ROOT / "release/python")
    if args.command == "build-benchmark":
        candidates = benchmark_candidates()
        if args.apply:
            if args.queries.exists() and not args.replace:
                raise AppError(
                    "Benchmark already exists. Back it up and use --replace to replace reviewed labels."
                )
            write(args.queries, candidates)
        return dict(
            dryRun=not args.apply,
            candidates=candidates,
            message="Source-label candidates require independent review.",
        )
    if args.command in ("ingest", "load-corpus"):
        entries = evaluation_entries()
        if not args.apply:
            return dict(
                dryRun=True,
                filings=len(entries),
                provider=config.provider,
                message="--apply uses paid providers.",
            )
        if args.command == "ingest":
            return ingest_corpus(settings, config, entries)
        headers = {"X-AI-Provider": config.provider}
        if settings.app_password:
            headers["Authorization"] = "Bearer " + settings.app_password
        with httpx.Client(timeout=180) as client:
            result = client.post(
                args.base + "/api/uploads",
                headers=headers,
                files={
                    f"file_{i}": (
                        Path(e["localPath"]).name,
                        (ROOT / e["localPath"]).read_bytes(),
                        "text/html",
                    )
                    for i, e in enumerate(entries)
                },
                data={
                    "details": json.dumps(
                        [
                            {k: e[k] for k in ("ticker", "company", "reportDate", "form")}
                            for e in entries
                        ]
                    )
                },
            )
            if result.status_code != 202:
                raise AppError(f"Upload HTTP {result.status_code}: {result.text}")
            return result.json()
    if args.command == "freshness":
        return freshness(settings, args.apply)
    if args.command == "score-reviews":
        if not args.run:
            raise AppError("Supply --run=EVALUATION_DIRECTORY.")
        result = score_reviews(
            json.loads((args.run / "results.json").read_text()),
            json.loads((args.run / "manual-review.json").read_text()),
        )
        write(args.run / "MANUAL_ACCEPTANCE.json", result)
        return result
    if args.command == "evaluate":
        suite = json.loads(args.queries.read_text(encoding="utf-8"))
        validate_suite(suite)
        if not args.apply:
            return dict(
                dryRun=True,
                validated=len(suite),
                sourceReviewed=sum(q.get("reviewed") is True for q in suite),
                message="No provider calls. --apply runs retrieval; --answers adds generation.",
            )
        if any(q.get("reviewed") is not True for q in suite):
            raise AppError(
                "Review every gold source/policy expectation before a measured benchmark."
            )
        if args.judge and not args.answers:
            raise AppError("--judge requires --answers.")
        store = Storage(settings.finwhiz_data_dir)
        directory = ROOT / "artifacts/evaluation" / re.sub(r"[:.]", "-", now())
        directory.mkdir(parents=True)
        provider = Providers(settings, config)
        try:
            with runtime_lock(store.directory):

                def on_result(row, provenance):
                    write(directory / "provenance.json", provenance)
                    with (directory / "trials.jsonl").open("a", encoding="utf-8") as output:
                        output.write(json.dumps(row) + "\n")

                run = run_benchmark(
                    suite,
                    Jobs(store).all(),
                    store,
                    provider,
                    args.answers,
                    args.judge,
                    args.repetitions,
                    on_result=on_result,
                )
            write(directory / "results.json", run)
            (directory / "COMPARISON_REPORT.md").write_text(
                comparison_report(run), encoding="utf-8"
            )
            write(
                directory / "manual-review.json",
                [
                    {
                        **{k: r[k] for k in ("queryId", "strategy", "rerank", "repeat")},
                        "reviewer": "",
                        "reviewed": False,
                        "claims": [],
                        "numericCorrect": None,
                        "unitsCorrect": None,
                        "periodsCorrect": None,
                        "relevantAnswer": None,
                        "notes": "",
                    }
                    for r in run["results"]
                ],
            )
            return dict(directory=str(directory), summary=run["summary"])
        finally:
            provider.close()


def main():
    """Parse terminal arguments, load server settings and dispatch a CLI command.

    Entry point used by run.py and the package launcher. execute owns the
    selected command; commands can serve HTTP, ingest files, evaluate answers
    or perform explicitly requested maintenance, depending on their flags.
    """
    args = parser().parse_args()
    settings = Settings(_env_file=ROOT / ".env")
    if args.data_dir:
        settings = settings.model_copy(update={"finwhiz_data_dir": args.data_dir})
    logging.basicConfig(level=logging.INFO)
    try:
        result = execute(args, settings)
        if result is not None:
            print(json.dumps(result, ensure_ascii=False, indent=2))
    except (AppError, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
