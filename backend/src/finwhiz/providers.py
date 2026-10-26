"""LangChain generation, validated embeddings and Pinecone transport."""

import math
import ssl
import time
import threading
from collections import OrderedDict

import httpx
import truststore
from langchain_core.embeddings import Embeddings

from .config import AppError, compact, digest
from .documents import token_count
from .transport import JsonTransport
from .generation import GenerationClient
from .vector_store import PineconeClient
from .telemetry import span, current_trace


class Providers(Embeddings):
    """Provider facade used by ingestion, retrieval, research and evaluation.

    ProviderPool owns API instances; CLI/worker entry points may construct them
    directly. Delegates generation to GenerationClient, JSON HTTP calls to
    JsonTransport, and vector operations to PineconeClient. Also implements
    the LangChain Embeddings interface."""
    def __init__(self, settings, config, client=None):
        """Bind settings/config and create transport, generation and index adapters.

        Called by ProviderPool or CLI/worker setup. An injected HTTP client enables
        offline tests; initialization itself does not call a remote provider."""
        self.settings, self.config = settings, config
        self.client = client or httpx.Client(
            timeout=settings.generation_timeout_seconds,
            follow_redirects=False,
            verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
        )
        self.transport = JsonTransport(self.client, settings)
        self.generation = GenerationClient(settings, config, self.client, self.base)
        self.index = PineconeClient(config, settings.pinecone_api_key, self.request)
        self.query_cache = OrderedDict()
        self.cache_lock = threading.Lock()

    def close(self):
        """Close the shared HTTP client owned by this facade.

        Called by ProviderPool.close or direct CLI/worker cleanup; also closes an
        injected client, so callers must account for that ownership."""
        self.client.close()

    def embed_documents(self, texts):
        """Implement LangChain's document embedding interface.

        Called by LangChain-compatible consumers; delegates to embed with
        purpose="document" and returns one validated vector per input text."""
        return self.embed(texts, purpose="document")

    def embed_query(self, text):
        """Implement LangChain's single-query embedding interface.

        Called by LangChain-compatible consumers; delegates to embed with query
        instructions/cache behavior and returns one vector."""
        return self.embed([text], purpose="query")[0]

    def request(self, url, body=None, headers=None, method="POST", attempts=None):
        """Delegate a JSON HTTP operation to JsonTransport.request.

        Called by embed and PineconeClient. Returns decoded JSON or raises
        AppError under transport, credential, quota or deadline failures."""
        return self.transport.request(url, body, headers, method, attempts)

    def vector(self, vector):
        """Validate and return one vector for the configured embedding dimension.

        Used by embedding responses and cached_embeddings. Rejects nonnumeric,
        nonfinite, all-zero or incorrectly sized vectors with AppError."""
        if (
            not isinstance(vector, list)
            or len(vector) != self.config.dimension
            or any(type(x) not in (int, float) or not math.isfinite(x) for x in vector)
            or not any(vector)
        ):
            raise AppError("Embedding dimensions or values are invalid.")
        return vector

    def embed(self, texts, purpose="document"):
        """Create validated embeddings in input order, or return [] for no texts.

        Called by ingestion/cached_embeddings and retrieval. Query mode adds the
        Nebius retrieval instruction and caches single queries for five minutes.
        Document mode has no query instruction. May make a paid HTTP call; model,
        response ordering, vector dimensions and values are checked."""
        if not texts:
            return []
        cache_key = texts[0] if purpose == "query" and len(texts) == 1 else None
        if cache_key is not None:
            with self.cache_lock:
                cached = self.query_cache.get(cache_key)
                if cached and time.monotonic() - cached[0] < 300:
                    self.query_cache.move_to_end(cache_key)
                    with span("query_embedding_cache_hit", model=self.config.embeddingModel, provider=self.config.provider):
                        return [list(cached[1])]
        key = self.settings.key(self.config)
        if not key:
            raise AppError(
                f"Configure {'NEBIUS_API_KEY' if self.config.provider == 'nebius' else 'OPENAI_API_KEY'} in server secrets to use {self.config.label}."
            )
        inputs = (
            [
                f"Instruct: Retrieve SEC filing evidence for the financial research question.\nQuery: {text}"
                for text in texts
            ]
            if self.config.provider == "nebius" and purpose == "query"
            else texts
        )
        with span('embedding_model_call', model=self.config.embeddingModel, provider=self.config.provider,
                  dimensions=self.config.dimension, inputs=len(texts), purpose=purpose):
            result = self.request(
                self.base + "/embeddings",
                {
                    "model": self.config.embeddingModel,
                    "dimensions": self.config.dimension,
                    "encoding_format": "float",
                    "input": inputs,
                },
                {"Authorization": f"Bearer {key}"},
            )
        if result.get("model") != self.config.embeddingModel or len(result.get("data", [])) != len(
            texts
        ):
            raise AppError("Unexpected embedding model or response size.")
        rows = sorted(result["data"], key=lambda r: r["index"])
        if [r["index"] for r in rows] != list(range(len(texts))):
            raise AppError("Unexpected embedding response order.")
        vectors = [self.vector(row["embedding"]) for row in rows]
        if cache_key is not None:
            with self.cache_lock:
                self.query_cache[cache_key] = (time.monotonic(), tuple(vectors[0]))
                self.query_cache.move_to_end(cache_key)
                while len(self.query_cache) > 128:
                    self.query_cache.popitem(last=False)
        return vectors

    @property
    def base(self):
        """Return the selected model provider's API base URL.

        Used when constructing GenerationClient and sending embedding requests;
        this property performs no I/O."""
        return (
            "https://api.tokenfactory.nebius.com/v1"
            if self.config.provider == "nebius"
            else "https://api.openai.com/v1"
        )

    def structured(self, system, user, schema, model=None):
        """Delegate a schema-constrained model call to GenerationClient.

        Used by retrieval ranking, agent assessment, research/repair and evaluation.
        Returns a validated dictionary; schema/model determine the requested task."""
        return self.generation.structured(system, user, schema, model)

    def pinecone_host(self):
        """Delegate validated, cached index host discovery to PineconeClient.

        Used by index operations and diagnostics; may make a control-plane call."""
        return self.index.pinecone_host()

    def pinecone(self, path, body):
        """Send a Pinecone operation such as query, fetch or delete.

        Used by retrieval, ingestion verification and explicit maintenance.
        path/body determine whether it is read-only; this facade is not itself
        a permission boundary."""
        started = time.monotonic()
        outcome, result = 'completed', None
        try:
            result = self.index.pinecone(path, body)
            return result
        except BaseException:
            outcome = 'unavailable'
            raise
        finally:
            trace = current_trace.get()
            if trace and path == 'query':
                trace.record('vector_db_call', (time.monotonic()-started)*1000,
                             database='Pinecone', index=self.config.indexName,
                             namespace=body.get('namespace'), topK=body.get('topK'),
                             filters={k: v for k, v in body.get('filter', {}).items() if k in ('ticker', 'report_date')},
                             matches=len((result or {}).get('matches', [])), outcome=outcome)

    def upsert(self, namespace, records):
        """Delegate vector writes and acknowledgement checks to PineconeClient.

        Called by ingestion index stages; records contain IDs, values and
        metadata, and namespace identifies the target corpus."""
        return self.index.upsert(namespace, records)


