"""Server Sent Events helpers."""

from collections.abc import AsyncIterator
from typing import Any, Mapping


def format_sse(data: str, event: str | None = None, id: str | None = None) -> str:
    """Format a payload as an SSE frame."""
    lines: list[str] = []
    if id:
        lines.append(f"id: {id}")
    if event:
        lines.append(f"event: {event}")
    for chunk in data.splitlines() or [""]:
        lines.append(f"data: {chunk}")
    return "\n".join(lines) + "\n\n"


async def sse_stream(gen: AsyncIterator[Mapping[str, Any]]) -> AsyncIterator[str]:
    """Yield encoded SSE frames from an async iterator of dict payloads."""
    async for msg in gen:
        yield format_sse(
            data=str(msg.get("data", "")),
            event=msg.get("event"),
            id=msg.get("id"),
        )
