"""Strict model outputs. Public request errors are translated by the API."""

from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """Base for LLM output contracts consumed by GenerationClient.

    Subclasses reject extra fields, coercion and nonfinite numeric values.
    These checks validate output shape; financial grounding is checked later."""
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Point(StrictModel):
    """One cited chart value nested inside Chart; validated by answer_validation."""
    label: str
    value: float
    source_ids: list[str]


class Chart(StrictModel):
    """Optional chart specification nested inside Answer and rendered by app.mjs."""
    type: Literal["none", "bar", "line"]
    title: str
    unit: str
    points: list[Point]


class Calculation(StrictModel):
    """Cited literal operands and an operation requested by an answer or repair.

    answer_validation checks the inputs; finance.calculate computes results.
    The model supplies operands, never a trusted computed result."""
    label: str
    operation: Literal[
        "sum",
        "difference",
        "ratio",
        "percentage",
        "percent_change",
        "gross_margin",
        "gross_margin_excluding_revenue",
    ]
    unit: str
    values: list[float]
    source_ids: list[str]


class Answer(StrictModel):
    """Structured initial answer returned by research generation.

    GenerationClient validates this shape; research then checks facts, scope
    and citations before returning it to the browser."""
    status: Literal["answered", "insufficient_evidence", "needs_clarification"]
    answer: str
    citation_ids: list[str]
    chart: Chart
    calculations: list[Calculation]


class Ranking(StrictModel):
    """Ordered candidate IDs returned by the retrieval reranking call.

    retrieval.complete_ranking repairs omissions without inventing candidates."""
    ids: list[str]


class AgentDecision(StrictModel):
    """Bounded next-action contract returned by agentic.assess.

    The research graph routes search/table actions; agentic validates IDs and
    the host preserves scope, deadlines and maximum gathering rounds."""
    action: Literal['answer', 'search', 'read_table', 'insufficient_evidence']
    query: str = Field(max_length=500)
    source_ids: list[Annotated[str, Field(pattern=r'^S\d+$', max_length=16)]] = Field(max_length=4)
    missing_evidence: list[Annotated[str, Field(max_length=200)]] = Field(max_length=8)
    task_id: str = Field(default='', max_length=24)
    target_tickers: list[str] = Field(default_factory=list, max_length=8)
    filing_dates: list[str] = Field(default_factory=list, max_length=32)
    retrieval_mode: Literal['hybrid', 'lexical', 'dense'] = 'hybrid'
    top_k: int = Field(default=5, ge=1, le=15)


class ResearchTask(StrictModel):
    ticker: str = Field(max_length=10)
    report_date: str = Field(max_length=10)
    period: str = Field(max_length=24)
    metric: str = Field(max_length=80)
    objective: str = Field(max_length=240)


class AgentPlan(StrictModel):
    tasks: list[ResearchTask] = Field(min_length=1, max_length=32)


class Sentence(StrictModel):
    """One repaired sentence and its supporting source IDs, nested inside Repair."""
    text: str
    source_ids: list[str]


class Repair(StrictModel):
    """Structured replacement sentences/calculations for one failed answer.

    research.repair renders citations, clears charts and validates the result
    again. It cannot bypass financial checks or start an unbounded repair loop."""
    sentences: list[Sentence]
    calculations: list[Calculation]


class Claim(StrictModel):
    """Per-claim evaluation judgment nested inside Review.

    Used by the CLI evaluation workflow, not interactive validation."""
    claim: str
    supported: bool
    citationCorrect: bool


class Review(StrictModel):
    """Structured model judgment used by evaluation to score an answer.

    Contains claim, numeric, unit and period judgments; does not replace the
    deterministic answer_validation checks in the chat workflow."""
    claims: list[Claim]
    numericCorrect: bool
    unitsCorrect: bool
    periodsCorrect: bool
    notes: str
