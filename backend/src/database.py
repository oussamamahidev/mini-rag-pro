"""Async MongoDB and Redis connection management."""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Literal, TypedDict

from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from redis.asyncio import Redis

from .config import Settings, get_settings
from .logging_config import get_logger

logger = get_logger(__name__)

ServiceState = Literal["up", "down", "unknown"]


class ServiceCheckResult(TypedDict, total=False):
    """Status payload for one dependency readiness check."""

    status: ServiceState
    latency_ms: int
    error: str


mongo_client: AsyncIOMotorClient[Any] | None = None
mongo_database: AsyncIOMotorDatabase[Any] | None = None
redis_client: Redis | None = None


async def initialize_connections(settings: Settings | None = None) -> None:
    """Initialize MongoDB and Redis clients once for the process."""
    resolved_settings = settings or get_settings()
    timeout_ms = int(resolved_settings.service_check_timeout_seconds * 1000)

    global mongo_client, mongo_database, redis_client

    if mongo_client is None:
        mongo_client = AsyncIOMotorClient(
            resolved_settings.mongo_url,
            serverSelectionTimeoutMS=timeout_ms,
            uuidRepresentation="standard",
        )
        mongo_database = mongo_client[resolved_settings.mongo_db_name]

    if redis_client is None:
        redis_client = Redis.from_url(
            resolved_settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=resolved_settings.service_check_timeout_seconds,
            socket_timeout=resolved_settings.service_check_timeout_seconds,
        )


def _require_db() -> AsyncIOMotorDatabase[Any]:
    """Return the MongoDB database or raise a service unavailable error."""
    if mongo_database is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MongoDB is not initialized",
        )
    return mongo_database


def _require_redis() -> Redis:
    """Return the Redis client or raise a service unavailable error."""
    if redis_client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Redis is not initialized",
        )
    return redis_client


@asynccontextmanager
async def db_context() -> AsyncIterator[AsyncIOMotorDatabase[Any]]:
    """Yield the MongoDB database for manual async context manager usage."""
    yield _require_db()


@asynccontextmanager
async def redis_context() -> AsyncIterator[Redis]:
    """Yield the Redis client for manual async context manager usage."""
    yield _require_redis()


async def get_db() -> AsyncIterator[AsyncIOMotorDatabase[Any]]:
    """FastAPI dependency that yields the MongoDB database."""
    async with db_context() as database:
        yield database


async def get_redis() -> AsyncIterator[Redis]:
    """FastAPI dependency that yields the Redis client."""
    async with redis_context() as client:
        yield client


def get_embedding_service() -> Any:
    """Return the initialized embedding service singleton."""
    from .services.embedding import get_embedding_service as _get_embedding_service

    return _get_embedding_service()


def get_vector_store() -> Any:
    """Return the initialized vector store singleton."""
    from .services.vector_store import get_vector_store as _get_vector_store

    return _get_vector_store()


def get_llm_service() -> Any:
    """Return the initialized LLM service singleton."""
    from .services.llm import get_llm_service as _get_llm_service

    return _get_llm_service()


def get_openai_client() -> Any:
    """Return the initialized shared AsyncOpenAI client."""
    from .services.llm import get_openai_client as _get_openai_client

    return _get_openai_client()


async def test_connections(
    settings: Settings | None = None,
    timeout_seconds: float | None = None,
) -> dict[str, ServiceCheckResult]:
    """Ping MongoDB and Redis and return status payloads for readiness."""
    resolved_settings = settings or get_settings()
    await initialize_connections(resolved_settings)
    timeout = timeout_seconds or resolved_settings.service_check_timeout_seconds

    mongodb, redis = await asyncio.gather(
        check_mongodb(timeout),
        check_redis(timeout),
    )
    return {
        "mongodb": mongodb,
        "redis": redis,
    }


async def check_mongodb(timeout_seconds: float) -> ServiceCheckResult:
    """Ping MongoDB with a timeout and return a readiness status."""
    started_at = time.perf_counter()
    try:
        if mongo_client is None:
            raise RuntimeError("MongoDB client is not initialized")
        await asyncio.wait_for(mongo_client.admin.command("ping"), timeout=timeout_seconds)
        return {"status": "up", "latency_ms": _elapsed_ms(started_at)}
    except Exception as exc:
        logger.warning("mongodb readiness check failed: %s", exc)
        return {
            "status": "down",
            "latency_ms": _elapsed_ms(started_at),
            "error": str(exc),
        }


async def check_redis(timeout_seconds: float) -> ServiceCheckResult:
    """Ping Redis with a timeout and return a readiness status."""
    started_at = time.perf_counter()
    try:
        if redis_client is None:
            raise RuntimeError("Redis client is not initialized")
        await asyncio.wait_for(redis_client.ping(), timeout=timeout_seconds)
        return {"status": "up", "latency_ms": _elapsed_ms(started_at)}
    except Exception as exc:
        logger.warning("redis readiness check failed: %s", exc)
        return {
            "status": "down",
            "latency_ms": _elapsed_ms(started_at),
            "error": str(exc),
        }


async def create_indexes() -> None:
    """Create MongoDB indexes required by model-backed collections."""
    database = _require_db()
    from .models.indexes import create_indexes as create_model_indexes

    await create_model_indexes(database)


async def close_connections() -> None:
    """Close MongoDB and Redis clients during graceful shutdown."""
    global mongo_client, mongo_database, redis_client

    if mongo_client is not None:
        mongo_client.close()
        mongo_client = None
        mongo_database = None

    if redis_client is not None:
        await redis_client.aclose()
        redis_client = None


def _elapsed_ms(started_at: float) -> int:
    """Return elapsed milliseconds since the supplied perf_counter value."""
    return max(0, round((time.perf_counter() - started_at) * 1000))
