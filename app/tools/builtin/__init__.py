"""Builtin tool registrations."""

from __future__ import annotations

from . import echo, kv_store


def register_all() -> None:
    """Register all builtin tools."""
    echo.register()
    kv_store.register()


__all__ = ["register_all"]
