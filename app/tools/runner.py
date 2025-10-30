"""Execution helpers for registered tools."""

from __future__ import annotations

import asyncio
from typing import Any, Dict

from .registry import get_tool
from .types import ArgMode, ArgSpec, ToolSpec

DEFAULT_TIMEOUT_MS = 10_000


def _normalize_mode(spec: ArgSpec | None) -> ArgMode:
    if not spec:
        return ArgMode.VISIBLE
    mode = spec.get("mode")
    if isinstance(mode, ArgMode):
        return mode
    if isinstance(mode, str):
        try:
            return ArgMode(mode)
        except ValueError:
            return ArgMode.VISIBLE
    return ArgMode.VISIBLE


def filter_schema_visible(spec: ToolSpec) -> Dict[str, Any]:
    """Filter a tool schema to only include arguments visible to the LLM."""
    schema = dict(spec.get("args_schema") or {"type": "object"})
    props = dict(schema.get("properties") or {})
    behaviors = spec.get("arg_behaviors") or {}

    visible_props = {}
    for name, definition in props.items():
        mode = _normalize_mode(behaviors.get(name))
        if mode is ArgMode.LLM_HIDDEN:
            continue
        visible_props[name] = definition

    filtered = dict(schema)
    filtered["properties"] = visible_props

    required = [arg for arg in schema.get("required", []) if arg in visible_props]
    if required:
        filtered["required"] = required
    else:
        filtered.pop("required", None)

    return filtered


def _apply_arg_behaviors(spec: ToolSpec, call_args: Dict[str, Any] | None) -> Dict[str, Any]:
    args = dict(call_args or {})
    behaviors = spec.get("arg_behaviors") or {}
    for name, metadata in behaviors.items():
        mode = _normalize_mode(metadata)
        if mode is ArgMode.AGENT_OVERRIDE and "default" in metadata:
            args[name] = metadata["default"]
        elif mode is ArgMode.LLM_HIDDEN and "default" in metadata:
            args[name] = metadata["default"]
    return args


async def invoke_tool(
    tool_id: str,
    call_args: Dict[str, Any] | None = None,
    *,
    per_call_timeout_ms: int | None = None,
) -> Dict[str, Any]:
    """Invoke a registered tool with argument behavior enforcement."""
    spec = get_tool(tool_id)
    if spec is None:
        raise LookupError(f"Unknown tool '{tool_id}'")

    handler = spec.get("handler")
    if handler is None:
        raise RuntimeError(f"Tool '{tool_id}' is missing a handler")

    args = _apply_arg_behaviors(spec, call_args)

    timeout_ms = per_call_timeout_ms or spec.get("timeout_ms") or DEFAULT_TIMEOUT_MS
    timeout_seconds = max(0.001, timeout_ms / 1000.0)

    async def _run() -> Dict[str, Any]:
        return await handler(args)

    return await asyncio.wait_for(_run(), timeout=timeout_seconds)


__all__ = ["DEFAULT_TIMEOUT_MS", "filter_schema_visible", "invoke_tool"]
