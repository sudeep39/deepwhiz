"""Workspace state and operational reporting endpoints."""

import asyncio
import json
from .config import ROOT, job_matches, digest
from .operations import operational_status
from .http_helpers import response, public_job


async def workspace(request, path, *, settings, store, jobs, cloud=None):
    config = request.state.ai
    if path == "/api/health":
        return response({"ok": True})
    if path == "/api/state" and request.method == "GET":
        all_jobs = await asyncio.to_thread(jobs.all)
        documents = [
            {**doc, "jobId": job["id"], "strategies": job["strategies"]}
            for job in all_jobs
            if job["status"] == "ready" and job_matches(job, config)
            for doc in job.get("documents", [])
        ]
        companies = []
        for doc in documents:
            company = next((c for c in companies if c["ticker"] == doc["ticker"]), None)
            if company is None:
                company = dict(name=doc["company"], ticker=doc["ticker"], periods=[])
                companies.append(company)
            if not any(
                p["reportDate"] == doc["reportDate"] and p["form"] == doc["form"]
                for p in company["periods"]
            ):
                company["periods"].append(
                    dict(
                        reportDate=doc["reportDate"],
                        form=doc["form"],
                        url=doc["sourceUrl"],
                        ready=True,
                    )
                )
        return response(
            dict(
                companies=companies,
                documents=documents,
                **({'recovery': store.json('library-bootstrap')} if settings.finwhiz_bootstrap_library else {}),
                jobs=[public_job(j) for j in all_jobs],
                config=dict(
                    providersConfigured=settings.configured(config),
                    aiProvider=config.provider,
                    aiProviders=[
                        {**c.model_dump(), "configured": settings.configured(c)}
                        for c in [settings.provider("openai"), settings.provider("nebius")]
                    ],
                    storageBackend="filesystem+replit-app-storage" if cloud else "filesystem",
                    libraryProtection=({**cloud.status, **({'status':'pending','message':'New changes are awaiting a verified cloud checkpoint.'} if cloud.signature != digest(all_jobs) and cloud.status.get('status') != 'error' else {})} if cloud else dict(enabled=False,status='unprotected',message='Cloud library protection is not configured. Export a backup before republishing.')),
                    authenticated=bool(settings.app_password),
                    asOf=json.loads((ROOT / "experiment.json").read_text())["as_of"],
                ),
                capabilities=dict(hybrid=True, rerank=True, strategies=["fixed", "semantic"]),
            )
        )
    if path == "/api/operations" and request.method == "GET":
        return response(
            operational_status((await asyncio.to_thread(jobs.all)), store.json("freshness-check"))
        )
    return None
