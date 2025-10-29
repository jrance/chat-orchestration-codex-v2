"""Factory helpers for constructing checkpointer implementations."""

from __future__ import annotations

from typing import Optional

from app.config.settings import settings

from .base import Checkpointer
from .memory import InMemoryCheckpointer

_CHECKPOINTER: Optional[Checkpointer] = None
_CHECKPOINTER_KIND: Optional[str] = None


def _build_checkpointer(kind: str) -> Checkpointer:
    if kind == "memory":
        return InMemoryCheckpointer()
    raise ValueError(f"Unknown CHECKPOINTER_KIND '{kind}'")


def get_checkpointer() -> Checkpointer:
    """Return the configured checkpointer (defaults to in-memory)."""

    global _CHECKPOINTER, _CHECKPOINTER_KIND

    kind = (settings.checkpointer_kind or "memory").strip().lower()
    if not kind:
        kind = "memory"

    if _CHECKPOINTER is None or _CHECKPOINTER_KIND != kind:
        _CHECKPOINTER = _build_checkpointer(kind)
        _CHECKPOINTER_KIND = kind

    return _CHECKPOINTER


def reset_checkpointer() -> None:
    """Clear the cached checkpointer (usually from tests after settings reload)."""

    global _CHECKPOINTER, _CHECKPOINTER_KIND
    _CHECKPOINTER = None
    _CHECKPOINTER_KIND = None
