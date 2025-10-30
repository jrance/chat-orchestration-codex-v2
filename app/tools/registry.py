"""In-memory registry of tool specifications."""

from __future__ import annotations

from typing import Dict

from .types import ToolSpec

_TOOLS: Dict[str, ToolSpec] = {}


def register_tool(spec: ToolSpec) -> None:
    """Register or overwrite a tool spec."""
    tool_id = spec.get("id")
    if not tool_id:
        raise ValueError("Tool specification must include an 'id'")
    _TOOLS[str(tool_id)] = spec


def get_tool(tool_id: str) -> ToolSpec | None:
    """Return the spec for the requested tool."""
    return _TOOLS.get(tool_id)


def list_tools() -> Dict[str, ToolSpec]:
    """Return a shallow copy of all registered tool specs."""
    return dict(_TOOLS)


def clear_tools() -> None:
    """Remove all registered tools. Primarily for tests."""
    _TOOLS.clear()


__all__ = ["clear_tools", "get_tool", "list_tools", "register_tool"]
