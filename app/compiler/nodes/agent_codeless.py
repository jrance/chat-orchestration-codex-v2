"""Compiler for ``agent.codeless`` nodes backed by the runtime agent."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from app.runtime.agents.codeless import build_codeless_runner

from . import register

if TYPE_CHECKING:
    from app.compiler.builder import GraphBuilder


def compile_agent_codeless(builder: "GraphBuilder", node: dict[str, Any], plan: dict[str, Any]) -> None:
    """Add the runtime-backed codeless agent to the graph."""

    node_id = node["id"]
    agent_prompts = plan.get("agentPrompts") or {}
    prompt = agent_prompts.get(node_id, "")
    builder.add_node(node_id, build_codeless_runner(node, prompt))


register("agent.codeless", compile_agent_codeless)
