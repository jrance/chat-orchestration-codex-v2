"""Middleware that ensures request/correlation IDs are present on responses."""

from __future__ import annotations

import time
import uuid
from typing import Tuple

from starlette.types import ASGIApp, Message, Receive, Scope, Send

CORRELATION = "X-Correlation-Id"
REQUEST_ID = "X-Request-Id"
TIMESTAMP = "X-Timestamp"
Header = Tuple[bytes, bytes]


class RequestIdMiddleware:
    """Inject correlation headers when responses are sent."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers: list[Header] = list(message.get("headers") or [])
                existing = dict(headers)

                key = CORRELATION.encode()
                if key not in existing:
                    headers.append((key, str(uuid.uuid4()).encode()))

                key = REQUEST_ID.encode()
                if key not in existing:
                    headers.append((key, str(uuid.uuid4()).encode()))

                key = TIMESTAMP.encode()
                headers = [h for h in headers if h[0] != key]
                headers.append((key, time.strftime("%Y-%m-%dT%H:%M:%SZ").encode()))

                message["headers"] = headers

            await send(message)

        await self.app(scope, receive, send_wrapper)
