"""API areas with explicit routes; unknown methods retain legacy 404 behavior."""

from fastapi import APIRouter, Request


def register_routes(app, *, workspace, documents, research, maintenance, fallback):
    methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
    def endpoint(handler):
        async def handle(request: Request):
            return await handler(request, request.url.path)
        return handle

    for area, handler, paths in [
        ('workspace', workspace, {'/api/health': 'GET', '/api/state': 'GET', '/api/operations': 'GET'}),
        ('documents', documents, {'/api/sec/import': 'POST', '/api/uploads': 'POST', '/api/jobs/{job_id}/retry': 'POST', '/api/sources/{source_id}': 'GET'}),
        ('maintenance', maintenance, {'/api/jobs/{job_id}/deduplicate': 'POST'}),
        ('research', research, {'/api/chat': 'POST', '/api/scope': 'POST'}),
    ]:
        router = APIRouter(tags=[area])
        for path, method in paths.items():
            router.add_api_route(path, endpoint(handler), methods=[method])
        app.include_router(router)

    async def unknown(request: Request, path: str):
        return await fallback(request)

    app.add_api_route("/{path:path}", unknown, methods=methods, include_in_schema=False)