class ProviderPool:
    """Application-owned provider clients keyed by complete nonsecret config.

    api.create_app creates one pool; ResearchEndpoints reuses its clients for
    chat requests. Application shutdown closes all pooled HTTP connections."""

    def __init__(self, settings, factory=Providers):
        """Initialize an empty thread-safe cache using the supplied client factory.

        Called once by api.create_app; factory injection supports offline API tests."""
        self.settings, self.factory = settings, factory
        self.providers = {}
        self.lock = threading.Lock()

    def get(self, config):
        """Return or construct the client for an exact nonsecret config snapshot.

        Called by ResearchEndpoints for chat. Reuses HTTP connections and query
        embedding caches; initialization is protected against concurrent misses."""
        key = compact(config.model_dump())
        with self.lock:
            if key not in self.providers:
                self.providers[key] = self.factory(self.settings, config)
            return self.providers[key]

    def close(self):
        """Remove and close all cached provider clients.

        Called during API lifespan shutdown after pending research/maintenance
        tasks finish; returns None."""
        with self.lock:
            providers = list(self.providers.values())
            self.providers.clear()
        for provider in providers:
            provider.close()


def cached_embeddings(provider, store, texts):
    config = provider.config

    def key(text):
        return "embedding/" + digest(
            [config.fingerprint, config.embeddingModel, config.dimension, text]
        )

    found = {text: store.json(key(text)) for text in dict.fromkeys(texts)}
    missing = [text for text, vector in found.items() if vector is None]
    cursor = 0
    while cursor < len(missing):
        batch, count = [], 0
        while cursor < len(missing) and len(batch) < 32:
            text = missing[cursor]
            n = token_count(text)
            if n > 8191:
                raise AppError("An embedding input exceeds the token limit.")
            if batch and count + n > 12000:
                break
            batch.append(text)
            count += n
            cursor += 1
        vectors = provider.embed(batch)
        for text, vector in zip(batch, vectors):
            store.put_json(key(text), vector)
            found[text] = vector
    return [provider.vector(found[text]) for text in texts]
