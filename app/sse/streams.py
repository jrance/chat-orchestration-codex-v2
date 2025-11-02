"""SSE framing helpers with optional heartbeats."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Optional, cast


@dataclass(slots=True)
class SSEMessage:
    """Representation of an SSE frame."""

    event: str
    data: Any
    id: Optional[str] = None


def encode_message(message: SSEMessage) -> str:
    """Serialize an SSE message to the wire format."""
    payload = message.data
    if not isinstance(payload, str):
        payload = json.dumps(payload)

    lines: list[str] = []
    if message.id:
        lines.append(f"id: {message.id}")
    lines.append(f"event: {message.event}")
    for chunk in payload.splitlines() or [""]:
        lines.append(f"data: {chunk}")
    return "\n".join(lines) + "\n\n"


_SENTINEL = object()


async def message_stream(
    iterator: AsyncIterator[SSEMessage],
    heartbeat_interval: float = 15.0,
) -> AsyncIterator[str]:
    """Yield encoded SSE frames, injecting periodic heartbeats.

    Cancellation-safe: breaks promptly on producer completion or client disconnect.
    """

    async def heartbeat() -> AsyncIterator[str]:
        while True:
            await asyncio.sleep(heartbeat_interval)
            yield encode_message(SSEMessage(event="ping", data={}))

    hb_task: asyncio.Task[None] | None = None
    queue: asyncio.Queue[object] = asyncio.Queue()
    done = asyncio.Event()

    async def pump() -> None:
        try:
            async for message in iterator:
                await queue.put(encode_message(message))
        finally:
            done.set()
            queue.put_nowait(_SENTINEL)

    async def heartbeat_pump() -> None:
        async for payload in heartbeat():
            if done.is_set():
                return
            await queue.put(payload)

    pump_task = asyncio.create_task(pump())
    if heartbeat_interval and heartbeat_interval > 0:
        hb_task = asyncio.create_task(heartbeat_pump())

    try:
        while True:
            try:
                item = await queue.get()
            except asyncio.CancelledError:
                break
            if item is _SENTINEL:
                break
            yield cast(str, item)
    except asyncio.CancelledError:
        pass
    finally:
        with suppress(asyncio.CancelledError, Exception):
            pump_task.cancel()
            await pump_task
        if hb_task is not None:
            with suppress(asyncio.CancelledError, Exception):
                hb_task.cancel()
                await hb_task
