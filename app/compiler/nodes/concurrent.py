"""Compiler for ``concurrent`` orchestration nodes."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING, Dict, List

from ..types import OrchestratorState
from . import register

if TYPE_CHECKING:
    from app.compiler.builder import GraphBuilder


def _passthrough(state: OrchestratorState) -> OrchestratorState:
    return state


def compile_concurrent(builder: "GraphBuilder", node: dict[str, Any], plan: dict[str, Any]) -> None:
    """Normalize concurrency settings for runtime consumption."""

    node_id = node["id"]
    data = node.get("data") or {}
    node_by_id: Dict[str, dict] = plan.get("nodeById") or {}
    edges: List[dict] = plan.get("edges") or []

    timeout_seconds = data.get("timeoutSeconds")
    if timeout_seconds is None:
        timeout_seconds = data.get("timeoutSec")
    try:
        timeout_seconds = float(timeout_seconds)
    except (TypeError, ValueError):
        timeout_seconds = 120.0
    if timeout_seconds <= 0:
        timeout_seconds = 120.0

    merge_cfg = data.get("merge") or {}
    strategy = str(merge_cfg.get("strategy") or "FirstBest")
    synth_prompt = merge_cfg.get("synthPrompt")
    first_best_threshold = merge_cfg.get("firstBestThreshold")
    try:
        first_best_threshold = float(first_best_threshold) if first_best_threshold is not None else None
    except (TypeError, ValueError):
        first_best_threshold = None
    cancel_remaining = data.get("cancelRemainingOnDecision")
    if cancel_remaining is None:
        cancel_remaining = strategy in ("FirstBest", "HighestScore")

    max_parallelism = data.get("maxParallelism")
    try:
        max_parallelism_int = int(max_parallelism)
    except (TypeError, ValueError):
        child_parallelisms: List[int] = []
        for child_id in (edge.get("to") for edge in edges if edge.get("from") == node_id):
            child_node = node_by_id.get(child_id) or {}
            tools_section = (child_node.get("data") or {}).get("tools") or {}
            try:
                child_parallelisms.append(int(tools_section.get("parallelism")))
            except (TypeError, ValueError):
                continue
        max_parallelism_int = max(child_parallelisms) if child_parallelisms else 4
    else:
        max_parallelism_int = max_parallelism_int if max_parallelism_int > 0 else 4

    if max_parallelism_int <= 0:
        max_parallelism_int = 4

    children = [
        edge.get("to")
        for edge in edges
        if isinstance(edge.get("from"), str)
        and edge.get("from") == node_id
        and isinstance(edge.get("to"), str)
    ]

    runtime_payload = plan.setdefault("runtimeConcurrent", {})
    runtime_payload[node_id] = {
        "id": node_id,
        "kind": "concurrent",
        "label": node.get("label") or "",
        "cfg": {
            "strategy": strategy,
            "timeout_seconds": timeout_seconds,
            "max_parallelism": max_parallelism_int,
            "cancel_remaining_on_decision": bool(cancel_remaining),
            "synth_prompt": synth_prompt if isinstance(synth_prompt, str) and synth_prompt.strip() else None,
            "first_best_threshold": first_best_threshold,
        },
        "children": children,
    }

    builder.add_node(node_id, _passthrough)


register("concurrent", compile_concurrent)
