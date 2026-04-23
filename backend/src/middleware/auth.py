"""FastAPI authentication dependencies and tenant isolation helpers."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import Header, HTTPException, Request, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from redis.asyncio import Redis
from redis.exceptions import RedisError

from .. import database as database_module
from ..auth.key_generator import verify_api_key
from ..logging_config import get_logger
from ..models.tenant import Tenant
from .rate_limit import check_rate_limit

logger = get_logger(__name__)

AUTH_CACHE_TTL_SECONDS = 300
INVALID_ATTEMPT_WINDOW_SECONDS = 600
INVALID_ATTEMPT_LIMIT = 10


async def get_current_tenant(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key", description="API key from registration"),
) -> Tenant:
    """
    Authenticate the request API key and return the active tenant.

    The Redis auth cache is keyed by API-key prefix only. The full key is never
    cached and is always verified against a bcrypt hash.
    """
    if x_api_key is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing API key")
    api_key = x_api_key.strip()

    try:
        prefix = validate_api_key_format(api_key)
    except HTTPException:
        failure_key = fingerprint_key(api_key)
        if database_module.redis_client is not None:
            await enforce_invalid_key_backoff(database_module.redis_client, failure_key)
            await record_invalid_key_attempt(database_module.redis_client, failure_key)
        raise

    db = require_database()
    redis_client = require_redis()
    failure_key = fingerprint_key(api_key)
    await enforce_invalid_key_backoff(redis_client, failure_key)
    tenant = await authenticate_from_cache(redis_client, prefix, api_key, failure_key)
    if tenant is None:
        tenant = await authenticate_from_database(db, redis_client, prefix, api_key, failure_key)

    if not tenant.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tenant is inactive")

    await clear_invalid_key_attempts(redis_client, failure_key)
    request.state.current_tenant = tenant
    request.state.rate_limit_headers = await check_rate_limit(tenant, redis_client)
    schedule_last_active_update(db, tenant.id)
    return tenant


async def get_optional_tenant(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> Tenant | None:
    """Return the current tenant or None when no API key is provided."""
    if x_api_key is None:
        return None
    return await get_current_tenant(request, x_api_key)


async def verify_project_ownership(
    db: AsyncIOMotorDatabase,
    project_id: str,
    tenant: Tenant,
) -> dict[str, Any]:
    """Load a tenant-owned project or raise 404 without leaking cross-tenant existence."""
    project = await db.projects.find_one(
        {
            "id": project_id,
            "tenant_id": tenant.id,
            "is_deleted": {"$ne": True},
        }
    )
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


async def authenticate_from_cache(redis_client: Redis, prefix: str, api_key: str, failure_key: str) -> Tenant | None:
    """Authenticate against a cached tenant document if present."""
    try:
        raw = await redis_client.get(auth_cache_key(prefix))
    except RedisError as exc:
        logger.warning("auth cache read failed prefix=%s error=%s", prefix, exc)
        return None
    if not raw:
        return None

    try:
        payload = json.loads(raw)
        key_hash = select_hash_for_prefix(payload, prefix)
        if key_hash is None or not verify_api_key(api_key, key_hash):
            await record_invalid_key_attempt(redis_client, failure_key)
            return None
        return Tenant.model_validate(payload)
    except Exception as exc:
        logger.warning("auth cache payload invalid prefix=%s error=%s", prefix, exc)
        return None


async def authenticate_from_database(
    db: AsyncIOMotorDatabase,
    redis_client: Redis,
    prefix: str,
    api_key: str,
    failure_key: str,
) -> Tenant:
    """Authenticate against MongoDB and populate the Redis auth cache."""
    now = datetime.now(UTC)
    document = await db.tenants.find_one(
        {
            "$or": [
                {"api_key_prefix": prefix},
                {
                    "previous_api_key_prefix": prefix,
                    "previous_key_expires_at": {"$gt": now},
                },
            ]
        }
    )
    if document is None:
        await record_invalid_key_attempt(redis_client, failure_key)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

    payload = tenant_payload_from_document(document)
    key_hash = select_hash_for_prefix(payload, prefix)
    if key_hash is None or not verify_api_key(api_key, key_hash):
        await record_invalid_key_attempt(redis_client, failure_key)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

    tenant = Tenant.model_validate(payload)
    await cache_tenant(redis_client, prefix, tenant)
    return tenant


def validate_api_key_format(api_key: str) -> str:
    """Validate API-key shape and return its display prefix."""
    if not api_key.startswith("sk-") or len(api_key) < 10:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key format")
    return api_key[:10]


def select_hash_for_prefix(payload: dict[str, Any], prefix: str) -> str | None:
    """Return the active hash matching a key prefix, including rotation grace period."""
    current_prefix = str(payload.get("api_key_prefix", ""))
    if hmac.compare_digest(current_prefix, prefix):
        return payload.get("api_key_hash")

    previous_prefix = payload.get("previous_api_key_prefix")
    previous_hash = payload.get("previous_api_key_hash") or payload.get("previous_key_hash")
    expires_at = parse_datetime(payload.get("previous_key_expires_at"))
    if (
        previous_prefix
        and previous_hash
        and expires_at is not None
        and expires_at > datetime.now(UTC)
        and hmac.compare_digest(str(previous_prefix), prefix)
    ):
        return str(previous_hash)
    return None


async def cache_tenant(redis_client: Redis, prefix: str, tenant: Tenant) -> None:
    """Cache a tenant auth document without storing the full API key."""
    try:
        await redis_client.setex(
            auth_cache_key(prefix),
            AUTH_CACHE_TTL_SECONDS,
            json.dumps(tenant.model_dump(mode="json"), separators=(",", ":")),
        )
    except RedisError as exc:
        logger.warning("auth cache write failed tenant_id=%s prefix=%s error=%s", tenant.id, prefix, exc)


async def invalidate_auth_cache(redis_client: Redis, *prefixes: str | None) -> None:
    """Delete auth cache entries for the provided API-key prefixes."""
    keys = [auth_cache_key(prefix) for prefix in prefixes if prefix]
    if not keys:
        return
    try:
        await redis_client.delete(*keys)
    except RedisError as exc:
        logger.warning("auth cache invalidation failed prefixes=%s error=%s", [redact_prefix(key) for key in keys], exc)


async def enforce_invalid_key_backoff(redis_client: Redis, prefix: str) -> None:
    """Block repeated invalid API-key attempts for a prefix."""
    try:
        attempts = await redis_client.get(invalid_attempt_key(prefix))
    except RedisError:
        return
    if attempts is not None and int(attempts) >= INVALID_ATTEMPT_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many invalid authentication attempts",
        )


async def record_invalid_key_attempt(redis_client: Redis, prefix_or_fingerprint: str) -> None:
    """Record an invalid authentication attempt without storing the full key."""
    key = invalid_attempt_key(prefix_or_fingerprint)
    try:
        count = await redis_client.incr(key)
        if count == 1:
            await redis_client.expire(key, INVALID_ATTEMPT_WINDOW_SECONDS)
    except RedisError as exc:
        logger.warning("invalid auth attempt tracking failed key=%s error=%s", redact_prefix(prefix_or_fingerprint), exc)


async def clear_invalid_key_attempts(redis_client: Redis, prefix: str) -> None:
    """Clear invalid-attempt counters after successful authentication."""
    try:
        await redis_client.delete(invalid_attempt_key(prefix))
    except RedisError:
        return


def schedule_last_active_update(db: AsyncIOMotorDatabase, tenant_id: str) -> None:
    """Update tenant activity in the background."""
    task = asyncio.create_task(
        db.tenants.update_one(
            {"id": tenant_id},
            {"$set": {"last_active_at": datetime.now(UTC), "updated_at": datetime.now(UTC)}},
        )
    )
    task.add_done_callback(log_background_auth_update)


def log_background_auth_update(task: asyncio.Task[Any]) -> None:
    """Log failed background auth updates without affecting responses."""
    try:
        task.result()
    except Exception as exc:
        logger.warning("tenant last_active update failed: %s", exc)


async def write_auth_audit_event(
    db: AsyncIOMotorDatabase,
    tenant_id: str,
    event_type: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Store a tenant-scoped authentication audit event."""
    await db.auth_events.insert_one(
        {
            "tenant_id": tenant_id,
            "event_type": event_type,
            "metadata": metadata or {},
            "created_at": datetime.now(UTC),
        }
    )


