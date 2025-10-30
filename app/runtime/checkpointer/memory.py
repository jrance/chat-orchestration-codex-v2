"""Compatibility shim that re-exports the in-memory checkpointer."""

from __future__ import annotations

from app.runtime.state.checkpointer import InMemoryCheckpointer

__all__ = ["InMemoryCheckpointer"]
