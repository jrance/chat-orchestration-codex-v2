"""Graph utilities for validating orchestration IR topologies."""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Set, Tuple

# Minimal kind constraints (phase-1):
# - Tool/MCP/Output nodes must not have outgoing edges
NO_OUTGOING = {"tool", "mcpServer", "output"}


def index_nodes(nodes: List[dict]) -> Tuple[Dict[str, dict], List[str]]:
    """Index nodes by id and surface duplicate/missing identifiers."""
    errors: List[str] = []
    by_id: Dict[str, dict] = {}
    for idx, node in enumerate(nodes):
        node_id = node.get("id")
        if not node_id:
            errors.append(f"Node at index {idx} is missing an id")
            continue
        if not isinstance(node_id, str):
            errors.append(f"Node id at index {idx} must be a string")
            continue
        if node_id in by_id:
            errors.append(f"Duplicate node id '{node_id}'")
            continue
        by_id[node_id] = node
    return by_id, errors


def build_graph(edges: List[dict]) -> Tuple[Dict[str, List[str]], Dict[str, int]]:
    """Build adjacency and indegree maps from a list of edges."""
    adjacency: Dict[str, List[str]] = defaultdict(list)
    indegree: Dict[str, int] = {}

    for edge in edges:
        from_id = edge.get("from")
        to_id = edge.get("to")
        if from_id is None or to_id is None:
            # jsonschema validation should already catch this, but guard anyway.
            raise ValueError("Edge is missing 'from' or 'to'")

        adjacency[from_id].append(to_id)
        indegree[to_id] = indegree.get(to_id, 0) + 1
        indegree.setdefault(from_id, indegree.get(from_id, 0))

    return dict(adjacency), indegree


def find_roots(node_ids: Set[str], indegree: Dict[str, int]) -> List[str]:
    """Return node ids with zero indegree."""
    return [node_id for node_id in node_ids if indegree.get(node_id, 0) == 0]


def reachable_from(start: str, adjacency: Dict[str, List[str]]) -> Set[str]:
    """Return set of node ids reachable via forward traversal from ``start``."""
    seen: Set[str] = set()
    stack: List[str] = [start]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        for neighbor in adjacency.get(current, []):
            if neighbor not in seen:
                stack.append(neighbor)
    return seen


def check_no_outgoing_constraints(edges: List[dict], node_by_id: Dict[str, dict]) -> List[str]:
    """Ensure nodes with constrained kinds do not declare outgoing edges."""
    errors: List[str] = []
    outgoing_counts: Dict[str, int] = defaultdict(int)

    for edge in edges:
        from_id = edge.get("from")
        if from_id is not None:
            outgoing_counts[from_id] += 1

    for node_id, count in outgoing_counts.items():
        node = node_by_id.get(node_id)
        if not node:
            continue
        kind = node.get("kind")
        if kind in NO_OUTGOING and count > 0:
            errors.append(f"Node '{node_id}' of kind '{kind}' must not have outgoing edges")

    return errors

