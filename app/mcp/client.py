"""Phase-one MCP client stub used for integration tests."""

from __future__ import annotations

from typing import Any, Dict


async def call_mcp_tool(server_cfg: Dict[str, Any], tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Return a deterministic echo payload for the requested MCP tool."""
    return {
        "mcp_server": server_cfg.get("url"),
        "tool": tool_name,
        "args": dict(args),
    }


__all__ = ["call_mcp_tool"]
