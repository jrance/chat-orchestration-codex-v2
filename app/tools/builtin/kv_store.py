"""In-memory key/value store tool for exercising stateful behaviors in tests."""

from __future__ import annotations

from typing import Any, Dict

from ..registry import register_tool

_STORE: Dict[str, Any] = {}


async def _kv(args: Dict[str, Any]) -> Dict[str, Any]:
    action = str(args.get("action") or "get").lower()
    key = str(args.get("key") or "")
    if not key:
        raise ValueError("Key is required")

    if action == "get":
        return {"value": _STORE.get(key)}

    if action == "set":
        value = args.get("value")
        _STORE[key] = value
        return {"value": value}

    raise ValueError(f"Unsupported action '{action}'")


def reset_store() -> None:
    """Reset the in-memory store. Used by tests."""
    _STORE.clear()


def register() -> None:
    """Register the builtin key/value tool."""
    register_tool(
        {
            "id": "tool:kv",
            "name": "Key/Value Store",
            "description": "Get or set values in an in-memory store.",
            "args_schema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["get", "set"]},
                    "key": {"type": "string"},
                    "value": {},
                },
                "required": ["action", "key"],
            },
            "arg_behaviors": {},
            "handler": _kv,
            "timeout_ms": 2_000,
        }
    )


__all__ = ["register", "reset_store"]
