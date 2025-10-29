"""In-memory registry for compiled LangGraph apps."""

from __future__ import annotations

from typing import Any, Dict

_registry: Dict[str, Any] = {}


def put(graph_id: str, app: Any) -> None:
    """Store a compiled graph under ``graph_id``."""

    _registry[graph_id] = app


def get(graph_id: str) -> Any | None:
    """Retrieve a compiled graph by ``graph_id``."""

    return _registry.get(graph_id)


def clear() -> None:
    """Clear the registry (used in tests)."""

    _registry.clear()
