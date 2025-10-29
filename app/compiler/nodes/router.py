"""Compiler for ``router`` nodes."""

from __future__ import annotations

from typing import Any, List, TYPE_CHECKING

from langgraph.graph import END

from ..types import OrchestratorState
from . import register

if TYPE_CHECKING:
    from app.compiler.builder import GraphBuilder


def compile_router(builder: "GraphBuilder", node: dict[str, Any], plan: dict[str, Any]) -> None:
    """Compile a router node into the graph."""

    node_id = node["id"]
    data = node.get("data") or {}
    targets: List[str] = [t for t in data.get("targets") or [] if isinstance(t, str)]
    min_confidence = float(data.get("minConfidence") or 0.0)

    def router_fn(state: OrchestratorState) -> OrchestratorState:
        choice = targets[0] if targets else None
        route = {
            "target": choice,
            "confidence": 1.0 if choice else 0.0,
            "minConfidence": min_confidence,
        }
        return {**state, "route": route}

    builder.add_node(node_id, router_fn)

    def selector(state: OrchestratorState) -> str | None:
        route = state.get("route") or {}
        target = route.get("target")
        if isinstance(target, str) and target:
            return target
        return END

    builder.register_conditional(node_id, selector, targets, default_to_end=True)


register("router", compile_router)
