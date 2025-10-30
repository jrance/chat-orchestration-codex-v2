"""Compiler for ``concurrent`` orchestration nodes."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING, Dict, Sequence

from app.runtime.patterns.concurrent import build_concurrent_runner

from . import register

if TYPE_CHECKING:
    from app.compiler.builder import GraphBuilder


def compile_concurrent(builder: "GraphBuilder", node: dict[str, Any], plan: dict[str, Any]) -> None:
    """Compile a concurrent node into the runtime graph."""

    node_id = node["id"]
    runtime_concurrent = (plan.get("runtimeConcurrent") or {}).get(node_id)
    if not runtime_concurrent:
        raise ValueError(f"Concurrent node '{node_id}' missing runtime metadata")

    cfg = runtime_concurrent.get("cfg") or {}
    children: Sequence[str] = runtime_concurrent.get("children") or ()
    node_by_id: Dict[str, dict] = plan.get("nodeById") or {}
    agent_prompts = plan.get("agentPrompts") or {}
    agent_tool_specs = plan.get("agentToolSpecs") or {}
    mcp_servers = plan.get("mcpServers") or {}

    runner = build_concurrent_runner(
        node,
        config=cfg,
        child_ids=children,
        node_by_id=node_by_id,
        agent_prompts=agent_prompts,
        agent_tool_specs=agent_tool_specs,
        mcp_servers=mcp_servers,
    )
    builder.add_node(node_id, runner)


register("concurrent", compile_concurrent)
