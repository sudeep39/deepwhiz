"""Typed research input with the browser's established HTTP error contract."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from .config import AppError
from .documents import iso_date

Ticker = Annotated[str, Field(strict=True, pattern=r'^[A-Z][A-Z0-9.-]{0,9}$')]


class ResearchRequest(BaseModel):
    """Validated browser input for POST /api/chat and POST /api/scope.

    research_requests.parse_request constructs it inside ResearchEndpoints;
    validation failures become the established public HTTP 400 messages."""
    model_config = ConfigDict(strict=True)
    question: str = Field(min_length=1, max_length=3000)
    tickers: list[Ticker] = Field(min_length=1, max_length=8)
    periods: list[str] = Field(default_factory=list, max_length=32)
    strategy: Literal['fixed', 'semantic'] = 'fixed'
    rerank: bool = True

    @field_validator('question')
    @classmethod
    def nonblank(cls, value):
        """Reject whitespace-only questions and return stripped question text.

        Pydantic invokes this after field type/length validation in parse_request."""
        if not value.strip():
            raise ValueError('blank question')
        return value.strip()

    @field_validator('periods')
    @classmethod
    def dates(cls, value):
        """Validate every selected period as an ISO date and return the list.

        Pydantic invokes this during request parsing; parse_request translates
        ValueError into the public HTTP 400 period-selection error."""
        if any(not iso_date(day) for day in value):
            raise ValueError('invalid date')
        return value


def parse_request(body):
    normalized = {**body, 'periods': body.get('periods') or [], 'strategy': body.get('strategy') or 'fixed'}
    try:
        return ResearchRequest.model_validate(normalized)
    except ValidationError as error:
        field = error.errors()[0]['loc'][0]
        messages = {'question': 'Enter a question of up to 3,000 characters.',
                    'tickers': 'Select at least one company.', 'strategy': 'Invalid retrieval strategy.',
                    'periods': 'Invalid period selection.', 'rerank': 'Rerank must be a boolean.'}
        raise AppError(messages.get(field, 'Invalid research request.'), 400) from None
