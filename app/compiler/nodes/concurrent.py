"""Compiler for ``concurrent`` orchestration nodes."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ..types import OrchestratorState
from . import register

if TYPE_CHECKING:
    from app.compiler.builder import GraphBuilder


def _passthrough(state: OrchestratorState) -> OrchestratorState:
    return state


def compile_concurrent(builder: "GraphBuilder", node: dict[str, Any], plan: dict[str, Any]) -> None:
    """Register a concurrent node as a passthrough placeholder."""

    node_id = node["id"]
    builder.add_node(node_id, _passthrough)


register("concurrent", compile_concurrent)
