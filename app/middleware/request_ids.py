"""Middleware that ensures request/correlation IDs are present on responses."""

from __future__ import annotations

import time
import uuid
from typing import Optional, Tuple

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.logging.context import bind_request_context, clear_request_context

CORRELATION = "X-Correlation-Id"
REQUEST_ID = "X-Request-Id"
TENANT_ID = "X-Tenant-Id"
TIMESTAMP = "X-Timestamp"
Header = Tuple[bytes, bytes]


def _lower_header_map(headers: list[Header]) -> dict[bytes, str]:
    """Create a dict mapping lower-cased header names to their decoded values."""
    mapped: dict[bytes, str] = {}
    for key, value in headers:
        mapped[key.lower()] = value.decode()
    return mapped


def _upsert_header(headers: list[Header], name: str, value: str) -> None:
    """Replace existing header (case-insensitive) or append if missing."""
    key = name.encode()
    lower = key.lower()
    filtered = [h for h in headers if h[0].lower() != lower]
    filtered.append((key, value.encode()))
    headers.clear()
    headers.extend(filtered)


class RequestIdMiddleware:
    """Inject correlation headers and bind request context for logging."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming_headers: list[Header] = list(scope.get("headers") or [])
        header_map = _lower_header_map(incoming_headers)

        correlation_id = header_map.get(CORRELATION.lower().encode()) or str(uuid.uuid4())
        request_id = header_map.get(REQUEST_ID.lower().encode()) or str(uuid.uuid4())
        tenant_id: Optional[str] = header_map.get(TENANT_ID.lower().encode())

        bind_request_context(
            correlation_id=correlation_id,
            request_id=request_id,
            tenant_id=tenant_id,
        )

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers: list[Header] = list(message.get("headers") or [])

                _upsert_header(headers, CORRELATION, correlation_id)
                _upsert_header(headers, REQUEST_ID, request_id)
                if tenant_id:
                    _upsert_header(headers, TENANT_ID, tenant_id)

                _upsert_header(headers, TIMESTAMP, time.strftime("%Y-%m-%dT%H:%M:%SZ"))

                message["headers"] = headers

            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            clear_request_context()
