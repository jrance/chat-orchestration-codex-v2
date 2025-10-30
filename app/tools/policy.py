"""Tool policy helpers for codeless agents."""

from __future__ import annotations

from enum import Enum


class ToolPolicy(str, Enum):
    """Policy modes supported by the IR."""

    DISABLED = "Disabled"
    AUTO = "Auto"
    ALWAYS_ASK = "AlwaysAsk"
    HEURISTIC = "Heuristic"


def can_use_tools(policy: str | ToolPolicy | None) -> bool:
    """Return True when the provided policy allows tool usage."""
    if policy is None:
        return False
    try:
        mode = ToolPolicy(policy)
    except ValueError:
        return False
    return mode in (ToolPolicy.AUTO, ToolPolicy.ALWAYS_ASK, ToolPolicy.HEURISTIC)


__all__ = ["ToolPolicy", "can_use_tools"]
