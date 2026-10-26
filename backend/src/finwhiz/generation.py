"""Structured generation adapter and SDK failure classification."""

import json
import time
import httpx
from openai import APIConnectionError, APITimeoutError
from langchain_openai import ChatOpenAI
from langchain_core.exceptions import OutputParserException
from pydantic import ValidationError
from .config import AppError, compact
from .telemetry import remaining, span, current_trace


class GenerationClient:
    """Structured LLM generation adapter owned by Providers.generation.

    Providers.structured delegates here for agent decisions, reranking,
    answers, repairs and evaluation. Uses the shared HTTP client and request
    deadline; it does not retrieve documents or perform financial arithmetic."""
    def __init__(self, settings, config, client, base):
        """Keep references to the selected config and shared HTTP client.

        Called by Providers.__init__; base is the model API URL and no network
        request is made until structured is called."""
        self.settings, self.config, self.client, self.base = settings, config, client, base

    def structured(self, system, user, schema, model=None):
        """Run a bounded LangChain model call and return a schema-validated dict.

        Called through Providers.structured. system is the task prompt; user may
        be text or a payload dictionary; schema is a Pydantic output model; model
        optionally overrides the configured chat model. Chooses agent/rerank or
        generation timeout, disables SDK retries, classifies quota/transport
        failures and records usage. Raises AppError for refusal, truncation,
        invalid output, unavailable credentials or exhausted budgets."""
        if not self.settings.key(self.config):
            raise AppError("Configure the selected provider API key in server secrets.")
        if self.config.provider == "nebius":
            system += "\nReturn JSON matching this schema: " + compact(schema.model_json_schema())
        stage_timeout = (
            self.settings.agent_timeout_seconds
            if schema.__name__ in ('AgentDecision', 'AgentPlan')
            else self.settings.rerank_timeout_seconds
            if schema.__name__ == "Ranking"
            else self.settings.answer_repair_timeout_seconds
            if schema.__name__ == "Repair"
            else self.settings.generation_timeout_seconds
        )
        stage_name = {'Repair': 'Answer repair', 'Ranking': 'Evidence reranking',
                      'AgentPlan': 'Research planning', 'AgentDecision': 'Evidence assessment'}.get(schema.__name__, 'Answer generation')
        timeout_message = f'{stage_name} timed out. Retry the request.'
        started = time.monotonic()

        def time_left():
            left = stage_timeout - (time.monotonic() - started)
            if left <= 0:
                raise AppError(timeout_message, 504)
            return remaining(left)

        for attempt in range(self.settings.provider_attempts):
            try:
                # Explicit retry loop: SDK retries are disabled, so billing exhaustion is never retried.
                llm = ChatOpenAI(
                    model=model or self.config.chatModel,
                    api_key=self.settings.key(self.config),
                    base_url=self.base,
                    temperature=0,
                    timeout=time_left(),
                    max_retries=0,
                    http_client=self.client,
                    use_responses_api=False,
                )
                chain = llm.with_structured_output(
                    schema, method="json_schema", strict=True, include_raw=True
                )
                operation = {
                    "Ranking": "reranking",
                    "AgentDecision": "evidence_assessment",
                    "AgentPlan": "research_planning",
                    "Answer": "answer_generation",
                    "Repair": "answer_repair",
                }.get(schema.__name__, "model_generation")
                with span(operation, attempt=attempt + 1, model=model or self.config.chatModel,
                          provider=self.config.provider, schema=schema.__name__):
                    output = chain.invoke(
                        [
                            ("system", system),
                            ("human", user if isinstance(user, str) else compact(user)),
                        ]
                    )
                time_left()
                raw = output["raw"]
                trace = current_trace.get()
                if trace and raw.usage_metadata:
                    trace.record(
                        "model_usage",
                        0,
                        operation=operation,
                        **{
                            k: v
                            for k, v in raw.usage_metadata.items()
                            if k in ("input_tokens", "output_tokens", "total_tokens")
                        }
                    )
                if (
                    raw.additional_kwargs.get("refusal")
                    or raw.response_metadata.get("finish_reason") != "stop"
                ):
                    raise AppError(
                        "The model could not complete this response. Try a narrower question."
                    )
                if output.get("parsing_error") or output.get("parsed") is None:
                    raise AppError("AI response failed schema validation.")
                return schema.model_validate(output["parsed"].model_dump()).model_dump()
            except AppError:
                raise
            except (ValidationError, OutputParserException, json.JSONDecodeError):
                raise AppError("AI response failed schema validation.") from None
            except Exception as error:
                body = getattr(error, "body", {}) or {}
                body = body.get("error", body) if isinstance(body, dict) else {}
                if body.get("type") == "insufficient_quota" or body.get("code") in (
                    "insufficient_quota",
                    "credit_balance_exhausted",
                    "billing_hard_limit_reached",
                ):
                    raise AppError(
                        "Provider API quota is exhausted. Restore API credits or configure a key with available quota, then retry to resume cached work."
                    ) from None
                status = getattr(error, "status_code", None)
                if status not in (429, 500, 502, 503, 504) and not isinstance(
                    error, (httpx.TransportError, TimeoutError, APIConnectionError, APITimeoutError)
                ):
                    raise AppError(
                        "AI provider response failed. Check credentials and model structured-output support."
                    ) from None
                if attempt == self.settings.provider_attempts - 1:
                    if isinstance(error, (APITimeoutError, httpx.TimeoutException, TimeoutError)):
                        raise AppError(
                            timeout_message, 504
                        ) from None
                    raise AppError("Provider temporarily unavailable.") from None
                time.sleep(min(2**attempt, time_left()))
