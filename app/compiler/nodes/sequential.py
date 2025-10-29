"""Compiler for ``sequential`` orchestration nodes."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ..types import OrchestratorState
from . import register

if TYPE_CHECKING:
    from app.compiler.builder import GraphBuilder


def _passthrough(state: OrchestratorState) -> OrchestratorState:
    return state


def compile_sequential(builder: "GraphBuilder", node: dict[str, Any], plan: dict[str, Any]) -> None:
    """Register a sequential grouping node as a passthrough."""

    node_id = node["id"]
    builder.add_node(node_id, _passthrough)


register("sequential", compile_sequential)
