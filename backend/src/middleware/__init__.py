"""HTTP middleware and authentication helpers."""

from __future__ import annotations

import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..logging_config import get_logger, request_id_context
from .rate_limit import RateLimitHeadersMiddleware

logger = get_logger(__name__)


class RequestIDMiddleware:
    """Add a request ID to each request, response, and log record."""

    def __init__(self, app: ASGIApp, header_name: str = "x-request-id") -> None:
        """Initialize the middleware with the wrapped ASGI application."""
        self.app = app
        self.header_name = header_name.lower()
        self.header_bytes = self.header_name.encode("latin-1")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Process an ASGI request and emit a structured access log line."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = self._get_or_create_request_id(scope)
        token = request_id_context.set(request_id)
        started_at = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = list(message.get("headers", []))
                if not any(name.lower() == self.header_bytes for name, _ in headers):
                    headers.append((self.header_bytes, request_id.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = (time.perf_counter() - started_at) * 1000
            logger.info(
                "request completed method=%s path=%s status_code=%s duration_ms=%.2f request_id=%s",
                scope.get("method", "-"),
                scope.get("path", "-"),
                status_code,
                duration_ms,
                request_id,
            )
            request_id_context.reset(token)

    def _get_or_create_request_id(self, scope: Scope) -> str:
        """Read X-Request-ID from headers or generate a new UUID4 value."""
        headers: list[tuple[bytes, bytes]] = scope.get("headers", [])
        header_value = self._find_header(headers)
        return header_value or str(uuid.uuid4())

    def _find_header(self, headers: list[tuple[bytes, bytes]]) -> str | None:
        """Find the configured request ID header in raw ASGI headers."""
        for name, value in headers:
            if name.lower() == self.header_bytes:
                decoded = value.decode("latin-1").strip()
                return decoded or None
        return None


__all__ = ["RequestIDMiddleware", "RateLimitHeadersMiddleware"]

