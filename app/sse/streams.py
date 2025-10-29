"""SSE framing helpers with optional heartbeats."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Optional


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


async def message_stream(
    iterator: AsyncIterator[SSEMessage],
    heartbeat_interval: float = 15.0,
) -> AsyncIterator[str]:
    """Yield encoded SSE frames, injecting periodic heartbeats."""

    async def heartbeat() -> AsyncIterator[str]:
        while True:
            await asyncio.sleep(heartbeat_interval)
            yield encode_message(SSEMessage(event="ping", data={}))

    hb_task: asyncio.Task[None] | None = None
    queue: asyncio.Queue[str] = asyncio.Queue()
    done = asyncio.Event()

    async def pump() -> None:
        try:
            async for message in iterator:
                await queue.put(encode_message(message))
        finally:
            done.set()

    async def heartbeat_pump() -> None:
        async for payload in heartbeat():
            if done.is_set():
                break
            await queue.put(payload)

    pump_task = asyncio.create_task(pump())
    if heartbeat_interval > 0:
        hb_task = asyncio.create_task(heartbeat_pump())

    try:
        while True:
            if done.is_set() and queue.empty():
                break
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=heartbeat_interval + 1)
            except asyncio.TimeoutError:
                continue
            yield payload
    finally:
        pump_task.cancel()
        if hb_task:
            hb_task.cancel()

