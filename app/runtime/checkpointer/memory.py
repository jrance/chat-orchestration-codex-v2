"""Compatibility shim exposing the new checkpoint manager as the legacy class."""

from __future__ import annotations

from app.runtime.state.checkpointer import CheckpointManager, get_checkpointer


class InMemoryCheckpointer(CheckpointManager):
    """Alias for the default checkpoint manager."""

    def __new__(cls) -> "InMemoryCheckpointer":  # pragma: no cover - thin shim
        return get_checkpointer()
