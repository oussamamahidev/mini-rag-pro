"""Redis-backed tenant rate limiting."""

from __future__ import annotations

import time
from typing import Any

from fastapi import HTTPException, status
from redis.asyncio import Redis
from redis.exceptions import RedisError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from ..logging_config import get_logger
from ..models.tenant import Tenant

logger = get_logger(__name__)

RATE_LIMIT_WINDOW_SECONDS = 3600


async def check_rate_limit(tenant: Tenant, redis_client: Redis) -> dict[str, str]:
    """
    Enforce sliding-window rate limiting using a Redis sorted set.

    Returns headers that should be added to the HTTP response.
    """
    limit = int(tenant.rate_limit_per_hour)
    if limit < 0:
        return {
            "X-RateLimit-Limit": "unlimited",
            "X-RateLimit-Remaining": "unlimited",
        }

    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW_SECONDS
    key = f"rl:{tenant.id}"
    member = f"{now:.6f}"

    try:
        async with redis_client.pipeline(transaction=True) as pipe:
            pipe.zadd(key, {member: now})
            pipe.zremrangebyscore(key, 0, window_start)
            pipe.zcard(key)
            pipe.expire(key, RATE_LIMIT_WINDOW_SECONDS)
            results = await pipe.execute()
        count = int(results[2])
    except RedisError as exc:
        logger.warning("rate limit check failed tenant_id=%s error=%s", tenant.id, exc)
        return {
            "X-RateLimit-Limit": str(limit),
            "X-RateLimit-Remaining": str(max(limit - 1, 0)),
        }

    if count > limit:
        reset_at = now + RATE_LIMIT_WINDOW_SECONDS
        try:
            oldest = await redis_client.zrange(key, 0, 0, withscores=True)
            if oldest:
                reset_at = float(oldest[0][1]) + RATE_LIMIT_WINDOW_SECONDS
        except RedisError as exc:
            logger.warning("rate limit reset lookup failed tenant_id=%s error=%s", tenant.id, exc)

        retry_after = max(1, int(reset_at - now))
        headers = {
            "X-RateLimit-Limit": str(limit),
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": str(int(reset_at)),
            "Retry-After": str(retry_after),
        }
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
            headers=headers,
        )

    return {
        "X-RateLimit-Limit": str(limit),
        "X-RateLimit-Remaining": str(max(limit - count, 0)),
        "X-RateLimit-Reset": str(int(now + RATE_LIMIT_WINDOW_SECONDS)),
    }


class RateLimitHeadersMiddleware(BaseHTTPMiddleware):
    """Attach rate-limit headers produced by the auth dependency to responses."""

    def __init__(self, app: ASGIApp) -> None:
        """Initialize the response header middleware."""
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        """Add tenant rate-limit headers when authentication populated them."""
        response = await call_next(request)
        headers = getattr(request.state, "rate_limit_headers", None)
        if isinstance(headers, dict):
            for name, value in headers.items():
                response.headers[name] = str(value)
        return response

