"""Typed primitives for describing tools and their arguments."""

from __future__ import annotations

from enum import Enum
from typing import Any, Awaitable, Callable, Dict, TypedDict


class ArgMode(str, Enum):
    """How an argument should be treated during tool invocation."""

    VISIBLE = "Visible"
    LLM_HIDDEN = "LLMHidden"
    AGENT_OVERRIDE = "AgentOverride"


class ArgSpec(TypedDict, total=False):
    """Configuration for a single tool argument."""

    mode: ArgMode
    default: Any


ToolHandler = Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]


class ToolSpec(TypedDict, total=False):
    """Specification of a tool that can be invoked by an agent."""

    id: str
    name: str
    description: str
    args_schema: Dict[str, Any]
    arg_behaviors: Dict[str, ArgSpec]
    handler: ToolHandler
    timeout_ms: int


class ToolCall(TypedDict, total=False):
    """Payload emitted by the model requesting a tool invocation."""

    id: str
    name: str
    arguments: Dict[str, Any]


class ToolResult(TypedDict, total=False):
    """Result payload returned after executing a tool."""

    id: str
    name: str
    output: Dict[str, Any] | str


__all__ = [
    "ArgMode",
    "ArgSpec",
    "ToolCall",
    "ToolHandler",
    "ToolResult",
    "ToolSpec",
]
