"""Full orchestration IR validator combining schema and topology checks."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple

from jsonschema import Draft202012Validator, ValidationError

from app.validation.schema_loader import load_schema
from app.validation.topology import (
    build_graph,
    check_no_outgoing_constraints,
    find_roots,
    index_nodes,
    reachable_from,
)


def _error_path(err: ValidationError) -> str:
    """Create a JSON pointer-like string for where the validation error occurred."""
    parts = [str(part) for part in err.absolute_path]
    return "/".join(parts) or "<root>"


def _format_jsonschema_error(err: ValidationError) -> str:
    """Render a deterministic validation error message."""
    return f"{_error_path(err)}: {err.message}"


def _sort_errors(errors: Iterable[ValidationError]) -> List[ValidationError]:
    """Sort jsonschema errors for deterministic output."""
    return sorted(
        errors,
        key=lambda e: (tuple(str(part) for part in e.absolute_path), e.message),
    )


def run_jsonschema_validation(ir: Dict[str, Any]) -> List[str]:
    """Validate the IR against the JSON schema, returning all error messages."""
    schema = load_schema()
    validator = Draft202012Validator(schema)
    validation_errors = _sort_errors(validator.iter_errors(ir))
    return [_format_jsonschema_error(err) for err in validation_errors]


def resolve_tools_for_agents(node_by_id: Dict[str, dict]) -> Tuple[Dict[str, List[str]], List[str]]:
    """Resolve tool ids attached to agent nodes, reporting structural issues."""
    errors: List[str] = []
    bindings: Dict[str, List[str]] = {}

    for node_id, node in node_by_id.items():
        kind = node.get("kind")
        if not isinstance(kind, str) or not kind.startswith("agent."):
            continue

        data = node.get("data") or {}
        tools_section = data.get("tools") or {}
        attached = tools_section.get("attached", [])

        if attached is None:
            attached = []

        if not isinstance(attached, list):
            errors.append(f"Agent '{node_id}' tools.attached must be a list")
            bindings[node_id] = []
            continue

        resolved: List[str] = []
        for idx, tool_id in enumerate(attached):
            if not isinstance(tool_id, str) or not tool_id:
                errors.append(
                    f"Agent '{node_id}' attached entry at index {idx} must be a non-empty string",
                )
                continue

            tool_node = node_by_id.get(tool_id)
            if tool_node is None:
                errors.append(f"Agent '{node_id}' references missing tool id '{tool_id}'")
                continue

            if tool_node.get("kind") != "tool":
                errors.append(f"Agent '{node_id}' attached id '{tool_id}' is not a tool node")
                continue

            resolved.append(tool_id)

        bindings[node_id] = resolved

    return bindings, errors


def validate_and_normalize(
    ir: Dict[str, Any],
) -> Tuple[bool, Dict[str, Any] | None, List[str], List[str]]:
    """Run validation stages and produce a normalized snapshot if successful."""
    errors: List[str] = []
    warnings: List[str] = []

    # 1) JSON Schema validation for structural shape.
    schema_errors = run_jsonschema_validation(ir)
    if schema_errors:
        errors.extend(schema_errors)
        return False, None, errors, warnings

    nodes = ir.get("nodes", [])
    edges = ir.get("edges", [])

    # 2) Index nodes and catch duplicate identifiers.
    node_by_id, index_errors = index_nodes(nodes)
    if index_errors:
        errors.extend(index_errors)
        return False, None, errors, warnings

    # 3) Edge integrity and topology constraints.
    adjacency, indegree = build_graph(edges)

    for edge in edges:
        edge_id = edge.get("id", "<no-id>")
        from_id = edge.get("from")
        to_id = edge.get("to")

        if from_id not in node_by_id:
            errors.append(f"Edge '{edge_id}' references missing 'from' node '{from_id}'")
        if to_id not in node_by_id:
            errors.append(f"Edge '{edge_id}' references missing 'to' node '{to_id}'")

    errors.extend(check_no_outgoing_constraints(edges, node_by_id))

    if errors:
        return False, None, errors, warnings

    # 4) Resolve entry point.
    entry_id = ir.get("entryId")
    if not entry_id:
        roots = sorted(find_roots(set(node_by_id.keys()), indegree))
        if len(roots) == 1:
            entry_id = roots[0]
        elif len(roots) == 0:
            errors.append(
                "No entryId provided and no root node could be determined (graph has cycles or all nodes have incoming edges).",
            )
        else:
            errors.append(f"No entryId provided and multiple root nodes found: {roots}")
    elif entry_id not in node_by_id:
        errors.append(f"entryId '{entry_id}' is not a valid node id")

    if errors:
        return False, None, errors, warnings

    # 5) Ensure full reachability from entry node.
    reachable = reachable_from(entry_id, adjacency)
    unreachable = sorted(set(node_by_id.keys()) - reachable)
    if unreachable:
        errors.append(f"Unreachable nodes from entryId '{entry_id}': {unreachable}")
        return False, None, errors, warnings

    # 6) Resolve agent tool bindings.
    tool_bindings, tool_binding_errors = resolve_tools_for_agents(node_by_id)
    if tool_binding_errors:
        errors.extend(tool_binding_errors)
        return False, None, errors, warnings

    normalized = {
        "entryId": entry_id,
        "nodeById": node_by_id,
        "edges": edges,
        "resolved": {"agentToolBindings": tool_bindings},
    }

    return True, normalized, errors, warnings

