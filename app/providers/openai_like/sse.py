"""Minimal SSE parsing utilities for OpenAI Responses streams."""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Dict, Iterable


def _finalize_event(event: str | None, data_lines: Iterable[str], event_id: str | None) -> Dict[str, Any] | None:
    payload_str = "\n".join(data_lines).strip()
    body: Dict[str, Any]

    if payload_str:
        try:
            parsed = json.loads(payload_str)
        except json.JSONDecodeError:
            body = {"data": payload_str}
        else:
            if isinstance(parsed, dict):
                body = dict(parsed)
            else:
                body = {"data": parsed}
    else:
        body = {}

    if not event and not body and event_id is None:
        return None

    message: Dict[str, Any] = {"type": event or "message"}
    if event_id is not None:
        message["id"] = event_id
    message.update(body)
    return message


async def parse_sse(lines: AsyncIterator[str]) -> AsyncIterator[Dict[str, Any]]:
    """Parse an async iterator of SSE lines into event dictionaries."""

    event: str | None = None
    event_id: str | None = None
    data_lines: list[str] = []

    async for raw_line in lines:
        line = raw_line.rstrip("\r")
        if not line:
            message = _finalize_event(event, data_lines, event_id)
            if message is not None:
                yield message
            event = None
            event_id = None
            data_lines = []
            continue

        if line.startswith(":"):
            continue

        field, _, value = line.partition(":")
        value = value.lstrip(" ")

        if field == "event":
            event = value or event
        elif field == "data":
            data_lines.append(value)
        elif field == "id":
            event_id = value
        else:
            # Ignore retry/unknown fields for now.
            continue

    message = _finalize_event(event, data_lines, event_id)
    if message is not None:
        yield message


__all__ = ["parse_sse"]