def tenant_payload_from_document(document: dict[str, Any]) -> dict[str, Any]:
    """Return a Pydantic-compatible tenant payload from MongoDB."""
    payload = dict(document)
    payload.pop("_id", None)
    return payload


def require_database() -> AsyncIOMotorDatabase:
    """Return initialized MongoDB or raise a service-unavailable error."""
    if database_module.mongo_database is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="MongoDB is not initialized")
    return database_module.mongo_database


def require_redis() -> Redis:
    """Return initialized Redis or raise a service-unavailable error."""
    if database_module.redis_client is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Redis is not initialized")
    return database_module.redis_client


def auth_cache_key(prefix: str) -> str:
    """Return the Redis auth cache key for an API-key prefix."""
    return f"auth_cache:{prefix}"


def invalid_attempt_key(prefix_or_fingerprint: str) -> str:
    """Return the Redis invalid-attempt counter key."""
    return f"auth_fail:{prefix_or_fingerprint}"


def fingerprint_key(api_key: str) -> str:
    """Hash an invalid key value so the full secret is never stored."""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]


def redact_prefix(value: str) -> str:
    """Return a redacted prefix suitable for logs."""
    if len(value) <= 4:
        return "****"
    return f"{value[:4]}****"


def parse_datetime(value: Any) -> datetime | None:
    """Parse datetimes from MongoDB or JSON cache payloads."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
        except ValueError:
            return None
    return None
