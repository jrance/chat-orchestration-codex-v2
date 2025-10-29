"""Compatibility layer for legacy SSE helpers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Mapping

from app.sse.streams import SSEMessage, encode_message


def format_sse(data: str, event: str | None = None, id: str | None = None) -> str:
    """Format a payload as an SSE frame."""
    return encode_message(SSEMessage(event=event or "message", data=data, id=id))


async def sse_stream(gen: AsyncIterator[Mapping[str, Any]]) -> AsyncIterator[str]:
    """Yield encoded SSE frames from an async iterator of dict payloads."""
    async for msg in gen:
        event = msg.get("event") or "message"
        yield encode_message(
            SSEMessage(
                event=event,
                data=msg.get("data", ""),
                id=msg.get("id"),
            )
        )
