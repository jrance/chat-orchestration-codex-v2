"""Compatibility shim for legacy imports."""

from __future__ import annotations

from app.runtime.state.checkpointer import (
    CheckpointManager as Checkpointer,
    get_checkpointer,
    reset_checkpointer,
)

__all__ = ["Checkpointer", "get_checkpointer", "reset_checkpointer"]
