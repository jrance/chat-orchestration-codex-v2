"""SSE framing helpers with optional heartbeats."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Dict, Optional, cast


@dataclass(slots=True)
class ToolCallArguments:
    """Accumulated tool call arguments used for final assembly."""

    tool_call_id: str
    index: int
    name: Optional[str]
    function_name: Optional[str]
    chunks: list[str]

    def append(self, value: str) -> None:
        if value:
            self.chunks.append(value)

    def finalized(self) -> str:
        return "".join(self.chunks)


class ToolArgBuffer:
    """Buffer argument deltas per tool call until completion."""

    def __init__(self) -> None:
        self._entries: Dict[str, ToolCallArguments] = {}

    def _key(self, payload: Dict[str, Any]) -> str:
        call_id = str(payload.get("tool_call_id") or "").strip()
        if call_id:
            return call_id
        index = payload.get("index")
        try:
            idx = int(index)
        except (TypeError, ValueError):
            idx = 0
        return f"index-{idx}"

    def _ensure_entry(self, payload: Dict[str, Any]) -> ToolCallArguments:
        key = self._key(payload)
        entry = self._entries.get(key)
        if entry is None:
            entry = ToolCallArguments(
                tool_call_id=payload.get("tool_call_id") or key,
                index=int(payload.get("index") or 0),
                name=payload.get("name"),
                function_name=payload.get("function_name"),
                chunks=[],
            )
            self._entries[key] = entry
        else:
            if "index" in payload:
                try:
                    entry.index = int(payload["index"])
                except (TypeError, ValueError):
                    pass
            if payload.get("name"):
                entry.name = payload["name"]
            if payload.get("function_name"):
                entry.function_name = payload["function_name"]
        return entry

    def append_delta(self, payload: Dict[str, Any]) -> ToolCallArguments:
        entry = self._ensure_entry(payload)
        chunk = str(payload.get("arguments") or "")
        entry.append(chunk)
        return entry

    def finalize(self, payload: Dict[str, Any]) -> ToolCallArguments:
        entry = self._ensure_entry(payload)
        final_chunk = payload.get("arguments")
        if final_chunk:
            entry.append(str(final_chunk))
        key = self._key(payload)
        self._entries.pop(key, None)
        return entry


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
        """Legacy heartbeat generator retained for compatibility with callers outside this module."""
        try:
            while True:
                await asyncio.sleep(heartbeat_interval)
                yield encode_message(SSEMessage(event="ping", data={}))
        except asyncio.CancelledError:
            return

    hb_task: asyncio.Task[None] | None = None
    queue: asyncio.Queue[object] = asyncio.Queue()
    done = asyncio.Event()

    async def pump() -> None:
        try:
            async for message in iterator:
                try:
                    await queue.put(encode_message(message))
                except ConnectionResetError:
                    break
        except ConnectionResetError:
            pass
        finally:
            done.set()
            queue.put_nowait(_SENTINEL)

    async def heartbeat_pump() -> None:
        """Emit ping events at a fixed cadence until the stream signals completion via 'done'."""
        try:
            while not done.is_set():
                try:
                    await asyncio.wait_for(done.wait(), timeout=heartbeat_interval)
                except asyncio.TimeoutError:
                    try:
                        await queue.put(encode_message(SSEMessage(event="ping", data={})))
                    except ConnectionResetError:
                        break
        except asyncio.CancelledError:
            pass
        except ConnectionResetError:
            pass

    pump_task = asyncio.create_task(pump())
    if heartbeat_interval and heartbeat_interval > 0:
        hb_task = asyncio.create_task(heartbeat_pump())

    try:
        while True:
            try:
                item = await queue.get()
            except asyncio.CancelledError:
                break
            except ConnectionResetError:
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
        done.set()
        if hb_task is not None:
            with suppress(Exception):
                await hb_task
