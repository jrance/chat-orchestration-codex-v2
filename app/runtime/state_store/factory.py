"""Factory helpers for run state stores."""

from __future__ import annotations

from typing import Optional

from app.config.settings import settings

from .base import RunStateStore
from .memory import InMemoryRunStateStore

_RUN_STATE_STORE: Optional[RunStateStore] = None
_RUN_STATE_STORE_KIND: Optional[str] = None


def _build_store(kind: str) -> RunStateStore:
    if kind == "memory":
        return InMemoryRunStateStore()
    raise ValueError(f"Unknown RUN_STORE_KIND '{kind}'")


def get_run_state_store() -> RunStateStore:
    """Return the configured run state store (defaults to in-memory)."""

    global _RUN_STATE_STORE, _RUN_STATE_STORE_KIND

    kind = (settings.run_store_kind or "memory").strip().lower()
    if not kind:
        kind = "memory"

    if _RUN_STATE_STORE is None or _RUN_STATE_STORE_KIND != kind:
        _RUN_STATE_STORE = _build_store(kind)
        _RUN_STATE_STORE_KIND = kind

    return _RUN_STATE_STORE


def reset_run_state_store() -> None:
    """Clear the cached run state store (used by tests after settings reload)."""

    global _RUN_STATE_STORE, _RUN_STATE_STORE_KIND
    _RUN_STATE_STORE = None
    _RUN_STATE_STORE_KIND = None
