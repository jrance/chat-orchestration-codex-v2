"""LangGraph compiler that translates normalized plans into StateGraph apps."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List

from langgraph.graph import END, StateGraph

from .nodes import ensure_builtin_compilers, get as get_compiler
from .types import OrchestratorState

SelectorFn = Callable[[OrchestratorState], str | None]


@dataclass(frozen=True)
class ConditionalRegistration:
    """Metadata required to add conditional edges after nodes are registered."""

    selector: SelectorFn
    targets: tuple[str, ...]
    default_to_end: bool


def _identity(state: OrchestratorState) -> OrchestratorState:
    """Simple passthrough reducer."""

    return state


class GraphBuilder:
    """Build a LangGraph StateGraph from a normalized orchestration plan."""

    def __init__(self, plan: Dict[str, Any]) -> None:
        if "entryId" not in plan:
            raise ValueError("Compilation plan missing 'entryId'")

        ensure_builtin_compilers()

        self.plan = plan
        self.node_by_id: Dict[str, dict[str, Any]] = dict(plan.get("nodeById") or {})
        self.edges: List[dict[str, Any]] = list(plan.get("edges") or [])
        self.entry_id: str = plan["entryId"]
        self.graph = StateGraph(OrchestratorState)
        self._conditional: Dict[str, ConditionalRegistration] = {}

    # -- public helpers -----------------------------------------------------------------

    def add_node(
        self,
        node_id: str,
        fn: Callable[[OrchestratorState], OrchestratorState],
    ) -> None:
        """Register a node implementation in the underlying graph."""

        self.graph.add_node(node_id, fn)

    def register_conditional(
        self,
        node_id: str,
        selector: SelectorFn,
        targets: Iterable[str],
        *,
        default_to_end: bool = False,
    ) -> None:
        """Record a conditional routing hook to be applied after edges are wired."""

        deduped = list(dict.fromkeys(t for t in targets if isinstance(t, str)))
        filtered = tuple(t for t in deduped if t in self.node_by_id)
        self._conditional[node_id] = ConditionalRegistration(selector, filtered, default_to_end)

    # -- compilation phases --------------------------------------------------------------

    def compile_nodes(self) -> None:
        """Materialize graph nodes using registered compilers."""

        for node_id, node in self.node_by_id.items():
            kind = node.get("kind", "")
            compiler = get_compiler(kind)
            if compiler:
                compiler(self, node, self.plan)
                continue
            # Unknown kinds become no-ops to keep graphs buildable.
            self.add_node(node_id, _identity)

    def _apply_conditional_edges(self) -> None:
        for node_id, registration in self._conditional.items():
            mapping = {target: target for target in registration.targets}
            if mapping:
                self.graph.add_conditional_edges(node_id, registration.selector, mapping)
            else:
                self.graph.add_conditional_edges(node_id, registration.selector)

    def compile_edges(self) -> None:
        """Wire explicit and implied edges for the compiled nodes."""

        has_outgoing: Dict[str, bool] = {}

        for edge in self.edges:
            src = edge.get("from")
            dst = edge.get("to")
            if not isinstance(src, str) or not isinstance(dst, str):
                continue
            if src not in self.node_by_id or dst not in self.node_by_id:
                continue
            self.graph.add_edge(src, dst)
            has_outgoing[src] = True

        self._apply_conditional_edges()

        for node_id, registration in self._conditional.items():
            if registration.targets or registration.default_to_end:
                has_outgoing[node_id] = True

        for node_id in self.node_by_id:
            if not has_outgoing.get(node_id):
                self.graph.add_edge(node_id, END)

    # -- entrypoint ----------------------------------------------------------------------

    def build(self):
        """Compile the plan into a runnable LangGraph app."""

        if self.entry_id not in self.node_by_id:
            raise ValueError(f"Entry node '{self.entry_id}' not present in plan")

        self.compile_nodes()
        self.compile_edges()
        self.graph.set_entry_point(self.entry_id)
        return self.graph.compile()
