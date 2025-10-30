"""In-memory registry tracking MCP server configurations."""

from __future__ import annotations

from typing import Any, Dict

_SERVERS: Dict[str, Dict[str, Any]] = {}


def register_server(node_id: str, config: Dict[str, Any]) -> None:
    """Register an MCP server configuration keyed by node id."""
    if not node_id:
        raise ValueError("node_id is required")
    _SERVERS[node_id] = dict(config or {})


def get_server(node_id: str) -> Dict[str, Any] | None:
    """Return the server configuration for the provided node id."""
    return _SERVERS.get(node_id)


def list_servers() -> Dict[str, Dict[str, Any]]:
    """Return a shallow copy of all registered server configurations."""
    return dict(_SERVERS)


def clear_servers() -> None:
    """Remove all server registrations. Intended for tests."""
    _SERVERS.clear()


__all__ = ["clear_servers", "get_server", "list_servers", "register_server"]
