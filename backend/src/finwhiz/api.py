"""Compatible browser API; application services own the financial behavior."""

import asyncio
import hmac
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import Response

from .config import ROOT, AppError, Settings
from .ingestion import worker_main
from .providers import Providers, ProviderPool
from .supervisor import supervise
from .http_helpers import response, read_json, BodyLimit
from .storage import Jobs, Storage, runtime_lock

logger = logging.getLogger(__name__)
SECURITY = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
}
STATIC = {
    "/": ("index.html", "text/html"),
    "/app.mjs": ("app.mjs", "text/javascript"),
    "/style.css": ("style.css", "text/css"),
    "/data/experiment-filings.json": ("data/experiment-filings.json", "application/json"),
    "/data/experiment-filings.csv": ("data/experiment-filings.csv", "text/csv"),
}


def create_app(
    settings=None,
    *,
    disable_workers=False,
    provider_factory=Providers,
    worker_target=worker_main,
    processing_timeout_seconds=1800,
):
    """Assemble FastAPI routes, security, shared services and lifespan ownership.

    Called by the serve CLI and offline API tests. Initializes Storage, Jobs,
    ProviderPool and ResearchEndpoints; lifespan owns the runtime lock, optional
    recovery and worker supervision. disable_workers/factories enable isolated
    tests. Returns the application; startup work runs when its lifespan starts.
    """
    settings = settings or Settings(_env_file=ROOT / ".env")
    if settings.replit_deployment and settings.finwhiz_bootstrap_auto and not settings.finwhiz_bootstrap_library:
        from .bootstrap import DEFAULT_BUNDLE_SHA256, RETAINED_BUNDLE_SHA256
        candidates = [('data/recovery/deepwhiz-retained-library-v1.json.gz', RETAINED_BUNDLE_SHA256),
                      ('data/recovery/replit-library-v1.json.gz', DEFAULT_BUNDLE_SHA256)]
        for name, checksum in candidates:
            bundled = ROOT / name
            if bundled.is_file():
                settings = settings.model_copy(update={'finwhiz_bootstrap_library': bundled,
                                                       'finwhiz_bootstrap_sha256': checksum})
                break
    if settings.finwhiz_backend_url:
        raise AppError('A shared backend is configured. Use restart.cmd to start its local frontend instead of creating another library owner.')
    store = Storage(settings.finwhiz_data_dir)
    if store.path("state").exists() and not (store.directory / "jobs.sqlite3").exists():
        raise AppError(
            "Legacy runtime detected. Stop Node and use run.py migrate --source=LEGACY --to=NEW_DIRECTORY --apply, then set FINWHIZ_DATA_DIR to the new directory."
        )
    jobs = Jobs(store)
    auth_limits = {}
    pool = ProviderPool(settings, provider_factory)
    pending_research = set()
    from .research_routes import ResearchEndpoints
    research_endpoints = ResearchEndpoints(settings, store, jobs, pool, pending_research)
    from .library_persistence import CloudLibrary, protect_loop, pack_library, restore_library
    from .config import digest
    cloud = CloudLibrary(settings) if settings.finwhiz_cloud_library else None

    @asynccontextmanager
    async def lifespan(app):
        with runtime_lock(store.directory):
            if settings.finwhiz_library_restore_path and not jobs.all():
                from pathlib import Path
                archive = Path(settings.finwhiz_library_restore_path)
                archive = archive if archive.is_absolute() else ROOT/archive
                data = await asyncio.to_thread(archive.read_bytes)
                if digest(data) != settings.finwhiz_library_restore_sha256:
                    raise AppError('Library restore checksum mismatch; no registry changes were made.')
                await asyncio.to_thread(restore_library, data, store, jobs)
            if cloud:
                await asyncio.to_thread(cloud.restore,store,jobs)
                records = jobs.all()
                if records and not any(j['status'] in ('queued','processing') for j in records):
                    await asyncio.to_thread(cloud.checkpoint,store,jobs)
            if settings.finwhiz_bootstrap_library:
                from .bootstrap import recover_empty_library
                path = settings.finwhiz_bootstrap_library
                path = path if path.is_absolute() else ROOT / path
                app.state.recovery = await asyncio.to_thread(
                    recover_empty_library, store, jobs, settings, path,
                    settings.finwhiz_bootstrap_sha256, provider_factory, True)
                logger.info('library bootstrap status=%s documents=%s reused_vectors=%s',
                            app.state.recovery['status'], app.state.recovery.get('documents'),
                            app.state.recovery.get('reusedVectors'))
            await asyncio.to_thread(jobs.recover)
            task = (
                None
                if disable_workers
                else asyncio.create_task(
                    supervise(jobs, store, settings, worker_target, processing_timeout_seconds)
                )
            )
            app.state.worker_task = task
            protection_task = asyncio.create_task(protect_loop(cloud,store,jobs)) if cloud else None
            try:
                yield
            finally:
                if protection_task:
                    protection_task.cancel()
                    await asyncio.gather(protection_task,return_exceptions=True)
                if task:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                if pending_research:
                    await asyncio.gather(*pending_research, return_exceptions=True)
                if app.state.maintenance_task:
                    await asyncio.gather(app.state.maintenance_task, return_exceptions=True)
                pool.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(BodyLimit)
    app.state.store, app.state.jobs = store, jobs
    app.state.maintenance = False
    app.state.active_mutations = 0
    app.state.maintenance_task = None

    @app.get('/api/ready', include_in_schema=False)
    async def readiness():
        """Report configuration, storage and worker readiness without exposing secrets."""
        def database_ok():
            if not jobs.path.is_file():
                return False
            with jobs.connection(write=False) as db:
                db.execute('SELECT COUNT(*) FROM jobs').fetchone()
                return db.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
        try:
            storage_ok = await asyncio.wait_for(asyncio.to_thread(database_ok), 2)
        except Exception:
            storage_ok = False
        production = bool(settings.replit_deployment or settings.node_env == 'production')
        checks = dict(storage=storage_ok, credentials=any(settings.configured(settings.provider(p)) for p in ('openai', 'nebius')),
                      accessControl=not production or len(settings.app_password) >= 12,
                      maintenance=not app.state.maintenance,
                      worker=disable_workers or bool(getattr(app.state, 'worker_task', None) and not app.state.worker_task.done()))
        ready = all(checks.values())
        return response(dict(ready=ready, checks=checks), 200 if ready else 503)

    @app.middleware("http")
    async def security(request, call_next):
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        started = time.monotonic()
        selected = False
        tracked_mutation = False
        try:
            request.state.ai = settings.provider(request.headers.get("x-ai-provider"))
            selected = True
            if request.url.path.startswith("/api/") and request.url.path not in ("/api/health", '/api/ready'):
                if (
                    settings.replit_deployment or settings.node_env == "production"
                ) and not settings.app_password:
                    raise AppError(
                        "Set APP_PASSWORD in deployment secrets before enabling this shared workspace.",
                        503,
                    )
                if (settings.replit_deployment or settings.node_env == 'production') and len(settings.app_password) < 12:
                    raise AppError('Set APP_PASSWORD to at least 12 characters before enabling this shared workspace.', 503)
                client = request.client.host if request.client else "local"
                current = time.monotonic()
                for key in list(auth_limits):
                    if current - auth_limits[key][0] > 60:
                        del auth_limits[key]
                if auth_limits.get(client, (0, 0))[1] >= 20:
                    raise AppError("Too many password attempts. Wait a minute.", 429)
                provided = re.sub(r"^Bearer ", "", request.headers.get("authorization", ""))
                if settings.app_password and not hmac.compare_digest(
                    provided.encode(), settings.app_password.encode()
                ):
                    start, count = auth_limits.get(client, (current, 0))
                    auth_limits[client] = start, count + 1
                    raise AppError("Enter your workspace password.", 401)
                origin = request.headers.get("origin")
                if request.method in ('POST', 'PUT', 'PATCH', 'DELETE') and origin:
                    parsed = urlparse(origin)
                    same_origin = f'{request.url.scheme}://{request.headers.get("host", "")}'
                    allowed = {same_origin, *(value.rstrip('/') for value in settings.allowed_origins)}
                    if parsed.scheme not in ('http', 'https') or parsed.path or parsed.query or parsed.fragment or origin not in allowed:
                        raise AppError('Origin not allowed.', 403)
            if request.method == 'POST' and request.url.path.startswith('/api/'):
                if app.state.maintenance:
                    raise AppError('Library maintenance is running. Retry when it finishes.', 409)
                if not re.fullmatch(r'/api/jobs/[a-f0-9-]+/deduplicate', request.url.path):
                    app.state.active_mutations += 1
                    tracked_mutation = True
            result = await call_next(request)
        except AppError as error:
            result = response({"error": str(error)}, error.status)
        except Exception as error:
            logger.exception("request failed id=%s type=%s", request_id, type(error).__name__)
            result = response({"error": f"The request could not be completed. Reference: {request_id}. Retry; if it persists, check the server log for this reference.", "requestId": request_id}, 502)
        finally:
            if tracked_mutation:
                app.state.active_mutations -= 1
        result.headers["X-Request-ID"] = request_id
        if selected:
            result.headers.update(SECURITY)
        logger.info(
            "request id=%s path=%s status=%s duration_ms=%d",
            request_id,
            request.url.path,
            result.status_code,
            (time.monotonic() - started) * 1000,
        )
        return result

    @app.get('/api/library/export')
    async def export_library():
        data,_ = await asyncio.to_thread(pack_library,store,jobs)
        return Response(data,media_type='application/zip',headers={'Content-Disposition':'attachment; filename="deepwhiz-library.zip"','Cache-Control':'no-store'})

    @app.post('/api/library/checkpoint')
    async def checkpoint_library():
        if not cloud:
            raise AppError('Enable FINWHIZ_CLOUD_LIBRARY with a Replit App Storage bucket before protecting the library. Download a library export before republishing.',409)
        return response(await asyncio.to_thread(cloud.checkpoint,store,jobs,True))

    @app.exception_handler(AppError)
    async def application_error(request, error):
        return response({"error": str(error)}, error.status)

    async def deduplicate_route(request, path):
        match = re.fullmatch(r'/api/jobs/([a-f0-9-]+)/deduplicate', path)
        if not match or request.method != 'POST':
            return None
        if not settings.app_password:
            raise AppError('Set APP_PASSWORD before using the maintenance API.', 403)
        from .deduplication import preview, public_plan, apply
        body = await read_json(request)
        keep_id = body.get('keepJobId')
        if not isinstance(keep_id, str) or not re.fullmatch(r'[a-f0-9-]{1,64}', keep_id) or type(body.get('apply', False)) is not bool:
            raise AppError('Supply keepJobId and an optional boolean apply flag.', 400)
        plan = await asyncio.to_thread(preview, jobs, match[1], keep_id)
        if not body.get('apply', False):
            return response(public_plan(plan))
        if body.get('confirmToken') != plan['confirmToken']:
            raise AppError('Preview this merge and supply its current confirmToken before applying.', 409)
        if app.state.active_mutations or research_endpoints.active or app.state.maintenance:
            raise AppError('Wait for active uploads, research or maintenance to finish.', 409)
        if request.state.ai.provider != plan['provider'] or request.state.ai.indexName != plan['index']:
            raise AppError('Select the duplicate job\'s AI provider before applying.', 409)
        app.state.maintenance = True
        async def execute():
            try:
                provider = pool.get(request.state.ai)
                return await asyncio.to_thread(apply, jobs, plan, provider)
            finally:
                app.state.maintenance = False
        task = asyncio.create_task(execute())
        app.state.maintenance_task = task
        task.add_done_callback(lambda completed: None if completed.cancelled() else completed.exception())
        return response(await asyncio.shield(task))

    async def static_route(request: Request):
        path = request.url.path
        if path.startswith("/api/"):
            return response({"error": "Endpoint not found."}, 404)
        if path in STATIC and request.method == "GET":
            file, content_type = STATIC[path]
            return Response(
                (ROOT / file).read_bytes(),
                headers={
                    "Content-Type": content_type + "; charset=utf-8",
                    "Cache-Control": "no-store",
                },
            )
        return Response("Not found", status_code=404)

    from .routes import register_routes

    from functools import partial
    from .workspace_routes import workspace
    from .document_routes import documents
    register_routes(app, workspace=partial(workspace, settings=settings, store=store, jobs=jobs, cloud=cloud),
                    documents=partial(documents, settings=settings, store=store, jobs=jobs),
                    research=research_endpoints, maintenance=deduplicate_route, fallback=static_route)
    return app
