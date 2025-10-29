"""Per-run telemetry streaming utilities."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Dict, Optional

from app.telemetry.models import TelemetryEvent, TelemetryLevel, redact_payload


@dataclass(slots=True)
class TelemetryStreamConfig:
    run_id: str
    level: TelemetryLevel
    redact: bool


class TelemetryStreamer:
    """Simple in-memory broadcaster for telemetry events."""

    def __init__(self, config: TelemetryStreamConfig) -> None:
        self.config = config
        self._queue: asyncio.Queue[TelemetryEvent | None] = asyncio.Queue()
        self._closed = asyncio.Event()
        self._has_consumer = False

    def enabled(self) -> bool:
        return self.config.level is not TelemetryLevel.NONE

    async def publish(self, event: TelemetryEvent) -> None:
        if not self.enabled():
            return
        sanitized = TelemetryEvent(
            event=event.event,
            payload=redact_payload(event.payload, self.config.redact),
        )
        await self._queue.put(sanitized)

    async def stream(self) -> AsyncIterator[TelemetryEvent]:
        self._has_consumer = True
        while True:
            item = await self._queue.get()
            if item is None:
                break
            yield item
        self._closed.set()

    async def close(self) -> None:
        await self._queue.put(None)
        if self._has_consumer:
            await self._closed.wait()
        else:
            self._closed.set()


_registry: Dict[str, TelemetryStreamer] = {}
_registry_lock = asyncio.Lock()


async def register_streamer(streamer: TelemetryStreamer) -> None:
    async with _registry_lock:
        _registry[streamer.config.run_id] = streamer


async def get_streamer(run_id: str) -> Optional[TelemetryStreamer]:
    async with _registry_lock:
        return _registry.get(run_id)


async def pop_streamer(run_id: str) -> Optional[TelemetryStreamer]:
    async with _registry_lock:
        return _registry.pop(run_id, None)
