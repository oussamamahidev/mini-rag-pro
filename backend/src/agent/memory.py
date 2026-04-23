"""Tenant-scoped conversation memory backed by Redis."""

from __future__ import annotations

import json
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

from ..logging_config import get_logger

logger = get_logger(__name__)


class ConversationMemory:
    """
    Store short conversation history per tenant/session in Redis.

    Format: list of {"role": "user"|"assistant", "content": str}
    Storage: Redis JSON string
    TTL: 24 hours, reset on every turn
    Max messages: 20 messages, or 10 user/assistant turns
    """

    MAX_MESSAGES = 20
    TTL_SECONDS = 86400

    def __init__(self, redis_client: Redis | None, tenant_id: str | None = None) -> None:
        """Create a Redis-backed memory helper."""
        self.redis = redis_client
        self.tenant_id = tenant_id

    async def get_history(self, session_id: str) -> list[dict[str, str]]:
        """Get conversation history, returning an empty list when absent or invalid."""
        if self.redis is None or not session_id:
            return []
        try:
            data = await self.redis.get(self._key(session_id))
        except RedisError as exc:
            logger.warning("conversation memory read failed tenant_id=%s error=%s", self.tenant_id, exc)
            return []
        if not data:
            return []
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            logger.warning("conversation memory contained invalid JSON tenant_id=%s", self.tenant_id)
            return []
        if not isinstance(parsed, list):
            return []
        return sanitize_history(parsed)[-self.MAX_MESSAGES :]

    async def add_turn(self, session_id: str, user_message: str, assistant_message: str) -> None:
        """Add a user and assistant turn to the session history."""
        if self.redis is None or not session_id:
            return

        history = await self.get_history(session_id)
        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": assistant_message})
        history = sanitize_history(history)[-self.MAX_MESSAGES :]

        try:
            await self.redis.setex(
                self._key(session_id),
                self.TTL_SECONDS,
                json.dumps(history, separators=(",", ":")),
            )
        except RedisError as exc:
            logger.warning("conversation memory write failed tenant_id=%s error=%s", self.tenant_id, exc)

    async def clear_session(self, session_id: str) -> None:
        """Delete a conversation session."""
        if self.redis is None or not session_id:
            return
        try:
            await self.redis.delete(self._key(session_id))
        except RedisError as exc:
            logger.warning("conversation memory delete failed tenant_id=%s error=%s", self.tenant_id, exc)

    async def get_session_count(self, tenant_id: str | None = None) -> int:
        """Count active sessions for a tenant using Redis SCAN."""
        if self.redis is None:
            return 0
        scoped_tenant_id = tenant_id or self.tenant_id
        if scoped_tenant_id is None:
            logger.warning("session count requested without tenant scope")
            return 0

        count = 0
        try:
            async for _key in self.redis.scan_iter(f"conv:{scoped_tenant_id}:*"):
                count += 1
        except RedisError as exc:
            logger.warning("conversation memory scan failed tenant_id=%s error=%s", scoped_tenant_id, exc)
        return count

    def _key(self, session_id: str) -> str:
        """Return a tenant-scoped Redis key."""
        if self.tenant_id is None:
            return f"conv:{session_id}"
        return f"conv:{self.tenant_id}:{session_id}"


def sanitize_history(history: list[Any]) -> list[dict[str, str]]:
    """Keep only valid OpenAI chat history messages."""
    sanitized: list[dict[str, str]] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role not in {"user", "assistant"} or not content:
            continue
        sanitized.append({"role": role, "content": str(content)[:4000]})
    return sanitized
