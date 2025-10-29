"""Simple in-memory checkpoint store suitable for tests and local dev."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional


class InMemoryCheckpointer:
    """Minimal checkpoint manager supporting run resume semantics."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._state: Dict[str, Dict[str, Any]] = {}

    @asynccontextmanager
    async def acquire(self, run_id: str):
        async with self._lock:
            yield

    async def save_state(self, run_id: str, state: Dict[str, Any]) -> None:
        async with self._lock:
            self._state[run_id] = dict(state)

    async def load_state(self, run_id: str) -> Optional[Dict[str, Any]]:
        async with self._lock:
            payload = self._state.get(run_id)
            return dict(payload) if payload is not None else None

