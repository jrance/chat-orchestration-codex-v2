"""Simple echo tool used for validation and tests."""

from __future__ import annotations

from typing import Any, Dict

from ..registry import register_tool


async def _echo(args: Dict[str, Any]) -> Dict[str, Any]:
    return {"echo": args}


def register() -> None:
    """Register the builtin echo tool."""
    register_tool(
        {
            "id": "tool:echo",
            "name": "Echo Tool",
            "description": "Echoes the provided arguments for debugging.",
            "args_schema": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            "arg_behaviors": {},
            "handler": _echo,
            "timeout_ms": 2_000,
        }
    )


__all__ = ["register"]
