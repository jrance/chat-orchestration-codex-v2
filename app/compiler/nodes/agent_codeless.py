"""Compiler for ``agent.codeless`` nodes."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ..types import OrchestratorState
from . import register

if TYPE_CHECKING:
    from app.compiler.builder import GraphBuilder


def _assistant_stub(node_id: str, prompt: str):
    """Create a deterministic assistant response for stubs."""

    def _run(state: OrchestratorState) -> OrchestratorState:
        messages = list(state.get("messages") or [])
        messages.append({"role": "assistant", "content": f"[{node_id}] {prompt[:120]}"})
        return {**state, "messages": messages}

    return _run


def compile_agent_codeless(builder: "GraphBuilder", node: dict[str, Any], plan: dict[str, Any]) -> None:
    """Add a stub agent node that appends an assistant message."""

    node_id = node["id"]
    agent_prompts = plan.get("agentPrompts") or {}
    prompt = agent_prompts.get(node_id, "")
    builder.add_node(node_id, _assistant_stub(node_id, prompt))


register("agent.codeless", compile_agent_codeless)
