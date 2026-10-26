"""Research endpoints and request-scoped task ownership."""
import asyncio
import time
from fastapi import Request
from .config import AppError, job_matches
from .research_requests import parse_request
from .http_helpers import response, read_json
from .research import research
from .scope import resolve_scope
from .fiscal import catalog_documents
from .telemetry import ResearchTrace
from .evidence_gaps import evidence_gaps


class ResearchEndpoints:
    """Application-scoped research routes and in-flight chat ownership.

    api.create_app constructs one instance; routes.register_routes forwards
    chat/scope POST requests to it. Owns admission limits and pending tasks,
    while research.research executes the financial workflow in a thread."""
    def __init__(self, settings, store, jobs, pool, pending):
        """Bind application services and initialize admission/task bookkeeping.

        Called once by api.create_app. pending is the shared task set awaited at
        shutdown; provider clients are owned by the supplied ProviderPool."""
        self.settings, self.store, self.jobs, self.pool = settings, store, jobs, pool
        self.pending, self.active, self.chat_limits = pending, 0, {}

    async def __call__(self, request: Request, path: str):
        """Handle authenticated chat/scope POST requests and return JSON responses.

        Called by explicit research routes after API security middleware. Parses
        ResearchRequest, resolves scope, returns preflight results or starts
        research in a thread. Enforces request/concurrency limits and timeout,
        tracks shielded tasks until completion, and signals cancellation on
        client/global timeout. Returns None for an unmatched path/method."""
        settings, store, jobs, pool = self.settings, self.store, self.jobs, self.pool
        chat_limits, pending_research = self.chat_limits, self.pending
        config = request.state.ai
        if path in ("/api/chat", "/api/scope") and request.method == "POST":
            client = request.client.host if request.client else "local"
            current = time.monotonic()
            for key in list(chat_limits):
                if current - chat_limits[key][0] > 60:
                    del chat_limits[key]
            start, count = chat_limits.get(client, (current, 0))
            if path == '/api/chat':
                chat_limits[client] = start, count + 1
            if path == '/api/chat' and count + 1 > 12:
                raise AppError("Please wait a moment before sending another question.", 429)
            body = await read_json(request)
            parsed = parse_request(body)
            question, tickers, strategy, periods = parsed.question, parsed.tickers, parsed.strategy, parsed.periods
            ready = [
                j
                for j in (await asyncio.to_thread(jobs.all))
                if j["status"] == "ready" and job_matches(j, config)
            ]
            if any(
                not any(any(d["ticker"] == ticker for d in j.get("documents", [])) for j in ready)
                for ticker in tickers
            ):
                raise AppError(
                    "The selected company is not ready yet. Finish loading its filings first.", 409
                )
            history = body.get("history", [])
            history = (
                [
                    dict(role=m["role"], content=m["content"][:1500], scope=m.get('scope'))
                    for m in history[-4:]
                    if isinstance(m, dict)
                    and m.get("role") in ("user", "assistant")
                    and isinstance(m.get("content"), str)
                ]
                if isinstance(history, list)
                else []
            )
            scoped_jobs = [job for job in ready if job.get('namespaces', {}).get(strategy)]
            documents = await asyncio.to_thread(catalog_documents, scoped_jobs, store)
            resolved = resolve_scope(question.strip(), documents, tickers, periods, history)
            requested = resolved.get('requestedPeriods', [])
            import re
            if resolved['status']=='insufficient_evidence' and requested and all(re.fullmatch(r'\d{4}-(?:Q[1-4]|FY)|\d{4}-\d{2}-\d{2}', p) for p in requested):
                recovery_periods = list(requested)
                # Q4 requires annual minus Q3 year-to-date; fetch both dependencies.
                for p in requested:
                    if p.endswith('-Q4'):
                        recovery_periods.append(p[:-2]+'Q3')
                resolved['recovery'] = dict(companies=resolved['tickers'], periods=1,
                                             requestedPeriods=list(dict.fromkeys(recovery_periods)))
            if resolved['status'] == 'insufficient_evidence':
                resolved['evidenceGaps'] = evidence_gaps(question, resolved)
            if path == '/api/scope':
                return response(resolved)
            if resolved['status'] != 'resolved':
                from .research import empty_answer
                return response({**empty_answer(resolved['status'], resolved['message']), 'scope': resolved,
                                 **({'evidenceGaps': resolved['evidenceGaps']} if resolved.get('evidenceGaps') else {})})
            if self.active >= 2:
                raise AppError("Two questions are already processing. Please retry shortly.", 429)
            self.active += 1
            context = ResearchTrace(request.state.request_id, settings.chat_timeout_seconds)

            async def execute_research():
                try:
                    provider = pool.get(config)
                    return await asyncio.to_thread(
                        research,
                        store,
                        ready,
                        provider,
                        question.strip(),
                        tickers,
                        periods,
                        strategy,
                        parsed.rerank,
                        history,
                        context=context,
                        scope_result=resolved,
                    )
                finally:
                    self.active -= 1

            task = asyncio.create_task(execute_research())
            pending_research.add(task)

            def finished(completed):
                pending_research.discard(completed)
                if not completed.cancelled():
                    completed.exception()  # consume exceptions even after a client timeout

            task.add_done_callback(finished)
            try:
                result = await asyncio.wait_for(asyncio.shield(task), settings.chat_timeout_seconds)
                return response(result)
            except (TimeoutError, asyncio.CancelledError) as error:
                context.cancelled.set()
                if isinstance(error, asyncio.CancelledError):
                    raise
                raise AppError(
                    "Research exceeded its time budget. Select fewer periods or retry.", 504
                ) from None
        return None

