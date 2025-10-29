"""Protocols for LangGraph-compatible checkpoint savers."""

from __future__ import annotations

from typing import Protocol


class LangGraphSaver(Protocol):
    """Minimal protocol for LangGraph saver/checkpointer objects."""

    # LangGraph saver objects are passed directly into graph.compile(checkpointer=saver)


class Checkpointer(Protocol):
    """Factory interface for providing a LangGraph-compatible saver."""

    def get_saver(self) -> LangGraphSaver:
        """Return a saver instance usable by LangGraph's compile(checkpointer=...)."""
