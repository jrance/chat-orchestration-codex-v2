"""In-memory checkpointer backed by LangGraph's MemorySaver."""

from __future__ import annotations

from typing import Any

try:  # pragma: no cover - import guarded for environments without langgraph
    from langgraph.checkpoint import MemorySaver  # type: ignore
except Exception:  # pragma: no cover - sentinel used when langgraph is unavailable
    MemorySaver = object  # type: ignore[assignment]

from .base import Checkpointer, LangGraphSaver


class InMemoryCheckpointer(Checkpointer):
    """Thin wrapper around LangGraph's MemorySaver."""

    def __init__(self) -> None:
        self._saver: LangGraphSaver | None
        if MemorySaver is object:
            self._saver = None
        else:
            self._saver = MemorySaver()  # type: ignore[call-arg]

    def get_saver(self) -> LangGraphSaver:
        if self._saver is None:
            raise RuntimeError("LangGraph MemorySaver unavailable; install langgraph>=1.0")
        return self._saver
