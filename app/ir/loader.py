"""Runtime IR loader that prepares agent prompts."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Mapping, Tuple

from app.prompt.assembler import assemble_system_prompt
from app.tools import ArgMode, list_tools
from app.tools.types import ArgSpec, ToolSpec
from app.validation.ir_validator import validate_and_normalize


def _tool_spec_from_node(node: dict[str, Any], base_specs: Dict[str, ToolSpec]) -> ToolSpec:
    data = node.get("data") or {}
    tool_id = str(data.get("toolId") or node.get("id") or "")
    base_spec = deepcopy(base_specs.get(tool_id) or {})

    spec: ToolSpec = {
        "id": tool_id,
        "name": str(data.get("name") or node.get("label") or base_spec.get("name") or tool_id),
        "description": str(
            data.get("description") or node.get("description") or base_spec.get("description") or ""
        ),
        "args_schema": deepcopy(
            data.get("argsSchema") or base_spec.get("args_schema") or {"type": "object", "properties": {}}
        ),
        "arg_behaviors": deepcopy(base_spec.get("arg_behaviors") or {}),
    }

    if "handler" in base_spec:
        spec["handler"] = base_spec["handler"]
    if data.get("timeoutMs") is not None:
        spec["timeout_ms"] = int(data["timeoutMs"])
    elif "timeout_ms" in base_spec:
        spec["timeout_ms"] = base_spec["timeout_ms"]

    overrides = data.get("parameterOverrides") or {}
    behaviors: Dict[str, ArgSpec] = spec.get("arg_behaviors") or {}
    for arg_name, override in overrides.items():
        if not isinstance(override, Mapping):
            continue
        mode = override.get("mode")
        value = override.get("value")
        if mode == ArgMode.LLM_HIDDEN.value:
            behaviors[arg_name] = {"mode": ArgMode.LLM_HIDDEN, "default": value}
        elif mode == ArgMode.AGENT_OVERRIDE.value:
            behaviors[arg_name] = {"mode": ArgMode.AGENT_OVERRIDE, "default": value}
    spec["arg_behaviors"] = behaviors
    return spec


def _resolve_tool_specs(node_by_id: Dict[str, dict]) -> Dict[str, ToolSpec]:
    base_specs = list_tools()
    result: Dict[str, ToolSpec] = {}
    for node_id, node in node_by_id.items():
        if node.get("kind") != "tool":
            continue
        result[node_id] = _tool_spec_from_node(node, base_specs)
    return result


def _resolve_mcp_servers(node_by_id: Dict[str, dict]) -> Dict[str, dict[str, Any]]:
    servers: Dict[str, dict[str, Any]] = {}
    for node_id, node in node_by_id.items():
        if node.get("kind") != "mcpServer":
            continue
        servers[node_id] = deepcopy(node.get("data") or {})
    return servers


def build_runtime_plan(
    ir: Dict[str, Any],
    runtime_vars: Mapping[str, Any] | None = None,
) -> Tuple[bool, Dict[str, Any], List[str]]:
    """Validate the IR and build a runtime plan with assembled prompts."""
    ok, normalized, errors, warnings = validate_and_normalize(ir)
    if not ok or not normalized:
        return False, {}, errors

    runtime_vars = runtime_vars or {}
    node_by_id = normalized["nodeById"]
    agent_prompts: Dict[str, str] = {}

    for node_id, node in node_by_id.items():
        if node.get("kind") != "agent.codeless":
            continue
        data = node.get("data") or {}
        agent_prompts[node_id] = assemble_system_prompt(data, runtime_vars=runtime_vars)

    tool_specs = _resolve_tool_specs(node_by_id)
    agent_tool_specs: Dict[str, List[ToolSpec]] = {}
    agent_bindings = (normalized.get("resolved") or {}).get("agentToolBindings") or {}
    for agent_id, tool_ids in agent_bindings.items():
        agent_tool_specs[agent_id] = [tool_specs[tool_id] for tool_id in tool_ids if tool_id in tool_specs]

    plan = {
        "entryId": normalized["entryId"],
        "nodeById": node_by_id,
        "edges": normalized["edges"],
        "resolved": normalized.get("resolved", {}),
        "warnings": warnings,
        "agentPrompts": agent_prompts,
        "toolSpecs": tool_specs,
        "agentToolSpecs": agent_tool_specs,
        "mcpServers": _resolve_mcp_servers(node_by_id),
    }

    return True, plan, errors


__all__ = ["build_runtime_plan"]
