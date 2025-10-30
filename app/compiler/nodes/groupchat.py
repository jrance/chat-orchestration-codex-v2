"""Compiler for ``groupchat`` orchestration nodes."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING, Mapping

from app.runtime.patterns.groupchat import build_groupchat_runner

from . import register

if TYPE_CHECKING:
    from app.compiler.builder import GraphBuilder


def compile_groupchat(builder: "GraphBuilder", node: dict[str, Any], plan: dict[str, Any]) -> None:
    """Compile a groupchat node into the runtime graph."""

    node_id = node["id"]
    runtime_groupchat = (plan.get("runtimeGroupchat") or {}).get(node_id)
    if not isinstance(runtime_groupchat, Mapping):
        raise ValueError(f"GroupChat node '{node_id}' missing runtime metadata")

    node_by_id: Mapping[str, Mapping[str, Any]] = plan.get("nodeById") or {}
    agent_prompts = plan.get("agentPrompts") or {}
    agent_tool_specs = plan.get("agentToolSpecs") or {}
    mcp_servers = plan.get("mcpServers") or {}

    runner = build_groupchat_runner(
        node,
        runtime_groupchat,
        node_by_id=node_by_id,
        agent_prompts=agent_prompts,
        agent_tool_specs=agent_tool_specs,
        mcp_servers=mcp_servers,
    )
    builder.add_node(node_id, runner)


register("groupchat", compile_groupchat)
