"""OpenAI embedding service with Redis caching and batch support."""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, RateLimitError
from redis.asyncio import Redis
from redis.exceptions import RedisError

from ..config import Settings
from ..logging_config import get_logger

logger = get_logger(__name__)

CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
MAX_BATCH_SIZE = 100
OPENAI_MAX_INPUTS_PER_REQUEST = 2048
MAX_RETRIES = 3
BASE_BACKOFF_SECONDS = 1.0

EMBEDDING_COST_PER_1K_TOKENS: dict[str, float] = {
    "text-embedding-ada-002": 0.0001,
    "text-embedding-3-small": 0.00002,
}

embedding_service: "EmbeddingService | None" = None


class EmbeddingService:
    """Generate OpenAI embeddings with Redis-backed per-text caching."""

    def __init__(self, settings: Settings, redis_client: Redis | None = None) -> None:
        """Create an embedding service using the configured OpenAI model."""
        self.settings = settings
        self.model = settings.openai_embedding_model
        self.expected_dimensions = settings.openai_embedding_dimensions
        self.redis_client = redis_client
        self._owns_redis_client = False
        self.openai_client = AsyncOpenAI(api_key=settings.openai_api_key)

        if self.redis_client is None:
            self.redis_client = Redis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=settings.service_check_timeout_seconds,
                socket_timeout=settings.service_check_timeout_seconds,
            )
            self._owns_redis_client = True

    async def embed_text(self, text: str) -> list[float]:
        """Embed a single text string, using cache before calling OpenAI."""
        embeddings = await self.embed_batch([text])
        return embeddings[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """
        Embed multiple texts efficiently while preserving input order.

        Cache hits are returned immediately. Cache misses are deduplicated and
        sent to OpenAI in batches of at most 100 texts to avoid long requests.
        """
        if not texts:
            return []
        if len(texts) > MAX_BATCH_SIZE:
            results: list[list[float]] = []
            for start in range(0, len(texts), MAX_BATCH_SIZE):
                results.extend(await self.embed_batch(texts[start : start + MAX_BATCH_SIZE]))
            return results

        normalized_texts = [self._validate_text(text) for text in texts]
        cache_keys = [self._cache_key(text) for text in normalized_texts]
        cached_values = await self._cache_get_many(cache_keys)

        results: list[list[float] | None] = [None] * len(normalized_texts)
        missing_by_key: dict[str, str] = {}
        indexes_by_key: dict[str, list[int]] = {}

        for index, (text, key, cached) in enumerate(zip(normalized_texts, cache_keys, cached_values, strict=True)):
            if cached is not None:
                results[index] = cached
                continue
            missing_by_key.setdefault(key, text)
            indexes_by_key.setdefault(key, []).append(index)

        if missing_by_key:
            missing_keys = list(missing_by_key)
            missing_texts = [missing_by_key[key] for key in missing_keys]
            generated = await self._embed_uncached_batch(missing_texts)
            await self._cache_set_many(dict(zip(missing_keys, generated, strict=True)))
            for key, embedding in zip(missing_keys, generated, strict=True):
                for index in indexes_by_key[key]:
                    results[index] = embedding

        finalized = [embedding for embedding in results if embedding is not None]
        if len(finalized) != len(texts):
            raise RuntimeError("embedding result assembly failed")
        return finalized

    async def estimate_tokens(self, text: str) -> int:
        """Approximate token count before calling the embedding API."""
        return len(text) // 4

    async def calculate_embedding_cost(self, texts: list[str]) -> float:
        """Estimate embedding API cost in USD for the configured model."""
        total_tokens = 0
        for text in texts:
            total_tokens += await self.estimate_tokens(text)
        rate = EMBEDDING_COST_PER_1K_TOKENS.get(self.model, EMBEDDING_COST_PER_1K_TOKENS["text-embedding-3-small"])
        return (total_tokens / 1000) * rate

    async def close(self) -> None:
        """Close owned network clients."""
        close_method = getattr(self.openai_client, "close", None)
        if close_method is not None:
            result = close_method()
            if hasattr(result, "__await__"):
                await result

        if self._owns_redis_client and self.redis_client is not None:
            await self.redis_client.aclose()

    async def _embed_uncached_batch(self, texts: list[str]) -> list[list[float]]:
        """Call OpenAI for uncached texts with retry/backoff."""
        if len(texts) > OPENAI_MAX_INPUTS_PER_REQUEST:
            raise ValueError(f"OpenAI embedding request cannot exceed {OPENAI_MAX_INPUTS_PER_REQUEST} texts")

        for attempt in range(MAX_RETRIES + 1):
            try:
                response = await self.openai_client.embeddings.create(
                    input=texts,
                    model=self.model,
                )
                ordered = sorted(response.data, key=lambda item: item.index)
                embeddings = [list(item.embedding) for item in ordered]
                for embedding in embeddings:
                    self.validate_embedding(embedding)
                return embeddings
            except (RateLimitError, APITimeoutError, APIConnectionError, APIStatusError) as exc:
                if isinstance(exc, APIStatusError) and exc.status_code not in {408, 409, 429, 500, 502, 503, 504}:
                    raise
                if attempt >= MAX_RETRIES:
                    logger.exception("embedding request failed after retries model=%s", self.model)
                    raise
                delay = BASE_BACKOFF_SECONDS * (2**attempt)
                logger.warning(
                    "embedding request failed; retrying attempt=%s delay=%.1fs error=%s",
                    attempt + 1,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)

        raise RuntimeError("embedding retry loop exited unexpectedly")

    def validate_embedding(self, embedding: list[float]) -> None:
        """Validate the embedding vector shape before use in Qdrant."""
        if len(embedding) != self.expected_dimensions:
            raise ValueError(
                f"embedding dimension mismatch: expected {self.expected_dimensions}, got {len(embedding)}"
            )
        if not all(isinstance(value, int | float) for value in embedding):
            raise ValueError("embedding must contain only numeric values")

    async def _cache_get_many(self, keys: list[str]) -> list[list[float] | None]:
        """Read embeddings from Redis cache, returning None for misses/errors."""
        if self.redis_client is None:
            return [None] * len(keys)
        try:
            raw_values = await self.redis_client.mget(keys)
        except RedisError as exc:
            logger.warning("embedding cache read failed: %s", exc)
            return [None] * len(keys)

        values: list[list[float] | None] = []
        for raw in raw_values:
            if raw is None:
                values.append(None)
                continue
            try:
                embedding = json.loads(raw)
                if isinstance(embedding, list):
                    self.validate_embedding(embedding)
                    values.append([float(value) for value in embedding])
                else:
                    values.append(None)
            except (TypeError, ValueError, json.JSONDecodeError):
                values.append(None)
        return values

    async def _cache_set_many(self, embeddings_by_key: dict[str, list[float]]) -> None:
        """Write generated embeddings to Redis cache with a seven-day TTL."""
        if self.redis_client is None or not embeddings_by_key:
            return
        try:
            async with self.redis_client.pipeline(transaction=False) as pipe:
                for key, embedding in embeddings_by_key.items():
                    pipe.setex(key, CACHE_TTL_SECONDS, json.dumps(embedding, separators=(",", ":")))
                await pipe.execute()
        except RedisError as exc:
            logger.warning("embedding cache write failed: %s", exc)

    def _cache_key(self, text: str) -> str:
        """Return the stable cache key required by the ingestion pipeline."""
        digest = hashlib.md5(text.encode("utf-8")).hexdigest()
        return f"emb:{digest}"

    def _validate_text(self, text: str) -> str:
        """Normalize and validate input text for embedding."""
        normalized = text.strip()
        if not normalized:
            raise ValueError("cannot embed empty text")
        return normalized


def initialize_embedding_service(settings: Settings, redis_client: Redis | None = None) -> EmbeddingService:
    """Create and store the module-level embedding service singleton."""
    global embedding_service
    embedding_service = EmbeddingService(settings, redis_client)
    return embedding_service


def get_embedding_service() -> EmbeddingService:
    """Return the initialized embedding service singleton."""
    if embedding_service is None:
        raise RuntimeError("EmbeddingService has not been initialized")
    return embedding_service
