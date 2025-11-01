"""Tool execution infrastructure and builtin registry."""

from __future__ import annotations

from .builtin import register_all as _register_builtin_tools
from .ddgs_search import register as _register_ddgs_search_tool
from .policy import ToolPolicy, can_use_tools
from .registry import clear_tools, get_tool, list_tools, register_tool
from .runner import filter_schema_visible, invoke_tool
from .types import ArgMode, ArgSpec, ToolCall, ToolSpec, ToolResult

_register_builtin_tools()
_register_ddgs_search_tool()

__all__ = [
    "ArgMode",
    "ArgSpec",
    "ToolCall",
    "ToolPolicy",
    "ToolResult",
    "ToolSpec",
    "can_use_tools",
    "clear_tools",
    "filter_schema_visible",
    "get_tool",
    "invoke_tool",
    "list_tools",
    "register_tool",
]
