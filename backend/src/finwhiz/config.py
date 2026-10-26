"""Validated settings and immutable provider snapshots; credentials stay server-side."""

import hashlib
import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def project_root():
    explicit = os.environ.get("FINWHIZ_PROJECT_ROOT")
    candidates = (
        [Path(explicit)]
        if explicit
        else [*Path(__file__).resolve().parents, Path.cwd(), *Path.cwd().parents]
    )
    for candidate in candidates:
        if (candidate / "experiment.json").is_file() and (candidate / "index.html").is_file():
            return candidate.resolve()
    raise RuntimeError("Run from the DeepWhiz project directory or set FINWHIZ_PROJECT_ROOT.")


ROOT = project_root()


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(value):
    data = (
        value
        if isinstance(value, bytes)
        else (value if isinstance(value, str) else compact(value)).encode()
    )
    return hashlib.sha256(data).hexdigest()


class AppError(Exception):
    """Expected application failure with a public message and HTTP status.

    Raised by services; api.create_app translates it into a JSON error response."""
    def __init__(self, message, status=502):
        """Initialize a public error message and HTTP status (default 502).

        Called by service failure paths; api.application_error reads status."""
        super().__init__(message)
        self.status = status


class AIConfig(BaseModel):
    """Immutable, nonsecret provider/model/index snapshot.

    Built by Settings.provider; stored with jobs and used by ProviderPool and
    job_matches to keep incompatible embedding spaces separate."""
    model_config = ConfigDict(frozen=True, extra="ignore")
    provider: Literal["openai", "nebius"]
    label: str
    embeddingModel: str = Field(min_length=1)
    dimension: int = Field(ge=1, le=4096)
    indexName: str = Field(min_length=1)
    chatModel: str = Field(min_length=1)
    judgeModel: str = Field(min_length=1)

    @property
    def fingerprint(self):
        """Return the compatibility hash for this embedding space.

        Used by job_matches, embedding caches, ingestion and maintenance. The hash
        includes provider, embedding model, dimensions, index and query-instruction
        version; chat/judge model changes do not invalidate stored vectors."""
        return digest(
            [
                self.provider,
                self.embeddingModel,
                self.dimension,
                self.indexName,
                "query-instruction-v1",
            ]
        )


class Settings(BaseSettings):
    """Validated server configuration, including credentials and research budgets.

    Created by api.create_app and cli.main; passed into provider, storage,
    worker and research services. Keep this object on the server."""
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)
    ai_provider: Literal["openai", "nebius"] = "openai"
    openai_api_key: str = ""
    nebius_api_key: str = ""
    pinecone_api_key: str = ""
    openai_chat_model: str = "gpt-4.1-mini"
    openai_judge_model: str = ""
    nebius_chat_model: str = "Qwen/Qwen3-30B-A3B-Instruct-2507"
    nebius_judge_model: str = ""
    nebius_embedding_model: str = "Qwen/Qwen3-Embedding-8B"
    nebius_embedding_dimensions: int = Field(default=4096, ge=1, le=4096)
    pinecone_index: str = "finwhiz-filings-v1"
    pinecone_nebius_index: str = "finwhiz-nebius-v1"
    app_password: str = ""
    finwhiz_shared_backend_password: str = ""
    finwhiz_backend_url: str = ""
    finwhiz_bootstrap_library: Path | None = None
    finwhiz_bootstrap_sha256: str = ""

    @field_validator('finwhiz_bootstrap_library', mode='before')
    @classmethod
    def optional_bootstrap_path(cls, value):
        """Normalize an empty bootstrap environment setting to None.

        Pydantic calls this before validating finwhiz_bootstrap_library."""
        return None if value == '' else value
    node_env: str = ""
    allowed_origins: list[str] = Field(default_factory=list)
    replit_deployment: str = ""
    finwhiz_data_dir: Path = ROOT / ".runtime-python"
    host: str = "0.0.0.0"
    port: int = 3100
    sec_user_agent: str = ""
    sec_import_timeout_seconds: float = Field(default=90, gt=0, le=100)
    chat_timeout_seconds: float = Field(default=60, gt=0, le=300)
    vector_timeout_seconds: float = Field(default=8, gt=0, le=60)
    embedding_timeout_seconds: float = Field(default=15, gt=0, le=120)
    generation_timeout_seconds: float = Field(default=30, gt=0, le=120)
    answer_repair_timeout_seconds: float = Field(default=12, gt=0, le=60)
    rerank_timeout_seconds: float = Field(default=10, gt=0, le=60)
    provider_attempts: int = Field(default=2, ge=1, le=4)
    rerank_max_tokens: int = Field(default=12000, ge=1000)
    answer_max_tokens: int = Field(default=20000, ge=1000)
    agentic_rag_enabled: bool = True
    agent_max_rounds: int = Field(default=6, ge=0, le=8)
    agent_timeout_seconds: float = Field(default=8, gt=0, le=20)
    agent_max_tokens: int = Field(default=8000, ge=1000, le=20000)
    agent_answer_reserve_seconds: float = Field(default=20, gt=0, le=60)

    @field_validator("finwhiz_data_dir")
    @classmethod
    def resolve_runtime(cls, value):
        """Resolve the runtime directory relative to the project root.

        Pydantic calls this for finwhiz_data_dir during Settings construction."""
        return value.resolve() if value.is_absolute() else (ROOT / value).resolve()

    def provider(self, selection=None):
        """Build the selected provider's nonsecret AIConfig snapshot.

        Called by request middleware and CLI/worker setup. selection may override
        the default provider; unsupported names raise AppError. Reads index
        defaults locally and performs no network calls."""
        selection = selection or self.ai_provider
        if selection not in ("openai", "nebius"):
            raise AppError("Choose OpenAI or Nebius Token Factory.", 400)
        nebius = selection == "nebius"
        template = json.loads((ROOT / "pinecone/index.json").read_text())
        chat = self.nebius_chat_model if nebius else self.openai_chat_model
        return AIConfig(
            provider=selection,
            label="Nebius Token Factory" if nebius else "OpenAI",
            embeddingModel=(
                self.nebius_embedding_model if nebius else template["tags"]["embedding-model"]
            ),
            dimension=self.nebius_embedding_dimensions if nebius else template["dimension"],
            indexName=self.pinecone_nebius_index if nebius else self.pinecone_index,
            chatModel=chat,
            judgeModel=(self.nebius_judge_model if nebius else self.openai_judge_model) or chat,
        )

    def key(self, config):
        """Return the selected provider's server-side API credential.

        Used by Providers.embed and GenerationClient. Never serialize this value
        into job snapshots, browser responses or diagnostic logs."""
        return self.nebius_api_key if config.provider == "nebius" else self.openai_api_key

    def configured(self, config):
        """Report whether model and Pinecone credentials are both present.

        Used by workspace/CLI readiness checks; it does not verify credentials
        or contact either provider."""
        return bool(self.key(config) and self.pinecone_api_key)


def job_matches(job, config):
    if not job.get("aiConfig"):
        return config.provider == job.get("provider", "openai")
    return AIConfig.model_validate(job["aiConfig"]).fingerprint == config.fingerprint


def index_config(config):
    template = json.loads((ROOT / "pinecone/index.json").read_text())
    return {
        **template,
        "name": config.indexName,
        "dimension": config.dimension,
        "tags": {
            **template["tags"],
            "embedding-model": config.embeddingModel,
            "embedding-dimensions": str(config.dimension),
            "embedding-provider": config.provider,
        },
    }
