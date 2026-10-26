"""Request-scoped deadlines and stage measurements, shared safely with search threads."""

import logging
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from .config import AppError

logger = logging.getLogger(__name__)
current_trace = ContextVar("finwhiz_trace", default=None)
stage_deadline = ContextVar('finwhiz_stage_deadline', default=None)


@dataclass
class ResearchTrace:
    """Request deadline, cancellation flag and thread-safe stage measurements.

    ResearchEndpoints creates one per chat; research() creates one for direct
    callers when needed. current_trace makes it available to model, vector
    and agent tools, including copied retrieval-thread contexts."""
    request_id: str
    timeout: float
    started: float = field(default_factory=time.monotonic)
    timings: dict = field(default_factory=dict)
    events: list = field(default_factory=list)
    cancelled: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def remaining(self):
        """Return seconds left or raise AppError on timeout/cancellation.

        Used by spans, transport, generation and agent budget checks. A cancelled
        request has no remaining budget even if its deadline has not elapsed."""
        remaining = self.timeout - (time.monotonic() - self.started)
        if self.cancelled.is_set() or remaining <= 0:
            raise AppError("Research exceeded its time budget. Select fewer periods or retry.", 504)
        return remaining

    def record(self, stage, duration, **details):
        """Accumulate a stage duration in milliseconds and append an event.

        Called by span and model/vector/agent instrumentation, including worker
        threads. Updates are locked and a matching log record is emitted; callers
        should pass operational metadata rather than credentials or raw prompts."""
        with self.lock:
            self.timings[stage] = round(self.timings.get(stage, 0) + duration, 2)
            self.events.append(dict(stage=stage, durationMs=round(duration, 2), **details))
        logger.info(
            "research request=%s stage=%s duration_ms=%.2f details=%s",
            self.request_id,
            stage,
            duration,
            {k: v for k, v in details.items() if k != 'outputs'},
        )

    def metrics(self):
        """Return a locked snapshot of request ID, timings and recorded events.

        Called by research() when building response metrics for the UI and
        diagnostics; durations are reported in milliseconds."""
        with self.lock:
            return dict(
                requestId=self.request_id,
                stageTimingsMs=dict(self.timings),
                operations=list(self.events),
            )


def remaining(default):
    trace = current_trace.get()
    seconds = min(default, trace.remaining()) if trace else default
    deadline = stage_deadline.get()
    if deadline is not None:
        seconds = min(seconds, deadline - time.monotonic())
        if seconds <= 0:
            raise AppError('This research step exceeded its time budget.', 504)
    return seconds


@contextmanager
def operation_budget(seconds):
    outer = stage_deadline.get()
    deadline = time.monotonic() + seconds
    token = stage_deadline.set(min(outer, deadline) if outer is not None else deadline)
    try:
        yield
    finally:
        stage_deadline.reset(token)


@contextmanager
def span(stage, **details):
    trace = current_trace.get()
    if trace:
        trace.remaining()
    started = time.monotonic()
    outcome = "ok"
    try:
        yield
    except BaseException as error:
        outcome = type(error).__name__
        raise
    finally:
        if trace:
            trace.record(stage, (time.monotonic() - started) * 1000, outcome=outcome, **details)
