"""Upload, retry and source download endpoints."""

import asyncio
import re
import uuid
from fastapi.responses import Response
from .config import ROOT, AppError
from .storage import now
from .library import accept_upload
from .http_helpers import response, public_job, receive_files, read_json


async def documents(request, path, *, settings, store, jobs):
    config = request.state.ai
    if path == '/api/sec/import' and request.method == 'POST':
        if not settings.configured(config):
            raise AppError('Configure the selected AI provider and PINECONE_API_KEY before importing reports.', 503)
        from .sec_import import import_reports
        result = await asyncio.to_thread(import_reports, await read_json(request), settings, config, store, jobs)
        result['job'] = public_job(result['job'])
        return response(result, 202 if result['created'] else 200)
    if path == "/api/uploads" and request.method == "POST":
        if not settings.configured(config):
            raise AppError(
                f"Configure {'NEBIUS_API_KEY' if config.provider == 'nebius' else 'OPENAI_API_KEY'} and PINECONE_API_KEY in server secrets before submitting files.",
                503,
            )
        files = await receive_files(request, store)
        job = dict(
            aiConfig=config.model_dump(),
            id=str(uuid.uuid4()),
            createdAt=now(),
            status="queued",
            phase="queued",
            percent=0,
            detail="Waiting to process",
            strategies=["fixed", "semantic"],
            files=files,
        )
        try:
            job, duplicates, created = await asyncio.to_thread(accept_upload, jobs, job)
        except BaseException:
            for file in files:
                store.remove(file["key"])
            raise
        retained = {file['key'] for file in job['files'] if file.get('key')}
        for file in files:
            if file['key'] not in retained:
                store.remove(file['key'])
        return response(
            dict(
                job=public_job(job),
                duplicates=duplicates,
                created=created,
                message=("New files queued for background processing. Existing copies were reused where available." if created else
                         "These files already exist for this ingestion configuration. No new indexing was scheduled. Retry the existing job if it failed."),
            ),
            202 if created else 200,
        )
    retry = re.fullmatch(r"/api/jobs/([a-f0-9-]+)/retry", path)
    if retry and request.method == "POST":
        return response({"job": public_job(await asyncio.to_thread(jobs.retry, retry[1]))}, 202)
    source = re.fullmatch(r"/api/sources/([a-f0-9-]+)", path)
    if source and request.method == "GET":
        file = next(
            (
                f
                for j in (await asyncio.to_thread(jobs.all))
                for f in j["files"]
                if f["id"] == source[1]
            ),
            None,
        )
        if file is None:
            raise AppError("Source not found.", 404)
        if file.get("samplePath"):
            sample_path = (ROOT / file["samplePath"]).resolve()
            if not sample_path.is_relative_to(ROOT / "data"):
                raise AppError("Source not found.", 404)
            data = await asyncio.to_thread(sample_path.read_bytes)
        else:
            data = await asyncio.to_thread(store.get, file["key"])
        return Response(
            data or b"",
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Disposition": 'attachment; filename="'
                + re.sub(r"[^a-zA-Z0-9._-]", "_", file["name"])
                + '"',
            },
        )
    return None
