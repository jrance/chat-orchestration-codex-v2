"""Compiler for ``router`` nodes."""

from __future__ import annotations

from typing import Any, Mapping, Sequence, TYPE_CHECKING

from langgraph.graph import END

from app.runtime.patterns.router import build_router_runner

from ..types import OrchestratorState
from . import register

if TYPE_CHECKING:
    from app.compiler.builder import GraphBuilder


def compile_router(builder: "GraphBuilder", node: dict[str, Any], plan: dict[str, Any]) -> None:
    """Compile a router node into the graph using normalized runtime metadata."""

    node_id = node["id"]
    runtime_router = (plan.get("runtimeRouter") or {}).get(node_id)
    if not runtime_router:
        raise ValueError(f"Router node '{node_id}' missing runtime metadata")

    cfg = runtime_router.get("cfg") or {}
    targets: Sequence[str] = runtime_router.get("targets") or ()
    child_ids: Sequence[str] = runtime_router.get("children") or ()
    target_to_child: Mapping[str, str] = runtime_router.get("target_to_child") or {}
    child_labels: Mapping[str, str] = runtime_router.get("child_labels") or {}

    runner = build_router_runner(
        node,
        config=cfg,
        child_ids=child_ids,
        targets=targets,
        target_to_child=target_to_child,
        child_labels=child_labels,
    )
    builder.add_node(node_id, runner)

    normalized_children = tuple(child_ids)

    def selector(state: OrchestratorState) -> str | None:
        scratch = state.get("scratch") or {}
        router_store = scratch.get("router") or {}
        node_state = router_store.get(node_id) or {}
        decision = node_state.get("decision") or {}
        target_node_id = (
            decision.get("targetNodeId")
            or decision.get("target_node_id")
            or decision.get("targetnodeid")
        )
        if isinstance(target_node_id, str) and target_node_id in normalized_children:
            return target_node_id
        return END

    builder.register_conditional(node_id, selector, normalized_children, default_to_end=True)


register("router", compile_router)
